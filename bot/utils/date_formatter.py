# ─────────────────────────────────────────────
#  utils / date_formatter.py
#
#  Utility functions for converting and formatting
#  datetimes into India Standard Time (IST — UTC+5:30).
# ─────────────────────────────────────────────

from datetime import datetime, timezone, timedelta

# IST timezone offset (UTC + 5:30)
IST = timezone(timedelta(hours=5, minutes=30), name="IST")


def to_ist(dt: datetime | None = None) -> datetime:
    """Convert any datetime (or current time) to IST."""
    if dt is None:
        return datetime.now(IST)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST)


def format_ist_time(dt: datetime | None = None) -> str:
    """Format time as '10:05:12 PM IST' or '03:15:00 AM IST'."""
    ist_dt = to_ist(dt)
    return ist_dt.strftime("%I:%M:%S %p IST")


from datetime import datetime


def format_ist_date(dt: datetime | None = None) -> str:
    """Format date as '04 08 2026'."""
    ist_dt = to_ist(dt)
    return ist_dt.strftime("%d %m %Y")


def format_ist_time(dt: datetime | None = None) -> str:
    """Format time as '10:05 PM'."""
    ist_dt = to_ist(dt)
    return ist_dt.strftime("%I:%M %p")


def format_ist_datetime(dt: datetime | None = None) -> str:
    """Format date & time as '04 08 2026 10:05 PM'."""
    ist_dt = to_ist(dt)
    return ist_dt.strftime("%d %m %Y %I:%M %p")


def format_duration(secs: int | float | None) -> str:
    """
    Format seconds into human readable short duration string.
    Examples:
      45 → '45s'
      120 → '2m'
      3600 → '1h'
      43200 → '12h'
      90000 → '1d 1h'
    """
    if secs is None:
        return "0s"
    secs = int(secs)
    if secs <= 0:
        return "0s"

    days, remainder = divmod(secs, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)

    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0:
        parts.append(f"{minutes}m")
    if seconds > 0 or not parts:
        parts.append(f"{seconds}s")

    return " ".join(parts)
