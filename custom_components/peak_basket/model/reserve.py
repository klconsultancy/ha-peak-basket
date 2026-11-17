"""The reserve of the loads that are only measured.

A steered load's room already leaves room for the measured-only loads: they are house to
it. The connection's house reserve does not hold them. So the measured-only loads keep a
reserve of their own, until the window ends, out of what the house leaves: the house
reserve, the measured reserves and the first steered load's room add up to what remains.

Only grid import comes out of the basket. What the reserves project is the difference
between the two houses: the first steered load's (the grid minus the steered loads) and
the connection's (the grid minus every load), each at least 0. With solar covering part
of the measured loads, that is their grid share, not their power. It goes to them in
priority order, each at most its own power.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from .engine import ModelResult

SECONDS_PER_HOUR = 3600


def measured_reserves(
    connection: ModelResult,
    powers_w: Sequence[float],
    rest_s: float,
    grid_w: float | None,
    steered_w: float,
) -> list[float | None]:
    """Reserve (Wh) per measured-only load, in the order given; None when unknown.

    ``connection`` is the basket with every load taken out of the house, ``grid_w`` the
    grid power and ``steered_w`` the steered loads' power together. The powers are at
    least 0: the coordinator reads a negative one as 0 W.
    """
    remaining_wh, house_reserve_wh = connection.remaining_wh, connection.house_reserve_wh
    if remaining_wh is None or house_reserve_wh is None or grid_w is None:
        return [None] * len(powers_w)
    beside_steered = max(max(grid_w, 0.0) - steered_w, 0.0)
    share = beside_steered - max(beside_steered - sum(powers_w), 0.0)
    left = max(remaining_wh, 0.0) - house_reserve_wh
    reserves: list[float | None] = []
    for power in powers_w:
        from_grid = min(power, share)
        share -= from_grid
        reserve = min(from_grid * rest_s / SECONDS_PER_HOUR, left)
        left -= reserve
        reserves.append(reserve)
    return reserves
