"""The Netze BW Portal integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from .coordinator import NetzeBwPortalCoordinator

    NetzeBwPortalConfigEntry = ConfigEntry["NetzeBwPortalRuntimeData"]
else:
    NetzeBwPortalConfigEntry = Any


@dataclass
class NetzeBwPortalRuntimeData:
    """Runtime data for config entry."""

    client: Any
    coordinator: Any


async def async_setup_entry(hass: HomeAssistant, entry: NetzeBwPortalConfigEntry) -> bool:
    """Set up Netze BW Portal from a config entry."""
    from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    from .api import NetzeBwPortalApiClient
    from .const import PLATFORMS
    from .coordinator import NetzeBwPortalCoordinator
    from .history import NetzeBwPortalHistoryManager

    session = async_get_clientsession(hass)
    client = NetzeBwPortalApiClient(
        session=session,
        username=entry.data[CONF_USERNAME],
        password=entry.data[CONF_PASSWORD],
    )
    history_manager = NetzeBwPortalHistoryManager(hass, entry.entry_id, client)
    await history_manager.async_initialize()
    coordinator = NetzeBwPortalCoordinator(hass, entry, client, history_manager)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = NetzeBwPortalRuntimeData(
        client=client,
        coordinator=coordinator,
    )
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_update_listener(hass: HomeAssistant, entry: NetzeBwPortalConfigEntry) -> None:
    """Handle options update."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: NetzeBwPortalConfigEntry) -> bool:
    """Unload a config entry."""
    from .const import PLATFORMS

    # If setup never completed (e.g. hung during first refresh), platforms were
    # never registered — skip platform unload to avoid ValueError.
    if not hasattr(entry, "runtime_data") or entry.runtime_data is None:
        return True
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
