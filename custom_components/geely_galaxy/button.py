"""Button platform for Geely Galaxy."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.components.button import ButtonEntity
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
    """Set up Geely Galaxy buttons based on a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    api = hass.data[DOMAIN][entry.entry_id]["api"]

    entities: list[ButtonEntity] = [
        GeelyDoorLockButton(coordinator, entry, api),
        GeelyDoorUnlockButton(coordinator, entry, api),
        GeelySearchCarButton(coordinator, entry, api),
        GeelyAcOnButton(coordinator, entry, api),
        GeelyAcOffButton(coordinator, entry, api),
        GeelyWindowCloseButton(coordinator, entry, api),
        GeelyWindowVentButton(coordinator, entry, api),
        GeelyDefrostOnButton(coordinator, entry, api),
        GeelyDefrostOffButton(coordinator, entry, api),
        GeelyPurifierOnButton(coordinator, entry, api),
        GeelyPurifierOffButton(coordinator, entry, api),
    ]

    # 如果有家用充电桩，添加充电桩控制按钮
    if coordinator.data and coordinator.data.get("home_charger_list"):
        entities.extend([
            HomeChargerStartButton(coordinator, entry, api),
            HomeChargerStopButton(coordinator, entry, api),
        ])

    entities.append(GeelySignInButton(coordinator, entry, api))

    async_add_entities(entities)


class GeelyBaseButton(CoordinatorEntity, ButtonEntity):
    """Base class for Geely Galaxy buttons."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        entry: ConfigEntry,
        api,
    ) -> None:
        """Initialize the button."""
        super().__init__(coordinator)
        self._entry = entry
        self._api = api

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


class GeelyDoorLockButton(GeelyBaseButton):
    """Button to lock doors."""

    _attr_name = "锁车"
    _attr_icon = "mdi:car-door-lock"

    def __init__(self, coordinator, entry, api) -> None:
        """Initialize."""
        super().__init__(coordinator, entry, api)
        self._attr_unique_id = f"{entry.entry_id}_btn_door_lock"

    async def async_press(self) -> None:
        """Lock the doors."""
        try:
            await self._api.control_door(None, lock=True)
            await self.coordinator.async_request_refresh()
        except Exception as err:
            _LOGGER.error("锁车失败: %s", err)


class GeelyDoorUnlockButton(GeelyBaseButton):
    """Button to unlock doors."""

    _attr_name = "解锁"
    _attr_icon = "mdi:car-door"

    def __init__(self, coordinator, entry, api) -> None:
        """Initialize."""
        super().__init__(coordinator, entry, api)
        self._attr_unique_id = f"{entry.entry_id}_btn_door_unlock"

    async def async_press(self) -> None:
        """Unlock the doors."""
        try:
            await self._api.control_door(None, lock=False)
            await self.coordinator.async_request_refresh()
        except Exception as err:
            _LOGGER.error("解锁失败: %s", err)


class GeelySearchCarButton(GeelyBaseButton):
    """Button to flash lights and honk."""

    _attr_name = "闪灯鸣笛寻车"
    _attr_icon = "mdi:car-light-alert"

    def __init__(self, coordinator, entry, api) -> None:
        """Initialize."""
        super().__init__(coordinator, entry, api)
        self._attr_unique_id = f"{entry.entry_id}_btn_search_car"

    async def async_press(self) -> None:
        """Flash lights and honk."""
        try:
            await self._api.control_search(None)
        except Exception as err:
            _LOGGER.error("寻车失败: %s", err)


class GeelyAcOnButton(GeelyBaseButton):
    """Button to turn on AC."""

    _attr_name = "开启空调"
    _attr_icon = "mdi:air-conditioner"

    def __init__(self, coordinator, entry, api) -> None:
        """Initialize."""
        super().__init__(coordinator, entry, api)
        self._attr_unique_id = f"{entry.entry_id}_btn_ac_on"

    async def async_press(self) -> None:
        """Turn on AC."""
        try:
            await self._api.control_ac(None, on=True)
            await self.coordinator.async_request_refresh()
        except Exception as err:
            _LOGGER.error("开启空调失败: %s", err)


class GeelyAcOffButton(GeelyBaseButton):
    """Button to turn off AC."""

    _attr_name = "关闭空调"
    _attr_icon = "mdi:air-conditioner"

    def __init__(self, coordinator, entry, api) -> None:
        """Initialize."""
        super().__init__(coordinator, entry, api)
        self._attr_unique_id = f"{entry.entry_id}_btn_ac_off"

    async def async_press(self) -> None:
        """Turn off AC."""
        try:
            await self._api.control_ac(None, on=False)
            await self.coordinator.async_request_refresh()
        except Exception as err:
            _LOGGER.error("关闭空调失败: %s", err)


class GeelyWindowCloseButton(GeelyBaseButton):
    """Button to close windows."""

    _attr_name = "关闭车窗"
    _attr_icon = "mdi:car-door"

    def __init__(self, coordinator, entry, api) -> None:
        """Initialize."""
        super().__init__(coordinator, entry, api)
        self._attr_unique_id = f"{entry.entry_id}_btn_window_close"

    async def async_press(self) -> None:
        """Close windows."""
        try:
            await self._api.control_window(None, action="close")
            await self.coordinator.async_request_refresh()
        except Exception as err:
            _LOGGER.error("关闭车窗失败: %s", err)


class GeelyWindowVentButton(GeelyBaseButton):
    """Button to vent windows (微开)."""

    _attr_name = "车窗微开"
    _attr_icon = "mdi:car-door"

    def __init__(self, coordinator, entry, api) -> None:
        """Initialize."""
        super().__init__(coordinator, entry, api)
        self._attr_unique_id = f"{entry.entry_id}_btn_window_vent"

    async def async_press(self) -> None:
        """Vent windows."""
        try:
            await self._api.control_window(None, action="lightOpen")
            await self.coordinator.async_request_refresh()
        except Exception as err:
            _LOGGER.error("车窗微开失败: %s", err)


class GeelyDefrostOnButton(GeelyBaseButton):
    """Button to turn on defrost."""

    _attr_name = "开启除霜"
    _attr_icon = "mdi:car-defrost-front"

    def __init__(self, coordinator, entry, api) -> None:
        """Initialize."""
        super().__init__(coordinator, entry, api)
        self._attr_unique_id = f"{entry.entry_id}_btn_defrost_on"

    async def async_press(self) -> None:
        """Turn on defrost."""
        try:
            await self._api.control_defrost(None, on=True)
            await self.coordinator.async_request_refresh()
        except Exception as err:
            _LOGGER.error("开启除霜失败: %s", err)


class GeelyDefrostOffButton(GeelyBaseButton):
    """Button to turn off defrost."""

    _attr_name = "关闭除霜"
    _attr_icon = "mdi:car-defrost-front"

    def __init__(self, coordinator, entry, api) -> None:
        """Initialize."""
        super().__init__(coordinator, entry, api)
        self._attr_unique_id = f"{entry.entry_id}_btn_defrost_off"

    async def async_press(self) -> None:
        """Turn off defrost."""
        try:
            await self._api.control_defrost(None, on=False)
            await self.coordinator.async_request_refresh()
        except Exception as err:
            _LOGGER.error("关闭除霜失败: %s", err)


class GeelyPurifierOnButton(GeelyBaseButton):
    """Button to turn on air purifier."""

    _attr_name = "开启空气净化"
    _attr_icon = "mdi:air-purifier"

    def __init__(self, coordinator, entry, api) -> None:
        """Initialize."""
        super().__init__(coordinator, entry, api)
        self._attr_unique_id = f"{entry.entry_id}_btn_purifier_on"

    async def async_press(self) -> None:
        """Turn on air purifier."""
        try:
            await self._api.control_purifier(None, on=True)
            await self.coordinator.async_request_refresh()
        except Exception as err:
            _LOGGER.error("开启空气净化失败: %s", err)


class GeelyPurifierOffButton(GeelyBaseButton):
    """Button to turn off air purifier."""

    _attr_name = "关闭空气净化"
    _attr_icon = "mdi:air-purifier"

    def __init__(self, coordinator, entry, api) -> None:
        """Initialize."""
        super().__init__(coordinator, entry, api)
        self._attr_unique_id = f"{entry.entry_id}_btn_purifier_off"

    async def async_press(self) -> None:
        """Turn off air purifier."""
        try:
            await self._api.control_purifier(None, on=False)
            await self.coordinator.async_request_refresh()
        except Exception as err:
            _LOGGER.error("关闭空气净化失败: %s", err)


class HomeChargerBaseButton(CoordinatorEntity, ButtonEntity):
    """Base class for home charger buttons."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        entry: ConfigEntry,
        api,
    ) -> None:
        """Initialize the button."""
        super().__init__(coordinator)
        self._entry = entry
        self._api = api

    @property
    def device_info(self) -> dict[str, Any]:
        """Return device info for the home charger."""
        charger_status = self.coordinator.data.get("home_charger_status", {}) if self.coordinator.data else {}
        piling_code = charger_status.get("pilingsCode", "unknown")
        charger_name = charger_status.get("pilingsName", "家用充电桩")

        return {
            "identifiers": {(DOMAIN, f"charger_{piling_code}")},
            "name": charger_name,
            "manufacturer": "吉利汽车",
            "model": "家用充电桩",
        }


