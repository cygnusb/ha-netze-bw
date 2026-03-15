"""Config flow for Netze BW Portal."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    NetzeBwPortalApiClient,
    NetzeBwPortalAuthError,
    NetzeBwPortalConnectionError,
    NetzeBwPortalError,
)
from .const import (
    CONF_ACCOUNT_SUB,
    CONF_ENABLE_15MIN_HISTORY,
    CONF_ENABLE_DAILY_HISTORY,
    CONF_ENABLE_HOURLY_HISTORY,
    CONF_HISTORY_BACKFILL_DAYS,
    CONF_SELECTED_METER_IDS,
    DOMAIN,
    HISTORY_DAYS,
)

_LOGGER = logging.getLogger(__name__)


class NetzeBwPortalConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Netze BW Portal."""

    VERSION = 1
    MINOR_VERSION = 1

    def __init__(self) -> None:
        self._username: str = ""
        self._password: str = ""
        self._account_sub: str = ""
        self._meter_choices: dict[str, str] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle the user step (credentials)."""
        errors: dict[str, str] = {}

        if user_input is not None:
            username = user_input.get(CONF_USERNAME, "").strip()
            password = user_input.get(CONF_PASSWORD, "")
            if not username or not password:
                errors["base"] = "invalid_auth"
            elif len(username) > 255 or len(password) > 255:
                errors["base"] = "invalid_auth"

        if user_input is not None and not errors:
            session = async_get_clientsession(self.hass)
            client = NetzeBwPortalApiClient(
                session=session,
                username=username,
                password=password,
            )

            try:
                account_sub = await client.async_ensure_login()
                meter_choices = await client.async_fetch_ims_meter_choices()
            except NetzeBwPortalAuthError:
                _LOGGER.exception("Authentication failed during config flow")
                errors["base"] = "invalid_auth"
            except NetzeBwPortalConnectionError:
                _LOGGER.exception("Connection failed during config flow")
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error during config flow")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(account_sub)
                self._abort_if_unique_id_configured()

                self._username = username
                self._password = password
                self._account_sub = account_sub
                self._meter_choices = meter_choices

                return await self.async_step_meters()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_USERNAME): str,
                    vol.Required(CONF_PASSWORD): str,
                }
            ),
            errors=errors,
        )

    async def async_step_meters(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle the meter selection and history options step."""
        if user_input is not None:
            return self.async_create_entry(
                title=self._username,
                data={
                    CONF_USERNAME: self._username,
                    CONF_PASSWORD: self._password,
                    CONF_ACCOUNT_SUB: self._account_sub,
                },
                options={
                    CONF_SELECTED_METER_IDS: user_input[CONF_SELECTED_METER_IDS],
                    CONF_ENABLE_DAILY_HISTORY: user_input[CONF_ENABLE_DAILY_HISTORY],
                    CONF_ENABLE_HOURLY_HISTORY: user_input[CONF_ENABLE_HOURLY_HISTORY],
                    CONF_ENABLE_15MIN_HISTORY: user_input[CONF_ENABLE_15MIN_HISTORY],
                    CONF_HISTORY_BACKFILL_DAYS: user_input[CONF_HISTORY_BACKFILL_DAYS],
                },
            )

        all_meter_ids = sorted(self._meter_choices.keys())

        return self.async_show_form(
            step_id="meters",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SELECTED_METER_IDS,
                        default=all_meter_ids,
                    ): cv.multi_select(self._meter_choices),
                    vol.Required(CONF_ENABLE_DAILY_HISTORY, default=True): bool,
                    vol.Required(CONF_ENABLE_HOURLY_HISTORY, default=True): bool,
                    vol.Required(CONF_ENABLE_15MIN_HISTORY, default=False): bool,
                    vol.Required(
                        CONF_HISTORY_BACKFILL_DAYS,
                        default=HISTORY_DAYS,
                    ): vol.All(vol.Coerce(int), vol.Range(min=1, max=30)),
                }
            ),
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> NetzeBwPortalOptionsFlow:
        """Get options flow for this handler."""
        return NetzeBwPortalOptionsFlow(config_entry)


class NetzeBwPortalOptionsFlow(config_entries.OptionsFlow):
    """Handle options flow for Netze BW Portal."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Manage options."""
        errors: dict[str, str] = {}

        runtime_data = getattr(self._config_entry, "runtime_data", None)

        meter_choices: dict[str, str] = {}
        if runtime_data is not None:
            try:
                meter_choices = await runtime_data.client.async_fetch_ims_meter_choices()
            except NetzeBwPortalError:
                errors["base"] = "cannot_connect"
        else:
            errors["base"] = "cannot_connect"

        current_selected = self._config_entry.options.get(CONF_SELECTED_METER_IDS)
        if not isinstance(current_selected, list):
            current_selected = list(meter_choices.keys())
        elif meter_choices:
            current_selected = [meter_id for meter_id in current_selected if meter_id in meter_choices]

        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SELECTED_METER_IDS,
                        default=current_selected,
                    ): cv.multi_select(meter_choices),
                    vol.Required(
                        CONF_ENABLE_DAILY_HISTORY,
                        default=self._config_entry.options.get(CONF_ENABLE_DAILY_HISTORY, True),
                    ): bool,
                    vol.Required(
                        CONF_ENABLE_HOURLY_HISTORY,
                        default=self._config_entry.options.get(CONF_ENABLE_HOURLY_HISTORY, True),
                    ): bool,
                    vol.Required(
                        CONF_ENABLE_15MIN_HISTORY,
                        default=self._config_entry.options.get(CONF_ENABLE_15MIN_HISTORY, False),
                    ): bool,
                    vol.Required(
                        CONF_HISTORY_BACKFILL_DAYS,
                        default=self._config_entry.options.get(CONF_HISTORY_BACKFILL_DAYS, HISTORY_DAYS),
                    ): vol.All(vol.Coerce(int), vol.Range(min=1, max=30)),
                }
            ),
            errors=errors,
        )
