"""History persistence and recorder export for Netze BW Portal."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timedelta, timezone
from functools import partial
import logging
from typing import Any
from zoneinfo import ZoneInfo

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import (
    get_metadata,
    statistics_during_period,
)
from homeassistant.const import MAJOR_VERSION, MINOR_VERSION
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
from homeassistant.util import slugify

from .api import NetzeBwPortalApiClient, NetzeBwPortalError
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
)
from .history_logic import (
    compute_history_state,
    expected_daily_dates,
    expected_hourly_dates,
    hourly_states_from_points,
    missing_dates,
    prune_dates,
    rebuild_cumulative_rows,
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

# Set per meter once all its statistics have had their cumulative sums
# recomputed from the stored hourly states (repairs issue #9 data).
SUMS_REBUILT_KEY = "statistic_sums_rebuilt"
# How far back to look for the last recorder row before a push.
STATISTICS_BASE_LOOKBACK = timedelta(days=7)
STATISTICS_EPOCH = datetime(2000, 1, 1, tzinfo=timezone.utc)
HA_VERSION = (MAJOR_VERSION, MINOR_VERSION)
HISTORY_INTERVALS = (MEASUREMENT_FILTER_DAY, MEASUREMENT_FILTER_HOUR, MEASUREMENT_FILTER_15MIN)


class NetzeBwPortalHistoryManager:
    """Manage local history metadata and recorder exports."""

    def __init__(self, hass: HomeAssistant, entry_id: str, api: NetzeBwPortalApiClient) -> None:
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
            rebuild_sums = not stored_meter.get(SUMS_REBUILT_KEY)
            pushed_intervals: set[str] = set()
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

                _LOGGER.debug(
                    "Meter %s: value_type=%s daily_enabled=%s hourly_enabled=%s "
                    "fifteenmin_enabled=%s backfill_days=%d "
                    "daily_fetched=%d hourly_fetched=%d fifteenmin_fetched=%d",
                    meter_id, history_value_type,
                    daily_enabled, hourly_enabled, fifteenmin_enabled, backfill_days,
                    len(daily_fetched), len(hourly_fetched), len(fifteenmin_fetched),
                )

                daily_series: MeasurementSeries | None = None
                hourly_series: MeasurementSeries | None = None

                # ---- Daily ----
                if daily_enabled:
                    exp_daily = expected_daily_dates(now, backfill_days)
                    daily_todo = missing_dates(exp_daily, daily_fetched)
                    _LOGGER.debug("Meter %s: daily todo=%d dates", meter_id, len(daily_todo))

                    if daily_todo:
                        daily_series = await self._async_fetch_daily(
                            snapshot.meter.id, history_value_type, backfill_days, now
                        )
                        if daily_series and daily_series.points:
                            # Mark all dates in the expected window as fetched
                            daily_fetched |= exp_daily
                            _LOGGER.debug(
                                "Meter %s: pushing %d daily stats rows",
                                meter_id, len(daily_series.points),
                            )
                            await self._async_push_statistics(
                                snapshot.meter, daily_series, full_rebuild=rebuild_sums
                            )
                            pushed_intervals.add(MEASUREMENT_FILTER_DAY)
                            # Populate latest value
                            latest = _latest_point(daily_series)
                            if latest is not None:
                                snapshot.latest_daily_value = latest.value

                # ---- Hourly ----
                if hourly_enabled:
                    exp_hourly = expected_hourly_dates(now, backfill_days)
                    hourly_todo = missing_dates(exp_hourly, hourly_fetched)
                    _LOGGER.debug("Meter %s: hourly todo=%d dates", meter_id, len(hourly_todo))

                    if hourly_todo:
                        hourly_series = await self._async_fetch_per_date_history(
                            snapshot.meter.id, history_value_type, hourly_todo,
                            MEASUREMENT_FILTER_HOUR, hourly_fetched,
                        )
                        if hourly_series:
                            _LOGGER.debug(
                                "Meter %s: pushing %d hourly stats rows",
                                meter_id, len(hourly_series.points),
                            )
                            await self._async_push_statistics(
                                snapshot.meter, hourly_series, full_rebuild=rebuild_sums
                            )
                            pushed_intervals.add(MEASUREMENT_FILTER_HOUR)
                            latest = _latest_point(hourly_series)
                            if latest is not None:
                                snapshot.latest_hourly_value = latest.value

                # ---- 15-minute ----
                fifteenmin_series: MeasurementSeries | None = None
                if fifteenmin_enabled:
                    exp_15min = expected_hourly_dates(now, backfill_days)
                    fifteenmin_todo = missing_dates(exp_15min, fifteenmin_fetched)
                    _LOGGER.debug("Meter %s: 15min todo=%d dates", meter_id, len(fifteenmin_todo))

                    if fifteenmin_todo:
                        fifteenmin_series = await self._async_fetch_per_date_history(
                            snapshot.meter.id, history_value_type, fifteenmin_todo,
                            MEASUREMENT_FILTER_15MIN, fifteenmin_fetched,
                        )
                        if fifteenmin_series:
                            _LOGGER.debug(
                                "Meter %s: pushing %d 15min stats rows",
                                meter_id, len(fifteenmin_series.points),
                            )
                            await self._async_push_statistics(
                                snapshot.meter, fifteenmin_series, full_rebuild=rebuild_sums
                            )
                            pushed_intervals.add(MEASUREMENT_FILTER_15MIN)
                            latest_15min = _latest_point(fifteenmin_series)
                            if latest_15min is not None:
                                snapshot.latest_15min_value = latest_15min.value

                # ---- One-time repair of sums written before issue #9 ----
                if rebuild_sums:
                    for interval in HISTORY_INTERVALS:
                        if interval not in pushed_intervals:
                            await self._async_rebuild_existing_statistic(
                                snapshot.meter, interval
                            )
                    stored_meter[SUMS_REBUILT_KEY] = True
                    _LOGGER.info(
                        "Meter %s: recomputed cumulative statistic sums", meter_id
                    )

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
            except NetzeBwPortalError as err:
                _LOGGER.error(
                    "History backfill failed for meter %s (API error): %s", meter_id, err
                )
                snapshot.history_status = "error"
                stored_meter["status"] = "error"
                continue
            except Exception:
                _LOGGER.exception(
                    "History backfill failed for meter %s (unexpected error)", meter_id
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

    async def _async_fetch_for_date(
        self,
        meter_id: str,
        value_type: str,
        target_date: date,
        interval: str,
    ) -> MeasurementSeries | None:
        """Fetch granular (hourly or 15-min) data for a single CET calendar day."""
        start = datetime.combine(target_date, time.min, tzinfo=PORTAL_TZ)
        end = start + timedelta(days=1)
        return await self.api.async_fetch_measurement_series(
            meter_id=meter_id,
            value_type=value_type,
            interval=interval,
            start=start.astimezone(timezone.utc),
            end=end.astimezone(timezone.utc),
        )

    async def _async_fetch_per_date_history(
        self,
        meter_id: str,
        value_type: str,
        todo_dates: set[date],
        interval: str,
        fetched_dates: set[date],
    ) -> MeasurementSeries | None:
        """Fetch per-date granular history, update fetched_dates in-place, return combined series."""
        all_points: list[MeasurementPoint] = []
        unit: str | None = None

        for i, target_date in enumerate(sorted(todo_dates)):
            if i > 0:
                await asyncio.sleep(HOURLY_FETCH_DELAY_SECONDS)
            _LOGGER.debug(
                "Meter %s: fetching %s for %s (%d/%d)",
                meter_id, interval, target_date, i + 1, len(todo_dates),
            )
            day_series = await self._async_fetch_for_date(meter_id, value_type, target_date, interval)
            if day_series and day_series.points:
                all_points.extend(day_series.points)
                if unit is None:
                    unit = day_series.unit
                fetched_dates.add(target_date)
                _LOGGER.debug(
                    "Meter %s: %s %s → %d points",
                    meter_id, interval, target_date, len(day_series.points),
                )
            else:
                _LOGGER.debug("Meter %s: %s %s → no data", meter_id, interval, target_date)

        if not all_points:
            return None

        all_points.sort(key=lambda p: p.start_datetime)
        return MeasurementSeries(
            meter_id=meter_id,
            value_type=value_type,
            interval=interval,
            points=all_points,
            unit=unit,
        )

    async def _async_fetch_hourly_for_date(
        self,
        meter_id: str,
        value_type: str,
        target_date: date,
    ) -> MeasurementSeries | None:
        """Fetch hourly data for a single CET calendar day."""
        return await self._async_fetch_for_date(meter_id, value_type, target_date, MEASUREMENT_FILTER_HOUR)

    async def _async_fetch_15min_for_date(
        self,
        meter_id: str,
        value_type: str,
        target_date: date,
    ) -> MeasurementSeries | None:
        """Fetch 15-minute data for a single CET calendar day."""
        return await self._async_fetch_for_date(meter_id, value_type, target_date, MEASUREMENT_FILTER_15MIN)

    # ------------------------------------------------------------------
    # Statistics push
    # ------------------------------------------------------------------

    async def _async_push_statistics(
        self,
        meter: MeterDefinition,
        series: MeasurementSeries,
        *,
        full_rebuild: bool = False,
    ) -> None:
        """Export imported intervals to recorder statistics."""
        fetched = hourly_states_from_points(series.points, series.interval)
        if not fetched:
            return
        metadata = _statistic_metadata(meter, series.interval, series.unit or "kWh")
        await self._async_write_statistic(metadata, fetched, full_rebuild=full_rebuild)

    async def _async_rebuild_existing_statistic(
        self, meter: MeterDefinition, interval: str
    ) -> None:
        """Recompute the sums of an already imported statistic from its states."""
        sid = statistic_id(meter, interval)
        existing = await get_instance(self.hass).async_add_executor_job(
            partial(get_metadata, self.hass, statistic_ids={sid})
        )
        if sid not in existing:
            return
        unit = existing[sid][1].get("unit_of_measurement") or "kWh"
        metadata = _statistic_metadata(meter, interval, unit)
        await self._async_write_statistic(metadata, {}, full_rebuild=True)

    async def _async_write_statistic(
        self,
        metadata: StatisticMetaData,
        fetched: dict[datetime, float],
        *,
        full_rebuild: bool,
    ) -> None:
        """Merge fetched states into the recorder with continuous sums.

        The sum continues from the last recorder row before the first
        fetched hour, and every later row is re-summed, so a re-fetched or
        gap-filled day never resets the sum (issue #9). With *full_rebuild*
        the whole statistic is recomputed from its stored states.
        """
        sid = metadata["statistic_id"]
        if full_rebuild or not fetched:
            base_sum = 0.0
            from_time = STATISTICS_EPOCH
        else:
            from_time = min(fetched)
            base_sum = await self._async_last_sum_before(sid, from_time)

        existing = await self._async_read_statistic(sid, from_time, None)
        rows = rebuild_cumulative_rows(
            base_sum, {start: state for start, state, _ in existing}, fetched
        )
        if not rows:
            return
        _LOGGER.debug(
            "%s: writing %d rows from %s (base sum %.3f)",
            sid, len(rows), rows[0][0].isoformat(), base_sum,
        )
        async_add_external_statistics(
            self.hass,
            metadata,
            [StatisticData(start=start, state=state, sum=total) for start, state, total in rows],
        )

    async def _async_last_sum_before(self, sid: str, before: datetime) -> float:
        """Return the sum of the last recorder row starting before *before*."""
        rows = await self._async_read_statistic(sid, before - STATISTICS_BASE_LOOKBACK, before)
        if not rows:
            rows = await self._async_read_statistic(sid, STATISTICS_EPOCH, before)
        if not rows:
            return 0.0
        return rows[-1][2] or 0.0

    async def _async_read_statistic(
        self, sid: str, start: datetime, end: datetime | None
    ) -> list[tuple[datetime, float | None, float | None]]:
        """Read ``(start, state, sum)`` hourly rows of one statistic, oldest first."""
        result = await get_instance(self.hass).async_add_executor_job(
            statistics_during_period,
            self.hass,
            start,
            end,
            {sid},
            "hour",
            None,
            {"state", "sum"},
        )
        return [
            (_row_start(row["start"]), row.get("state"), row.get("sum"))
            for row in result.get(sid, [])
        ]


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _history_value_type(meter: MeterDefinition) -> str:
    return meter.direction


def statistic_id(meter: MeterDefinition, interval: str) -> str:
    """Build the external statistic id for a logical meter and interval."""
    if interval == MEASUREMENT_FILTER_DAY:
        resolution = "daily"
    elif interval == MEASUREMENT_FILTER_HOUR:
        resolution = "hourly"
    else:
        resolution = slugify(interval.lower())
    return f"{DOMAIN}:{slugify(f'{meter.key}_{resolution}')}"


def _statistic_metadata(meter: MeterDefinition, interval: str, unit: str) -> StatisticMetaData:
    metadata: dict[str, Any] = {
        "has_mean": False,
        "has_sum": True,
        "name": f"{meter.friendly_name} {interval.lower()}",
        "source": DOMAIN,
        "statistic_id": statistic_id(meter, interval),
        "unit_of_measurement": unit,
    }
    # Older recorders reject unknown metadata keys when importing.
    if HA_VERSION >= (2025, 4):
        metadata["mean_type"] = 0
    if HA_VERSION >= (2025, 11):
        metadata["unit_class"] = "energy"
    return metadata  # type: ignore[return-value]


def _row_start(value: float | datetime) -> datetime:
    """Recorder rows carry a UTC timestamp (float) as start."""
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    return dt_util.utc_from_timestamp(value)


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
