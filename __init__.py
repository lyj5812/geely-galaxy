"""
The Geely Galaxy integration.

基于 geely-galaxy-assistant 项目实现
https://github.com/suyunkai/geely-galaxy-assistant
"""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import GeelyGalaxyApi, GeelyApiError, GeelyAuthError
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

    # 记录初始 refresh_token，用于检测是否需要持久化新 token
    last_saved_refresh_token = entry.data.get(CONF_REFRESH_TOKEN, "")

    async def async_update_data():
        """Fetch data from API."""
        nonlocal cached_vin, cached_vehicle_info, last_saved_refresh_token
        try:
            # 检查 refresh_token 是否已更新，若有变化则持久化到配置
            current_refresh_token = api._refresh_token
            if (
                current_refresh_token
                and current_refresh_token != last_saved_refresh_token
            ):
                new_data = {**entry.data, CONF_REFRESH_TOKEN: current_refresh_token}
                hass.config_entries.async_update_entry(entry, data=new_data)
                last_saved_refresh_token = current_refresh_token
                _LOGGER.info("已自动保存新的 refreshToken 到配置")

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

            # 获取所有家用充电桩数据
            home_charger_list = []
            home_chargers = {}  # 按 pilingsCode 索引的充电桩数据

            await asyncio.sleep(1)

            try:
                home_charger_list = await api.get_home_charger_list()
                _LOGGER.debug("家用充电桩列表: %s 个", len(home_charger_list))
            except GeelyApiError as err:
                _LOGGER.warning("获取家用充电桩列表失败: %s", err)

            for charger_info in home_charger_list:
                pc = charger_info.get("pilingsCode")
                if not pc:
                    continue

                # 合并状态：列表基础信息 + 设备状态 + 详情
                status = charger_info.copy()

                await asyncio.sleep(1)
                try:
                    equip_status = await api.get_home_charger_status(pc)
                    if equip_status:
                        status.update(equip_status)
                except GeelyApiError:
                    pass

                await asyncio.sleep(1)
                try:
                    detail = await api.get_home_charger_detail(pc)
                    if detail:
                        status.update(detail)
                except GeelyApiError:
                    pass

                # 充电实时数据
                charging_data = {}
                await asyncio.sleep(1)
                try:
                    charging_data = await api.get_home_charger_charging_data(pc)
                except GeelyApiError:
                    pass

                # 最后充电记录
                last_record = {}
                await asyncio.sleep(1)
                try:
                    last_record = await api.get_home_charger_last_record(pc)
                except GeelyApiError:
                    pass

                # 充电记录列表
                records = []
                await asyncio.sleep(1)
                try:
                    records_result = await api.get_home_charger_records(pc, page=1, page_size=10)
                    if isinstance(records_result, list):
                        records = records_result
                    elif isinstance(records_result, dict):
                        records = records_result.get("list") or records_result.get("records") or []
                except GeelyApiError:
                    pass

                home_chargers[pc] = {
                    "info": charger_info,
                    "status": status,
                    "charging_data": charging_data,
                    "last_record": last_record,
                    "records": records,
                }
                _LOGGER.debug("充电桩 %s 数据已获取", pc)

            if not home_charger_list:
                _LOGGER.info("未检测到家用充电桩")

            # 向后兼容：主充电桩（isOwner=1）数据供车辆传感器使用
            primary = {}
            for cd in home_chargers.values():
                if cd["info"].get("isOwner") == 1:
                    primary = cd
                    break
            if not primary and home_chargers:
                primary = next(iter(home_chargers.values()))

            return {
                "vehicle_info": cached_vehicle_info,
                "vehicle_status": vehicle_status,
                "switch_status": switch_status,
                "last_soc": last_soc,
                "charge_records": charge_records,
                "reservation_info": reservation_info,
                "home_charger_list": home_charger_list,
                "home_chargers": home_chargers,
                # 向后兼容字段（主充电桩）
                "home_charger_status": primary.get("status", {}),
                "home_charger_charging_data": primary.get("charging_data", {}),
                "home_charger_last_record": primary.get("last_record", {}),
                "home_charger_records": primary.get("records", []),
            }
        except GeelyAuthError as err:
            # Token 失效（如 APP 重新登录导致），触发重新认证流程
            _LOGGER.error("认证失败，触发重新登录流程: %s", err)
            entry.async_start_reauth(hass)
            raise UpdateFailed(f"认证失败，请重新登录: {err}") from err
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

    # 注册服务（仅首次）
    if not hass.services.has_service(DOMAIN, "query_charger_records"):

        async def handle_query_charger_records(call: ServiceCall) -> ServiceResponse:
            """Handle query_charger_records service call."""
            piling_code = call.data.get("piling_code")
            page = call.data.get("page", 1)
            page_size = call.data.get("page_size", 20)

            # 获取任意一个已配置的 API 实例
            entry_data = next(iter(hass.data[DOMAIN].values()), None)
            if not entry_data or "api" not in entry_data:
                return {"error": "集成未配置"}

            api_instance: GeelyGalaxyApi = entry_data["api"]

            results = {}
            if piling_code:
                # 查询指定充电桩
                try:
                    records_result = await api_instance.get_home_charger_records(
                        piling_code, page=page, page_size=page_size
                    )
                    if isinstance(records_result, list):
                        records = records_result
                    elif isinstance(records_result, dict):
                        records = records_result.get("list") or records_result.get("records") or []
                    else:
                        records = []
                    results[piling_code] = records
                except GeelyApiError as err:
                    results[piling_code] = {"error": str(err)}
            else:
                # 查询所有充电桩
                coordinator = entry_data["coordinator"]
                home_chargers = (
                    coordinator.data.get("home_chargers", {}) if coordinator.data else {}
                )
                for pc in home_chargers:
                    try:
                        records_result = await api_instance.get_home_charger_records(
                            pc, page=page, page_size=page_size
                        )
                        if isinstance(records_result, list):
                            records = records_result
                        elif isinstance(records_result, dict):
                            records = records_result.get("list") or records_result.get("records") or []
                        else:
                            records = []
                        results[pc] = records
                    except GeelyApiError as err:
                        results[pc] = {"error": str(err)}

            return {"page": page, "page_size": page_size, "records": results}

        hass.services.async_register(
            DOMAIN,
            "query_charger_records",
            handle_query_charger_records,
            schema=vol.Schema({
                vol.Optional("piling_code"): str,
                vol.Optional("page", default=1): vol.Coerce(int),
                vol.Optional("page_size", default=20): vol.Coerce(int),
            }),
            supports_response=SupportsResponse.ONLY,
        )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        entry_data = hass.data[DOMAIN].pop(entry.entry_id)
        # 关闭独立 session
        await entry_data["api"].close()

    return unload_ok
