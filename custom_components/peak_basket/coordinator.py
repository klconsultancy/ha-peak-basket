"""The coordinator: reads the source entities, tracks the window and runs the model."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import partial
from typing import TYPE_CHECKING, Any, cast

from homeassistant.components.recorder import history
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_UNIT_OF_MEASUREMENT,
    STATE_OFF,
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
    CONF_DEMAND,
    CONF_EXTRA_A,
    CONF_GRID_EXPORT_ENERGY,
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
    CONF_PV_POWER,
    CONF_REACTION,
    CONF_WINDOW_AVERAGE,
    DEFAULT_ENTRY_OPTIONS,
    DEFAULT_LOAD,
    DOMAIN,
    LOGGER,
    SUBENTRY_LOAD,
    TICK_SECONDS,
)
from .model import (
    PROFILES,
    Mode,
    ModelInput,
    ModelParams,
    ModelResult,
    allocate_solar,
    compute_target,
    evaluate,
)
from .model.window import window_start

if TYPE_CHECKING:
    from collections.abc import Mapping

    from homeassistant.core import Event, EventStateChangedData

type PeakBasketConfigEntry = ConfigEntry[PeakBasketCoordinator]

SECONDS_PER_HOUR = 3600
# A generous physical bound on any grid connection's import: 3 x 230 V x 100 A.
MAX_GRID_POWER_W = 3 * 230 * 100
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
    demand_entity: str | None
    """On while the load wants power (plugged in, calling for heat); None when unknown."""
    priority: float
    params: ModelParams


@dataclass(frozen=True)
class Flows:
    """Energy this window so far (Wh).

    The loads' grid and solar energy add up between evaluations. The house is the rest:
    the import the meter counted minus what the loads drew from the grid, so the two
    always add up to the import. Solar goes to the loads first, in priority order, then
    the house. A display convention: physically, and for the grid operator, it makes no
    difference.
    """

    solar: float = 0.0
    export: float = 0.0
    house_solar: float = 0.0
    house_grid: float | None = None
    load_solar: dict[str, float] = field(default_factory=dict)
    load_grid: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class PeakBasketData:
    """One evaluation: the grid connection's basket, and the decision for each load."""

    connection: ModelResult
    """The basket with every load taken out of the house; its mode and limits mean nothing."""
    loads: dict[str, ModelResult]
    """By subentry id."""
    flows: Flows = field(default_factory=Flows)
    """Who drew from the basket this window."""


