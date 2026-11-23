from datetime import UTC, datetime, timedelta, timezone

DESK_TIMEZONE = timezone(timedelta(hours=8))


def utc_time(seconds=0):
    return (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat(timespec='seconds')
