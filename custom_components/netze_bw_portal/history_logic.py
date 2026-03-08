"""Pure history gap and status logic for Netze BW Portal."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from .const import (
    HISTORY_DAILY_DELAY_DAYS,
    HISTORY_HOURLY_DELAY_HOURS,
    MEASUREMENT_FILTER_DAY,
    MEASUREMENT_FILTER_HOUR,
    PORTAL_TIMEZONE,
)
from .models import HistoryGap, HistoryState, MeasurementSeries

PORTAL_TZ = ZoneInfo(PORTAL_TIMEZONE)


@dataclass(frozen=True)
class HistoryComputation:
    """Result of evaluating interval completeness."""

    last_daily_point: datetime | None
    last_hourly_point: datetime | None
    open_gaps: tuple[HistoryGap, ...]
    status: str


def _ensure_utc(value: datetime) -> datetime:
    """Return a timezone-aware UTC datetime."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def expected_daily_starts(now: datetime, days: int) -> list[datetime]:
    """Return expected completed day interval starts in portal local time.

    The portal uses Europe/Berlin midnight as day boundaries, so a daily
    interval for e.g. March 6 CET starts at 2026-03-05T23:00:00Z (CET+1).
    """
    now = _ensure_utc(now)
    local_now = now.astimezone(PORTAL_TZ)
    completed_until = local_now.date() - timedelta(days=HISTORY_DAILY_DELAY_DAYS)
    first_day = completed_until - timedelta(days=days - 1)
    return [
        datetime.combine(
            first_day + timedelta(days=offset), time.min, tzinfo=PORTAL_TZ
        ).astimezone(timezone.utc)
        for offset in range(days)
    ]


def expected_hourly_starts(now: datetime, days: int) -> list[datetime]:
    """Return expected completed hour interval starts."""
    now = _ensure_utc(now)
    cutoff = now.replace(minute=0, second=0, microsecond=0) - timedelta(
        hours=HISTORY_HOURLY_DELAY_HOURS
    )
    first = cutoff - timedelta(days=days) + timedelta(hours=1)
    return [first + timedelta(hours=offset) for offset in range(int((cutoff - first).total_seconds() // 3600) + 1)]


def _find_gaps(
    interval: str,
    expected_starts: list[datetime],
    series: MeasurementSeries | None,
) -> tuple[HistoryGap, ...]:
    if not expected_starts:
        return ()

    actual = {
        _ensure_utc(point.start_datetime)
        for point in (series.points if series is not None else [])
        if point.value is not None
    }
    if series is not None and series.min_measurement_start_datetime is not None:
        lower_bound = _ensure_utc(series.min_measurement_start_datetime)
    elif actual:
        lower_bound = min(actual)
    else:
        return ()

    step = timedelta(days=1) if interval == MEASUREMENT_FILTER_DAY else timedelta(hours=1)
    if series is not None and series.max_measurement_end_datetime is not None:
        upper_bound = _ensure_utc(series.max_measurement_end_datetime) - step
    elif actual:
        upper_bound = max(actual)
    else:
        return ()

    gaps = []
    for expected_start in expected_starts:
        es = _ensure_utc(expected_start)
        if es < lower_bound or es > upper_bound:
            continue
        if es not in actual:
            gaps.append(
                HistoryGap(
                    interval=interval,
                    start_datetime=es,
                    end_datetime=es + step,
                )
            )
    return tuple(gaps)


def compute_history_state(
    *,
    now: datetime,
    daily_series: MeasurementSeries | None,
    hourly_series: MeasurementSeries | None,
    daily_enabled: bool,
    hourly_enabled: bool,
    backfill_days: int,
    last_backfill: datetime | None,
) -> HistoryState:
    """Compute visible history status for a meter."""
    now = _ensure_utc(now)
    daily_points = daily_series.points if daily_series is not None else []
    hourly_points = hourly_series.points if hourly_series is not None else []

    last_daily_point = max(
        (_ensure_utc(point.end_datetime) for point in daily_points if point.end_datetime is not None),
        default=None,
    )
    last_hourly_point = max(
        (_ensure_utc(point.end_datetime) for point in hourly_points if point.end_datetime is not None),
        default=None,
    )

    gaps: list[HistoryGap] = []
    if daily_enabled:
        gaps.extend(
            _find_gaps(
                MEASUREMENT_FILTER_DAY,
                expected_daily_starts(now, backfill_days),
                daily_series,
            )
        )
    if hourly_enabled:
        gaps.extend(
            _find_gaps(
                MEASUREMENT_FILTER_HOUR,
                expected_hourly_starts(now, backfill_days),
                hourly_series,
            )
        )

    if gaps:
        status = "gaps"
    elif daily_enabled or hourly_enabled:
        status = "ok"
    else:
        status = "disabled"

    return HistoryState(
        status=status,
        last_daily_point=last_daily_point,
        last_hourly_point=last_hourly_point,
        open_gaps=tuple(gaps),
        last_backfill=last_backfill,
    )
