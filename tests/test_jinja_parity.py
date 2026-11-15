"""Golden test: the model must match the Jinja macro it was ported from.

``fixtures/jinja_parity.txt`` was rendered by Home Assistant itself (15/11/2026) from
the macro body, with its ``states()`` reads replaced by the loop variables below and
marge=100, reactie=90, min_laadtijd=300, extra_a=2, target=5500, grid = house + ev,
gemiddeld_verbruik = frac * 5400 * t / 900. One line per case, loop order H,E,T,F,V:
``<mode><limiet>,<laadminuten>,<nog_wh>,<reserve_huis_wh>,<ruimte_laadpaal_wh>``
with mode c=continu, a=puls_aan, u=puls_uit.
"""

from __future__ import annotations

import itertools
from pathlib import Path

import pytest

from custom_components.peak_basket.model import BE_FLANDERS, ModelInput, evaluate

H = [0, 300, 1000, 1150, 1350, 1650, 2200, 3000, 5600]
E = [0, 90, 150, 4140, 7000, 11040]
T = [0, 1, 300, 599, 600, 650, 810, 850, 899, 900]
F = [0.0, 1.0, 1.3, 2.0]
V = [[230, 230, 230], [225, 226, 227], [240, 238, 239]]
MODE = {"continuous": "c", "pulse_on": "a", "pulse_off": "u"}

ROWS = (Path(__file__).parent / "fixtures" / "jinja_parity.txt").read_text().split()
CASES = list(itertools.product(H, E, T, F, V))


def test_fixture_complete() -> None:
    assert len(ROWS) == len(CASES) == 6480


def test_matches_production_macro() -> None:
    mismatches = []
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
        limit, minutes, remaining, reserve, room = map(float, row[1:].split(","))
        ok = (
            row[0] == MODE[res.mode]
            and res.limit_w == pytest.approx(limit, abs=0.5)
            and res.charge_minutes == pytest.approx(minutes, abs=0.051)
            and res.remaining_wh == pytest.approx(remaining, abs=0.5)
            and res.house_reserve_wh == pytest.approx(reserve, abs=0.5)
            and res.load_room_wh == pytest.approx(room, abs=0.5)
        )
        if not ok:
            mismatches.append((house, ev, t, frac, v, row, res))
    assert not mismatches, f"{len(mismatches)} mismatches, first: {mismatches[0]}"
