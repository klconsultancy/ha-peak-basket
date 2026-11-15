"""The coordinator: reads the source entities, tracks the window and runs the model."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import partial
from typing import TYPE_CHECKING, Any

from homeassistant.components.recorder import history
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_UNIT_OF_MEASUREMENT,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    UnitOfEnergy,
    UnitOfPower,
)
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, State, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.helpers.recorder import get_instance
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import EnergyConverter, PowerConverter

from .const import (
    CONF_EXTRA_A,
    CONF_GRID_IMPORT_ENERGY,
    CONF_GRID_POWER,
    CONF_LOAD_POWER,
    CONF_LOAD_VOLTAGES,
    CONF_MARGIN,
    CONF_MAX_A,
    CONF_MAX_AGE,
    CONF_METER_VOLTAGES,
    CONF_MIN_A,
    CONF_MIN_CHARGE,
    CONF_MONTH_PEAK,
    CONF_PEAK_GOAL,
    CONF_PRIORITY,
    CONF_PROFILE,
    CONF_REACTION,
    CONF_WINDOW_AVERAGE,
    DEFAULT_ENTRY_OPTIONS,
    DEFAULT_LOAD,
    DOMAIN,
    LOGGER,
    SUBENTRY_LOAD,
    TICK_SECONDS,
)
from .model import PROFILES, ModelInput, ModelParams, ModelResult, compute_target, evaluate
from .model.window import window_start

if TYPE_CHECKING:
    from collections.abc import Mapping

    from homeassistant.core import Event, EventStateChangedData

type PeakBasketConfigEntry = ConfigEntry[PeakBasketCoordinator]

SECONDS_PER_HOUR = 3600
POWER_STEP_W = 10


def floor_power(value: float) -> float:
    """Round a power down to the published step, so it never exceeds the model's.

    A unit conversion leaves noise such as 1649.9999999999998 W for 1.65 kW: that is
    removed first, or it would round down a whole step.
    """
    return math.floor(round(value, 6) / POWER_STEP_W) * POWER_STEP_W


@dataclass(frozen=True)
class Load:
    """A controllable load: a subentry of the grid connection."""

    subentry_id: str
    title: str
    power_entity: str
    voltage_entities: list[str]
    priority: float
    params: ModelParams


@dataclass(frozen=True)
class PeakBasketData:
    """One evaluation: the grid connection's basket, and the decision for each load."""

    connection: ModelResult
    """The basket with every load taken out of the house; its mode and limits mean nothing."""
    loads: dict[str, ModelResult]
    """By subentry id."""


