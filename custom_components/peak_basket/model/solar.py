"""Attribution of solar self-consumption to loads, a display convention.

Physically and for the grid operator this makes no difference. For display, solar goes
to the loads first, in the order given (the steered loads by priority, then the
measured-only ones), and the rest to the house, so the house carries most of the grid
import and you see how much the flexible loads leaned on the basket.

Works on power (W) or on energy per window (Wh) alike.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence


@dataclass(frozen=True)
class LoadSplit:
    """Where one load's consumption came from."""

    solar: float
    grid: float


@dataclass(frozen=True)
class SolarAllocation:
    """Solar and grid split over the loads and the house."""

    loads: tuple[LoadSplit, ...]
    """In the order of the ``loads`` argument (priority order)."""
    house_solar: float
    house_grid: float
    export: float


def allocate_solar(
    solar: float, export: float, grid_import: float, loads: Sequence[float]
) -> SolarAllocation:
    """Split solar self-consumption and grid import over the loads, then the house."""
    export = max(export, 0.0)
    grid_left = max(grid_import, 0.0)
    solar_left = max(max(solar, 0.0) - export, 0.0)

    splits = []
    for load_value in loads:
        load = max(load_value, 0.0)
        from_solar = min(load, solar_left)
        solar_left -= from_solar
        from_grid = min(load - from_solar, grid_left)
        grid_left -= from_grid
        splits.append(LoadSplit(solar=from_solar, grid=from_grid))

    return SolarAllocation(
        loads=tuple(splits), house_solar=solar_left, house_grid=grid_left, export=export
    )
