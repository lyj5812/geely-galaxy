"""Sensor platform for Geely Galaxy."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    UnitOfLength,
    UnitOfTemperature,
    UnitOfEnergy,
    UnitOfPower,
)
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
    """Set up Geely Galaxy sensors based on a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    entities: list[GeelyBaseSensor] = [
        # 基础信息
        GeelyVehicleModelSensor(coordinator, entry),
        GeelyVinSensor(coordinator, entry),
        # 电池和续航
        GeelyBatteryLevelSensor(coordinator, entry),
        GeelyRangeSensor(coordinator, entry),
        GeelyOdometerSensor(coordinator, entry),
        GeelyChargeTimeSensor(coordinator, entry),
        # 温度和环境
        GeelyInteriorTempSensor(coordinator, entry),
        GeelyExteriorTempSensor(coordinator, entry),
        GeelyPM25Sensor(coordinator, entry),
        GeelyPowerConsumptionSensor(coordinator, entry),
        # 状态
        GeelyDoorLockSensor(coordinator, entry),
        GeelyAcStatusSensor(coordinator, entry),
        GeelySentryModeSensor(coordinator, entry),
        # 充电相关
        GeelyChargingStatusSensor(coordinator, entry),
        GeelyChargingPowerSensor(coordinator, entry),
        GeelyChargingVoltageSensor(coordinator, entry),
        GeelyChargingCurrentSensor(coordinator, entry),
        GeelyLastChargeSocSensor(coordinator, entry),
        GeelyChargeReservationSensor(coordinator, entry),
        GeelyLastChargeEnergySensor(coordinator, entry),
    ]

    # 如果有家用充电桩，添加充电记录传感器
    if coordinator.data and coordinator.data.get("home_charger_list"):
        entities.append(HomeChargerRecordsSensor(coordinator, entry))

    async_add_entities(entities)


