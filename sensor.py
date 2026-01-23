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

    entities = [
        # 基础信息
        GeelyVehicleModelSensor(coordinator, entry),
        GeelyVinSensor(coordinator, entry),
        # 电池和续航
        GeelyBatteryLevelSensor(coordinator, entry),
        GeelyRangeSensor(coordinator, entry),
        GeelyOdometerSensor(coordinator, entry),
        GeelyChargeTimeSensor(coordinator, entry),
        # 温度
        GeelyInteriorTempSensor(coordinator, entry),
        GeelyExteriorTempSensor(coordinator, entry),
        # PM2.5
        GeelyPM25Sensor(coordinator, entry),
        # 能耗
        GeelyPowerConsumptionSensor(coordinator, entry),
        # 状态
        GeelyDoorLockSensor(coordinator, entry),
        GeelyAcStatusSensor(coordinator, entry),
        GeelySentryModeSensor(coordinator, entry),
    ]

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
