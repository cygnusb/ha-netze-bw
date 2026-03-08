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


@dataclass
class CoordinatorData:
    """Coordinator payload."""

    account_sub: str
    meters: dict[str, MeterSnapshot] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
