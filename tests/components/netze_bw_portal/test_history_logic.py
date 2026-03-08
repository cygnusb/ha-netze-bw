"""Tests for history gap computation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from custom_components.netze_bw_portal.const import MEASUREMENT_FILTER_DAY, MEASUREMENT_FILTER_HOUR
from custom_components.netze_bw_portal.history_logic import (
    compute_history_state,
    expected_daily_starts,
    expected_hourly_starts,
)
from custom_components.netze_bw_portal.models import MeasurementPoint, MeasurementSeries

CET = ZoneInfo("Europe/Berlin")


def _cet_midnight_utc(year: int, month: int, day: int) -> datetime:
    """Return midnight CET/CEST for the given date, converted to UTC.

    This matches the portal's day boundary: e.g. March 6 CET midnight
    = 2026-03-05T23:00:00Z (CET = UTC+1 in winter).
    """
    return datetime(year, month, day, 0, 0, tzinfo=CET).astimezone(timezone.utc)


def test_expected_daily_starts_uses_cet_boundaries() -> None:
    """Expected daily starts must align with CET midnight, not UTC midnight."""
    # March 8 2026 is CET (UTC+1), DST switch is March 29
    now = datetime(2026, 3, 8, 12, 0, tzinfo=timezone.utc)  # 13:00 CET
    starts = expected_daily_starts(now, days=3)

    # completed_until = March 7 (delay=1), first_day = March 5
    assert len(starts) == 3
    assert starts[0] == _cet_midnight_utc(2026, 3, 5)  # March 5 00:00 CET = March 4 23:00 UTC
    assert starts[1] == _cet_midnight_utc(2026, 3, 6)  # March 6 00:00 CET = March 5 23:00 UTC
    assert starts[2] == _cet_midnight_utc(2026, 3, 7)  # March 7 00:00 CET = March 6 23:00 UTC


def test_expected_daily_starts_handles_dst_transition() -> None:
    """Day boundaries must be correct across DST switch (CET→CEST)."""
    # March 30 2026 is CEST (UTC+2), DST switch was March 29
    now = datetime(2026, 3, 30, 12, 0, tzinfo=timezone.utc)
    starts = expected_daily_starts(now, days=3)

    # March 27 is still CET (UTC+1), March 29+ is CEST (UTC+2)
    assert starts[0] == _cet_midnight_utc(2026, 3, 27)  # CET: March 26 23:00 UTC
    assert starts[1] == _cet_midnight_utc(2026, 3, 28)  # CET: March 27 23:00 UTC
    assert starts[2] == _cet_midnight_utc(2026, 3, 29)  # CEST: March 28 22:00 UTC


def test_compute_history_state_ignores_current_incomplete_windows() -> None:
    """Recent incomplete day/hour windows must not count as gaps."""
    now = datetime(2026, 3, 8, 12, 0, tzinfo=timezone.utc)

    # Portal daily data uses CET midnight boundaries
    mar6_start = _cet_midnight_utc(2026, 3, 6)  # March 5 23:00 UTC
    mar7_start = _cet_midnight_utc(2026, 3, 7)  # March 6 23:00 UTC
    mar8_start = _cet_midnight_utc(2026, 3, 8)  # March 7 23:00 UTC

    daily_series = MeasurementSeries(
        meter_id="meter-1",
        value_type="CONSUMPTION",
        interval=MEASUREMENT_FILTER_DAY,
        unit="kWh",
        points=[
            MeasurementPoint(
                start_datetime=mar6_start,
                end_datetime=mar7_start,
                value=4.0,
                unit="kWh",
                status="ORIGINAL",
            ),
            MeasurementPoint(
                start_datetime=mar7_start,
                end_datetime=mar8_start,
                value=5.0,
                unit="kWh",
                status="ORIGINAL",
            ),
        ],
        min_measurement_start_datetime=datetime(2026, 3, 6, 0, 0, tzinfo=timezone.utc),
        max_measurement_end_datetime=datetime(2026, 3, 8, 0, 0, tzinfo=timezone.utc),
    )
    hourly_series = MeasurementSeries(
        meter_id="meter-1",
        value_type="CONSUMPTION",
        interval=MEASUREMENT_FILTER_HOUR,
        unit="kWh",
        points=[
            MeasurementPoint(
                start_datetime=datetime(2026, 3, 8, 5, 0, tzinfo=timezone.utc),
                end_datetime=datetime(2026, 3, 8, 6, 0, tzinfo=timezone.utc),
                value=0.4,
                unit="kWh",
                status="ORIGINAL",
            ),
        ],
        min_measurement_start_datetime=datetime(2026, 3, 8, 5, 0, tzinfo=timezone.utc),
        max_measurement_end_datetime=datetime(2026, 3, 8, 6, 0, tzinfo=timezone.utc),
    )

    state = compute_history_state(
        now=now,
        daily_series=daily_series,
        hourly_series=hourly_series,
        daily_enabled=True,
        hourly_enabled=True,
        backfill_days=2,
        last_backfill=now,
    )

    assert state.status == "ok"
    assert state.open_gaps == ()
    assert state.last_daily_point == mar8_start
    assert state.last_hourly_point == datetime(2026, 3, 8, 6, 0, tzinfo=timezone.utc)


def test_compute_history_state_detects_missing_intervals() -> None:
    """Completed missing intervals must be marked as gaps."""
    now = datetime(2026, 3, 8, 12, 0, tzinfo=timezone.utc)

    mar6_start = _cet_midnight_utc(2026, 3, 6)  # March 5 23:00 UTC
    mar7_start = _cet_midnight_utc(2026, 3, 7)  # March 6 23:00 UTC

    daily_series = MeasurementSeries(
        meter_id="meter-1",
        value_type="CONSUMPTION",
        interval=MEASUREMENT_FILTER_DAY,
        unit="kWh",
        points=[
            MeasurementPoint(
                start_datetime=mar6_start,
                end_datetime=mar7_start,
                value=4.0,
                unit="kWh",
                status="ORIGINAL",
            )
        ],
        min_measurement_start_datetime=datetime(2026, 3, 6, 0, 0, tzinfo=timezone.utc),
        max_measurement_end_datetime=datetime(2026, 3, 8, 0, 0, tzinfo=timezone.utc),
    )

    state = compute_history_state(
        now=now,
        daily_series=daily_series,
        hourly_series=None,
        daily_enabled=True,
        hourly_enabled=False,
        backfill_days=2,
        last_backfill=now,
    )

    assert state.status == "gaps"
    assert len(state.open_gaps) == 1
    assert state.open_gaps[0].interval == MEASUREMENT_FILTER_DAY
    # The gap is for March 7 CET (start = March 6 23:00 UTC)
    assert state.open_gaps[0].start_datetime == mar7_start


def test_compute_history_state_with_naive_min_max_datetimes() -> None:
    """Naive min/max datetimes from the API must not crash comparisons."""
    now = datetime(2026, 3, 8, 12, 0, tzinfo=timezone.utc)

    mar6_start = _cet_midnight_utc(2026, 3, 6)
    mar7_start = _cet_midnight_utc(2026, 3, 7)

    daily_series = MeasurementSeries(
        meter_id="meter-1",
        value_type="CONSUMPTION",
        interval=MEASUREMENT_FILTER_DAY,
        unit="kWh",
        points=[
            MeasurementPoint(
                start_datetime=mar6_start,
                end_datetime=mar7_start,
                value=4.0,
                unit="kWh",
                status="ORIGINAL",
            ),
            MeasurementPoint(
                start_datetime=mar7_start,
                end_datetime=_cet_midnight_utc(2026, 3, 8),
                value=5.0,
                unit="kWh",
                status="ORIGINAL",
            ),
        ],
        # Simulate the API returning naive datetimes for min/max
        min_measurement_start_datetime=datetime(2026, 3, 6, 0, 0, tzinfo=timezone.utc),
        max_measurement_end_datetime=datetime(2026, 3, 8, 0, 0, tzinfo=timezone.utc),
    )

    state = compute_history_state(
        now=now,
        daily_series=daily_series,
        hourly_series=None,
        daily_enabled=True,
        hourly_enabled=False,
        backfill_days=2,
        last_backfill=now,
    )

    # Should not crash and should find no gaps
    assert state.status == "ok"
    assert state.open_gaps == ()


def test_compute_history_state_disabled() -> None:
    """When both daily and hourly are disabled, status should be disabled."""
    now = datetime(2026, 3, 8, 12, 0, tzinfo=timezone.utc)
    state = compute_history_state(
        now=now,
        daily_series=None,
        hourly_series=None,
        daily_enabled=False,
        hourly_enabled=False,
        backfill_days=2,
        last_backfill=now,
    )
    assert state.status == "disabled"
    assert state.open_gaps == ()
