"""Constants for the Netze BW Portal integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "netze_bw_portal"
PLATFORMS: Final = ["sensor"]

CONF_SELECTED_METER_IDS: Final = "selected_meter_ids"
CONF_ACCOUNT_SUB: Final = "account_sub"
CONF_ENABLE_DAILY_HISTORY: Final = "enable_daily_history"
CONF_ENABLE_HOURLY_HISTORY: Final = "enable_hourly_history"
CONF_HISTORY_BACKFILL_DAYS: Final = "history_backfill_days"
HISTORY_RECHECK_DAYS: Final = 2
HOURLY_FETCH_DELAY_SECONDS: Final = 0.3

DEFAULT_SCAN_INTERVAL_HOURS: Final = 6
DEFAULT_SCAN_INTERVAL: Final = timedelta(hours=DEFAULT_SCAN_INTERVAL_HOURS)

BASE_URL: Final = "https://meine.netze-bw.de"
AUTH_URL: Final = "https://login.netze-bw.de"

METER_TYPE_IMS: Final = "IMS"
VALUE_TYPE_CONSUMPTION: Final = "CONSUMPTION"
VALUE_TYPE_READING: Final = "READING"
VALUE_TYPE_FEEDIN: Final = "FEEDIN"
VALUE_TYPE_FEEDIN_READING: Final = "FEEDIN_READING"

HISTORY_DAYS: Final = 30
HISTORY_SHORT_DAYS: Final = 7
HISTORY_DAILY_DELAY_DAYS: Final = 1
HISTORY_HOURLY_DELAY_HOURS: Final = 6
MEASUREMENT_FILTER_DAY: Final = "1DAY"
MEASUREMENT_FILTER_HOUR: Final = "1HOUR"
MEASUREMENT_FILTER_15MIN: Final = "15MIN"
MEASUREMENT_FILTER_MONTH: Final = "1MONTH"

CONF_ENABLE_15MIN_HISTORY: Final = "enable_15min_history"

PORTAL_TIMEZONE: Final = "Europe/Berlin"