class HomeChargerStartButton(HomeChargerBaseButton):
    """Button to start home charger."""

    _attr_name = "开始充电"
    _attr_icon = "mdi:ev-station"

    def __init__(self, coordinator, entry, api) -> None:
        """Initialize."""
        super().__init__(coordinator, entry, api)
        self._attr_unique_id = f"{entry.entry_id}_btn_charger_start"

    async def async_press(self) -> None:
        """Start charging."""
        try:
            await self._api.start_home_charger()
            # 等待充电桩状态变化后再刷新
            await asyncio.sleep(3)
            await self.coordinator.async_request_refresh()
        except Exception as err:
            _LOGGER.error("开始充电失败: %s", err)


class HomeChargerStopButton(HomeChargerBaseButton):
    """Button to stop home charger."""

    _attr_name = "停止充电"
    _attr_icon = "mdi:ev-station"

    def __init__(self, coordinator, entry, api) -> None:
        """Initialize."""
        super().__init__(coordinator, entry, api)
        self._attr_unique_id = f"{entry.entry_id}_btn_charger_stop"

    async def async_press(self) -> None:
        """Stop charging."""
        try:
            await self._api.stop_home_charger()
            # 等待充电桩状态变化后再刷新
            await asyncio.sleep(3)
            await self.coordinator.async_request_refresh()
        except Exception as err:
            _LOGGER.error("停止充电失败: %s", err)


# ========== 签到按钮 ==========

class GeelySignInButton(GeelyBaseButton):
    """Button to sign in."""

    _attr_name = "每日签到"
    _attr_icon = "mdi:calendar-check"

    def __init__(self, coordinator, entry, api) -> None:
        """Initialize."""
        super().__init__(coordinator, entry, api)
        self._attr_unique_id = f"{entry.entry_id}_btn_sign_in"

    async def async_press(self) -> None:
        """Perform sign-in."""
        try:
            result = await self._api.do_sign()
            _LOGGER.info("签到成功: %s", result)
            # 刷新 coordinator 更新签到状态
            await self.coordinator.async_request_refresh()
        except Exception as err:
            _LOGGER.error("签到失败: %s", err)
