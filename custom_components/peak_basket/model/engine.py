"""Window-budget engine, the decision of docs/design.md "The model".

Pure functions, no Home Assistant imports. Units: W, Wh, seconds.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .profiles import RegionProfile

NOMINAL_VOLTAGE = 230.0
SECONDS_PER_HOUR = 3600

type PhaseVoltages = Sequence[float | Sequence[float | None] | None]


class Mode(StrEnum):
    """How the load is driven in the current window."""

    CONTINUOUS = "continuous"
    """Enough budget to keep the load running at or above its minimum."""
    PULSE_ON = "pulse_on"
    """Not enough for continuous; budget left for a burst at minimum power."""
    PULSE_OFF = "pulse_off"
    """The budget for this window is used up; only the house fits."""
    FALLBACK = "fallback"
    """Inputs missing or stale; static, safe limit."""


@dataclass(frozen=True)
class ModelParams:
    """Tunables. The defaults are the values calibrated in practice."""

    margin_w: float = 100.0
    """Kept free in every window for measurement and timing errors."""
    reaction_s: float = 90.0
    """Time between a lower limit and the load actually dropping."""
    min_charge_s: float = 300.0
    """In pulse mode, start only with budget for at least this long."""
    extra_a: float = 2.0
    """Catch-up allowed above the base in the tail of the window, per phase."""
    min_a: float = 6.0
    max_a: float = 16.0
    phases: int = 3
    """Phases assumed at the nominal voltage when none are measured."""
    tail_s: float = 300.0
    """Last part of the window in which catch-up is limited to the base plus extra_a."""
    final_s: float = 90.0
    """Last part of the window in which the limit is capped at the base."""
    pulse_headroom_w: float = 200.0
    """Added to the minimum load power in pulse mode, so the load surely starts."""
    charging_threshold_w: float = 100.0
    """Measured load power above which the load counts as running."""
    min_valid_voltage: float = 180.0
    """Phase voltages at or below this are treated as missing."""


@dataclass(frozen=True)
class ModelInput:
    """One evaluation's measurements."""

    target_w: float
    seconds_in_window: float
    grid_import_w: float | None
    """Instantaneous grid import. Negative (export) counts as 0."""
    load_w: float | None
    """Measured power of the controllable load."""
    window_import_wh: float | None
    """Grid import energy since the start of the window (import register only)."""
    voltages: PhaseVoltages | None = None
    """Per phase: a voltage, or candidates in priority order (load, meter).

    A missing or invalid phase uses the nominal voltage; ``None`` means nominal on all.
    """


@dataclass(frozen=True)
class ModelResult:
    """The decision and the basket fields of one evaluation."""

    mode: Mode
    limit_w: float
    """Total grid limit (house and load together), for a circuit limit."""
    allowed_load_w: float
    """Power the controllable load may draw now: the pull output."""
    house_w: float | None
    charge_minutes: float
    """Minutes the load can still run this window (0 when pulse is off)."""
    basket_wh: float
    margin_wh: float
    used_wh: float | None
    remaining_wh: float | None
    """Goal budget left this window, before the house reserve; may be negative."""
    house_reserve_wh: float | None
    load_room_wh: float | None
    p_load_min_w: float
    p_load_max_w: float


DEFAULT_PARAMS = ModelParams()


def phase_voltage_sum(
    voltages: PhaseVoltages | None, params: ModelParams = DEFAULT_PARAMS
) -> float:
    """Sum the phase voltages: per phase the first valid candidate, else nominal."""
    phases: list[float | Sequence[float | None] | None] = list(voltages or [])
    phases += [None] * (params.phases - len(phases))
    total = 0.0
    for phase in phases:
        candidates = phase if isinstance(phase, Sequence) else [phase]
        total += next(
            (v for v in candidates if v is not None and v > params.min_valid_voltage),
            NOMINAL_VOLTAGE,
        )
    return total


def compute_target(
    profile: RegionProfile, peak_goal_w: float, month_peak_w: float | None = None
) -> float:
    """Return max(peak already reached this period, goal, billed minimum).

    Up to the peak already reached, or the billed minimum, costs nothing extra.
    """
    return max(peak_goal_w, month_peak_w or 0.0, profile.min_peak_w)


def evaluate(
    inp: ModelInput, profile: RegionProfile, params: ModelParams = DEFAULT_PARAMS
) -> ModelResult:
    """Decide the grid limit and the load's allowed power for this moment."""
    window = float(profile.window_s)
    t = min(max(inp.seconds_in_window, 0.0), window)
    rest = window - t

    volt_sum = phase_voltage_sum(inp.voltages, params)
    p_min = params.min_a * volt_sum
    p_max = params.max_a * volt_sum

    goal = inp.target_w - params.margin_w
    basket_wh = inp.target_w * window / SECONDS_PER_HOUR
    margin_wh = params.margin_w * window / SECONDS_PER_HOUR

    house: float | None = None
    if inp.grid_import_w is not None and inp.load_w is not None:
        house = max(max(inp.grid_import_w, 0.0) - max(inp.load_w, 0.0), 0.0)

    if house is None or inp.window_import_wh is None:
        # Behave like a static circuit at the goal: safe, never unlimited.
        return ModelResult(
            mode=Mode.FALLBACK,
            limit_w=goal,
            allowed_load_w=max(goal - house, 0.0) if house is not None else 0.0,
            house_w=house,
            charge_minutes=0.0,
            basket_wh=basket_wh,
            margin_wh=margin_wh,
            used_wh=inp.window_import_wh,
            remaining_wh=None,
            house_reserve_wh=None,
            load_room_wh=None,
            p_load_min_w=p_min,
            p_load_max_w=p_max,
        )

    load = max(inp.load_w or 0.0, 0.0)
    e_used = max(inp.window_import_wh, 0.0) * SECONDS_PER_HOUR  # J
    e_rest = goal * window - e_used
    p_avg = e_rest / max(rest, 1.0)  # no division by zero on the boundary itself
    e_load = e_rest - house * rest  # J left for the load after the house

    if p_avg - house >= p_min:
        mode = Mode.CONTINUOUS
        limit = min(p_avg, house + p_max)
        base = max(goal, house + p_min)
        if rest <= params.tail_s:
            limit = min(limit, base + params.extra_a * volt_sum)
        if rest <= params.final_s:
            limit = min(limit, base)
        charge_s = rest
    else:
        p_load = max(load, p_min)
        running = load > params.charging_threshold_w
        threshold = p_load * (params.reaction_s + (0.0 if running else params.min_charge_s))
        if e_load > threshold:
            mode = Mode.PULSE_ON
            limit = house + p_min + params.pulse_headroom_w
            charge_s = min(max(e_load / p_load - params.reaction_s, 0.0), rest)
        else:
            mode = Mode.PULSE_OFF
            limit = house
            charge_s = 0.0

    remaining_wh = e_rest / SECONDS_PER_HOUR
    return ModelResult(
        mode=mode,
        limit_w=limit,
        allowed_load_w=max(limit - house, 0.0),
        house_w=house,
        charge_minutes=charge_s / 60,
        basket_wh=basket_wh,
        margin_wh=margin_wh,
        used_wh=inp.window_import_wh,
        remaining_wh=remaining_wh,
        house_reserve_wh=min(house * rest / SECONDS_PER_HOUR, max(remaining_wh, 0.0)),
        load_room_wh=max(e_load / SECONDS_PER_HOUR, 0.0),
        p_load_min_w=p_min,
        p_load_max_w=p_max,
    )
