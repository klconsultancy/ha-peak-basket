"""The coordinator: reads the source entities, tracks the window and runs the model."""

from __future__ import annotations

import math
from collections import deque
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
from homeassistant.helpers import entity_registry as er
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
    CONF_STEER,
    CONF_WINDOW_AVERAGE,
    DEFAULT_ENTRY_OPTIONS,
    DEFAULT_LOAD,
    DOMAIN,
    LOGGER,
    ROLLOVER_SECONDS,
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
    measured_reserves,
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
# The slowest load sensor Peak Basket waits for: a grid step this recent can still be a
# load's start or stop that its sensor has not reported yet.
LAG_S = 45


def floor_power(value: float) -> float:
    """Round a power down to the published step, so it never exceeds the model's.

    A unit conversion leaves noise such as 1649.9999999999998 W for 1.65 kW: that is
    removed first, or it would round down a whole step.
    """
    return math.floor(round(value, 6) / POWER_STEP_W) * POWER_STEP_W


@dataclass(frozen=True)
class Load:
    """A load: a subentry of the grid connection, steered or only measured."""

    subentry_id: str
    title: str
    power_entity: str
    voltage_entities: list[str]
    demand_entity: str | None
    """On while the load wants power (plugged in, calling for heat); None when unknown."""
    priority: float
    params: ModelParams
    steered: bool = True
    """False for a load that is only measured: it counts as house for the steered loads."""


