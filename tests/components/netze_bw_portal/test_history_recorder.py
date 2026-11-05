"""End-to-end check of issue #9 against a real HA recorder.

Needs pytest-homeassistant-custom-component; skipped without it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    statistics_during_period,
)
from homeassistant.components.recorder import get_instance
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.netze_bw_portal import history as history_mod
from custom_components.netze_bw_portal.history import (
    NetzeBwPortalHistoryManager, statistic_id, _statistic_metadata,
)
from custom_components.netze_bw_portal.models import (
    CoordinatorData, MeasurementPoint, MeasurementSeries, MeterDefinition, MeterDetails, MeterSnapshot,
)

TZ = ZoneInfo("Europe/Berlin")
HOURLY = 0.1


class FakeApi:
    def __init__(self):
        self.calls = []

    async def async_fetch_measurement_series(self, *, meter_id, value_type, interval, start, end):
        self.calls.append((interval, start, end))
        points = []
        if interval == "1DAY":
            d = start.astimezone(TZ).date()
            last = end.astimezone(TZ).date() - timedelta(days=1)
            while d <= last:
                s = datetime(d.year, d.month, d.day, tzinfo=TZ)
                points.append(MeasurementPoint(s, s + timedelta(days=1), HOURLY * 24, "kWh", None))
                d += timedelta(days=1)
        else:
            step = timedelta(hours=1) if interval == "1HOUR" else timedelta(minutes=15)
            v = HOURLY if interval == "1HOUR" else HOURLY / 4
            t = start
            while t < end:
                points.append(MeasurementPoint(t, t + step, v, "kWh", None))
                t += step
        return MeasurementSeries(meter_id, value_type, interval, points, "kWh")


METER = MeterDefinition("inst1", "Zaehler", "m1", ["CONSUMPTION"], "IMS", None, "CONSUMPTION", "Zaehler")


def _data():
    snap = MeterSnapshot(METER, MeterDetails(None, None, None, None), None, None, None, None, None, "kWh")
    return CoordinatorData(account_sub="x", meters={"inst1": snap})


async def _rows(hass, interval):
    sid = statistic_id(METER, interval)
    res = await get_instance(hass).async_add_executor_job(
        statistics_during_period, hass, datetime(2000, 1, 1, tzinfo=timezone.utc), None, {sid}, "hour", None, {"state", "sum"})
    return res.get(sid, [])


def _deltas(rows):
    sums = [r["sum"] for r in rows]
    return [round(b - a, 6) for a, b in zip(sums, sums[1:])]


OPTIONS = {"enable_daily_history": True, "enable_hourly_history": True, "enable_15min_history": True, "history_backfill_days": 10}


async def test_upgrade_repairs_and_keeps_sums_continuous(recorder_mock, hass, freezer):
    api = FakeApi()
    with patch.object(history_mod, "HOURLY_FETCH_DELAY_SECONDS", 0):
        # --- simulate old (0.6.3) behaviour: sums restart at 0 per push ---
        sid_meta = _statistic_metadata(METER, "1HOUR", "kWh")
        def old_push(start, hours):
            rows, s = [], 0.0
            for i in range(hours):
                s += HOURLY
                rows.append({"start": start + timedelta(hours=i), "state": HOURLY, "sum": s})
            async_add_external_statistics(hass, sid_meta, rows)
        base = datetime(2026, 10, 20, 23, 0, tzinfo=timezone.utc)  # 21.10. 00:00 local
        old_push(base, 10 * 24)
        old_push(base + timedelta(days=8), 2 * 24)
        old_push(base + timedelta(days=9), 2 * 24)
        await async_wait_recording_done(hass)
        assert min(_deltas(await _rows(hass, "1HOUR"))) < 0  # broken like issue #9

        # --- first run after upgrade: everything fetched already except recheck ---
        freezer.move_to("2026-11-01 12:00:00+00:00")
        mgr = NetzeBwPortalHistoryManager(hass, "entry", api)
        await mgr.async_initialize()
        dates = [(datetime(2026, 10, 21) + timedelta(days=i)).date().isoformat() for i in range(11)]
        mgr._data["entries"]["entry"] = {"meters": {"inst1": {
            "daily_fetched_dates": dates, "hourly_fetched_dates": dates, "fifteenmin_fetched_dates": []}}}
        await mgr.async_update_histories(_data(), OPTIONS)
        await async_wait_recording_done(hass)

        hourly = await _rows(hass, "1HOUR")
        d = _deltas(hourly)
        assert min(d) > 0, d
        assert hourly[-1]["sum"] == pytest.approx(HOURLY * len(hourly))
        assert mgr._data["entries"]["entry"]["meters"]["inst1"]["statistic_sums_rebuilt"] is True
        for interval in ("1DAY", "15MIN"):
            rows = await _rows(hass, interval)
            assert rows and min(_deltas(rows)) > 0, interval
        n_before = len(hourly)
        first_rows = {r["start"]: r["sum"] for r in hourly}

        # --- following days: recheck window moves, sums must stay continuous ---
        for day in range(2, 6):
            freezer.move_to(f"2026-11-0{day} 12:00:00+00:00")
            await mgr.async_update_histories(_data(), OPTIONS)
            await async_wait_recording_done(hass)
        for interval in ("1HOUR", "1DAY", "15MIN"):
            rows = await _rows(hass, interval)
            d = _deltas(rows)
            assert min(d) > 0, (interval, [r for r, x in zip(rows[1:], d) if x <= 0][:3])
            per = HOURLY * (24 if interval == "1DAY" else 1)
            assert rows[-1]["sum"] == pytest.approx(per * len(rows)), interval
        hourly = await _rows(hass, "1HOUR")
        assert len(hourly) == n_before + 4 * 24
        # rows before the moving window are untouched by incremental pushes
        unchanged = [r for r in hourly if r["start"] in first_rows and r["sum"] == pytest.approx(first_rows[r["start"]])]
        assert len(unchanged) == n_before
