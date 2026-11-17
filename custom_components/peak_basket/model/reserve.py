"""The reserve of the loads that are only measured.

A steered load's room already leaves room for the measured-only loads: they are house to
it. The connection's house reserve does not hold them. So each measured-only load keeps a
reserve of its own, its power until the window ends, out of what the house leaves: the
house reserve, the measured reserves and the first steered load's room add up to what
remains.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

SECONDS_PER_HOUR = 3600


def measured_reserves(
    remaining_wh: float | None,
    house_reserve_wh: float | None,
    powers_w: Sequence[float],
    rest_s: float,
) -> list[float | None]:
    """Reserve (Wh) per measured-only load, in the order given; None when unknown."""
    if remaining_wh is None or house_reserve_wh is None:
        return [None] * len(powers_w)
    left = max(remaining_wh, 0.0) - house_reserve_wh
    reserves: list[float | None] = []
    for power in powers_w:
        reserve = min(max(power, 0.0) * rest_s / SECONDS_PER_HOUR, left)
        left -= reserve
        reserves.append(reserve)
    return reserves