class GeelyBaseSensor(CoordinatorEntity, SensorEntity):
    """Base class for Geely Galaxy sensors."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._entry = entry

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
    def vehicle_status(self) -> dict[str, Any]:
        """Get vehicle status from coordinator data."""
        if not self.coordinator.data:
            return {}
        return self.coordinator.data.get("vehicle_status", {})

    @property
    def switch_status(self) -> dict[str, Any]:
        """Get switch status from coordinator data."""
        if not self.coordinator.data:
            return {}
        return self.coordinator.data.get("switch_status", {})

    @property
    def vehicle_info(self) -> dict[str, Any]:
        """Get vehicle info from coordinator data."""
        if not self.coordinator.data:
            return {}
        return self.coordinator.data.get("vehicle_info", {})

    @property
    def last_soc(self) -> dict[str, Any]:
        """Get last SOC info from coordinator data."""
        if not self.coordinator.data:
            return {}
        return self.coordinator.data.get("last_soc", {})

    @property
    def charge_records(self) -> dict[str, Any]:
        """Get charge records from coordinator data."""
        if not self.coordinator.data:
            return {}
        return self.coordinator.data.get("charge_records", {})

    @property
    def reservation_info(self) -> dict[str, Any]:
        """Get reservation info from coordinator data."""
        if not self.coordinator.data:
            return {}
        return self.coordinator.data.get("reservation_info", {})

    @property
    def home_charger_list(self) -> list[dict[str, Any]]:
        """Get home charger list from coordinator data."""
        if not self.coordinator.data:
            return []
        return self.coordinator.data.get("home_charger_list", [])

    @property
    def home_charger_status(self) -> dict[str, Any]:
        """Get home charger status from coordinator data."""
        if not self.coordinator.data:
            return {}
        return self.coordinator.data.get("home_charger_status", {})

    @property
    def home_charger_charging_data(self) -> dict[str, Any]:
        """Get home charger charging data from coordinator data."""
        if not self.coordinator.data:
            return {}
        return self.coordinator.data.get("home_charger_charging_data", {})

    @property
    def home_charger_last_record(self) -> dict[str, Any]:
        """Get home charger last record from coordinator data."""
        if not self.coordinator.data:
            return {}
        return self.coordinator.data.get("home_charger_last_record", {})

    @property
    def home_charger_records(self) -> list[dict[str, Any]]:
        """Get home charger records from coordinator data."""
        if not self.coordinator.data:
            return []
        return self.coordinator.data.get("home_charger_records", [])


class GeelyVehicleModelSensor(GeelyBaseSensor):
    """Sensor for vehicle model."""

    _attr_name = "车型"
    _attr_icon = "mdi:car"

    def __init__(self, coordinator: DataUpdateCoordinator, entry: ConfigEntry) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_model"

    @property
    def native_value(self) -> str | None:
        """Return the vehicle model."""
        return self.vehicle_info.get("seriesNameVs")


class GeelyVinSensor(GeelyBaseSensor):
    """Sensor for VIN."""

    _attr_name = "车架号"
    _attr_icon = "mdi:identifier"

    def __init__(self, coordinator: DataUpdateCoordinator, entry: ConfigEntry) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_vin"

    @property
    def native_value(self) -> str | None:
        """Return the VIN."""
        return self.vehicle_info.get("vin")


class GeelyBatteryLevelSensor(GeelyBaseSensor):
    """Sensor for battery level."""

    _attr_name = "电池电量"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:battery"

    def __init__(self, coordinator: DataUpdateCoordinator, entry: ConfigEntry) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_battery_level"

    @property
    def native_value(self) -> int | None:
        """Return the battery level."""
        battery_status = self.vehicle_status.get("vehicleBatteryStatus", {})
        level = battery_status.get("chargeLevel")
        if level is not None:
            return int(level)
        return None

    @property
    def icon(self) -> str:
        """Return the icon based on battery level."""
        level = self.native_value
        if level is None:
            return "mdi:battery-unknown"
        if level >= 90:
            return "mdi:battery"
        if level >= 70:
            return "mdi:battery-80"
        if level >= 50:
            return "mdi:battery-60"
        if level >= 30:
            return "mdi:battery-40"
        if level >= 10:
            return "mdi:battery-20"
        return "mdi:battery-alert"


class GeelyRangeSensor(GeelyBaseSensor):
    """Sensor for remaining range."""

    _attr_name = "续航里程"
    _attr_native_unit_of_measurement = UnitOfLength.KILOMETERS
    _attr_device_class = SensorDeviceClass.DISTANCE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:road-variant"

    def __init__(self, coordinator: DataUpdateCoordinator, entry: ConfigEntry) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_range"

    @property
    def native_value(self) -> int | None:
        """Return the remaining range."""
        basic_status = self.vehicle_status.get("basicVehicleStatus", {})
        range_value = basic_status.get("distanceToEmptyOnBatteryOnly")
        if range_value is not None:
            return int(range_value)
        return None


class GeelyOdometerSensor(GeelyBaseSensor):
    """Sensor for odometer."""

    _attr_name = "总里程"
    _attr_native_unit_of_measurement = UnitOfLength.KILOMETERS
    _attr_device_class = SensorDeviceClass.DISTANCE
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_icon = "mdi:counter"

    def __init__(self, coordinator: DataUpdateCoordinator, entry: ConfigEntry) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_odometer"

    @property
    def native_value(self) -> float | None:
        """Return the odometer reading."""
        basic_status = self.vehicle_status.get("basicVehicleStatus", {})
        odometer = basic_status.get("odometer")
        if odometer is not None:
            return round(float(odometer), 1)
        return None


class GeelyChargeTimeSensor(GeelyBaseSensor):
    """Sensor for time to fully charged."""

    _attr_name = "充满剩余时间"
    _attr_native_unit_of_measurement = "min"
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_icon = "mdi:battery-clock"

    def __init__(self, coordinator: DataUpdateCoordinator, entry: ConfigEntry) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_charge_time"

    @property
    def native_value(self) -> int | None:
        """Return the time to fully charged."""
        battery_status = self.vehicle_status.get("vehicleBatteryStatus", {})
        time_value = battery_status.get("timeToFullyCharged")
        if time_value is not None:
            time_int = int(time_value)
            # 2047 表示未在充电
            if time_int == 2047:
                return None
            return time_int
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        battery_status = self.vehicle_status.get("vehicleBatteryStatus", {})
        time_value = battery_status.get("timeToFullyCharged")
        if time_value is not None and int(time_value) == 2047:
            return {"charging": False}
        return {"charging": True}


class GeelyInteriorTempSensor(GeelyBaseSensor):
    """Sensor for interior temperature."""

    _attr_name = "车内温度"
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:thermometer"

    def __init__(self, coordinator: DataUpdateCoordinator, entry: ConfigEntry) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_interior_temp"

    @property
    def native_value(self) -> float | None:
        """Return the interior temperature."""
        env_status = self.vehicle_status.get("vehicleEnvironmentStatus", {})
        temp = env_status.get("interiorTemp")
        if temp is not None:
            return float(temp)
        return None


class GeelyExteriorTempSensor(GeelyBaseSensor):
    """Sensor for exterior temperature."""

    _attr_name = "车外温度"
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:thermometer"

    def __init__(self, coordinator: DataUpdateCoordinator, entry: ConfigEntry) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_exterior_temp"

    @property
    def native_value(self) -> float | None:
        """Return the exterior temperature."""
        env_status = self.vehicle_status.get("vehicleEnvironmentStatus", {})
        temp = env_status.get("exteriorTemp")
        if temp is not None:
            return float(temp)
        return None


class GeelyPM25Sensor(GeelyBaseSensor):
    """Sensor for interior PM2.5."""

    _attr_name = "车内PM2.5"
    _attr_native_unit_of_measurement = "μg/m³"
    _attr_device_class = SensorDeviceClass.PM25
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:air-filter"

    def __init__(self, coordinator: DataUpdateCoordinator, entry: ConfigEntry) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_pm25"

    @property
    def native_value(self) -> int | None:
        """Return the PM2.5 level."""
        env_status = self.vehicle_status.get("vehicleEnvironmentStatus", {})
        pm25 = env_status.get("interiorPM25Level")
        if pm25 is not None:
            return int(pm25)
        return None


class GeelyPowerConsumptionSensor(GeelyBaseSensor):
    """Sensor for average power consumption."""

    _attr_name = "平均能耗"
    _attr_native_unit_of_measurement = "kWh/100km"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:lightning-bolt"

    def __init__(self, coordinator: DataUpdateCoordinator, entry: ConfigEntry) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_power_consumption"

    @property
    def native_value(self) -> float | None:
        """Return the average power consumption."""
        running_status = self.vehicle_status.get("vehicleRunningStatus", {})
        consumption = running_status.get("averPowerConsumption")
        if consumption is not None:
            return round(float(consumption), 1)
        return None


class GeelyDoorLockSensor(GeelyBaseSensor):
    """Sensor for door lock status."""

    _attr_name = "车锁状态"
    _attr_icon = "mdi:car-door-lock"

    def __init__(self, coordinator: DataUpdateCoordinator, entry: ConfigEntry) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_door_lock"

    @property
    def native_value(self) -> str | None:
        """Return the door lock status."""
        door_status = self.vehicle_status.get("vehicleDoorCoverStatus", {})
        lock_status = door_status.get("doorLockStatusDriver")
        if lock_status == "2":
            return "已锁定"
        elif lock_status == "1":
            return "已解锁"
        return None

    @property
    def icon(self) -> str:
        """Return icon based on lock status."""
        if self.native_value == "已锁定":
            return "mdi:car-door-lock"
        return "mdi:car-door"


class GeelyAcStatusSensor(GeelyBaseSensor):
    """Sensor for AC status."""

    _attr_name = "空调状态"
    _attr_icon = "mdi:air-conditioner"

    def __init__(self, coordinator: DataUpdateCoordinator, entry: ConfigEntry) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_ac_status"

    @property
    def native_value(self) -> str | None:
        """Return the AC status."""
        climate_status = self.vehicle_status.get("vehicleClimateStatus", {})
        ac_active = climate_status.get("preClimateActive")
        if ac_active is True:
            return "开启"
        elif ac_active is False:
            return "关闭"
        return None


class GeelySentryModeSensor(GeelyBaseSensor):
    """Sensor for sentry mode status."""

    _attr_name = "哨兵模式"
    _attr_icon = "mdi:shield-car"

    def __init__(self, coordinator: DataUpdateCoordinator, entry: ConfigEntry) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_sentry_mode"

    @property
    def native_value(self) -> str | None:
        """Return the sentry mode status."""
        sentry_status = self.switch_status.get("vstdModeStatus")
        if sentry_status == "1":
            return "开启"
        elif sentry_status == "0":
            return "关闭"
        return None

    @property
    def icon(self) -> str:
        """Return icon based on sentry mode."""
        if self.native_value == "开启":
            return "mdi:shield-car"
        return "mdi:shield-off"


class GeelyChargingStatusSensor(GeelyBaseSensor):
    """Sensor for charging status."""

    _attr_name = "充电状态"
    _attr_icon = "mdi:ev-station"

    def __init__(self, coordinator: DataUpdateCoordinator, entry: ConfigEntry) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_charging_status"

    @property
    def native_value(self) -> str | None:
        """Return the charging status."""
        # 优先从家用充电桩状态获取
        charger_status = self.home_charger_status
        _LOGGER.warning("[诊断] 充电状态传感器读取 home_charger_status: %s", charger_status)
        if charger_status:
            # 充电桩状态字段是 status（0=空闲，1=充电中，等）
            status = charger_status.get("status")
            _LOGGER.warning("[诊断] 充电桩 status 字段值: %s (类型: %s)", status, type(status).__name__ if status is not None else "None")
            if status is not None:
                status_map = {
                    0: "空闲",
                    1: "已插枪",
                    2: "充电中",
                    4: "充电完成",
                    "0": "空闲",
                    "1": "已插枪",
                    "2": "充电中",
                    "4": "充电完成",
                }
                result = status_map.get(status, f"状态({status})")
                _LOGGER.warning("[诊断] 充电状态传感器返回: %s", result)
                return result

        # 回退到 last_soc 数据
        _LOGGER.warning("[诊断] charger_status 为空或无 status，尝试 last_soc")
        soc_data = self.last_soc
        charging_status = soc_data.get("chargingStatus") or soc_data.get("chargeStatus")
        if charging_status is not None:
            status_map = {
                0: "未充电",
                1: "充电中",
                2: "充电完成",
                3: "充电暂停",
                "0": "未充电",
                "1": "充电中",
                "2": "充电完成",
                "3": "充电暂停",
            }
            return status_map.get(charging_status, f"未知({charging_status})")
        _LOGGER.warning("[诊断] 充电状态传感器返回 None（无数据）")
        return None

    @property
    def icon(self) -> str:
        """Return icon based on charging status."""
        if self.native_value == "充电中":
            return "mdi:battery-charging"
        elif self.native_value == "充电完成":
            return "mdi:battery-check"
        elif self.native_value == "已插枪":
            return "mdi:ev-plug-type2"
        return "mdi:ev-station"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        attrs = {}
        # 家用充电桩数据
        charger_status = self.home_charger_status
        if charger_status:
            for key in ["pilingsCode", "name", "isOnline", "isOwner", "bleName"]:
                if key in charger_status and charger_status[key] is not None:
                    attrs[key] = charger_status[key]
        # last_soc 数据
        soc_data = self.last_soc
        if soc_data:
            if "soc" in soc_data:
                attrs["soc"] = soc_data["soc"]
            if "updateTime" in soc_data:
                attrs["update_time"] = soc_data["updateTime"]
        return attrs


class GeelyChargingPowerSensor(GeelyBaseSensor):
    """Sensor for charging power."""

    _attr_name = "充电功率"
    _attr_native_unit_of_measurement = UnitOfPower.KILO_WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:flash"

    def __init__(self, coordinator: DataUpdateCoordinator, entry: ConfigEntry) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_charging_power"

    @property
    def native_value(self) -> float | None:
        """Return the charging power."""
        # 优先从家用充电桩充电数据获取
        charging_data = self.home_charger_charging_data
        if charging_data:
            power = charging_data.get("power") or charging_data.get("chargingPower") or charging_data.get("realPower")
            if power is not None:
                return round(float(power), 2)

        # 也检查充电桩状态
        charger_status = self.home_charger_status
        if charger_status:
            power = charger_status.get("power") or charger_status.get("chargingPower")
            if power is not None:
                return round(float(power), 2)

        # 回退到 last_soc
        soc_data = self.last_soc
        power = soc_data.get("chargingPower") or soc_data.get("power")
        if power is not None:
            return round(float(power), 2)
        return None


class GeelyChargingVoltageSensor(GeelyBaseSensor):
    """Sensor for charging voltage."""

    _attr_name = "充电电压"
    _attr_native_unit_of_measurement = "V"
    _attr_device_class = SensorDeviceClass.VOLTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:lightning-bolt"

    def __init__(self, coordinator: DataUpdateCoordinator, entry: ConfigEntry) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_charging_voltage"

    @property
    def native_value(self) -> float | None:
        """Return the charging voltage."""
        # 优先从家用充电桩充电数据获取
        charging_data = self.home_charger_charging_data
        if charging_data:
            voltage = charging_data.get("voltage") or charging_data.get("chargingVoltage") or charging_data.get("realVoltage")
            if voltage is not None:
                return round(float(voltage), 1)

        # 也检查充电桩状态
        charger_status = self.home_charger_status
        if charger_status:
            voltage = charger_status.get("voltage") or charger_status.get("chargingVoltage")
            if voltage is not None:
                return round(float(voltage), 1)

        # 回退到 last_soc
        soc_data = self.last_soc
        voltage = soc_data.get("voltage") or soc_data.get("chargingVoltage")
        if voltage is not None:
            return round(float(voltage), 1)
        return None


class GeelyChargingCurrentSensor(GeelyBaseSensor):
    """Sensor for charging current."""

    _attr_name = "充电电流"
    _attr_native_unit_of_measurement = "A"
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:current-ac"

    def __init__(self, coordinator: DataUpdateCoordinator, entry: ConfigEntry) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_charging_current"

    @property
    def native_value(self) -> float | None:
        """Return the charging current."""
        # 优先从家用充电桩充电数据获取
        charging_data = self.home_charger_charging_data
        if charging_data:
            current = charging_data.get("current") or charging_data.get("chargingCurrent") or charging_data.get("realCurrent") or charging_data.get("currentA")
            if current is not None:
                return round(float(current), 1)

        # 也检查充电桩状态
        charger_status = self.home_charger_status
        if charger_status:
            current = charger_status.get("current") or charger_status.get("chargingCurrent") or charger_status.get("currentA")
            if current is not None:
                return round(float(current), 1)

        # 回退到 last_soc
        soc_data = self.last_soc
        current = soc_data.get("current") or soc_data.get("chargingCurrent")
        if current is not None:
            return round(float(current), 1)
        return None


class GeelyLastChargeSocSensor(GeelyBaseSensor):
    """Sensor for last charge SOC."""

    _attr_name = "最后充电电量"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:battery-charging-100"

    def __init__(self, coordinator: DataUpdateCoordinator, entry: ConfigEntry) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_last_charge_soc"

    @property
    def native_value(self) -> int | None:
        """Return the last charge SOC."""
        # 优先从家用充电桩最后记录获取
        last_record = self.home_charger_last_record
        if last_record:
            soc = last_record.get("endSoc") or last_record.get("soc") or last_record.get("chargeLevel")
            if soc is not None:
                return int(soc)

        # 回退到 last_soc
        soc_data = self.last_soc
        soc = soc_data.get("soc") or soc_data.get("chargeLevel")
        if soc is not None:
            return int(soc)
        return None


class GeelyChargeReservationSensor(GeelyBaseSensor):
    """Sensor for charge reservation status."""

    _attr_name = "预约充电"
    _attr_icon = "mdi:calendar-clock"

    def __init__(self, coordinator: DataUpdateCoordinator, entry: ConfigEntry) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_charge_reservation"

    @property
    def native_value(self) -> str | None:
        """Return the charge reservation status."""
        reservation = self.reservation_info
        if not reservation:
            return None

        enabled = reservation.get("enabled") or reservation.get("reservationEnabled")
        if enabled in (True, 1, "1"):
            start_time = reservation.get("startTime", "")
            end_time = reservation.get("endTime", "")
            if start_time and end_time:
                return f"已开启 ({start_time}-{end_time})"
            return "已开启"
        elif enabled in (False, 0, "0"):
            return "未开启"
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        reservation = self.reservation_info
        if not reservation:
            return {}

        attrs = {}
        for key in ["startTime", "endTime", "targetSoc", "departuretime", "departureEnabled"]:
            if key in reservation:
                attrs[key] = reservation[key]
        return attrs


class GeelyLastChargeEnergySensor(GeelyBaseSensor):
    """Sensor for last charge energy."""

    _attr_name = "上次充电电量"
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_icon = "mdi:battery-plus"

    def __init__(self, coordinator: DataUpdateCoordinator, entry: ConfigEntry) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_last_charge_energy"

    @property
    def native_value(self) -> float | None:
        """Return the last charge energy."""
        # 优先从家用充电桩最后记录获取
        last_record = self.home_charger_last_record
        if last_record:
            energy = last_record.get("chargeEnergy") or last_record.get("energy") or last_record.get("totalEnergy") or last_record.get("chargeCapacity")
            if energy is not None:
                return round(float(energy), 2)

        # 回退到车辆充电记录
        records = self.charge_records
        if not records:
            return None

        # 获取记录列表
        record_list = records.get("list") or records.get("records") or []
        if not record_list:
            return None

        # 取最近一条记录
        latest = record_list[0] if record_list else {}
        energy = latest.get("chargeEnergy") or latest.get("energy") or latest.get("totalEnergy")
        if energy is not None:
            return round(float(energy), 2)
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        attrs = {}

        # 优先从家用充电桩最后记录获取
        last_record = self.home_charger_last_record
        if last_record:
            for key in ["startTime", "endTime", "startSoc", "endSoc", "duration", "chargeType", "pilingsCode"]:
                if key in last_record and last_record[key]:
                    attrs[key] = last_record[key]
            if attrs:
                return attrs

        # 回退到车辆充电记录
        records = self.charge_records
        if not records:
            return {}

        record_list = records.get("list") or records.get("records") or []
        if not record_list:
            return {}

        latest = record_list[0] if record_list else {}
        for key in ["startTime", "endTime", "startSoc", "endSoc", "duration", "chargeType", "stationName"]:
            if key in latest and latest[key]:
                attrs[key] = latest[key]
        return attrs


class HomeChargerRecordsSensor(GeelyBaseSensor):
    """Sensor for home charger records."""

    _attr_name = "充电桩充电记录"
    _attr_icon = "mdi:clipboard-list"

    def __init__(self, coordinator: DataUpdateCoordinator, entry: ConfigEntry) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_charger_records"

    @property
    def device_info(self) -> dict[str, Any]:
        """Return device info for the home charger."""
        charger_status = self.home_charger_status
        piling_code = charger_status.get("pilingsCode", "unknown")
        charger_name = charger_status.get("pilingsName", "家用充电桩")

        return {
            "identifiers": {(DOMAIN, f"charger_{piling_code}")},
            "name": charger_name,
            "manufacturer": "吉利汽车",
            "model": "家用充电桩",
        }

    @property
    def native_value(self) -> int:
        """Return the number of charge records."""
        return len(self.home_charger_records)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes with full records list."""
        records = self.home_charger_records
        if not records:
            return {"records": []}

        # 格式化记录列表
        formatted_records = []
        for i, record in enumerate(records[:10]):  # 最多显示10条
            formatted_record = {
                "序号": i + 1,
            }
            # API 返回的字段映射
            field_map = {
                "startTime": "开始时间",
                "endTime": "结束时间",
                "degree": "充电电量(kWh)",
                "chargeTypeDesc": "充电类型",
                "chargerName": "充电者",
            }
            for key, label in field_map.items():
                if key in record and record[key] is not None:
                    formatted_record[label] = record[key]

            formatted_records.append(formatted_record)

        # 统计信息
        total_energy = 0
        total_count = len(records)
        for record in records:
            # API 返回的充电度数字段是 degree
            energy = record.get("degree") or 0
            try:
                total_energy += float(energy)
            except (ValueError, TypeError):
                pass

        return {
            "total_count": total_count,
            "total_energy_kwh": round(total_energy, 2),
            "records": formatted_records,
        }
