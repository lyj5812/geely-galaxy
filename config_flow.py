"""Config flow for Geely Galaxy integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import GeelyGalaxyApi, GeelyApiError, GeelyAuthError
from .const import (
    DOMAIN,
    CONF_REFRESH_TOKEN,
    CONF_DEVICE_SN,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_REFRESH_TOKEN): str,
        vol.Required(CONF_DEVICE_SN): str,
    }
)


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate the user input allows us to connect."""
    session = async_get_clientsession(hass)

    api = GeelyGalaxyApi(
        refresh_token=data[CONF_REFRESH_TOKEN],
        device_sn=data[CONF_DEVICE_SN],
        session=session,
    )

    if not await api.test_connection():
        raise GeelyAuthError("Invalid credentials")

    # 获取车辆信息用于标题
    try:
        vehicles = await api.get_vehicle_list()
        if vehicles:
            model = vehicles[0].get("seriesNameVs", "吉利银河")
            vin = vehicles[0].get("vin", "")
            return {"title": f"{model}", "vin": vin}
    except GeelyApiError:
        pass

    return {"title": "吉利银河", "vin": ""}


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Geely Galaxy."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                info = await validate_input(self.hass, user_input)
            except GeelyAuthError:
                errors["base"] = "invalid_auth"
            except GeelyApiError:
                errors["base"] = "cannot_connect"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                # 使用 VIN 作为唯一标识
                if info.get("vin"):
                    await self.async_set_unique_id(info["vin"])
                    self._abort_if_unique_id_configured()
                return self.async_create_entry(title=info["title"], data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )
