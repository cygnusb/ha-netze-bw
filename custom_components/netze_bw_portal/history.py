"""History persistence and recorder export for Netze BW Portal."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
from homeassistant.util import slugify

from .const import (
    CONF_ENABLE_DAILY_HISTORY,
    CONF_ENABLE_HOURLY_HISTORY,
    CONF_HISTORY_BACKFILL_DAYS,
    CONF_HOURLY_BACKFILL_RECHECK_DAYS,
    DOMAIN,
    HISTORY_DAYS,
    HISTORY_HOURLY_PRIORITY_DAYS,
    MEASUREMENT_FILTER_DAY,
    MEASUREMENT_FILTER_HOUR,
    VALUE_TYPE_CONSUMPTION,
    VALUE_TYPE_FEEDIN,
)
from .history_logic import compute_history_state
from .models import CoordinatorData, MeasurementSeries, MeterDefinition

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


class NetzeBwPortalHistoryManager:
    """Manage local history metadata and recorder exports."""

    def __init__(self, hass: HomeAssistant, entry_id: str, api: Any) -> None:
        self.hass = hass
        self.entry_id = entry_id
        self.api = api
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._data: dict[str, Any] = {"entries": {}}

    async def async_initialize(self) -> None:
        """Load persisted metadata."""
        stored = await self._store.async_load()
        if isinstance(stored, dict):
            self._data = stored

    async def async_update_histories(
        self,
        coordinator_data: CoordinatorData,
        options: dict[str, Any],
    ) -> None:
        """Fetch and persist history state for all meters."""
        entry_state = self._data.setdefault("entries", {}).setdefault(self.entry_id, {"meters": {}})
        meter_state: dict[str, Any] = entry_state.setdefault("meters", {})

        daily_enabled = bool(options.get(CONF_ENABLE_DAILY_HISTORY, True))
        hourly_enabled = bool(options.get(CONF_ENABLE_HOURLY_HISTORY, True))
        backfill_days = int(options.get(CONF_HISTORY_BACKFILL_DAYS, HISTORY_DAYS))
        hourly_recheck_days = int(
            options.get(CONF_HOURLY_BACKFILL_RECHECK_DAYS, HISTORY_HOURLY_PRIORITY_DAYS)
        )

        now = dt_util.utcnow()
        for meter_id, snapshot in coordinator_data.meters.items():
            stored_meter = meter_state.setdefault(meter_id, {})
            try:
                history_value_type = _history_value_type(snapshot.meter)

                daily_series = None
                hourly_series = None

                if daily_enabled:
                    daily_series = await self.api.async_fetch_measurement_series(
                        meter_id=meter_id,
                        value_type=history_value_type,
                        interval=MEASUREMENT_FILTER_DAY,
                        start=now - timedelta(days=backfill_days),
                        end=now,
                    )
                    await self._async_push_statistics(snapshot.meter, daily_series)

                if hourly_enabled:
                    hourly_series = await self.api.async_fetch_measurement_series(
                        meter_id=meter_id,
                        value_type=history_value_type,
                        interval=MEASUREMENT_FILTER_HOUR,
                        start=now - timedelta(days=hourly_recheck_days),
                        end=now,
                    )
                    await self._async_push_statistics(snapshot.meter, hourly_series)

                last_backfill = dt_util.utcnow()
                history_state = compute_history_state(
                    now=now,
                    daily_series=daily_series,
                    hourly_series=hourly_series,
                    daily_enabled=daily_enabled,
                    hourly_enabled=hourly_enabled,
                    backfill_days=backfill_days,
                    last_backfill=last_backfill,
                )
            except Exception as err:
                _LOGGER.warning("History backfill failed for meter %s: %s", meter_id, err)
                snapshot.history_status = "error"
                stored_meter["status"] = "error"
                continue

            snapshot.history_status = history_state.status
            snapshot.history_last_daily_point = history_state.last_daily_point
            snapshot.history_last_hourly_point = history_state.last_hourly_point
            snapshot.history_last_backfill = history_state.last_backfill
            snapshot.history_open_gaps = len(history_state.open_gaps)

            stored_meter.update(
                {
                    "status": history_state.status,
                    "last_backfill": _dt_as_iso(last_backfill),
                    "open_gaps": [
                        {
                            "interval": gap.interval,
                            "start_datetime": _dt_as_iso(gap.start_datetime),
                            "end_datetime": _dt_as_iso(gap.end_datetime),
                        }
                        for gap in history_state.open_gaps
                    ],
                    "last_daily_point": _dt_as_iso(history_state.last_daily_point),
                    "last_hourly_point": _dt_as_iso(history_state.last_hourly_point),
                }
            )

        await self._store.async_save(self._data)

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


def _history_value_type(meter: MeterDefinition) -> str:
    if VALUE_TYPE_FEEDIN in meter.value_types:
        return VALUE_TYPE_FEEDIN
    return VALUE_TYPE_CONSUMPTION


def _statistic_id(meter: MeterDefinition, interval: str) -> str:
    resolution = "daily" if interval == MEASUREMENT_FILTER_DAY else "hourly"
    return f"{DOMAIN}:{slugify(f'{meter.id}_{resolution}')}"


def _statistics_rows_from_series(series: MeasurementSeries) -> list[StatisticData]:
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


def _normalize_statistic_start(value: datetime, interval: str) -> datetime:
    """Normalize recorder timestamps to interval boundaries.

    The API already returns startDatetime at portal-local midnight (for daily)
    or at hour boundaries (for hourly), both expressed in UTC.  We only need
    to truncate sub-hour noise; we must NOT force hour=0 for daily data because
    CET midnight is 23:00 UTC (winter) or 22:00 UTC (summer).
    """
    value = value.astimezone(timezone.utc)
    return value.replace(minute=0, second=0, microsecond=0)


def _dt_as_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat()
