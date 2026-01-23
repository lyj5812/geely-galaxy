"""
The Geely Galaxy integration.

基于 geely-galaxy-assistant 项目实现
https://github.com/suyunkai/geely-galaxy-assistant
"""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import GeelyGalaxyApi, GeelyApiError
from .const import (
    DOMAIN,
    CONF_REFRESH_TOKEN,
    CONF_DEVICE_SN,
    DEFAULT_SCAN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Geely Galaxy from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    session = async_get_clientsession(hass)

    api = GeelyGalaxyApi(
        refresh_token=entry.data[CONF_REFRESH_TOKEN],
        device_sn=entry.data[CONF_DEVICE_SN],
        session=session,
    )

    async def async_update_data():
        """Fetch data from API."""
        try:
            # 获取车辆列表
            vehicles = await api.get_vehicle_list()
            if not vehicles:
                raise UpdateFailed("No vehicles found")

            vin = vehicles[0].get("vin")

            # 获取车辆状态
            vehicle_status = await api.get_vehicle_status(vin)

            # 获取开关状态
            try:
                switch_status = await api.get_switch_status(vin)
            except GeelyApiError:
                switch_status = {}

            return {
                "vehicle_info": vehicles[0],
                "vehicle_status": vehicle_status,
                "switch_status": switch_status,
            }
        except GeelyApiError as err:
            raise UpdateFailed(f"Error communicating with API: {err}") from err

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=DOMAIN,
        update_method=async_update_data,
        update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
    )

    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = {
        "api": api,
        "coordinator": coordinator,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
