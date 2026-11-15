from __future__ import annotations

from datetime import UTC, datetime

import pytest

from custom_components.peak_basket.model import allocate_solar, seconds_in_window, window_start


class TestSolarAllocation:
    def test_solar_goes_to_load_first(self) -> None:
        a = allocate_solar(solar=3000, export=500, grid_import=2000, loads=[4000])
        assert a.loads[0].solar == 2500
        assert a.loads[0].grid == 1500
        assert a.house_solar == 0
        assert a.house_grid == 500
        assert a.export == 500

    def test_surplus_solar_goes_to_house(self) -> None:
        a = allocate_solar(solar=5000, export=1000, grid_import=0, loads=[2000])
        assert a.loads[0].solar == 2000
        assert a.loads[0].grid == 0
        assert a.house_solar == 2000
        assert a.house_grid == 0

    def test_priority_order(self) -> None:
        a = allocate_solar(solar=3000, export=0, grid_import=4000, loads=[2000, 2000])
        assert (a.loads[0].solar, a.loads[0].grid) == (2000, 0)
        assert (a.loads[1].solar, a.loads[1].grid) == (1000, 1000)
        assert a.house_grid == 3000

    def test_load_grid_part_bounded_by_import(self) -> None:
        """A load counter that lags can report more than the grid supplied."""
        a = allocate_solar(solar=0, export=0, grid_import=1000, loads=[1200])
        assert a.loads[0].grid == 1000
        assert a.house_grid == 0

    def test_night_no_solar(self) -> None:
        a = allocate_solar(solar=0, export=0, grid_import=5000, loads=[4000])
        assert a.loads[0].grid == 4000
        assert a.house_grid == 1000


class TestWindow:
    @pytest.mark.parametrize(
        ("now", "start"),
        [
            (
                datetime(2026, 11, 15, 10, 14, 59, tzinfo=UTC),
                datetime(2026, 11, 15, 10, 0, tzinfo=UTC),
            ),
            (
                datetime(2026, 11, 15, 10, 15, 0, tzinfo=UTC),
                datetime(2026, 11, 15, 10, 15, tzinfo=UTC),
            ),
            (
                datetime(2026, 11, 15, 23, 59, 59, tzinfo=UTC),
                datetime(2026, 11, 15, 23, 45, tzinfo=UTC),
            ),
        ],
    )
    def test_quarter_blocks(self, now: datetime, start: datetime) -> None:
        assert window_start(now, 900) == start

    def test_seconds_in_window(self) -> None:
        assert seconds_in_window(datetime(2026, 11, 15, 3, 7, 30, tzinfo=UTC), 900) == 450

    def test_hourly_blocks(self) -> None:
        now = datetime(2026, 11, 15, 3, 59, 0, tzinfo=UTC)
        assert window_start(now, 3600) == datetime(2026, 11, 15, 3, 0, tzinfo=UTC)

    def test_window_must_divide_day(self) -> None:
        with pytest.raises(ValueError, match="does not divide a day"):
            window_start(datetime(2026, 11, 15, tzinfo=UTC), 7 * 60)
