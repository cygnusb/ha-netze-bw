"""Data update coordinator for Netze BW Portal."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import NetzeBwPortalApiClient, NetzeBwPortalAuthError, NetzeBwPortalConnectionError
from .const import CONF_SELECTED_METER_IDS, DEFAULT_SCAN_INTERVAL, DOMAIN
from .models import CoordinatorData

_LOGGER = logging.getLogger(__name__)


class NetzeBwPortalCoordinator(DataUpdateCoordinator[CoordinatorData]):
    """Coordinates updates from Netze BW Portal."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: NetzeBwPortalApiClient,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=DEFAULT_SCAN_INTERVAL,
        )
        self.client = client

    async def _async_update_data(self) -> CoordinatorData:
        selected_meter_ids = self.config_entry.options.get(CONF_SELECTED_METER_IDS)
        selected_set = set(selected_meter_ids) if isinstance(selected_meter_ids, list) else None

        try:
            data = await self.client.async_fetch_data(selected_set)
        except NetzeBwPortalAuthError as err:
            raise ConfigEntryAuthFailed("Authentication failed") from err
        except NetzeBwPortalConnectionError as err:
            raise UpdateFailed(str(err)) from err

        if not data.meters:
            if data.errors:
                raise UpdateFailed(f"No meters available, errors: {data.errors}")
            raise UpdateFailed("No matching IMS meters found")

        return data
