"""Clock-aligned metering windows: fixed blocks, not a sliding average."""

from __future__ import annotations

from datetime import datetime, timedelta


def window_start(now: datetime, window_s: int) -> datetime:
    """Start of the window containing ``now``.

    Windows are aligned to local midnight, so a 15-minute window starts at
    :00/:15/:30/:45 and a 60-minute window on the hour. ``window_s`` must divide
    one day.
    """
    if 86400 % window_s:
        msg = f"window of {window_s}s does not divide a day"
        raise ValueError(msg)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elapsed = (now - midnight).total_seconds()
    return midnight + timedelta(seconds=int(elapsed // window_s) * window_s)


def seconds_in_window(now: datetime, window_s: int) -> float:
    """Seconds elapsed since the start of the current window."""
    return (now - window_start(now, window_s)).total_seconds()
