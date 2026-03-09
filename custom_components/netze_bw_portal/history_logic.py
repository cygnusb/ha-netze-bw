"""Pure history gap and status logic for Netze BW Portal."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from .const import (
    HISTORY_DAILY_DELAY_DAYS,
    HISTORY_HOURLY_DELAY_HOURS,
    HISTORY_RECHECK_DAYS,
    MEASUREMENT_FILTER_15MIN,
    MEASUREMENT_FILTER_DAY,
    MEASUREMENT_FILTER_HOUR,
    PORTAL_TIMEZONE,
)
from .models import HistoryGap, HistoryState

PORTAL_TZ = ZoneInfo(PORTAL_TIMEZONE)


def _ensure_utc(value: datetime) -> datetime:
    """Return a timezone-aware UTC datetime."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def expected_daily_dates(now: datetime, days: int) -> set[date]:
    """Return the set of CET calendar dates we expect daily data for."""
    now = _ensure_utc(now)
    local_now = now.astimezone(PORTAL_TZ)
    completed_until = local_now.date() - timedelta(days=HISTORY_DAILY_DELAY_DAYS)
    first_day = completed_until - timedelta(days=days - 1)
    return {first_day + timedelta(days=offset) for offset in range(days)}


def expected_hourly_dates(now: datetime, days: int) -> set[date]:
    """Return the set of CET calendar dates we expect hourly data for.

    The hourly cutoff is now minus HISTORY_HOURLY_DELAY_HOURS. Any day
    whose 24h window is fully completed before the cutoff is expected.
    """
    now = _ensure_utc(now)
    cutoff = now - timedelta(hours=HISTORY_HOURLY_DELAY_HOURS)
    local_cutoff = cutoff.astimezone(PORTAL_TZ)
    # A day is complete when its end-of-day (next midnight) <= cutoff
    completed_until = local_cutoff.date() - timedelta(days=1)
    first_day = completed_until - timedelta(days=days - 1)
    if first_day > completed_until:
        return set()
    return {first_day + timedelta(days=offset) for offset in range((completed_until - first_day).days + 1)}


def missing_dates(
    expected: set[date],
    fetched: set[date],
    recheck_days: int = HISTORY_RECHECK_DAYS,
) -> set[date]:
    """Compute which dates still need to be fetched.

    The last *recheck_days* dates are always considered missing (data
    arrives with a delay and may be revised).
    """
    if not expected:
        return set()
    sorted_expected = sorted(expected)
    always_recheck = set(sorted_expected[-recheck_days:]) if recheck_days else set()
    return (expected - fetched) | (always_recheck & expected)


def prune_dates(dates: set[date], max_age_days: int, reference: date) -> set[date]:
    """Remove dates older than *max_age_days* from the set."""
    cutoff = reference - timedelta(days=max_age_days)
    return {d for d in dates if d >= cutoff}


def compute_history_state(
    *,
    now: datetime,
    daily_fetched_dates: set[date],
    hourly_fetched_dates: set[date],
    daily_enabled: bool,
    hourly_enabled: bool,
    backfill_days: int,
    last_backfill: datetime | None,
    last_daily_point: datetime | None = None,
    last_hourly_point: datetime | None = None,
    fifteenmin_fetched_dates: set[date] | None = None,
    fifteenmin_enabled: bool = False,
    last_15min_point: datetime | None = None,
) -> HistoryState:
    """Compute visible history status for a meter from fetched-date sets."""
    now = _ensure_utc(now)

    gaps: list[HistoryGap] = []
    if daily_enabled:
        exp_daily = expected_daily_dates(now, backfill_days)
        daily_missing = missing_dates(exp_daily, daily_fetched_dates)
        for d in sorted(daily_missing):
            start = datetime.combine(d, time.min, tzinfo=PORTAL_TZ).astimezone(timezone.utc)
            gaps.append(HistoryGap(
                interval=MEASUREMENT_FILTER_DAY,
                start_datetime=start,
                end_datetime=start + timedelta(days=1),
            ))

    if hourly_enabled:
        exp_hourly = expected_hourly_dates(now, backfill_days)
        hourly_missing = missing_dates(exp_hourly, hourly_fetched_dates)
        for d in sorted(hourly_missing):
            start = datetime.combine(d, time.min, tzinfo=PORTAL_TZ).astimezone(timezone.utc)
            gaps.append(HistoryGap(
                interval=MEASUREMENT_FILTER_HOUR,
                start_datetime=start,
                end_datetime=start + timedelta(days=1),
            ))

    if fifteenmin_enabled:
        exp_15min = expected_hourly_dates(now, backfill_days)
        for d in sorted(missing_dates(exp_15min, fifteenmin_fetched_dates or set())):
            start = datetime.combine(d, time.min, tzinfo=PORTAL_TZ).astimezone(timezone.utc)
            gaps.append(HistoryGap(
                interval=MEASUREMENT_FILTER_15MIN,
                start_datetime=start,
                end_datetime=start + timedelta(days=1),
            ))

    if gaps:
        status = "gaps"
    elif daily_enabled or hourly_enabled or fifteenmin_enabled:
        status = "ok"
    else:
        status = "disabled"

    return HistoryState(
        status=status,
        last_daily_point=last_daily_point,
        last_hourly_point=last_hourly_point,
        open_gaps=tuple(gaps),
        last_backfill=last_backfill,
        last_15min_point=last_15min_point,
    )
