"""Engine tests: decisions, boundaries, fallback and whole simulated windows."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from custom_components.peak_basket.model import (
    BE_FLANDERS,
    Mode,
    ModelInput,
    ModelParams,
    ModelResult,
    RegionProfile,
    compute_target,
    evaluate,
    phase_voltage_sum,
)

if TYPE_CHECKING:
    from custom_components.peak_basket.model.engine import PhaseVoltages

P = ModelParams()
TARGET = 5500.0
GOAL = TARGET - P.margin_w
V3 = (230.0, 230.0, 230.0)
P_MIN = 6 * 690.0  # 4140 W
HOURLY = RegionProfile(key="test_hourly", window_s=3600)


def inp(
    t: float,
    grid: float | None,
    load: float | None,
    used_wh: float | None,
    target: float = TARGET,
    voltages: PhaseVoltages | None = V3,
) -> ModelInput:
    return ModelInput(
        target_w=target,
        seconds_in_window=t,
        grid_import_w=grid,
        load_w=load,
        window_import_wh=used_wh,
        voltages=voltages,
    )


def simulate(
    house_w: float,
    profile: RegionProfile = BE_FLANDERS,
    target: float = TARGET,
    step: float = 5.0,
    lag_s: float = 0.0,
) -> tuple[float, float, set[Mode]]:
    """Run one window where the load follows the allowed power after ``lag_s``.

    Returns (import_wh, load_on_seconds, modes).
    """
    window = profile.window_s
    used_wh = 0.0
    load = 0.0
    pending: list[tuple[float, float]] = []  # (apply_at, setpoint)
    on_s = 0.0
    modes = set()
    t = 0.0
    while t < window:
        res = evaluate(inp(t, house_w + load, load, used_wh, target), profile)
        modes.add(res.mode)
        allowed = res.allowed_load_w
        setpoint = 0.0 if allowed < res.p_load_min_w else min(allowed, res.p_load_max_w)
        pending.append((t + lag_s, setpoint))
        while pending and pending[0][0] <= t:
            load = pending.pop(0)[1]
        used_wh += (house_w + load) * step / 3600
        on_s += step if load > 0 else 0
        t += step
    return used_wh, on_s, modes


class TestDecisions:
    def test_continuous_at_1kw_house(self) -> None:
        t = 300
        res = evaluate(inp(t, 1000, 0, 1000 * t / 3600), BE_FLANDERS)
        assert res.mode is Mode.CONTINUOUS
        # e_rest = 5400*900 - 1000*300 J over 600 s → 7600 W average left
        assert res.limit_w == pytest.approx(7600)
        assert res.allowed_load_w == pytest.approx(6600)
        assert res.charge_minutes == pytest.approx(10)

    def test_continuous_capped_at_house_plus_max(self) -> None:
        # nothing used, 400 s left: p_avg = 12150 W > 0 + 16 A x 690 V
        res = evaluate(inp(500, 0, 0, 0), BE_FLANDERS)
        assert res.mode is Mode.CONTINUOUS
        assert res.limit_w == pytest.approx(16 * 690)

    def test_pulse_at_1_65kw_house_starts(self) -> None:
        res = evaluate(inp(0, 1650, 0, 0), BE_FLANDERS)
        assert res.mode is Mode.PULSE_ON
        assert res.limit_w == pytest.approx(1650 + P_MIN + 200)
        assert res.allowed_load_w == pytest.approx(P_MIN + 200)

    def test_pulse_stops_when_budget_runs_out(self) -> None:
        charging = 1650 + P_MIN

        def at(t: float) -> ModelResult:
            return evaluate(inp(t, charging, P_MIN, charging * t / 3600), BE_FLANDERS)

        assert at(600).mode is Mode.PULSE_ON
        off = at(750)
        assert off.mode is Mode.PULSE_OFF
        assert off.limit_w == pytest.approx(1650)
        assert off.allowed_load_w == 0

    def test_pulse_start_needs_min_charge_time(self) -> None:
        """Not yet running: needs budget for reaction + min_charge_s, not only reaction."""
        # e_load just above reaction*p_min but below (reaction+min_charge)*p_min
        house, t = 1650.0, 600.0
        rest = 900 - t
        e_load = P_MIN * (P.reaction_s + 10)
        used_j = GOAL * 900 - house * rest - e_load
        idle = evaluate(inp(t, house, 0, used_j / 3600), BE_FLANDERS)
        running = evaluate(inp(t, house + P_MIN, P_MIN, used_j / 3600), BE_FLANDERS)
        assert idle.mode is Mode.PULSE_OFF
        assert running.mode is Mode.PULSE_ON

    def test_tail_limits_catch_up(self) -> None:
        # Little used so far: p_avg is high, but in the last 300 s only base + 2 A.
        res = evaluate(inp(650, 1000, 0, 500), BE_FLANDERS)
        assert res.mode is Mode.CONTINUOUS
        base = max(GOAL, 1000 + P_MIN)
        assert res.limit_w == pytest.approx(base + 2 * 690)

    def test_final_90s_capped_at_base(self) -> None:
        res = evaluate(inp(850, 1000, 0, 500), BE_FLANDERS)
        assert res.mode is Mode.CONTINUOUS
        assert res.limit_w == pytest.approx(max(GOAL, 1000 + P_MIN))

    def test_measured_voltage_sets_min_power(self) -> None:
        # 1.2 kW house: continuous at 3x240 V? p_min = 4320; at 3x220 V p_min = 3960.
        used = 1200 * 0 / 3600
        lo = evaluate(inp(0, 1200, 0, used, voltages=(220, 220, 220)), BE_FLANDERS)
        hi = evaluate(inp(0, 1200, 0, used, voltages=(240, 240, 240)), BE_FLANDERS)
        assert lo.p_load_min_w == pytest.approx(3960)
        assert hi.p_load_min_w == pytest.approx(4320)
        assert lo.mode is Mode.CONTINUOUS  # 5400 - 1200 = 4200 ≥ 3960
        assert hi.mode is not Mode.CONTINUOUS  # 4200 < 4320

    def test_nominal_voltage_when_unmeasured(self) -> None:
        res = evaluate(inp(0, 1000, 0, 0, voltages=None), BE_FLANDERS)
        assert res.p_load_min_w == pytest.approx(P_MIN)


class TestSolarAndExport:
    def test_export_counts_as_zero_import(self) -> None:
        res = evaluate(inp(300, -2000, 0, 0), BE_FLANDERS)
        assert res.house_w == 0
        assert res.mode is Mode.CONTINUOUS

    def test_export_does_not_refill_basket(self) -> None:
        """Only the import register feeds the window: same import, same budget."""
        a = evaluate(inp(450, 1000, 0, 300), BE_FLANDERS)
        b = evaluate(inp(450, -1500, 0, 300), BE_FLANDERS)
        assert a.load_room_wh is not None
        assert b.load_room_wh is not None
        assert b.load_room_wh >= a.load_room_wh  # export only lowers the house estimate
        assert a.basket_wh == b.basket_wh
        assert a.used_wh == b.used_wh


class TestWindowBoundaries:
    def test_end_of_window_does_not_divide_by_zero(self) -> None:
        res = evaluate(inp(900, 1000, 0, 1300), BE_FLANDERS)
        assert res.limit_w > 0

    def test_seconds_out_of_range_are_clamped(self) -> None:
        assert evaluate(inp(-5, 1000, 0, 0), BE_FLANDERS) == evaluate(
            inp(0, 1000, 0, 0), BE_FLANDERS
        )

    def test_basket_size(self) -> None:
        res = evaluate(inp(0, 0, 0, 0), BE_FLANDERS)
        assert res.basket_wh == pytest.approx(1375)
        assert res.margin_wh == pytest.approx(25)

    def test_60_min_window(self) -> None:
        res = evaluate(inp(1800, 1650, 0, 1650 * 0.5), HOURLY)
        assert res.basket_wh == pytest.approx(5500)
        # 5400*3600 - 1650*1800 J over 1800 s → 9150 W
        assert res.limit_w == pytest.approx(9150)
        assert res.mode is Mode.CONTINUOUS


class TestFallback:
    @pytest.mark.parametrize(
        ("grid", "load", "used"),
        [(None, 0, 0), (1000, None, 0), (1000, 0, None), (None, None, None)],
    )
    def test_missing_input_gives_safe_static_limit(
        self, grid: float | None, load: float | None, used: float | None
    ) -> None:
        res = evaluate(inp(300, grid, load, used), BE_FLANDERS)
        assert res.mode is Mode.FALLBACK
        assert res.limit_w == pytest.approx(GOAL)
        assert res.limit_w < TARGET

    def test_fallback_without_house_allows_nothing(self) -> None:
        res = evaluate(inp(300, None, 0, 100), BE_FLANDERS)
        assert res.allowed_load_w == 0

    def test_fallback_with_house_acts_as_static_circuit(self) -> None:
        res = evaluate(inp(300, 1500, 0, None), BE_FLANDERS)
        assert res.allowed_load_w == pytest.approx(GOAL - 1500)


class TestTarget:
    def test_target_is_max_of_peak_goal_and_minimum(self) -> None:
        assert compute_target(BE_FLANDERS, 5500, 5288) == 5500
        assert compute_target(BE_FLANDERS, 5500, 6100) == 6100
        assert compute_target(BE_FLANDERS, 2000, None) == 2500


class TestSimulatedWindows:
    """Whole-window behaviour: the window average stays under the goal."""

    @pytest.mark.parametrize("house", [300, 1000, 1350, 1650, 3000])
    def test_ideal_response_stays_under_goal(self, house: float) -> None:
        used_wh, _, _ = simulate(house)
        assert used_wh * 3600 / 900 <= GOAL + 1

    def test_pulse_duty_at_night_load(self) -> None:
        """At a 1.65 kW house load the pulse model keeps the load running ~85 % of the time."""
        _used_wh, on_s, modes = simulate(1650)
        assert Mode.PULSE_ON in modes
        assert Mode.PULSE_OFF in modes
        assert 0.75 <= on_s / 900 <= 0.95

    @pytest.mark.parametrize("house", [1000, 1650])
    def test_reaction_lag_stays_under_target(self, house: float) -> None:
        """With a 50 s actuator lag (EVCC) the margin may be used, the target not."""
        used_wh, _, _ = simulate(house, lag_s=50)
        assert used_wh * 3600 / 900 <= TARGET

    def test_hourly_window_stays_under_goal(self) -> None:
        used_wh, _, _ = simulate(1650, profile=HOURLY)
        assert used_wh <= GOAL + 1


class TestJinjaParity:
    """Details of the Jinja macro this model was ported from."""

    def test_pulse_threshold_uses_actual_load_power_when_higher(self) -> None:
        # Running at 16 A: threshold = 11040 W x 90 s instead of 4140 W x 90 s.
        house, t = 1650.0, 600.0
        rest = 900 - t
        e_load = P_MIN * P.reaction_s * 2  # above 4140x90, below 11040x90
        used_wh = (GOAL * 900 - house * rest - e_load) / 3600
        slow = evaluate(inp(t, house + P_MIN, P_MIN, used_wh), BE_FLANDERS)
        fast = evaluate(inp(t, house + 11040, 11040, used_wh), BE_FLANDERS)
        assert slow.mode is Mode.PULSE_ON
        assert fast.mode is Mode.PULSE_OFF

    def test_running_threshold_is_100w(self) -> None:
        house, t = 1650.0, 600.0
        e_load = P_MIN * (P.reaction_s + 10)
        used_wh = (GOAL * 900 - house * 300 - e_load) / 3600
        assert evaluate(inp(t, house + 150, 150, used_wh), BE_FLANDERS).mode is Mode.PULSE_ON
        assert evaluate(inp(t, house + 90, 90, used_wh), BE_FLANDERS).mode is Mode.PULSE_OFF

    def test_pulse_charge_minutes(self) -> None:
        on = evaluate(inp(0, 1650, 0, 0), BE_FLANDERS)
        e_load = GOAL * 900 - 1650 * 900
        assert on.charge_minutes == pytest.approx((e_load / P_MIN - P.reaction_s) / 60)
        off = evaluate(inp(750, 5790, P_MIN, 5790 * 750 / 3600), BE_FLANDERS)
        assert off.mode is Mode.PULSE_OFF
        assert off.charge_minutes == 0

    def test_house_reserve_capped_by_remaining(self) -> None:
        # Over budget: remaining negative → reserve 0, room 0.
        res = evaluate(inp(600, 3000, 0, 1400), BE_FLANDERS)
        assert res.remaining_wh == pytest.approx(1350 - 1400)
        assert res.house_reserve_wh == 0
        assert res.load_room_wh == 0
        # Little left: reserve limited to what is left.
        res = evaluate(inp(600, 3000, 0, 1150), BE_FLANDERS)
        assert res.house_reserve_wh == pytest.approx(200)


class TestVoltages:
    def test_priority_per_phase_then_nominal(self) -> None:
        # L1 from charger, L2 charger invalid → meter, L3 nothing → 230
        assert phase_voltage_sum([(235, 232), (0, 231), (None, None)]) == pytest.approx(696)
        assert phase_voltage_sum([240]) == pytest.approx(240 + 230 + 230)
        assert phase_voltage_sum(None) == pytest.approx(690)
        assert phase_voltage_sum([180, 181, None]) == pytest.approx(230 + 181 + 230)


class TestOtherLoads:
    def test_lower_priority_loads_do_not_count_as_house(self) -> None:
        res = evaluate(
            ModelInput(
                target_w=TARGET,
                seconds_in_window=0,
                grid_import_w=7140,
                load_w=4140,
                window_import_wh=0,
                other_loads_w=2000,
            ),
            BE_FLANDERS,
        )
        assert res.house_w == 1000

    def test_unknown_other_loads_fall_back(self) -> None:
        res = evaluate(
            ModelInput(
                target_w=TARGET,
                seconds_in_window=0,
                grid_import_w=7140,
                load_w=4140,
                window_import_wh=0,
                other_loads_w=None,
            ),
            BE_FLANDERS,
        )
        assert res.mode is Mode.FALLBACK
        assert res.allowed_load_w == 0


class TestSeveralLoads:
    def _inp(self, **kwargs: float | None) -> ModelInput:
        base: dict[str, Any] = {
            "target_w": TARGET,
            "seconds_in_window": 0,
            "grid_import_w": 7140,
            "load_w": 4140,
            "window_import_wh": 0,
            "other_loads_w": 2000,
        }
        return ModelInput(**(base | kwargs))

    def test_grid_limit_includes_the_loads_that_yield(self) -> None:
        alone = evaluate(self._inp(grid_import_w=5140, other_loads_w=0), BE_FLANDERS)
        res = evaluate(self._inp(), BE_FLANDERS)
        assert res.allowed_load_w == pytest.approx(alone.allowed_load_w)
        assert res.limit_w == pytest.approx(alone.limit_w + 2000)

    def test_fallback_counts_the_loads_that_yield_as_house(self) -> None:
        res = evaluate(self._inp(window_import_wh=None), BE_FLANDERS)
        assert res.mode is Mode.FALLBACK
        assert res.limit_w == pytest.approx(GOAL)
        assert res.allowed_load_w == pytest.approx(GOAL - 1000 - 2000)

    def test_a_claim_of_a_starting_load_counts_as_house(self) -> None:
        free = evaluate(self._inp(grid_import_w=1000, load_w=0, other_loads_w=0), BE_FLANDERS)
        claimed = evaluate(
            self._inp(grid_import_w=1000, load_w=0, other_loads_w=0, reserved_w=3000),
            BE_FLANDERS,
        )
        assert claimed.house_w == pytest.approx(4000)
        assert claimed.allowed_load_w < free.allowed_load_w
