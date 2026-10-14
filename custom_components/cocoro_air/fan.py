"""Power and model-supported operating presets, adapted from original b31e216."""
import httpx
from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_send

from . import DOMAIN
from .api import CocoroAirAuthError, CocoroAirConnectionError
from .entity import CocoroAirRefreshMixin, update_signal


async def async_setup_entry(hass, entry, async_add_entities):
    api = hass.data[DOMAIN][entry.entry_id]["cocoro_air_api"]
    async_add_entities([CocoroAirFan(api)], True)


class CocoroAirControlEntity(CocoroAirRefreshMixin):
    _attr_has_entity_name = True

    def __init__(self, api, key):
        self._api = api
        self._attr_unique_id = f"{api.device_id}_{key}"
        self._attr_device_info = api.device_info

    @property
    def available(self):
        return self._api.available and bool(self._api.spec) and self._api.control_status["current_mode"] is not None

    async def async_update(self):
        try:
            await self._api.update()
        except CocoroAirAuthError:
            if self.registry_entry:
                entry = self.hass.config_entries.async_get_entry(self.registry_entry.config_entry_id)
                if entry:
                    entry.async_start_reauth(self.hass)
        except (httpx.HTTPError, CocoroAirConnectionError):
            pass

    async def _async_control(self, action, value):
        try:
            await self._api.async_control(action, value)
        except (ValueError, httpx.HTTPError, CocoroAirAuthError, CocoroAirConnectionError) as err:
            raise HomeAssistantError(f"Cocoro Air control failed: {err}") from err
        self.async_write_ha_state()
        async_dispatcher_send(self.hass, update_signal(self._api))


class CocoroAirFan(CocoroAirControlEntity, FanEntity):
    _attr_translation_key = "air_purifier"
    _attr_supported_features = FanEntityFeature.PRESET_MODE
    for _feature in ("TURN_ON", "TURN_OFF"):
        _attr_supported_features |= getattr(FanEntityFeature, _feature, 0)

    def __init__(self, api):
        super().__init__(api, "fan")

    @property
    def is_on(self):
        return self._api.get_sensor_data()["running"]

    @property
    def preset_modes(self):
        return self._api.control_modes

    @property
    def preset_mode(self):
        mode = self._api.get_sensor_data()["operation_mode"]
        return mode if mode in self.preset_modes else None

    @property
    def extra_state_attributes(self):
        return {"operating_status": self._api.get_sensor_data()["operation_mode"]}

    async def async_set_preset_mode(self, preset_mode):
        await self._async_control("mode", preset_mode)

    async def async_turn_on(self, percentage=None, preset_mode=None, **kwargs):
        if percentage is not None:
            raise HomeAssistantError("Use a supported Sharp preset instead of percentage")
        if preset_mode is not None and preset_mode not in self.preset_modes:
            raise HomeAssistantError("Mode is not supported by this device")
        await self._async_control("power", True)
        if preset_mode is not None:
            await self.async_set_preset_mode(preset_mode)

    async def async_turn_off(self, **kwargs):
        await self._async_control("power", False)
