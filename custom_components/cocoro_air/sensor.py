"""Sensors for Cocoro Air; keep existing entity IDs for automations."""
import logging

import httpx
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import PERCENTAGE, UnitOfTemperature

from . import DOMAIN
from .api import CocoroAirAuthError, CocoroAirConnectionError

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    api = hass.data[DOMAIN][entry.entry_id]["cocoro_air_api"]
    async_add_entities([CocoroAirTemperatureSensor(api), CocoroAirHumiditySensor(api),
                        CocoroAirWaterTankSensor(api)], True)


class CocoroAirSensor(SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, api, key):
        self._api = api
        self._key = key
        self._attr_unique_id = f"{api.device_id}_{key}"
        self._attr_device_info = api.device_info

    @property
    def available(self):
        return self._api.available and self._attr_native_value is not None

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
