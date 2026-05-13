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

    # 注册 refresh token 更新回调：一旦服务端返回新 token 立即持久化
    def _on_refresh_token_updated(new_token: str) -> None:
        new_data = {**entry.data, CONF_REFRESH_TOKEN: new_token}
        hass.config_entries.async_update_entry(entry, data=new_data)
        _LOGGER.info("已即时保存新的 refreshToken 到配置")

    api._on_refresh_token_updated = _on_refresh_token_updated

    # 缓存车辆信息，避免每次更新都调用 get_vehicle_list
    cached_vin: str | None = None
    cached_vehicle_info: dict = {}

    # 充电桩分页：piling_code -> 当前页码
    charger_pages: dict[str, int] = {}

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

                # 充电记录列表（加载当前页）
                records = []
                records_total = 0
                page = charger_pages.get(pc, 1)
                await asyncio.sleep(1)
                try:
                    records_result = await api.get_home_charger_records(pc, page=page, page_size=10)
                    if isinstance(records_result, list):
                        records = records_result
                    elif isinstance(records_result, dict):
                        records = records_result.get("list") or records_result.get("records") or []
                        records_total = records_result.get("total", 0)
                except GeelyApiError:
                    pass

                home_chargers[pc] = {
                    "info": charger_info,
                    "status": status,
                    "charging_data": charging_data,
                    "last_record": last_record,
                    "records": records,
                    "records_page": page,
                    "records_total": records_total,
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

    # 翻页服务：直接更新记录数据，无需完整刷新
    if not hass.services.has_service(DOMAIN, "charger_next_page"):

        async def _fetch_and_update_records(coord, api_inst, piling_code, new_page):
            """实时获取指定页记录并更新 coordinator 数据。"""
            try:
                result = await api_inst.get_home_charger_records(
                    piling_code, page=new_page, page_size=10
                )
                if isinstance(result, list):
                    records = result
                    total = 0
                elif isinstance(result, dict):
                    records = result.get("list") or result.get("records") or []
                    total = result.get("total", 0)
                else:
                    records = []
                    total = 0
            except GeelyApiError as err:
                _LOGGER.warning("翻页获取记录失败 (page=%d): %s", new_page, err)
                return False

            if not records and new_page > 1:
                _LOGGER.debug("第 %d 页无记录，已到最后一页", new_page)
                return False

            if coord.data and piling_code in coord.data.get("home_chargers", {}):
                charger = coord.data["home_chargers"][piling_code]
                charger["records"] = records
                charger["records_page"] = new_page
                if total:
                    charger["records_total"] = total
                coord.async_set_updated_data(coord.data)
            return True

        async def handle_charger_next_page(call: ServiceCall) -> None:
            """翻到下一页充电记录。"""
            piling_code = call.data["piling_code"]
            entry_data = next(iter(hass.data[DOMAIN].values()), None)
            if not entry_data:
                _LOGGER.warning("翻页失败：集成未加载")
                return
            new_page = charger_pages.get(piling_code, 1) + 1
            ok = await _fetch_and_update_records(
                entry_data["coordinator"], entry_data["api"], piling_code, new_page
            )
            if ok:
                charger_pages[piling_code] = new_page

        async def handle_charger_prev_page(call: ServiceCall) -> None:
            """翻到上一页充电记录。"""
            piling_code = call.data["piling_code"]
            entry_data = next(iter(hass.data[DOMAIN].values()), None)
            if not entry_data:
                _LOGGER.warning("翻页失败：集成未加载")
                return
            new_page = max(charger_pages.get(piling_code, 1) - 1, 1)
            if new_page == charger_pages.get(piling_code, 1):
                return
            ok = await _fetch_and_update_records(
                entry_data["coordinator"], entry_data["api"], piling_code, new_page
            )
            if ok:
                charger_pages[piling_code] = new_page

        page_schema = vol.Schema({
            vol.Required("piling_code"): str,
        })
        hass.services.async_register(
            DOMAIN, "charger_next_page", handle_charger_next_page, schema=page_schema,
        )
        hass.services.async_register(
            DOMAIN, "charger_prev_page", handle_charger_prev_page, schema=page_schema,
        )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        entry_data = hass.data[DOMAIN].pop(entry.entry_id)
        # 关闭独立 session
        await entry_data["api"].close()

    return unload_ok
