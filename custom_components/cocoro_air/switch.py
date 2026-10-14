"""The existing Cocoro Air humidity mode switch."""
import logging

import httpx
from homeassistant.components.switch import SwitchEntity, SwitchDeviceClass
from homeassistant.exceptions import HomeAssistantError

from . import DOMAIN
from .api import CocoroAirAuthError, CocoroAirConnectionError

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, config_entry, async_add_entities):
    api = hass.data[DOMAIN][config_entry.entry_id]["cocoro_air_api"]
    async_add_entities([CocoroAirHumidifier(api)], True)


class CocoroAirHumidifier(SwitchEntity):
    _attr_has_entity_name = True
    _attr_name = "Humidity Mode"
    _attr_device_class = SwitchDeviceClass.SWITCH

    def __init__(self, api):
        self._api = api
        self._attr_unique_id = f"{api.device_id}_humidity_mode"
        self._attr_device_info = api.device_info
        self._attr_is_on = None

    @property
    def available(self):
        return self._api.available and self._attr_is_on is not None

    @property
    def icon(self):
        return "mdi:air-humidifier" if self._attr_is_on else "mdi:air-humidifier-off"

    async def async_update(self):
        try:
            self._attr_is_on = self._api.get_sensor_data(await self._api.update())["humidity_mode"]
        except CocoroAirAuthError:
            self._attr_is_on = None
            if self.registry_entry:
                entry = self.hass.config_entries.async_get_entry(self.registry_entry.config_entry_id)
                if entry:
                    entry.async_start_reauth(self.hass)
        except (httpx.HTTPError, CocoroAirConnectionError) as err:
            self._attr_is_on = None
            _LOGGER.debug("Cocoro Air switch update failed: %s", err)

    async def _async_set_mode(self, mode):
        try:
            await self._api.set_humidity_mode(mode)
        except (httpx.HTTPError, CocoroAirAuthError, CocoroAirConnectionError) as err:
            raise HomeAssistantError(f"Could not set Cocoro Air humidity mode: {err}") from err
        self._attr_is_on = mode == "on"
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs):
        await self._async_set_mode("on")

    async def async_turn_off(self, **kwargs):
        await self._async_set_mode("off")
