"""Constants for the Netze BW Portal integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "netze_bw_portal"
PLATFORMS: Final = ["sensor"]

CONF_SELECTED_METER_IDS: Final = "selected_meter_ids"
CONF_SCAN_INTERVAL_MINUTES: Final = "scan_interval_minutes"
CONF_ACCOUNT_SUB: Final = "account_sub"

DEFAULT_SCAN_INTERVAL_MINUTES: Final = 15
MIN_SCAN_INTERVAL_MINUTES: Final = 5
MAX_SCAN_INTERVAL_MINUTES: Final = 120

DEFAULT_SCAN_INTERVAL: Final = timedelta(minutes=DEFAULT_SCAN_INTERVAL_MINUTES)

BASE_URL: Final = "https://meine.netze-bw.de"
AUTH_URL: Final = "https://login.netze-bw.de"

METER_TYPE_IMS: Final = "IMS"
VALUE_TYPE_CONSUMPTION: Final = "CONSUMPTION"
VALUE_TYPE_READING: Final = "READING"
VALUE_TYPE_FEEDIN: Final = "FEEDIN"
VALUE_TYPE_FEEDIN_READING: Final = "FEEDIN_READING"

HISTORY_DAYS: Final = 30
HISTORY_SHORT_DAYS: Final = 7