@dataclass
class _Counter:
    """An energy register read per window: import or export."""

    name: str
    window: datetime
    start_wh: float | None = None
    last: tuple[float, datetime] | None = None

    def since_start(
        self, start: datetime, now: datetime, reading: float | None, max_age: timedelta
    ) -> float | None:
        """Return the energy counted since the window start, None when unknown.

        At a new window the start value is the last fresh reading before the boundary:
        it may count a few seconds of the previous window, which errs on the safe side.
        Without one, the first reading after it.
        """
        if start != self.window:
            self.window = start
            self.start_wh = reading
            if self.last is not None and start - self.last[1] <= max_age:
                self.start_wh = self.last[0]
        if reading is not None:
            self.last = (reading, now)
        if reading is None or self.start_wh is None:
            return None
        counted = reading - self.start_wh
        if counted < 0:
            LOGGER.warning("The %s counter went backwards: fallback for this window", self.name)
            self.start_wh = None
            return None
        return counted


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
        window = window_start(dt_util.now(), self.profile.window_s)
        self._import = _Counter("import", window)
        self._export = _Counter("export", window)
        self._published_at = dt_util.now()
        # Claims of idle loads without a demand entity: since when, and which lapsed.
        self._claim_started: dict[str, datetime] = {}
        self._claim_lapsed: set[str] = set()
        self._claim_window = window
        # Energy added up per window: "solar", "export", "load_solar:<id>", "load_grid:<id>".
        self._sums: dict[str, float] = {}
        self._sums_window = window
        self._last_powers: tuple[datetime, list[float | None]] | None = None
        self._unsubs: list[CALLBACK_TYPE] = []

    async def async_start(self) -> None:
        """Restore the running window, subscribe to the grid power and evaluate once."""
        window = window_start(dt_util.now(), self.profile.window_s)
        self._import.window = self._export.window = self._sums_window = window
        if export := self._src.get(CONF_GRID_EXPORT_ENERGY):
            self._export.start_wh = await self._async_counter_at(str(export), window)
        if not self._src.get(CONF_WINDOW_AVERAGE):
            self._import.start_wh = await self._async_counter_at(
                self._entity(CONF_GRID_IMPORT_ENERGY), window
            )
            if self._import.start_wh is None:
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
        seconds = (now - start).total_seconds()
        used_wh = self._used_wh(start, now)
        cap_wh = seconds * MAX_GRID_POWER_W / SECONDS_PER_HOUR
        if used_wh is not None and used_wh > cap_wh:
            # More than any grid connection can import since the boundary: a reading that
            # still carries the previous window, whatever its timestamp says.
            LOGGER.debug("Used energy %.0f Wh capped to %.0f Wh", used_wh, cap_wh)
            used_wh = cap_wh

        month_peak = None
        if peak_entity := self._src.get(CONF_MONTH_PEAK):
            month_peak = self._power_w(str(peak_entity), now, check_age=False)
        self.target_w = compute_target(self.profile, self.peak_goal_w, month_peak)

        grid = self._power_w(self._entity(CONF_GRID_POWER), now)
        meter = [self._number(e, now) for e in self._entities(CONF_METER_VOLTAGES)]
        # A load's power that is missing or too old counts as 0 W. Many sources only write
        # a change, so a load at rest goes stale; waiting for it would lock the load out.
        # 0 W is the safe side: its share counts as house, which leaves less room.
        powers = [self._power_w(load.power_entity, now) or 0.0 for load in self.loads]

        connection = evaluate(
            ModelInput(
                target_w=self.target_w,
                seconds_in_window=seconds,
                grid_import_w=grid,
                load_w=sum(powers),
                window_import_wh=used_wh,
                voltages=meter or None,
            ),
            self.profile,
            self.params,
        )
        results: dict[str, ModelResult] = {}
        claimed = 0.0
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
                    other_loads_w=sum(powers[k + 1 :]),
                    reserved_w=claimed,
                ),
                self.profile,
                load.params,
            )
            result = results[load.subentry_id]
            idle = powers[k] <= load.params.charging_threshold_w
            if not idle:
                self._claim_started.pop(load.subentry_id, None)
                self._claim_lapsed.discard(load.subentry_id)
            elif result.mode in {Mode.CONTINUOUS, Mode.PULSE_ON} and self._claims(load, start, now):
                # It may start but does not draw yet, so the grid does not show it: the
                # loads after it must not count on the same room.
                claimed += result.allowed_load_w

        pv = 0.0
        if pv_entity := self._src.get(CONF_PV_POWER):
            # Missing or stale PV counts as 0 W, like a load's power: an inverter that
            # sleeps at night reports unavailable, and the loads' grid energy must still
            # count. During a dropout it only moves solar to grid in the display.
            pv = self._power_w(str(pv_entity), now) or 0.0
        self._integrate(start, now, [grid, pv, *powers])
        exported = None
        if export_entity := self._src.get(CONF_GRID_EXPORT_ENERGY):
            reading = self._energy_wh(str(export_entity), now)
            exported = self._export.since_start(start, now, reading, self._max_age)
        data = PeakBasketData(
            connection=connection, loads=results, flows=self._snapshot(used_wh, exported)
        )
        if force or self._must_publish(data, now):
            self._published_at = now
            self.async_set_updated_data(data)

    def _claims(self, load: Load, start: datetime, now: datetime) -> bool:
        """Whether an idle load that may start keeps its room from the loads after it.

        With a demand entity: while it is not off. Without one, a load that does not
        start is not told apart from one that cannot (a car not plugged in), so the claim
        lasts the load's reaction time, then lapses until the load draws or a new window
        starts.
        """
        if load.demand_entity:
            state = self.hass.states.get(load.demand_entity)
            return state is None or state.state != STATE_OFF
        if start != self._claim_window:
            self._claim_window = start
            self._claim_started.clear()
            self._claim_lapsed.clear()
        if load.subentry_id in self._claim_lapsed:
            return False
        since = self._claim_started.setdefault(load.subentry_id, now)
        if now - since <= timedelta(seconds=load.params.reaction_s):
            return True
        self._claim_lapsed.add(load.subentry_id)
        return False

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

    def _integrate(self, start: datetime, now: datetime, powers: list[float | None]) -> None:
        """Add the interval since the previous evaluation to the window's sums.

        ``powers`` is grid, PV (0 without a PV source), then each load in priority order.
        The interval counts at the powers reported at its start, and only when they were
        all known and the interval is no longer than the maximum source age: a gap is not
        filled in. An interval across the boundary counts its part after the boundary in
        the new window; the part before is lost with the old window's last published sums.
        """
        if start != self._sums_window:
            self._sums_window = start
            self._sums = {}
        if self._last_powers is not None:
            since, (grid, pv, *loads) = self._last_powers
            known = grid is not None and None not in loads
            if known and now - since <= self._max_age:
                hours = (now - max(since, start)).total_seconds() / SECONDS_PER_HOUR
                split = allocate_solar(
                    solar=cast("float", pv),
                    export=max(-cast("float", grid), 0.0),
                    grid_import=cast("float", grid),
                    loads=cast("list[float]", loads),
                )
                self._add("solar", max(cast("float", pv), 0.0) * hours)
                self._add("export", split.export * hours)
                for load, part in zip(self.loads, split.loads, strict=True):
                    self._add(f"load_solar:{load.subentry_id}", part.solar * hours)
                    self._add(f"load_grid:{load.subentry_id}", part.grid * hours)
        self._last_powers = (now, powers)

    def _add(self, key: str, wh: float) -> None:
        self._sums[key] = self._sums.get(key, 0.0) + wh

    @callback
    def restore(self, key: str, subentry_id: str | None, wh: float, at: datetime) -> None:
        """Take back a sum a sensor had before a restart, when it is from this window."""
        if window_start(at, self.profile.window_s) == self._sums_window:
            self._add(f"{key}:{subentry_id}" if subentry_id else key, wh)

    def _snapshot(self, used_wh: float | None, exported: float | None) -> Flows:
        sums = self._sums
        ids = [load.subentry_id for load in self.loads]
        load_solar = {i: sums.get(f"load_solar:{i}", 0.0) for i in ids}
        load_grid = {i: sums.get(f"load_grid:{i}", 0.0) for i in ids}
        # The export register when there is one and it is known, else the grid power's.
        export = exported if exported is not None else sums.get("export", 0.0)
        solar = sums.get("solar", 0.0)
        return Flows(
            solar=solar,
            export=export,
            house_solar=max(solar - export - sum(load_solar.values()), 0.0),
            house_grid=None if used_wh is None else max(used_wh - sum(load_grid.values()), 0.0),
            load_solar=load_solar,
            load_grid=load_grid,
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

        reading = self._energy_wh(self._entity(CONF_GRID_IMPORT_ENERGY), now)
        return self._import.since_start(start, now, reading, self._max_age)

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

    async def _async_counter_at(self, entity_id: str, when: datetime) -> float | None:
        """Read an energy register at ``when`` from the recorder, after a restart."""
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
        demand_entity=cfg.get(CONF_DEMAND) or None,
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
