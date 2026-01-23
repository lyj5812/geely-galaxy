"""Constants for the Geely Galaxy integration."""

DOMAIN = "geely_galaxy"

# 配置键
CONF_REFRESH_TOKEN = "refresh_token"
CONF_DEVICE_SN = "device_sn"

# 默认值
DEFAULT_SCAN_INTERVAL = 60  # 60秒更新一次

# 车辆状态字段映射
VEHICLE_STATUS_FIELDS = {
    "basicVehicleStatus": {
        "distanceToEmptyOnBatteryOnly": {"name": "续航里程", "unit": "km", "icon": "mdi:road-variant"},
        "odometer": {"name": "总里程", "unit": "km", "icon": "mdi:counter"},
    },
    "vehicleBatteryStatus": {
        "chargeLevel": {"name": "电池电量", "unit": "%", "icon": "mdi:battery"},
        "timeToFullyCharged": {"name": "充满剩余时间", "unit": "min", "icon": "mdi:battery-clock"},
    },
    "vehicleEnvironmentStatus": {
        "interiorTemp": {"name": "车内温度", "unit": "°C", "icon": "mdi:thermometer"},
        "exteriorTemp": {"name": "车外温度", "unit": "°C", "icon": "mdi:thermometer"},
        "interiorPM25Level": {"name": "车内PM2.5", "unit": "μg/m³", "icon": "mdi:air-filter"},
    },
    "vehicleRunningStatus": {
        "averPowerConsumption": {"name": "平均能耗", "unit": "kWh/100km", "icon": "mdi:lightning-bolt"},
    },
    "vehicleDoorCoverStatus": {
        "doorLockStatusDriver": {"name": "车锁状态", "icon": "mdi:car-door-lock"},
    },
    "vehicleClimateStatus": {
        "preClimateActive": {"name": "空调状态", "icon": "mdi:air-conditioner"},
    },
}

# 开关状态字段映射
SWITCH_STATUS_FIELDS = {
    "vstdModeStatus": {"name": "哨兵模式", "icon": "mdi:shield-car"},
    "strangerModeActive": {"name": "陌生人预警", "icon": "mdi:account-alert"},
}
