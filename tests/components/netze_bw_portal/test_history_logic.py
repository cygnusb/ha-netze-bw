"""Tests for history gap computation."""

from __future__ import annotations

from datetime import date, datetime, timezone

from custom_components.netze_bw_portal.const import MEASUREMENT_FILTER_DAY, MEASUREMENT_FILTER_HOUR
from custom_components.netze_bw_portal.history_logic import (
    compute_history_state,
    expected_daily_dates,
    expected_hourly_dates,
)


def test_expected_daily_dates_use_portal_calendar_boundaries() -> None:
    """Expected daily dates should follow the portal timezone cutoff."""
    now = datetime(2026, 3, 8, 12, 0, tzinfo=timezone.utc)

    dates = expected_daily_dates(now, days=3)

    assert dates == {
        date(2026, 3, 5),
        date(2026, 3, 6),
        date(2026, 3, 7),
    }


def test_expected_daily_dates_handle_dst_transition() -> None:
    """Day selection must stay correct across the March DST switch."""
    now = datetime(2026, 3, 30, 12, 0, tzinfo=timezone.utc)

    dates = expected_daily_dates(now, days=3)

    assert dates == {
        date(2026, 3, 27),
        date(2026, 3, 28),
        date(2026, 3, 29),
    }


def test_expected_hourly_dates_exclude_incomplete_current_day() -> None:
    """Hourly expectations should ignore the still-incomplete current local day."""
    now = datetime(2026, 3, 8, 12, 0, tzinfo=timezone.utc)

    dates = expected_hourly_dates(now, days=2)

    assert dates == {
        date(2026, 3, 6),
        date(2026, 3, 7),
    }


def test_compute_history_state_rechecks_recent_completed_windows() -> None:
    """Recent completed windows stay marked as open for recheck."""
    now = datetime(2026, 3, 8, 12, 0, tzinfo=timezone.utc)

    state = compute_history_state(
        now=now,
        daily_fetched_dates={date(2026, 3, 6), date(2026, 3, 7)},
        hourly_fetched_dates={date(2026, 3, 6), date(2026, 3, 7)},
        daily_enabled=True,
        hourly_enabled=True,
        backfill_days=2,
        last_backfill=now,
        last_daily_point=datetime(2026, 3, 7, 23, 0, tzinfo=timezone.utc),
        last_hourly_point=datetime(2026, 3, 8, 6, 0, tzinfo=timezone.utc),
    )

    assert state.status == "gaps"
    assert {gap.interval for gap in state.open_gaps} == {
        MEASUREMENT_FILTER_DAY,
        MEASUREMENT_FILTER_HOUR,
    }
    assert state.last_daily_point == datetime(2026, 3, 7, 23, 0, tzinfo=timezone.utc)
    assert state.last_hourly_point == datetime(2026, 3, 8, 6, 0, tzinfo=timezone.utc)


def test_compute_history_state_detects_missing_intervals() -> None:
    """Completed missing intervals must be marked as gaps."""
    now = datetime(2026, 3, 8, 12, 0, tzinfo=timezone.utc)

    state = compute_history_state(
        now=now,
        daily_fetched_dates={date(2026, 3, 6)},
        hourly_fetched_dates=set(),
        daily_enabled=True,
        hourly_enabled=False,
        backfill_days=2,
        last_backfill=now,
    )

    assert state.status == "gaps"
    assert len(state.open_gaps) == 2
    assert {gap.interval for gap in state.open_gaps} == {MEASUREMENT_FILTER_DAY}
    assert {gap.start_datetime for gap in state.open_gaps} == {
        datetime(2026, 3, 5, 23, 0, tzinfo=timezone.utc),
        datetime(2026, 3, 6, 23, 0, tzinfo=timezone.utc),
    }


def test_compute_history_state_rechecks_recent_dates() -> None:
    """Recently fetched dates stay open because they are intentionally rechecked."""
    now = datetime(2026, 3, 8, 12, 0, tzinfo=timezone.utc)

    state = compute_history_state(
        now=now,
        daily_fetched_dates={date(2026, 3, 6), date(2026, 3, 7)},
        hourly_fetched_dates=set(),
        daily_enabled=True,
        hourly_enabled=False,
        backfill_days=2,
        last_backfill=now,
    )

    assert state.status == "gaps"
    assert {gap.start_datetime for gap in state.open_gaps} == {
        datetime(2026, 3, 5, 23, 0, tzinfo=timezone.utc),
        datetime(2026, 3, 6, 23, 0, tzinfo=timezone.utc),
    }


def test_expected_daily_dates_raises_on_non_positive_days() -> None:
    """expected_daily_dates must reject days <= 0."""
    import pytest

    now = datetime(2026, 3, 8, 12, 0, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="days must be > 0"):
        expected_daily_dates(now, days=0)
    with pytest.raises(ValueError, match="days must be > 0"):
        expected_daily_dates(now, days=-1)


def test_expected_hourly_dates_raises_on_non_positive_days() -> None:
    """expected_hourly_dates must reject days <= 0."""
    import pytest

    from custom_components.netze_bw_portal.history_logic import expected_hourly_dates

    now = datetime(2026, 3, 8, 12, 0, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="days must be > 0"):
        expected_hourly_dates(now, days=0)
    with pytest.raises(ValueError, match="days must be > 0"):
        expected_hourly_dates(now, days=-5)


def test_compute_history_state_disabled() -> None:
    """When all history modes are disabled, status should be disabled."""
    now = datetime(2026, 3, 8, 12, 0, tzinfo=timezone.utc)

    state = compute_history_state(
        now=now,
        daily_fetched_dates=set(),
        hourly_fetched_dates=set(),
        daily_enabled=False,
        hourly_enabled=False,
        backfill_days=2,
        last_backfill=now,
    )

    assert state.status == "disabled"
    assert state.open_gaps == ()
