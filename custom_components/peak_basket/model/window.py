"""Clock-aligned metering windows: fixed blocks, not a sliding average."""

from __future__ import annotations

from datetime import UTC, datetime


def window_start(now: datetime, window_s: int) -> datetime:
    """Return the start of the window containing ``now``, in UTC.

    Windows are aligned to the local wall clock of ``now``: a 15-minute window starts at
    :00, :15, :30 and :45 local time, a 60-minute window on the local hour, also in a
    zone with a half-hour offset. The alignment uses the UTC offset in force at ``now``,
    so the hour that repeats when daylight saving time ends gives windows of its own.
    ``window_s`` must divide one day.
    """
    if 86400 % window_s:
        msg = f"window of {window_s}s does not divide a day"
        raise ValueError(msg)
    offset = now.utcoffset()
    if offset is None:
        msg = "now must carry a time zone"
        raise ValueError(msg)
    local_s = now.timestamp() + offset.total_seconds()
    start_local_s = local_s - local_s % window_s
    return datetime.fromtimestamp(start_local_s - offset.total_seconds(), tz=UTC)


def seconds_in_window(now: datetime, window_s: int) -> float:
    """Return the seconds elapsed since the start of the current window."""
    return (now - window_start(now, window_s)).total_seconds()
