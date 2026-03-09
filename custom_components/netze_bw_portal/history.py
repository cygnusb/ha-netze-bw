"""History persistence and recorder export for Netze BW Portal."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timedelta, timezone
import logging
from typing import Any
from zoneinfo import ZoneInfo

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
from homeassistant.util import slugify

from .const import (
    CONF_ENABLE_15MIN_HISTORY,
    CONF_ENABLE_DAILY_HISTORY,
    CONF_ENABLE_HOURLY_HISTORY,
    CONF_HISTORY_BACKFILL_DAYS,
    DOMAIN,
    HISTORY_DAYS,
    HISTORY_RECHECK_DAYS,
    HOURLY_FETCH_DELAY_SECONDS,
    MEASUREMENT_FILTER_15MIN,
    MEASUREMENT_FILTER_DAY,
    MEASUREMENT_FILTER_HOUR,
    PORTAL_TIMEZONE,
    VALUE_TYPE_CONSUMPTION,
    VALUE_TYPE_FEEDIN,
)
from .history_logic import (
    compute_history_state,
    expected_daily_dates,
    expected_hourly_dates,
    missing_dates,
    prune_dates,
)
from .models import (
    CoordinatorData,
    MeasurementPoint,
    MeasurementSeries,
    MeterDefinition,
)

try:
    from homeassistant.components.recorder.statistics import (
        StatisticData,
        StatisticMetaData,
        async_add_external_statistics,
    )
except ImportError:
    from homeassistant.components.recorder.models import StatisticData, StatisticMetaData
    from homeassistant.components.recorder.statistics import async_add_external_statistics

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
STORAGE_KEY = f"{DOMAIN}_history"

PORTAL_TZ = ZoneInfo(PORTAL_TIMEZONE)


class NetzeBwPortalHistoryManager:
    """Manage local history metadata and recorder exports."""

    def __init__(self, hass: HomeAssistant, entry_id: str, api: Any) -> None:
        self.hass = hass
        self.entry_id = entry_id
        self.api = api
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._data: dict[str, Any] = {"entries": {}}

    async def async_initialize(self) -> None:
        """Load persisted metadata, migrating from v1 if needed."""
        stored = await self._store.async_load()
        if isinstance(stored, dict):
            self._data = stored
        # Ensure structure exists
        self._data.setdefault("entries", {})

    # ------------------------------------------------------------------
    # Public update entry point
    # ------------------------------------------------------------------

    async def async_update_histories(
        self,
        coordinator_data: CoordinatorData,
        options: dict[str, Any],
    ) -> None:
        """Fetch and persist history state for all meters."""
        entry_state = self._data.setdefault("entries", {}).setdefault(
            self.entry_id, {"meters": {}}
        )
        meter_state: dict[str, Any] = entry_state.setdefault("meters", {})

        daily_enabled = bool(options.get(CONF_ENABLE_DAILY_HISTORY, True))
        hourly_enabled = bool(options.get(CONF_ENABLE_HOURLY_HISTORY, True))
        fifteenmin_enabled = bool(options.get(CONF_ENABLE_15MIN_HISTORY, False))
        backfill_days = int(options.get(CONF_HISTORY_BACKFILL_DAYS, HISTORY_DAYS))

        now = dt_util.utcnow()
        today = now.astimezone(PORTAL_TZ).date()

        for meter_id, snapshot in coordinator_data.meters.items():
            stored_meter = meter_state.setdefault(meter_id, {})
            try:
                history_value_type = _history_value_type(snapshot.meter)

                # ---- load fetched-date sets from store ----
                daily_fetched = _load_date_set(stored_meter, "daily_fetched_dates")
                hourly_fetched = _load_date_set(stored_meter, "hourly_fetched_dates")
                fifteenmin_fetched = _load_date_set(stored_meter, "fifteenmin_fetched_dates")

                # Prune dates older than the backfill window
                daily_fetched = prune_dates(daily_fetched, backfill_days, today)
                hourly_fetched = prune_dates(hourly_fetched, backfill_days, today)
                fifteenmin_fetched = prune_dates(fifteenmin_fetched, backfill_days, today)

                daily_series: MeasurementSeries | None = None
                hourly_series: MeasurementSeries | None = None

                # ---- Daily ----
                if daily_enabled:
                    exp_daily = expected_daily_dates(now, backfill_days)
                    daily_todo = missing_dates(exp_daily, daily_fetched)

                    if daily_todo:
                        daily_series = await self._async_fetch_daily(
                            meter_id, history_value_type, backfill_days, now
                        )
                        if daily_series and daily_series.points:
                            # Mark all dates in the expected window as fetched
                            daily_fetched |= exp_daily
                            await self._async_push_statistics(
                                snapshot.meter, daily_series
                            )
                            # Populate latest value
                            latest = _latest_point(daily_series)
                            if latest is not None:
                                snapshot.latest_daily_value = latest.value

                # ---- Hourly ----
                if hourly_enabled:
                    exp_hourly = expected_hourly_dates(now, backfill_days)
                    hourly_todo = missing_dates(exp_hourly, hourly_fetched)

                    if hourly_todo:
                        all_points: list[MeasurementPoint] = []
                        unit: str | None = None

                        for i, target_date in enumerate(sorted(hourly_todo)):
                            if i > 0:
                                await asyncio.sleep(HOURLY_FETCH_DELAY_SECONDS)

                            day_series = await self._async_fetch_hourly_for_date(
                                meter_id, history_value_type, target_date
                            )
                            if day_series and day_series.points:
                                all_points.extend(day_series.points)
                                if unit is None:
                                    unit = day_series.unit
                                hourly_fetched.add(target_date)

                        if all_points:
                            all_points.sort(key=lambda p: p.start_datetime)
                            hourly_series = MeasurementSeries(
                                meter_id=meter_id,
                                value_type=history_value_type,
                                interval=MEASUREMENT_FILTER_HOUR,
                                points=all_points,
                                unit=unit,
                            )
                            await self._async_push_statistics(
                                snapshot.meter, hourly_series
                            )
                            latest = _latest_point(hourly_series)
                            if latest is not None:
                                snapshot.latest_hourly_value = latest.value

                # ---- 15-minute ----
                fifteenmin_series: MeasurementSeries | None = None
                if fifteenmin_enabled:
                    exp_15min = expected_hourly_dates(now, backfill_days)
                    fifteenmin_todo = missing_dates(exp_15min, fifteenmin_fetched)

                    if fifteenmin_todo:
                        all_15min_points: list[MeasurementPoint] = []
                        unit_15min: str | None = None

                        for i, target_date in enumerate(sorted(fifteenmin_todo)):
                            if i > 0:
                                await asyncio.sleep(HOURLY_FETCH_DELAY_SECONDS)

                            day_series = await self._async_fetch_15min_for_date(
                                meter_id, history_value_type, target_date
                            )
                            if day_series and day_series.points:
                                all_15min_points.extend(day_series.points)
                                if unit_15min is None:
                                    unit_15min = day_series.unit
                                fifteenmin_fetched.add(target_date)

                        if all_15min_points:
                            all_15min_points.sort(key=lambda p: p.start_datetime)
                            fifteenmin_series = MeasurementSeries(
                                meter_id=meter_id,
                                value_type=history_value_type,
                                interval=MEASUREMENT_FILTER_15MIN,
                                points=all_15min_points,
                                unit=unit_15min,
                            )
                            await self._async_push_statistics(
                                snapshot.meter, fifteenmin_series
                            )
                            latest_15min = _latest_point(fifteenmin_series)
                            if latest_15min is not None:
                                snapshot.latest_15min_value = latest_15min.value

                # ---- Persist fetched-date sets ----
                stored_meter["daily_fetched_dates"] = _save_date_set(daily_fetched)
                stored_meter["hourly_fetched_dates"] = _save_date_set(hourly_fetched)
                stored_meter["fifteenmin_fetched_dates"] = _save_date_set(fifteenmin_fetched)

                last_backfill = dt_util.utcnow()
                stored_meter["last_backfill"] = _dt_as_iso(last_backfill)

                # ---- Compute last points for diagnostics ----
                last_daily_point = _last_point_dt(daily_series)
                last_hourly_point = _last_point_dt(hourly_series)
                last_15min_point = _last_point_dt(fifteenmin_series)

                history_state = compute_history_state(
                    now=now,
                    daily_fetched_dates=daily_fetched,
                    hourly_fetched_dates=hourly_fetched,
                    daily_enabled=daily_enabled,
                    hourly_enabled=hourly_enabled,
                    backfill_days=backfill_days,
                    last_backfill=last_backfill,
                    last_daily_point=last_daily_point,
                    last_hourly_point=last_hourly_point,
                    fifteenmin_fetched_dates=fifteenmin_fetched,
                    fifteenmin_enabled=fifteenmin_enabled,
                    last_15min_point=last_15min_point,
                )
            except Exception as err:
                _LOGGER.warning(
                    "History backfill failed for meter %s: %s", meter_id, err
                )
                snapshot.history_status = "error"
                stored_meter["status"] = "error"
                continue

            snapshot.history_status = history_state.status
            snapshot.history_last_daily_point = history_state.last_daily_point
            snapshot.history_last_hourly_point = history_state.last_hourly_point
            snapshot.history_last_15min_point = history_state.last_15min_point
            snapshot.history_last_backfill = history_state.last_backfill
            snapshot.history_open_gaps = len(history_state.open_gaps)

            stored_meter["status"] = history_state.status

            _LOGGER.debug(
                "Meter %s history: status=%s, daily_fetched=%d, hourly_fetched=%d, gaps=%d",
                meter_id,
                history_state.status,
                len(daily_fetched),
                len(hourly_fetched),
                len(history_state.open_gaps),
            )

        await self._store.async_save(self._data)

    # ------------------------------------------------------------------
    # Fetching helpers
    # ------------------------------------------------------------------

    async def _async_fetch_daily(
        self,
        meter_id: str,
        value_type: str,
        backfill_days: int,
        now: datetime,
    ) -> MeasurementSeries | None:
        """Fetch daily data for the entire backfill window (single API call)."""
        start = now - timedelta(days=backfill_days)
        return await self.api.async_fetch_measurement_series(
            meter_id=meter_id,
            value_type=value_type,
            interval=MEASUREMENT_FILTER_DAY,
            start=start,
            end=now,
        )

    async def _async_fetch_hourly_for_date(
        self,
        meter_id: str,
        value_type: str,
        target_date: date,
    ) -> MeasurementSeries | None:
        """Fetch hourly data for a single CET calendar day."""
        start = datetime.combine(target_date, time.min, tzinfo=PORTAL_TZ)
        end = start + timedelta(days=1)
        return await self.api.async_fetch_measurement_series(
            meter_id=meter_id,
            value_type=value_type,
            interval=MEASUREMENT_FILTER_HOUR,
            start=start.astimezone(timezone.utc),
            end=end.astimezone(timezone.utc),
        )

    async def _async_fetch_15min_for_date(
        self,
        meter_id: str,
        value_type: str,
        target_date: date,
    ) -> MeasurementSeries | None:
        """Fetch 15-minute data for a single CET calendar day."""
        start = datetime.combine(target_date, time.min, tzinfo=PORTAL_TZ)
        end = start + timedelta(days=1)
        return await self.api.async_fetch_measurement_series(
            meter_id=meter_id,
            value_type=value_type,
            interval=MEASUREMENT_FILTER_15MIN,
            start=start.astimezone(timezone.utc),
            end=end.astimezone(timezone.utc),
        )

    # ------------------------------------------------------------------
    # Statistics push
    # ------------------------------------------------------------------

    async def _async_push_statistics(
        self,
        meter: MeterDefinition,
        series: MeasurementSeries,
    ) -> None:
        """Export imported intervals to recorder statistics."""
        rows = _statistics_rows_from_series(series)
        if not rows:
            return

        metadata = StatisticMetaData(
            has_mean=False,
            has_sum=True,
            mean_type=0,
            name=f"{meter.friendly_name} {series.interval.lower()}",
            source=DOMAIN,
            statistic_id=_statistic_id(meter, series.interval),
            unit_of_measurement=series.unit or "kWh",
            unit_class="energy",
        )
        async_add_external_statistics(self.hass, metadata, rows)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _history_value_type(meter: MeterDefinition) -> str:
    if VALUE_TYPE_FEEDIN in meter.value_types:
        return VALUE_TYPE_FEEDIN
    return VALUE_TYPE_CONSUMPTION


def _statistic_id(meter: MeterDefinition, interval: str) -> str:
    if interval == MEASUREMENT_FILTER_DAY:
        resolution = "daily"
    elif interval == MEASUREMENT_FILTER_HOUR:
        resolution = "hourly"
    else:
        resolution = slugify(interval.lower())
    return f"{DOMAIN}:{slugify(f'{meter.id}_{resolution}')}"


def _statistics_rows_from_series(series: MeasurementSeries) -> list[StatisticData]:
    # HA external statistics only support hourly boundaries.
    # For sub-hourly intervals (15MIN), aggregate into hourly buckets first.
    if series.interval == MEASUREMENT_FILTER_15MIN:
        return _statistics_rows_aggregated_hourly(series)

    running_sum = 0.0
    rows: list[StatisticData] = []
    for point in series.points:
        if point.value is None:
            continue
        running_sum += point.value
        start = _normalize_statistic_start(point.start_datetime, series.interval)
        rows.append(
            StatisticData(
                start=start,
                state=point.value,
                sum=running_sum,
            )
        )
    return rows


def _statistics_rows_aggregated_hourly(series: MeasurementSeries) -> list[StatisticData]:
    """Aggregate sub-hourly points into hourly buckets for recorder compatibility."""
    buckets: dict[datetime, float] = {}
    for point in series.points:
        if point.value is None:
            continue
        hour = point.start_datetime.astimezone(timezone.utc).replace(
            minute=0, second=0, microsecond=0
        )
        buckets[hour] = buckets.get(hour, 0.0) + point.value

    running_sum = 0.0
    rows: list[StatisticData] = []
    for hour in sorted(buckets):
        value = buckets[hour]
        running_sum += value
        rows.append(StatisticData(start=hour, state=value, sum=running_sum))
    return rows


def _normalize_statistic_start(value: datetime, interval: str) -> datetime:
    """Normalize recorder timestamps to interval boundaries."""
    value = value.astimezone(timezone.utc)
    return value.replace(minute=0, second=0, microsecond=0)


def _latest_point(series: MeasurementSeries | None) -> MeasurementPoint | None:
    """Return the latest non-None point in a series."""
    if series is None:
        return None
    valid = [p for p in series.points if p.value is not None]
    if not valid:
        return None
    return max(valid, key=lambda p: p.start_datetime)


def _last_point_dt(series: MeasurementSeries | None) -> datetime | None:
    """Return the end_datetime of the latest point."""
    p = _latest_point(series)
    if p is None:
        return None
    return p.end_datetime


def _load_date_set(stored: dict[str, Any], key: str) -> set[date]:
    """Load a set of dates from stored ISO strings."""
    raw = stored.get(key, [])
    if not isinstance(raw, list):
        return set()
    result: set[date] = set()
    for item in raw:
        try:
            result.add(date.fromisoformat(item))
        except (ValueError, TypeError):
            pass
    return result


def _save_date_set(dates: set[date]) -> list[str]:
    """Serialize a set of dates to sorted ISO strings."""
    return sorted(d.isoformat() for d in dates)


def _dt_as_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat()
