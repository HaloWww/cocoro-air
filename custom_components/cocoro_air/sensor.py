"""Sensors for Cocoro Air; keep existing entity IDs for automations."""
import logging

import httpx
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorEntityDescription, SensorStateClass
from homeassistant.const import CONCENTRATION_MICROGRAMS_PER_CUBIC_METER, PERCENTAGE, UnitOfTemperature, UnitOfTime
from homeassistant.helpers.entity import EntityCategory

from . import DOMAIN
from .api import CocoroAirAuthError, CocoroAirConnectionError
from .const import FAN_SPEED_OPTIONS, FILTER_FIELDS, MODE_OPTIONS, PM25_STATUS_OPTIONS
from .entity import CocoroAirRefreshMixin

_LOGGER = logging.getLogger(__name__)

EXTRA_SENSORS = (
    SensorEntityDescription(key="fan_speed", translation_key="fan_speed", device_class=SensorDeviceClass.ENUM,
                            options=list(FAN_SPEED_OPTIONS), icon="mdi:fan"),
    SensorEntityDescription(key="operation_mode", translation_key="operation_mode", device_class=SensorDeviceClass.ENUM,
                            options=list(MODE_OPTIONS), icon="mdi:air-purifier"),
    SensorEntityDescription(key="pm25", translation_key="pm25", device_class=SensorDeviceClass.PM25,
                            native_unit_of_measurement=CONCENTRATION_MICROGRAMS_PER_CUBIC_METER, state_class=SensorStateClass.MEASUREMENT),
    SensorEntityDescription(key="pm25_status", translation_key="pm25_status", device_class=SensorDeviceClass.ENUM,
                            options=list(PM25_STATUS_OPTIONS), entity_category=EntityCategory.DIAGNOSTIC),
    SensorEntityDescription(key="odor_level", translation_key="odor_level", icon="mdi:scent", state_class=SensorStateClass.MEASUREMENT),
    SensorEntityDescription(key="dust_level", translation_key="dust_level", icon="mdi:blur", state_class=SensorStateClass.MEASUREMENT),
    SensorEntityDescription(key="air_quality_level", translation_key="air_quality_level", icon="mdi:air-filter", state_class=SensorStateClass.MEASUREMENT),
    SensorEntityDescription(key="operating_time", translation_key="operating_time", device_class=SensorDeviceClass.DURATION,
                            native_unit_of_measurement=UnitOfTime.HOURS, state_class=SensorStateClass.TOTAL_INCREASING,
                            entity_category=EntityCategory.DIAGNOSTIC),
    *(SensorEntityDescription(key=key, translation_key=key, native_unit_of_measurement=PERCENTAGE,
                             icon="mdi:air-filter", state_class=SensorStateClass.MEASUREMENT) for key in FILTER_FIELDS),
)


async def async_setup_entry(hass, entry, async_add_entities):
    api = hass.data[DOMAIN][entry.entry_id]["cocoro_air_api"]
    entities = [CocoroAirTemperatureSensor(api), CocoroAirHumiditySensor(api), CocoroAirWaterTankSensor(api)]
    for description in EXTRA_SENSORS:
        if description.key in ("pm25", "pm25_status") and api.spec.get("hasPM25Sensor") is False:
            continue
        if description.key == "dust_level" and api.spec.get("hasDustSensor") is False:
            continue
        if description.key in FILTER_FIELDS and not api.spec.get(FILTER_FIELDS[description.key][1]):
            continue
        entities.append(CocoroAirAdditionalSensor(api, description))
    async_add_entities(entities, True)


class CocoroAirSensor(CocoroAirRefreshMixin, SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, api, key):
        self._api = api
        self._key = key
        self._attr_unique_id = f"{api.device_id}_{key}"
        self._attr_device_info = api.device_info

    @property
    def available(self):
        return self._api.available and self._attr_native_value is not None

    @property
    def extra_state_attributes(self):
        if self._key in ("temperature", "humidity"):
            attributes = {"measurement_status": self._api.get_sensor_data()[f"{self._key}_status"]}
            if self._key == "humidity":
                low, high = (24, 76) if self._api.spec.get("seriiesCode", 0) >= 6 else (6, 100)
                attributes.update(lower_limit=low, upper_limit=high)
            return attributes
        return None

    async def async_update(self):
        try:
            data = self._api.get_sensor_data(await self._api.update())
            value = data[self._key]
            self._attr_native_value = ("on" if value else "off") if self._key == "water_tank" and value is not None else value
        except CocoroAirAuthError:
            self._attr_native_value = None
            if self.registry_entry:
                entry = self.hass.config_entries.async_get_entry(self.registry_entry.config_entry_id)
                if entry:
                    entry.async_start_reauth(self.hass)
        except (httpx.HTTPError, CocoroAirConnectionError) as err:
            self._attr_native_value = None
            _LOGGER.debug("Cocoro Air sensor update failed: %s", err)


class CocoroAirTemperatureSensor(CocoroAirSensor):
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_name = "Temperature"
    _attr_icon = "mdi:thermometer"

    def __init__(self, api):
        super().__init__(api, "temperature")


class CocoroAirHumiditySensor(CocoroAirSensor):
    _attr_device_class = SensorDeviceClass.HUMIDITY
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_name = "Humidity"
    _attr_icon = "mdi:water-percent"

    def __init__(self, api):
        super().__init__(api, "humidity")


class CocoroAirWaterTankSensor(CocoroAirSensor):
    # Previously a BinarySensorEntity was registered under the sensor platform.
    # Retain its sensor entity ID and on/off values for existing dashboards.
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["on", "off"]
    _attr_name = "Water tank"
    _attr_icon = "mdi:water"

    def __init__(self, api):
        super().__init__(api, "water_tank")


class CocoroAirAdditionalSensor(CocoroAirSensor):
    """Read-only extra measurements and settings."""

    def __init__(self, api, description):
        self.entity_description = description
        self._attr_suggested_object_id = f"cocoro_air_{api.model_name.lower()}_{description.key}"
        super().__init__(api, description.key)

    @property
    def extra_state_attributes(self):
        if self._key == "fan_speed":
            return {"epc": "0xA0", "raw_value": self._api.cache.get("0xA0")}
        if self._key == "operation_mode":
            return {"raw_value": self._api.cache.get("k3", {}).get("s1")}
        if self._key in ("pm25", "pm25_status"):
            return {"measurement_status": self._api.get_sensor_data()["pm25_status"]}
        if self._key in FILTER_FIELDS:
            supply = self._api.supplies.get(FILTER_FIELDS[self._key][2], {})
            return {"filter_model": supply.get("0x01")}
        return None
