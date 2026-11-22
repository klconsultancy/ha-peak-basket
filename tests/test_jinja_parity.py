"""Golden test: the model must match the Jinja macro it was ported from.

``fixtures/jinja_parity.txt`` was rendered by Home Assistant itself (15/11/2026) from
the macro body, with its ``states()`` reads replaced by the loop variables below and
marge=100, reactie=90, min_laadtijd=300, extra_a=2, target=5500, grid = house + ev,
gemiddeld_verbruik = frac * 5400 * t / 900. One line per case, loop order H,E,T,F,V:
``<mode><limiet>,<laadminuten>,<nog_wh>,<reserve_huis_wh>,<ruimte_laadpaal_wh>``
with mode c=continu, a=puls_aan, u=puls_uit.

The model departs from the macro in two places, both for #91, and both are stated below
rather than written into the fixture: the fixture stays the macro's output.

- **The continuous boundary.** The macro is continuous from the load's minimum; the model
  from the minimum plus the pulse headroom, so continuous never offers less than pulse on.
  With room for the minimum but not the headroom, the model pulses.
- **The base of the caps in the last 300 s.** The macro's base is ``house + p_min``, the
  model's ``house + p_min + headroom``, so the last 90 s do not offer the bare minimum.
"""

from __future__ import annotations

import itertools
from pathlib import Path

import pytest

from custom_components.peak_basket.model import (
    BE_FLANDERS,
    ModelInput,
    ModelParams,
    ModelResult,
    evaluate,
    phase_voltage_sum,
)

H = [0, 300, 1000, 1150, 1350, 1650, 2200, 3000, 5600]
E = [0, 90, 150, 4140, 7000, 11040]
T = [0, 1, 300, 599, 600, 650, 810, 850, 899, 900]
F = [0.0, 1.0, 1.3, 2.0]
V = [[230, 230, 230], [225, 226, 227], [240, 238, 239]]
MODE = {"continuous": "c", "pulse_on": "a", "pulse_off": "u"}
P = ModelParams()
GOAL = 5500 - P.margin_w

ROWS = (Path(__file__).parent / "fixtures" / "jinja_parity.txt").read_text().split()
CASES = list(itertools.product(H, E, T, F, V))


def test_fixture_complete() -> None:
    assert len(ROWS) == len(CASES) == 6480


def _matches(res: ModelResult, row: str) -> bool:
    limit, minutes = map(float, row[1:].split(",")[:2])
    return (
        row[0] == MODE[res.mode]
        and res.limit_w == pytest.approx(limit, abs=0.5)
        and res.charge_minutes == pytest.approx(minutes, abs=0.051)
        and _same_basket(res, row)
    )


def _same_basket(res: ModelResult, row: str) -> bool:
    """The basket does not depend on the mode: remaining, house reserve, load room."""
    _, _, remaining, reserve, room = map(float, row[1:].split(","))
    return (
        res.remaining_wh == pytest.approx(remaining, abs=0.5)
        and res.house_reserve_wh == pytest.approx(reserve, abs=0.5)
        and res.load_room_wh == pytest.approx(room, abs=0.5)
    )


def _divergence(house: float, t: float, used_wh: float, v: list[int], res: ModelResult) -> str:
    """Which of the two stated divergences a case the macro calls continuous falls under.

    ``boundary``: room for the minimum but not the headroom, and the model pulses with the
    pulse limits. ``base``: the house above the goal minus the minimum plus the headroom in
    the last 300 s, and the model's limit is the macro's with the raised base.
    """
    rest = 900 - t
    p_min = P.min_a * phase_voltage_sum(v)
    floor = p_min + P.pulse_headroom_w
    p_avg = (GOAL * 900 - used_wh * 3600) / max(rest, 1.0)
    if p_min <= p_avg - house < floor:
        on = res.mode == "pulse_on" and res.limit_w == pytest.approx(house + floor)
        off = res.mode == "pulse_off" and res.limit_w == pytest.approx(house)
        return "boundary" if on or off else ""
    if res.mode == "continuous" and rest <= P.tail_s and house + floor > GOAL:
        base = max(GOAL, house + floor)
        cap = base if rest <= P.final_s else base + P.extra_a * phase_voltage_sum(v)
        expected = min(p_avg, house + P.max_a * phase_voltage_sum(v), cap)
        # Still continuous to the window's end: only the cap moves.
        same_minutes = res.charge_minutes == pytest.approx(rest / 60, abs=0.051)
        return "base" if same_minutes and res.limit_w == pytest.approx(expected) else ""
    return ""


def test_matches_production_macro() -> None:
    mismatches = []
    divergences = {"boundary": 0, "base": 0}
    for (house, ev, t, frac, v), row in zip(CASES, ROWS, strict=True):
        used_wh = frac * 5400 * t / 900 * 900 / 3600
        res = evaluate(
            ModelInput(
                target_w=5500,
                seconds_in_window=t,
                grid_import_w=house + ev,
                load_w=ev,
                window_import_wh=used_wh,
                voltages=v,
            ),
            BE_FLANDERS,
        )
        if _matches(res, row):
            continue
        kind = _divergence(house, t, used_wh, v, res) if row[0] == "c" else ""
        if kind and _same_basket(res, row):
            divergences[kind] += 1
        else:
            mismatches.append((house, ev, t, frac, v, row, res))
    assert not mismatches, f"{len(mismatches)} mismatches, first: {mismatches[0]}"
    # Pinned, so a change in either divergence shows here.
    assert divergences == {"boundary": 276, "base": 684}
