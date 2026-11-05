"""End-to-end check of issue #9 against a real HA recorder.

Needs pytest-homeassistant-custom-component; skipped without it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    statistics_during_period,
)
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.netze_bw_portal import history as history_mod
from custom_components.netze_bw_portal.history import (
    NetzeBwPortalHistoryManager,
    _statistic_metadata,
    statistic_id,
)
from custom_components.netze_bw_portal.models import (
    CoordinatorData,
    MeasurementPoint,
    MeasurementSeries,
    MeterDefinition,
    MeterDetails,
    MeterSnapshot,
)

TZ = ZoneInfo("Europe/Berlin")
HOURLY = 0.1
DAILY = HOURLY * 24
EPOCH = datetime(2000, 1, 1, tzinfo=timezone.utc)


def _local_midnight(value: datetime) -> datetime:
    local = value.astimezone(TZ)
    return datetime(local.year, local.month, local.day, tzinfo=TZ)


class FakeApi:
    """Behaves like the portal, including a partial first day."""

    async def async_fetch_measurement_series(
        self, *, meter_id, value_type, interval, start, end
    ):
        points = []
        if interval == "1DAY":
            day = _local_midnight(start)
            if day != start:
                # The portal starts the first bucket at the requested time.
                next_day = day + timedelta(days=1)
                points.append(MeasurementPoint(start, next_day, 0.5, "kWh", None))
                day = next_day
            while day + timedelta(days=1) <= end:
                points.append(
                    MeasurementPoint(day, day + timedelta(days=1), DAILY, "kWh", None)
                )
                day += timedelta(days=1)
        else:
            step = timedelta(hours=1) if interval == "1HOUR" else timedelta(minutes=15)
            value = HOURLY if interval == "1HOUR" else HOURLY / 4
            t = start
            while t < end:
                points.append(MeasurementPoint(t, t + step, value, "kWh", None))
                t += step
        return MeasurementSeries(meter_id, value_type, interval, points, "kWh")


METER = MeterDefinition(
    "inst1", "Zaehler", "m1", ["CONSUMPTION"], "IMS", None, "CONSUMPTION", "Zaehler"
)
OPTIONS = {
    "enable_daily_history": True,
    "enable_hourly_history": True,
    "enable_15min_history": True,
    "history_backfill_days": 10,
}


def _data() -> CoordinatorData:
    snapshot = MeterSnapshot(
        METER, MeterDetails(None, None, None, None), None, None, None, None, None, "kWh"
    )
    return CoordinatorData(account_sub="x", meters={"inst1": snapshot})


async def _rows(hass, interval, period="hour", types=frozenset({"state", "sum"})):
    sid = statistic_id(METER, interval)
    result = await get_instance(hass).async_add_executor_job(
        statistics_during_period, hass, EPOCH, None, {sid}, period, None, set(types)
    )
    return result.get(sid, [])


def _deltas(rows):
    sums = [row["sum"] for row in rows]
    return [round(b - a, 6) for a, b in zip(sums, sums[1:])]


def _old_push(hass, interval, rows):
    """Write rows the way 0.6.3 did: the sum restarts at 0 per push."""
    total, data = 0.0, []
    for start, state in rows:
        total += state
        data.append({"start": start, "state": state, "sum": total})
    async_add_external_statistics(hass, _statistic_metadata(METER, interval, "kWh"), data)


async def _assert_daily_changes(hass):
    """HA's own day aggregation must show exactly one day's value per day."""
    rows = await _rows(hass, "1DAY")
    for row in rows:
        start = datetime.fromtimestamp(row["start"], timezone.utc)
        if start.astimezone(TZ).hour != 0:
            assert row["state"] == 0.0, start
    changes = await _rows(hass, "1DAY", period="day", types={"change"})
    values = [round(row["change"], 6) for row in changes]
    assert values and set(values[1:]) == {round(DAILY, 6)}, values


async def test_upgrade_repairs_and_keeps_sums_continuous(recorder_mock, hass, freezer):
    await hass.config.async_set_time_zone("Europe/Berlin")
    with patch.object(history_mod, "HOURLY_FETCH_DELAY_SECONDS", 0):
        # --- data as written by 0.6.3 ---
        base = datetime(2026, 10, 20, 22, 0, tzinfo=timezone.utc)  # 21.10. local
        hours = [(base + timedelta(hours=i), HOURLY) for i in range(10 * 24 + 2)]
        _old_push(hass, "1HOUR", hours)
        _old_push(hass, "1HOUR", hours[8 * 24 :])
        _old_push(hass, "1HOUR", hours[9 * 24 :])
        days = [(_local_midnight(base + timedelta(days=i, hours=2)), DAILY) for i in range(10)]
        _old_push(hass, "1DAY", days)
        for offset in (6, 12, 18):  # partial first days of earlier pushes
            _old_push(hass, "1DAY", [(base + timedelta(days=1, hours=offset), 0.5)] + days[1:])
        await async_wait_recording_done(hass)
        assert min(_deltas(await _rows(hass, "1HOUR"))) < 0

        # --- first run after the upgrade, history already fetched ---
        freezer.move_to("2026-11-01 12:00:00+00:00")
        manager = NetzeBwPortalHistoryManager(hass, "entry", FakeApi())
        await manager.async_initialize()
        dates = [
            (datetime(2026, 10, 21) + timedelta(days=i)).date().isoformat() for i in range(11)
        ]
        manager._data["entries"]["entry"] = {
            "meters": {
                "inst1": {
                    "daily_fetched_dates": dates,
                    "hourly_fetched_dates": dates,
                    "fifteenmin_fetched_dates": [],
                }
            }
        }
        await manager.async_update_histories(_data(), OPTIONS)
        await async_wait_recording_done(hass)

        stored = manager._data["entries"]["entry"]["meters"]["inst1"]
        assert stored["statistic_sums_rebuilt_v2"] is True
        hourly = await _rows(hass, "1HOUR")
        assert min(_deltas(hourly)) > 0
        assert hourly[-1]["sum"] == pytest.approx(HOURLY * len(hourly))
        assert min(_deltas(await _rows(hass, "15MIN"))) > 0
        assert min(_deltas(await _rows(hass, "1DAY"))) >= 0
        await _assert_daily_changes(hass)
        first_sums = {row["start"]: row["sum"] for row in hourly}

        # --- following days: windows move, polls at odd hours ---
        for day in range(2, 6):
            for hour in ("06:07", "12:07"):
                freezer.move_to(f"2026-11-0{day} {hour}:00+00:00")
                await manager.async_update_histories(_data(), OPTIONS)
                await async_wait_recording_done(hass)

        for interval in ("1HOUR", "15MIN"):
            rows = await _rows(hass, interval)
            assert min(_deltas(rows)) > 0, interval
            assert rows[-1]["sum"] == pytest.approx(HOURLY * len(rows)), interval
        assert min(_deltas(await _rows(hass, "1DAY"))) >= 0
        await _assert_daily_changes(hass)

        hourly = await _rows(hass, "1HOUR")
        assert len(hourly) == len(first_sums) + 4 * 24
        unchanged = [
            row for row in hourly
            if row["start"] in first_sums and row["sum"] == pytest.approx(first_sums[row["start"]])
        ]
        assert len(unchanged) == len(first_sums)
