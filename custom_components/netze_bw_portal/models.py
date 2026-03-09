"""Data models for Netze BW Portal."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class MeterDefinition:
    """Base metadata for a discovered IMS meter."""

    id: str
    friendly_name: str
    meter_id: str | None
    value_types: list[str]
    meter_type: str
    state: str | None


@dataclass
class MeterDetails:
    """Detailed metadata for a single IMS meter."""

    serial_no: str | None
    metering_code: str | None
    smgw_id: str | None
    division: str | None


@dataclass
class MeterSnapshot:
    """Runtime values for a single meter."""

    meter: MeterDefinition
    details: MeterDetails
    daily_value: float | None
    total_reading: float | None
    last_date: datetime | None
    sum_7d: float | None
    sum_30d: float | None
    unit: str | None
    latest_hourly_value: float | None = None
    latest_daily_value: float | None = None
    history_status: str | None = None
    history_last_daily_point: datetime | None = None
    history_last_hourly_point: datetime | None = None
    history_last_backfill: datetime | None = None
    history_open_gaps: int = 0
    last_fetch: datetime | None = None
    next_fetch: datetime | None = None


@dataclass(frozen=True)
class MeasurementPoint:
    """Single measurement interval or reading point."""

    start_datetime: datetime
    end_datetime: datetime | None
    value: float | None
    unit: str | None
    status: str | None


@dataclass(frozen=True)
class MeasurementSeries:
    """Time series returned by the measurements endpoint."""

    meter_id: str
    value_type: str
    interval: str
    points: list[MeasurementPoint]
    unit: str | None
    min_measurement_start_datetime: datetime | None = None
    max_measurement_end_datetime: datetime | None = None


@dataclass(frozen=True)
class HistoryGap:
    """Missing interval in the expected history window."""

    interval: str
    start_datetime: datetime
    end_datetime: datetime


@dataclass(frozen=True)
class HistoryState:
    """Computed history status for a meter."""

    status: str
    last_daily_point: datetime | None
    last_hourly_point: datetime | None
    open_gaps: tuple[HistoryGap, ...] = ()
    last_backfill: datetime | None = None


@dataclass
class CoordinatorData:
    """Coordinator payload."""

    account_sub: str
    meters: dict[str, MeterSnapshot] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