class PeakBasketCoordinator(DataUpdateCoordinator[PeakBasketData]):
    """Evaluate the model on every grid-power update, and on a safety tick."""

    config_entry: PeakBasketConfigEntry

    def __init__(self, hass: HomeAssistant, entry: PeakBasketConfigEntry) -> None:
        """Read the sources and the tunables of the connection and its loads."""
        super().__init__(hass, LOGGER, config_entry=entry, name=DOMAIN, update_interval=None)
        self._src: dict[str, str | list[str]] = dict(entry.data)
        opts: dict[str, float] = {**DEFAULT_ENTRY_OPTIONS, **entry.options}
        self.profile = PROFILES[self._entity(CONF_PROFILE)]
        self.params = ModelParams(margin_w=opts[CONF_MARGIN])
        self.loads = sorted(
            (
                _load(subentry.subentry_id, subentry.title, subentry.data, opts[CONF_MARGIN])
                for subentry in entry.subentries.values()
                if subentry.subentry_type == SUBENTRY_LOAD
            ),
            key=lambda load: (load.priority, load.title),
        )
        self.peak_goal_w = opts[CONF_PEAK_GOAL]
        self.target_w = self.peak_goal_w
        self._max_age = timedelta(seconds=opts[CONF_MAX_AGE])
        self._window_start = window_start(dt_util.now(), self.profile.window_s)
        self._counter_start_wh: float | None = None
        self._last_counter: tuple[float, datetime] | None = None
        self._published_at = dt_util.now()
        self._unsubs: list[CALLBACK_TYPE] = []

    async def async_start(self) -> None:
        """Restore the running window, subscribe to the grid power and evaluate once."""
        self._window_start = window_start(dt_util.now(), self.profile.window_s)
        if not self._src.get(CONF_WINDOW_AVERAGE):
            self._counter_start_wh = await self._async_counter_at(self._window_start)
            if self._counter_start_wh is None:
                LOGGER.info("No import counter value at the window start: fallback until the next")
        self._unsubs.append(
            async_track_state_change_event(
                self.hass, [self._entity(CONF_GRID_POWER)], self._on_grid_change
            )
        )
        self._unsubs.append(
            async_track_time_interval(self.hass, self._on_tick, timedelta(seconds=TICK_SECONDS))
        )
        self._evaluate()

    @callback
    def async_stop(self) -> None:
        """Unsubscribe from everything."""
        while self._unsubs:
            self._unsubs.pop()()

    @callback
    def _on_grid_change(self, _event: Event[EventStateChangedData]) -> None:
        self._evaluate(force=False)

    @callback
    def _on_tick(self, _now: datetime) -> None:
        self._evaluate()

    @callback
    def _evaluate(self, *, force: bool = True) -> None:
        """Run the model; publish on every tick, between ticks only when a limit drops.

        A meter can report every second, and every published change is a recorder row.
        Between ticks a result waits, unless a load's mode changes or its grid limit or
        allowed power goes down: a consumer must learn that at once.

        Loads go in priority order. A load sees the loads before it as house, and the
        loads after it yield to it: they get what it leaves unused right now.
        """
        now = dt_util.now()
        start = window_start(now, self.profile.window_s)
        used_wh = self._used_wh(start, now)

        month_peak = None
        if peak_entity := self._src.get(CONF_MONTH_PEAK):
            month_peak = self._power_w(str(peak_entity), now, check_age=False)
        self.target_w = compute_target(self.profile, self.peak_goal_w, month_peak)

        seconds = (now - start).total_seconds()
        grid = self._power_w(self._entity(CONF_GRID_POWER), now)
        meter = [self._number(e, now) for e in self._entities(CONF_METER_VOLTAGES)]
        powers = [self._power_w(load.power_entity, now) for load in self.loads]

        connection = evaluate(
            ModelInput(
                target_w=self.target_w,
                seconds_in_window=seconds,
                grid_import_w=grid,
                load_w=_sum(powers),
                window_import_wh=used_wh,
                voltages=meter or None,
            ),
            self.profile,
            self.params,
        )
        results: dict[str, ModelResult] = {}
        for k, load in enumerate(self.loads):
            # Per phase: the load's voltage, then the meter's, then nominal (in the model).
            own = [self._number(e, now) for e in load.voltage_entities]
            voltages = [
                [src[i] if i < len(src) else None for src in (own, meter)]
                for i in range(max(len(own), len(meter)))
            ]
            results[load.subentry_id] = evaluate(
                ModelInput(
                    target_w=self.target_w,
                    seconds_in_window=seconds,
                    grid_import_w=grid,
                    load_w=powers[k],
                    window_import_wh=used_wh,
                    voltages=voltages or None,
                    other_loads_w=_sum(powers[k + 1 :]),
                ),
                self.profile,
                load.params,
            )

        data = PeakBasketData(connection=connection, loads=results)
        if force or self._must_publish(data, now):
            self._published_at = now
            self.async_set_updated_data(data)

    def _must_publish(self, data: PeakBasketData, now: datetime) -> bool:
        if now - self._published_at >= timedelta(seconds=TICK_SECONDS):
            return True
        for subentry_id, result in data.loads.items():
            previous = self.data.loads[subentry_id]
            if (
                result.mode != previous.mode
                or floor_power(result.limit_w) < floor_power(previous.limit_w)
                or floor_power(result.allowed_load_w) < floor_power(previous.allowed_load_w)
            ):
                return True
        return False

    def _used_wh(self, start: datetime, now: datetime) -> float | None:
        """Return the grid import since the window start.

        Preferred: the meter's running window average (DSMR 1.4.0, HomeWizard's
        average demand), which is energy so far over the window length, so it survives a
        restart. Otherwise: the difference of the import register since the window start.
        """
        if avg_entity := self._src.get(CONF_WINDOW_AVERAGE):
            state = self._fresh_state(str(avg_entity), now)
            if state is not None and state.last_reported < start:
                # The meter resets its average a second or two after the boundary; until
                # then it shows the previous window's. Next to nothing is used this early.
                return 0.0
            avg = self._power_w(str(avg_entity), now)
            return None if avg is None else max(avg, 0.0) * self.profile.window_s / SECONDS_PER_HOUR

        counter = self._energy_wh(self._entity(CONF_GRID_IMPORT_ENERGY), now)
        if start != self._window_start:
            # A new window: a full basket. The start value is the last fresh reading
            # before the boundary: it may count a few seconds of the previous window as
            # used, which errs on the safe side. Without one, the first reading after it.
            self._window_start = start
            self._counter_start_wh = counter
            if self._last_counter is not None and start - self._last_counter[1] <= self._max_age:
                self._counter_start_wh = self._last_counter[0]
        if counter is not None:
            self._last_counter = (counter, now)
        if counter is None or self._counter_start_wh is None:
            return None
        used = counter - self._counter_start_wh
        if used < 0:
            LOGGER.warning("The import counter went backwards: fallback for this window")
            self._counter_start_wh = None
            return None
        return used

    def _entity(self, key: str) -> str:
        return str(self._src[key])

    def _entities(self, key: str) -> list[str]:
        value = self._src.get(key) or []
        return [value] if isinstance(value, str) else list(value)

    def _fresh_state(
        self, entity_id: str, now: datetime, *, check_age: bool = True
    ) -> State | None:
        state = self.hass.states.get(entity_id)
        if state is None or state.state in {STATE_UNAVAILABLE, STATE_UNKNOWN}:
            return None
        if check_age and now - state.last_reported > self._max_age:
            return None
        return state

    def _number(self, entity_id: str, now: datetime, *, check_age: bool = True) -> float | None:
        value, _ = self._reading(entity_id, now, check_age=check_age)
        return value

    def _reading(
        self, entity_id: str, now: datetime, *, check_age: bool = True
    ) -> tuple[float | None, str | None]:
        state = self._fresh_state(entity_id, now, check_age=check_age)
        if state is None:
            return None, None
        try:
            return float(state.state), state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
        except ValueError:
            return None, None

    def _power_w(self, entity_id: str, now: datetime, *, check_age: bool = True) -> float | None:
        value, unit = self._reading(entity_id, now, check_age=check_age)
        if value is None:
            return None
        try:
            return PowerConverter.convert(value, unit or UnitOfPower.WATT, UnitOfPower.WATT)
        except HomeAssistantError:
            LOGGER.warning("Unsupported power unit %s on %s", unit, entity_id)
            return None

    def _energy_wh(self, entity_id: str, now: datetime) -> float | None:
        value, unit = self._reading(entity_id, now)
        return None if value is None else _to_wh(value, unit, entity_id)

    async def _async_counter_at(self, when: datetime) -> float | None:
        """Read the import counter at ``when`` from the recorder, after a restart."""
        entity_id = self._entity(CONF_GRID_IMPORT_ENERGY)
        current = self.hass.states.get(entity_id)
        unit = current.attributes.get(ATTR_UNIT_OF_MEASUREMENT) if current else None
        try:
            states = await get_instance(self.hass).async_add_executor_job(
                partial(
                    history.get_significant_states,
                    self.hass,
                    when,
                    when + timedelta(seconds=1),
                    [entity_id],
                    include_start_time_state=True,
                    significant_changes_only=False,
                    no_attributes=True,
                )
            )
        except KeyError:  # the recorder is not set up
            return None
        for state in states.get(entity_id, []):
            if isinstance(state, State):
                try:
                    return _to_wh(float(state.state), unit, entity_id)
                except ValueError:
                    continue
        return None


