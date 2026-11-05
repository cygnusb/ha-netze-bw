"""Tests for cumulative statistic sums across overlapping pushes (issue #9)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from custom_components.netze_bw_portal.const import (
    MEASUREMENT_FILTER_15MIN,
    MEASUREMENT_FILTER_HOUR,
)
from custom_components.netze_bw_portal.history_logic import (
    hourly_states_from_points,
    rebuild_cumulative_rows,
)
from custom_components.netze_bw_portal.models import MeasurementPoint

T0 = datetime(2026, 11, 1, 23, 0, tzinfo=timezone.utc)  # 00:00 local on 2 Nov


def _hours(count: int, value: float, start: datetime = T0) -> dict[datetime, float]:
    return {start + timedelta(hours=i): value for i in range(count)}


def _apply(recorder: dict[datetime, tuple[float, float]], rows) -> None:
    for start, state, total in rows:
        recorder[start] = (state, total)


def _deltas(recorder: dict[datetime, tuple[float, float]]) -> list[float]:
    """Hour-to-hour sum differences, as the energy dashboard computes them."""
    sums = [recorder[start][1] for start in sorted(recorder)]
    return [round(b - a, 6) for a, b in zip(sums, sums[1:])]


def _existing_from(recorder, start):
    return {s: v[0] for s, v in recorder.items() if s >= start}


def _base_before(recorder, start) -> float:
    before = [s for s in recorder if s < start]
    return recorder[max(before)][1] if before else 0.0


def test_rebuild_continues_from_base_sum() -> None:
    rows = rebuild_cumulative_rows(10.0, {}, _hours(3, 0.5))
    assert [r[2] for r in rows] == [10.5, 11.0, 11.5]
    assert [r[1] for r in rows] == [0.5, 0.5, 0.5]


def test_overlapping_recheck_pushes_never_produce_negative_deltas() -> None:
    """Reproduce issue #9: recheck window shifts by a day, sums must not reset."""
    recorder: dict[datetime, tuple[float, float]] = {}

    # Initial backfill: 5 days
    fetched = _hours(5 * 24, 0.2)
    _apply(recorder, rebuild_cumulative_rows(0.0, {}, fetched))

    # Daily recheck pushes of the last two days, window moving forward
    for day in range(3, 8):
        start = T0 + timedelta(days=day - 1)
        fetched = _hours(2 * 24, 0.1, start)
        rows = rebuild_cumulative_rows(
            _base_before(recorder, start), _existing_from(recorder, start), fetched
        )
        _apply(recorder, rows)

    deltas = _deltas(recorder)
    assert min(deltas) > 0
    assert set(deltas) == {0.2, 0.1}


def test_old_reset_logic_would_produce_negative_delta() -> None:
    """Guard: the scenario above fails if every push restarts at zero."""
    recorder: dict[datetime, tuple[float, float]] = {}
    _apply(recorder, rebuild_cumulative_rows(0.0, {}, _hours(48, 0.2)))
    start = T0 + timedelta(days=1)
    _apply(recorder, rebuild_cumulative_rows(0.0, {}, _hours(48, 0.1, start)))
    assert min(_deltas(recorder)) < 0


def test_gap_fill_in_the_middle_shifts_later_sums() -> None:
    recorder: dict[datetime, tuple[float, float]] = {}
    day1 = _hours(24, 1.0)
    day3 = _hours(24, 1.0, T0 + timedelta(days=2))
    _apply(recorder, rebuild_cumulative_rows(0.0, {}, {**day1, **day3}))

    # Day 2 arrives later; day 3 is not re-fetched but must be re-summed.
    start = T0 + timedelta(days=1)
    day2 = _hours(24, 1.0, start)
    rows = rebuild_cumulative_rows(
        _base_before(recorder, start), _existing_from(recorder, start), day2
    )
    _apply(recorder, rows)

    assert len(recorder) == 72
    assert recorder[max(recorder)][1] == 72.0
    assert set(_deltas(recorder)) == {1.0}


def test_full_rebuild_repairs_broken_sums_from_states() -> None:
    broken = {
        T0: (1.0, 1.0),
        T0 + timedelta(hours=1): (1.0, 2.0),
        T0 + timedelta(hours=2): (1.0, 1.0),  # reset by an old push
        T0 + timedelta(hours=3): (1.0, 2.0),
    }
    existing = {start: value[0] for start, value in broken.items()}
    rows = rebuild_cumulative_rows(0.0, existing, {})
    assert [r[2] for r in rows] == [1.0, 2.0, 3.0, 4.0]


def test_fetched_values_override_existing_states() -> None:
    existing = {T0: 1.0, T0 + timedelta(hours=1): 1.0}
    rows = rebuild_cumulative_rows(0.0, existing, {T0 + timedelta(hours=1): 3.0})
    assert [(r[1], r[2]) for r in rows] == [(1.0, 1.0), (3.0, 4.0)]


def test_existing_rows_without_state_are_skipped() -> None:
    existing = {T0: 1.0, T0 + timedelta(hours=1): None}
    rows = rebuild_cumulative_rows(0.0, existing, {})
    assert [(r[0], r[2]) for r in rows] == [(T0, 1.0)]


def _point(start: datetime, minutes: int, value: float | None) -> MeasurementPoint:
    return MeasurementPoint(
        start_datetime=start,
        end_datetime=start + timedelta(minutes=minutes),
        value=value,
        unit="kWh",
        status=None,
    )


def test_hourly_states_aggregate_quarter_hours() -> None:
    points = [_point(T0 + timedelta(minutes=15 * i), 15, 0.25) for i in range(8)]
    points.append(_point(T0 + timedelta(hours=2), 15, None))
    states = hourly_states_from_points(points, MEASUREMENT_FILTER_15MIN)
    assert states == {T0: 1.0, T0 + timedelta(hours=1): 1.0}


def test_hourly_states_normalize_to_utc_hour() -> None:
    from zoneinfo import ZoneInfo

    local = datetime(2026, 11, 2, 0, 0, tzinfo=ZoneInfo("Europe/Berlin"))
    states = hourly_states_from_points([_point(local, 60, 0.4)], MEASUREMENT_FILTER_HOUR)
    assert states == {T0: 0.4}
    assert next(iter(states)).tzinfo == timezone.utc
