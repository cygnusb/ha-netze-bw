"""Sensor platform for Netze BW Portal."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import NetzeBwPortalConfigEntry
from .const import DOMAIN, VALUE_TYPE_FEEDIN
from .coordinator import NetzeBwPortalCoordinator
from .models import MeterSnapshot


@dataclass(frozen=True, kw_only=True)
class NetzeBwSensorDescription(SensorEntityDescription):
    """Description for Netze BW sensor."""

    value_fn: Callable[[MeterSnapshot], Any]
    feedin_translation_key: str | None = None


SENSOR_DESCRIPTIONS: tuple[NetzeBwSensorDescription, ...] = (
    NetzeBwSensorDescription(
        key="daily_value",
        translation_key="daily_value",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        suggested_display_precision=3,
        value_fn=lambda snapshot: snapshot.daily_value,
    ),
    NetzeBwSensorDescription(
        key="hourly_consumption",
        translation_key="hourly_consumption",
        feedin_translation_key="hourly_feedin",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=3,
        value_fn=lambda snapshot: snapshot.latest_hourly_value,
    ),
    NetzeBwSensorDescription(
        key="daily_consumption",
        translation_key="daily_consumption",
        feedin_translation_key="daily_feedin",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=3,
        value_fn=lambda snapshot: snapshot.latest_daily_value,
    ),
    NetzeBwSensorDescription(
        key="total_reading",
        translation_key="total_reading",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=3,
        value_fn=lambda snapshot: snapshot.total_reading,
    ),
    NetzeBwSensorDescription(
        key="sum_7d",
        translation_key="sum_7d",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        suggested_display_precision=3,
        value_fn=lambda snapshot: snapshot.sum_7d,
    ),
    NetzeBwSensorDescription(
        key="sum_30d",
        translation_key="sum_30d",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        suggested_display_precision=3,
        value_fn=lambda snapshot: snapshot.sum_30d,
    ),
    NetzeBwSensorDescription(
        key="last_date",
        translation_key="last_date",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda snapshot: snapshot.last_date,
    ),
    NetzeBwSensorDescription(
        key="serial_no",
        translation_key="serial_no",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda snapshot: snapshot.details.serial_no,
    ),
    NetzeBwSensorDescription(
        key="metering_code",
        translation_key="metering_code",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda snapshot: (
            _format_metering_code(snapshot.details.metering_code)
            if snapshot.details.metering_code
            else None
        ),
    ),
    NetzeBwSensorDescription(
        key="smgw_id",
        translation_key="smgw_id",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda snapshot: snapshot.details.smgw_id,
    ),
    NetzeBwSensorDescription(
        key="value_types",
        translation_key="value_types",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda snapshot: ", ".join(snapshot.meter.value_types),
    ),
    NetzeBwSensorDescription(
        key="history_status",
        translation_key="history_status",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda snapshot: snapshot.history_status,
    ),
    NetzeBwSensorDescription(
        key="history_last_daily_point",
        translation_key="history_last_daily_point",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda snapshot: snapshot.history_last_daily_point,
    ),
    NetzeBwSensorDescription(
        key="history_last_hourly_point",
        translation_key="history_last_hourly_point",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda snapshot: snapshot.history_last_hourly_point,
    ),
    NetzeBwSensorDescription(
        key="history_last_backfill",
        translation_key="history_last_backfill",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda snapshot: snapshot.history_last_backfill,
    ),
    NetzeBwSensorDescription(
        key="history_open_gaps",
        translation_key="history_open_gaps",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda snapshot: snapshot.history_open_gaps,
    ),
    NetzeBwSensorDescription(
        key="last_fetch",
        translation_key="last_fetch",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda snapshot: snapshot.last_fetch,
    ),
    NetzeBwSensorDescription(
        key="next_fetch",
        translation_key="next_fetch",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda snapshot: snapshot.next_fetch,
    ),
    NetzeBwSensorDescription(
        key="15min_value",
        translation_key="15min_value",
        feedin_translation_key="15min_feedin",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=3,
        value_fn=lambda snapshot: snapshot.latest_15min_value,
    ),
    NetzeBwSensorDescription(
        key="history_last_15min_point",
        translation_key="history_last_15min_point",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda snapshot: snapshot.history_last_15min_point,
    ),
)


def _format_metering_code(code: str) -> str:
    """Format MeLo-ID with spaces every 4 chars for readability."""
    # DE + 31 digits → "DE00 1234 5678 …"
    return " ".join(code[i : i + 4] for i in range(0, len(code), 4))


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NetzeBwPortalConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Netze BW Portal sensors."""
    coordinator = entry.runtime_data.coordinator

    entities: list[NetzeBwPortalSensor] = []
    for meter_id in coordinator.data.meters:
        for description in SENSOR_DESCRIPTIONS:
            entities.append(NetzeBwPortalSensor(coordinator, meter_id, description))

    async_add_entities(entities)


class NetzeBwPortalSensor(CoordinatorEntity[NetzeBwPortalCoordinator], SensorEntity):
    """Representation of a Netze BW sensor."""

    entity_description: NetzeBwSensorDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: NetzeBwPortalCoordinator,
        meter_id: str,
        description: NetzeBwSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._meter_id = meter_id
        self._attr_unique_id = (
            f"{coordinator.data.account_sub}_{self._meter_id}_{description.key}"
        )
        if (
            description.feedin_translation_key is not None
            and coordinator.data
            and meter_id in coordinator.data.meters
            and VALUE_TYPE_FEEDIN in coordinator.data.meters[meter_id].meter.value_types
        ):
            self._attr_translation_key = description.feedin_translation_key

    @property
    def _snapshot(self) -> MeterSnapshot | None:
        return self.coordinator.data.meters.get(self._meter_id)

    @property
    def available(self) -> bool:
        snapshot = self._snapshot
        if snapshot is None:
            return False
        value = self.entity_description.value_fn(snapshot)
        return value is not None

    @property
    def device_info(self) -> DeviceInfo:
        snapshot = self._snapshot
        if snapshot is None:
            return DeviceInfo(identifiers={(DOMAIN, self._meter_id)})
        return DeviceInfo(
            identifiers={(DOMAIN, self._meter_id)},
            name=snapshot.meter.friendly_name,
            manufacturer="Netze BW",
            model=snapshot.meter.meter_type,
            serial_number=snapshot.details.serial_no,
        )

    @property
    def native_value(self) -> str | float | datetime | None:
        snapshot = self._snapshot
        if snapshot is None:
            return None
        return self.entity_description.value_fn(snapshot)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        snapshot = self._snapshot
        if snapshot is None:
            return {}
        return {
            "meter_id": snapshot.meter.id,
            "gateway_meter_id": snapshot.meter.meter_id,
            "value_types": snapshot.meter.value_types,
        }