def _to_wh(value: float, unit: str | None, entity_id: str) -> float | None:
    try:
        return EnergyConverter.convert(
            value, unit or UnitOfEnergy.WATT_HOUR, UnitOfEnergy.WATT_HOUR
        )
    except HomeAssistantError:
        LOGGER.warning("Unsupported energy unit %s on %s", unit, entity_id)
        return None


def _load(subentry_id: str, title: str, data: Mapping[str, Any], margin_w: float) -> Load:
    cfg = {**DEFAULT_LOAD, **data}
    voltages = cfg.get(CONF_LOAD_VOLTAGES) or []
    return Load(
        subentry_id=subentry_id,
        title=title,
        power_entity=str(cfg[CONF_LOAD_POWER]),
        voltage_entities=list(voltages),
        priority=float(cfg[CONF_PRIORITY]),
        params=ModelParams(
            margin_w=margin_w,
            reaction_s=float(cfg[CONF_REACTION]),
            min_charge_s=float(cfg[CONF_MIN_CHARGE]),
            extra_a=float(cfg[CONF_EXTRA_A]),
            min_a=float(cfg[CONF_MIN_A]),
            max_a=float(cfg[CONF_MAX_A]),
        ),
    )


def _sum(powers: list[float | None]) -> float | None:
    """Return the total, or None when one of them is unknown."""
    known = [p for p in powers if p is not None]
    return sum(known) if len(known) == len(powers) else None
