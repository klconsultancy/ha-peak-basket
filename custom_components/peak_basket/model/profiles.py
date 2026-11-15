"""Region profiles: tariff rules as data, kept out of the engine."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RegionProfile:
    """The rules of one capacity-tariff region."""

    key: str
    window_s: int
    """Length of one metering window, aligned to clock blocks."""
    min_peak_w: float = 0.0
    """Billed minimum; peaks below it cost nothing extra."""


BE_FLANDERS = RegionProfile(key="be_flanders", window_s=15 * 60, min_peak_w=2500.0)

PROFILES: dict[str, RegionProfile] = {p.key: p for p in (BE_FLANDERS,)}
