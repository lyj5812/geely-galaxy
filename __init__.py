"""
The Geely Galaxy integration.

基于 geely-galaxy-assistant 项目实现
https://github.com/suyunkai/geely-galaxy-assistant
"""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import GeelyGalaxyApi, GeelyApiError
from .const import (
    DOMAIN,
    CONF_REFRESH_TOKEN,
    CONF_DEVICE_SN,
    DEFAULT_SCAN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.SWITCH, Platform.BUTTON]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Geely Galaxy from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    # 使用独立 session，不用 HA 共享 session，避免默认 headers/cookie 干扰 API 签名
    api = GeelyGalaxyApi(
        refresh_token=entry.data[CONF_REFRESH_TOKEN],
        device_sn=entry.data[CONF_DEVICE_SN],
    )

    # 缓存车辆信息，避免每次更新都调用 get_vehicle_list
    cached_vin: str | None = None
    cached_vehicle_info: dict = {}

    async def async_update_data():
        """Fetch data from API."""
        nonlocal cached_vin, cached_vehicle_info
        try:
            # 只在首次或缓存为空时获取车辆列表
            if not cached_vin:
                vehicles = await api.get_vehicle_list()
                if not vehicles:
                    raise UpdateFailed("No vehicles found")
                cached_vehicle_info = vehicles[0]
                cached_vin = cached_vehicle_info.get("vin")

            # API 调用之间添加延迟，避免服务端限流
            await asyncio.sleep(2)

            # 获取车辆状态
            vehicle_status = {}
            try:
                vehicle_status = await api.get_vehicle_status(cached_vin)
            except GeelyApiError as err:
                _LOGGER.warning("获取车辆状态失败（将在下次更新时重试）: %s", err)

            await asyncio.sleep(1)

            # 获取开关状态
            switch_status = {}
            try:
                switch_status = await api.get_switch_status(cached_vin)
            except GeelyApiError:
                pass

            await asyncio.sleep(1)

            # 获取充电相关信息
            last_soc = {}
            charge_records = {}
            reservation_info = {}
            try:
                last_soc = await api.get_last_soc(cached_vin)
            except GeelyApiError:
                pass

            await asyncio.sleep(1)

            try:
                charge_records = await api.get_charge_records(cached_vin, page=1, page_size=5)
            except GeelyApiError:
                pass

            await asyncio.sleep(1)

            try:
                reservation_info = await api.get_reservation_info(cached_vin)
            except GeelyApiError:
                pass

            # 获取家用充电桩数据
            home_charger_list = []
            home_charger_status = {}
            home_charger_charging_data = {}
            home_charger_last_record = {}

            await asyncio.sleep(1)

            _LOGGER.warning("[诊断] 准备获取家用充电桩数据...")
            try:
                home_charger_list = await api.get_home_charger_list()
                _LOGGER.warning("[诊断] 家用充电桩列表结果: %s", home_charger_list)
            except GeelyApiError as err:
                _LOGGER.warning("[诊断] 获取家用充电桩列表失败: %s", err)

            if home_charger_list:
                # 充电桩列表已包含 status 字段，先用它作为初始状态
                home_charger_status = home_charger_list[0]
                _LOGGER.warning("[诊断] 使用充电桩列表数据作为初始状态: %s", home_charger_status)

                await asyncio.sleep(1)
                try:
                    # 获取更详细的状态（可能包含更多字段）
                    detailed_status = await api.get_home_charger_status()
                    _LOGGER.warning("[诊断] 充电桩详细状态: %s", detailed_status)
                    if detailed_status:
                        home_charger_status = {**home_charger_status, **detailed_status}
                except GeelyApiError as err:
                    _LOGGER.warning("[诊断] 获取充电桩详细状态失败（使用列表数据）: %s", err)

                await asyncio.sleep(1)
                try:
                    # 获取详情，合并到状态中（详情包含 isOnline 等更多字段）
                    detail = await api.get_home_charger_detail()
                    _LOGGER.info("家用充电桩详情: %s", detail)
                    if detail:
                        home_charger_status = {**home_charger_status, **detail}
                        _LOGGER.info("合并后充电桩状态: %s", home_charger_status)
                except GeelyApiError as err:
                    _LOGGER.warning("获取家用充电桩详情失败: %s", err)

                await asyncio.sleep(1)
                try:
                    home_charger_charging_data = await api.get_home_charger_charging_data()
                    _LOGGER.info("家用充电桩充电数据: %s", home_charger_charging_data)
                except GeelyApiError as err:
                    _LOGGER.info("获取家用充电桩充电数据失败（可能未在充电）: %s", err)

                await asyncio.sleep(1)
                try:
                    home_charger_last_record = await api.get_home_charger_last_record()
                    _LOGGER.info("家用充电桩最后记录: %s", home_charger_last_record)
                except GeelyApiError as err:
                    _LOGGER.info("获取家用充电桩最后记录失败: %s", err)
            else:
                _LOGGER.info("未检测到家用充电桩")

            return {
                "vehicle_info": cached_vehicle_info,
                "vehicle_status": vehicle_status,
                "switch_status": switch_status,
                "last_soc": last_soc,
                "charge_records": charge_records,
                "reservation_info": reservation_info,
                "home_charger_list": home_charger_list,
                "home_charger_status": home_charger_status,
                "home_charger_charging_data": home_charger_charging_data,
                "home_charger_last_record": home_charger_last_record,
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
        entry_data = hass.data[DOMAIN].pop(entry.entry_id)
        # 关闭独立 session
        await entry_data["api"].close()

    return unload_ok
