"""The coordinator: reads the source entities, tracks the window and runs the model."""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from functools import partial
from typing import TYPE_CHECKING

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
    CONF_MARGIN,
    CONF_MAX_A,
    CONF_MAX_AGE,
    CONF_MIN_A,
    CONF_MIN_CHARGE,
    CONF_MONTH_PEAK,
    CONF_PEAK_GOAL,
    CONF_PROFILE,
    CONF_REACTION,
    CONF_VOLTAGES,
    CONF_VOLTAGES_FALLBACK,
    CONF_WINDOW_AVERAGE,
    DEFAULT_OPTIONS,
    DOMAIN,
    LOGGER,
    TICK_SECONDS,
)
from .model import PROFILES, ModelInput, ModelParams, ModelResult, compute_target, evaluate
from .model.window import window_start

if TYPE_CHECKING:
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


class PeakBasketCoordinator(DataUpdateCoordinator[ModelResult]):
    """Evaluate the model on every grid-power update, and on a safety tick."""

    config_entry: PeakBasketConfigEntry

    def __init__(self, hass: HomeAssistant, entry: PeakBasketConfigEntry) -> None:
        """Read the sources and the tunables from the entry."""
        super().__init__(hass, LOGGER, config_entry=entry, name=DOMAIN, update_interval=None)
        self._src: dict[str, str | list[str]] = dict(entry.data)
        opts: dict[str, float] = {**DEFAULT_OPTIONS, **entry.options}
        self.profile = PROFILES[self._entity(CONF_PROFILE)]
        self.params = ModelParams(
            margin_w=opts[CONF_MARGIN],
            reaction_s=opts[CONF_REACTION],
            min_charge_s=opts[CONF_MIN_CHARGE],
            extra_a=opts[CONF_EXTRA_A],
            min_a=opts[CONF_MIN_A],
            max_a=opts[CONF_MAX_A],
        )
        self.peak_goal_w = opts[CONF_PEAK_GOAL]
        self.target_w = self.peak_goal_w
        self._max_age = timedelta(seconds=opts[CONF_MAX_AGE])
        self._window_start = window_start(dt_util.now(), self.profile.window_s)
        self._counter_start_wh: float | None = None
        self._last_counter: tuple[float, datetime] | None = None
        self._published_at: datetime | None = None
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
        Between ticks a result waits, unless the mode changes or the grid limit or the
        load's allowed power goes down: a consumer must learn that at once.
        """
        now = dt_util.now()
        start = window_start(now, self.profile.window_s)
        used_wh = self._used_wh(start, now)

        month_peak = None
        if peak_entity := self._src.get(CONF_MONTH_PEAK):
            month_peak = self._power_w(str(peak_entity), now, check_age=False)
        self.target_w = compute_target(self.profile, self.peak_goal_w, month_peak)

        # Per phase: the load's voltage, then the meter's, then nominal (in the model).
        primary = self._entities(CONF_VOLTAGES)
        fallback = self._entities(CONF_VOLTAGES_FALLBACK)
        voltages = [
            [self._number(src[i], now) if i < len(src) else None for src in (primary, fallback)]
            for i in range(max(len(primary), len(fallback)))
        ]

        result = evaluate(
            ModelInput(
                target_w=self.target_w,
                seconds_in_window=(now - start).total_seconds(),
                grid_import_w=self._power_w(self._entity(CONF_GRID_POWER), now),
                load_w=self._power_w(self._entity(CONF_LOAD_POWER), now),
                window_import_wh=used_wh,
                voltages=voltages or None,
            ),
            self.profile,
            self.params,
        )
        if force or self._must_publish(result, now):
            self._published_at = now
            self.async_set_updated_data(result)

    def _must_publish(self, result: ModelResult, now: datetime) -> bool:
        previous = self.data
        return (
            self._published_at is None
            or now - self._published_at >= timedelta(seconds=TICK_SECONDS)
            or result.mode != previous.mode
            or floor_power(result.limit_w) < floor_power(previous.limit_w)
            or floor_power(result.allowed_load_w) < floor_power(previous.allowed_load_w)
        )

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
