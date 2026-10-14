"""Simple airflow presets implemented through Sharp operating modes."""
from homeassistant.components.select import SelectEntity
from . import DOMAIN
from .fan import CocoroAirControlEntity


async def async_setup_entry(hass, entry, async_add_entities):
    api = hass.data[DOMAIN][entry.entry_id]["cocoro_air_api"]
    async_add_entities([CocoroAirFanSpeedSelect(api)], True)


class CocoroAirFanSpeedSelect(CocoroAirControlEntity, SelectEntity):
    _attr_translation_key = "fan_speed_control"
    _attr_icon = "mdi:fan"
    _attr_options = ["auto", "quiet", "medium", "strong"]

    def __init__(self, api):
        super().__init__(api, "fan_speed_control")

    @property
    def available(self):
        return super().available and self._api.control_status["current_mode"] not in (
            "off", "settings", "demo", "humidifier_cleaning", "cleaning_stopped", "cleaning", "error")

    @property
    def current_option(self):
        mode = self._api.get_sensor_data()["operation_mode"]
        if mode in ("quiet", "medium", "strong"):
            return mode
        if self._api.get_sensor_data()["fan_speed"] == "auto":
            return "auto"
        return None

    async def async_select_option(self, option):
        if option == "auto":
            mode = "ai_auto" if "ai_auto" in self._api.control_modes else "auto"
        else:
            mode = option
        await self._async_control("mode", mode)