@dataclass(frozen=True)
class Flows:
    """Energy this window so far (Wh).

    The loads' grid and solar energy add up between evaluations. The house is the rest:
    the import the meter counted minus what the loads drew from the grid, so the two
    always add up to the import. Solar goes to the steered loads first, in priority order,
    then the measured-only loads, then the house. A display convention: physically, and
    for the grid operator, it makes no difference.
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
    reserves: dict[str, float | None] = field(default_factory=dict)
    """Per measured-only load, by subentry id: its expected import until the window ends."""
    powers: dict[str, float] = field(default_factory=dict)
    """Per load, by subentry id: its power as the model uses it (W)."""
    window: datetime | None = None
    """The start of the window this evaluation, and so its sums, belongs to."""


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
            # The steered loads first: solar goes to them before the loads only measured.
            key=lambda load: (not load.steered, load.priority, load.title),
        )
        self.steered = [load for load in self.loads if load.steered]
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
        # The grid power over the lag limit, to pair a load's reading with the grid's of
        # the same moment, with what each steered load was allowed then; and last allowed.
        self._grid_history: deque[tuple[datetime, float, dict[str, tuple[float, float]]]] = deque()
        # Per steered load: its allowed power and its minimum power.
        self._allowed: dict[str, tuple[float, float]] = {}
        # Each steered load's mode of the previous evaluation, for the band around the
        # continuous boundary.
        self._modes: dict[str, Mode] = {}
        # Per steered load: the house its evaluations saw over the last house_recent_s,
        # as the model saw it (the loads before it and their claims included).
        self._houses: dict[str, deque[tuple[datetime, float | None]]] = {}
        # The window and its import from the meter's average, last taken as it was.
        # With the moment of its report: the import since then is added at the grid power.
        self._average_used: tuple[datetime, float, datetime] | None = None
        self._unsubs: list[CALLBACK_TYPE] = []
        self._started_at = dt_util.now()

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
        self._started_at = dt_util.now()
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
        grid = self._power_w(self._entity(CONF_GRID_POWER), now)
        used_wh = self._used_wh(start, now, grid)
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

        meter = [self._number(e, now) for e in self._entities(CONF_METER_VOLTAGES)]
        # A load's power that is missing or too old counts as 0 W. Many sources only write
        # a change, so a load at rest goes stale; waiting for it would lock the load out.
        # 0 W is the safe side: its share counts as house, which leaves less room. A load
        # does not produce power, so a negative reading (a meter's standby offset) counts
        # as 0 W too: the engine, the sums and the reserves then all see the same powers.
        powers = [max(self._power_w(load.power_entity, now) or 0.0, 0.0) for load in self.loads]
        powers = self._paired(powers, grid, now)

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
        # A load that is only measured is not taken out of the house a steered load sees.
        for k, load in enumerate(self.steered):
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
                    other_loads_w=sum(powers[k + 1 : len(self.steered)]),
                    reserved_w=claimed,
                    previous_mode=self._modes.get(load.subentry_id),
                    house_recent_w=self._recent_house(load, now),
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

        self._allowed = {
            key: (result.allowed_load_w, result.p_load_min_w) for key, result in results.items()
        }
        self._remember(results, now)
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
            if exported is not None and exported > cap_wh:
                # More than any grid connection can export since the boundary: the start
                # value is wrong. The grid power's export is the better guess.
                LOGGER.debug("Export %.0f Wh above %.0f Wh: from grid power", exported, cap_wh)
                exported = None
        measured = self.loads[len(self.steered) :]
        reserves = measured_reserves(
            connection,
            powers[len(self.steered) :],
            self.profile.window_s - seconds,
            grid,
            sum(powers[: len(self.steered)]),
        )
        data = PeakBasketData(
            connection=connection,
            loads=results,
            flows=self._snapshot(used_wh, exported),
            reserves={load.subentry_id: r for load, r in zip(measured, reserves, strict=True)},
            powers={load.subentry_id: p for load, p in zip(self.loads, powers, strict=True)},
            window=start,
        )
        if self._must_publish(data, now, force=force):
            self._published_at = now
            self.async_set_updated_data(data)

    def _recent_house(self, load: Load, now: datetime) -> float | None:
        """Return the highest house this load's evaluations saw over the last house_recent_s."""
        houses = self._houses.setdefault(load.subentry_id, deque())
        while houses and houses[0][0] <= now - timedelta(seconds=load.params.house_recent_s):
            houses.popleft()
        return max((house for _, house in houses if house is not None), default=None)

    def _remember(self, results: dict[str, ModelResult], now: datetime) -> None:
        """Keep each load's mode and house for its next evaluations.

        A fallback without a house is kept as ``None`` and does not count; a fallback with
        one (no used energy) counts, a real measurement and the safe side.
        """
        self._modes = {key: result.mode for key, result in results.items()}
        for key, result in results.items():
            self._houses.setdefault(key, deque()).append((now, result.house_w))

    def _paired(self, powers: list[float], grid: float | None, now: datetime) -> list[float]:
        """Take a grid step since a load's last reading as the load's, within limits.

        A load's sensor can lag the grid power: a start or a stop shows on the grid first.
        Each reading is paired with the grid power of its own moment, at most the lag
        limit back. A rise since then is the load's up to what it was allowed on top of
        its reading at that moment (not now: a rise taken as house lowers what it is allowed),
        and, for an idle load, only while it can start (``_may_start``) and from half its
        minimum power: it starts at least at its minimum, so a smaller rise is the house's
        (a meter may catch a start half-way).
        A drop is the load's down to 0 W; the rest is the house's. Loads go
        in order, so the same step is not taken twice; each load measures its step from its
        own anchor, exact for one steered load and an approximation for several. A
        measured-only load is allowed nothing, so its start counts as house until its
        sensor follows: the safe side. Without a grid power the history starts over: a
        reading from before an outage is not paired with one after it.
        """
        if grid is None:
            self._grid_history.clear()
            return powers
        lag = min(timedelta(seconds=LAG_S), self._max_age)
        history = self._grid_history
        history.append((now, grid, self._allowed))
        while len(history) > 1 and history[1][0] <= now - lag:
            history.popleft()
        rise = drop = 0.0
        paired: list[float] = []
        for load, reading in zip(self.loads, powers, strict=True):
            power = reading
            state = self.hass.states.get(load.power_entity)
            anchor = max(state.last_reported, now - lag) if state else now - lag
            before = next((entry for entry in reversed(history) if entry[0] <= anchor), None)
            step = 0.0 if before is None else grid - before[1]
            allowed, p_min = before[2].get(load.subentry_id, (0.0, 0.0)) if before else (0, 0)
            idle = power <= load.params.charging_threshold_w
            starting = not idle or (step - rise >= p_min / 2 and self._may_start(load))
            if step > 0 and starting:
                may_add = max(allowed - power, 0.0)
                take = min(max(step - rise, 0.0), may_add)
                rise += take
                power += take
            elif step < 0:
                take = min(max(-step - drop, 0.0), power)
                drop += take
                power -= take
            paired.append(power)
        return paired

    def _may_start(self, load: Load) -> bool:
        """Whether an idle load can start: only a demand entity that is off says it cannot.

        Without one, Peak Basket cannot tell a car about to start from no car, and a start
        taken as house is what stops the load again; a house rise taken as the load's only
        moves the split of the import, not the basket.
        """
        if not load.demand_entity:
            return True
        state = self.hass.states.get(load.demand_entity)
        return state is None or state.state != STATE_OFF

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

    def _meter_pending(self, now: datetime) -> bool:
        """Whether the meter has not reported since start-up, for less than the maximum age."""
        if now - self._started_at >= self._max_age:
            return False
        energy = self._src.get(CONF_WINDOW_AVERAGE) or self._src[CONF_GRID_IMPORT_ENERGY]
        return any(
            self._fresh_state(entity_id, now) is None
            for entity_id in (self._entity(CONF_GRID_POWER), str(energy))
        )

    def _must_publish(self, data: PeakBasketData, now: datetime, *, force: bool) -> bool:
        if self.data is None:
            # Right after a restart the meter's integration may not have written a state
            # yet. Its fallback would publish 0 W allowed power, which stops a load that
            # was running: stay unavailable, so a consumer keeps its own safe default,
            # until the meter reports or has been missing for the maximum age.
            return not self._meter_pending(now)
        if force:
            return True
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

    def _used_wh(self, start: datetime, now: datetime, grid: float | None) -> float | None:
        """Return the grid import since the window start.

        Preferred: the meter's running window average (DSMR 1.4.0, HomeWizard's
        average demand), which is energy so far over the window length, so it survives a
        restart. Otherwise: the difference of the import register since the window start.
        Either one is as old as its last report: the grid power adds the import since.
        """
        if avg_entity := self._src.get(CONF_WINDOW_AVERAGE):
            state = self._fresh_state(str(avg_entity), now)
            if state is not None and state.last_reported < start:
                # The meter resets its average a second or two after the boundary; until
                # then it shows the previous window's.
                return self._since(start, now, grid)
            avg = self._power_w(str(avg_entity), now)
            if avg is None:
                return None
            used = max(avg, 0.0) * self.profile.window_s / SECONDS_PER_HOUR
            last = self._average_used
            left = self.profile.window_s - (now - start).total_seconds()
            if (
                last is not None
                and last[0] == start
                and used < last[1]
                and left <= ROLLOVER_SECONDS
            ):
                # The window's import only grows: the meter, its clock a few seconds
                # ahead, has started the next window. Keep this one's until the boundary.
                LOGGER.debug(
                    "Window average dropped %.0f s before the boundary: meter ahead, kept %.0f Wh",
                    left,
                    last[1],
                )
                return last[1] + self._since(last[2], now, grid)
            reported = max(state.last_reported, start) if state is not None else now
            self._average_used = (start, used, reported)
            return used + self._since(reported, now, grid)

        entity_id = self._entity(CONF_GRID_IMPORT_ENERGY)
        reading = self._energy_wh(entity_id, now)
        counted = self._import.since_start(start, now, reading, self._max_age)
        if counted is None:
            return None
        # From the reading's report, also before the boundary: the start value is the last
        # reading before it, so the first seconds count a little of the previous window
        # again, the safe side and the start value's own convention.
        state = self.hass.states.get(entity_id)
        return counted + self._since(state.last_reported if state else None, now, grid)

    @staticmethod
    def _since(since: datetime | None, now: datetime, grid: float | None) -> float:
        """Return the import since an energy reading's report, at the grid power, in Wh.

        A meter writes its power and its energy one after the other, and the energy may
        report less often: an evaluation on the power alone would see too little used.
        """
        if since is None or grid is None:
            return 0.0
        return max(grid, 0.0) * max((now - since).total_seconds(), 0.0) / SECONDS_PER_HOUR

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
        """Read an energy register at ``when`` from the recorder, after a restart.

        Right after a restart the meter may not have written a state yet, so the unit comes
        from the recorded state itself, else the current state, else the entity registry.
        Without one the start stays unknown: a kWh register taken as Wh would count about
        the whole register as this window's.
        """
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
                )
            )
        except KeyError:  # the recorder is not set up
            return None
        for state in states.get(entity_id, []):
            if isinstance(state, State):
                try:
                    reading = float(state.state)
                except ValueError:
                    continue
                unit = self._energy_unit(entity_id, state)
                if unit is None:
                    LOGGER.info("No unit known for %s: fallback until the next window", entity_id)
                    return None
                return _to_wh(reading, unit, entity_id)
        return None

    def _energy_unit(self, entity_id: str, recorded: State) -> str | None:
        for state in (recorded, self.hass.states.get(entity_id)):
            if state is not None and (unit := state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)):
                return str(unit)
        entry = er.async_get(self.hass).async_get(entity_id)
        return None if entry is None else entry.unit_of_measurement


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
        steered=bool(cfg.get(CONF_STEER, True)),
        params=ModelParams(
            margin_w=margin_w,
            reaction_s=float(cfg[CONF_REACTION]),
            min_charge_s=float(cfg[CONF_MIN_CHARGE]),
            extra_a=float(cfg[CONF_EXTRA_A]),
            min_a=float(cfg[CONF_MIN_A]),
            max_a=float(cfg[CONF_MAX_A]),
        ),
    )
