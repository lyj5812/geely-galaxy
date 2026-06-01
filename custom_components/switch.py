"""Switch platform for Geely Galaxy."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Geely Galaxy switches based on a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    api = hass.data[DOMAIN][entry.entry_id]["api"]

    entities: list[SwitchEntity] = [
        GeelySentryModeSwitch(coordinator, entry, api),
    ]

    async_add_entities(entities)


class GeelySentryModeSwitch(CoordinatorEntity, SwitchEntity):
    """Switch for sentry mode (哨兵模式)."""

    _attr_has_entity_name = True
    _attr_name = "哨兵模式开关"
    _attr_icon = "mdi:shield-car"

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        entry: ConfigEntry,
        api,
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator)
        self._entry = entry
        self._api = api
        self._attr_unique_id = f"{entry.entry_id}_sentry_mode_switch"

    @property
    def device_info(self) -> dict[str, Any]:
        """Return device info."""
        vehicle_info = self.coordinator.data.get("vehicle_info", {}) if self.coordinator.data else {}
        vin = vehicle_info.get("vin", "unknown")
        model = vehicle_info.get("seriesNameVs", "吉利银河")

        return {
            "identifiers": {(DOMAIN, vin)},
            "name": f"{model}",
            "manufacturer": "吉利汽车",
            "model": model,
        }

    @property
    def is_on(self) -> bool | None:
        """Return true if sentry mode is on."""
        if not self.coordinator.data:
            return None
        switch_status = self.coordinator.data.get("switch_status", {})
        status = switch_status.get("vstdModeStatus")
        if status == "1":
            return True
        elif status == "0":
            return False
        return None

    @property
    def icon(self) -> str:
        """Return icon based on state."""
        if self.is_on:
            return "mdi:shield-car"
        return "mdi:shield-off"

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on sentry mode."""
        try:
            await self._api.control_switch(None, "vstdMode", True)
            await self.coordinator.async_request_refresh()
        except Exception as err:
            _LOGGER.error("开启哨兵模式失败: %s", err)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off sentry mode."""
        try:
            await self._api.control_switch(None, "vstdMode", False)
            await self.coordinator.async_request_refresh()
        except Exception as err:
            _LOGGER.error("关闭哨兵模式失败: %s", err)
