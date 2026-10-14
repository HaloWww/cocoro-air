"""Read-only running and maintenance states."""
import logging

import httpx
from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity, BinarySensorEntityDescription
from homeassistant.helpers.entity import EntityCategory

from . import DOMAIN
from .api import CocoroAirAuthError, CocoroAirConnectionError
from .entity import CocoroAirRefreshMixin

_LOGGER = logging.getLogger(__name__)
DESCRIPTIONS = (
    BinarySensorEntityDescription(key="running", translation_key="running", device_class=BinarySensorDeviceClass.RUNNING),
    BinarySensorEntityDescription(key="humidifier_filter_maintenance", translation_key="humidifier_filter_maintenance",
                                  device_class=BinarySensorDeviceClass.PROBLEM, entity_category=EntityCategory.DIAGNOSTIC),
    BinarySensorEntityDescription(key="dustbox_maintenance", translation_key="dustbox_maintenance",
                                  device_class=BinarySensorDeviceClass.PROBLEM, entity_category=EntityCategory.DIAGNOSTIC),
)


async def async_setup_entry(hass, entry, async_add_entities):
    api = hass.data[DOMAIN][entry.entry_id]["cocoro_air_api"]
    async_add_entities([CocoroAirBinarySensor(api, description) for description in DESCRIPTIONS
                        if not (description.key == "dustbox_maintenance" and api.spec.get("hasCleaningMecha") is False)
                        and not (description.key == "humidifier_filter_maintenance" and api.spec.get("hasHumidFunc") is False)], True)


class CocoroAirBinarySensor(CocoroAirRefreshMixin, BinarySensorEntity):
    _attr_has_entity_name = True

    def __init__(self, api, description):
        self._api = api
        self.entity_description = description
        self._attr_suggested_object_id = f"cocoro_air_{api.model_name.lower()}_{description.key}"
        self._attr_unique_id = f"{api.device_id}_{description.key}"
        self._attr_device_info = api.device_info
        self._attr_is_on = None

    @property
    def available(self):
        return self._api.available and self._attr_is_on is not None

    async def async_update(self):
        try:
            self._attr_is_on = self._api.get_sensor_data(await self._api.update())[self.entity_description.key]
        except CocoroAirAuthError:
            self._attr_is_on = None
            if self.registry_entry:
                entry = self.hass.config_entries.async_get_entry(self.registry_entry.config_entry_id)
                if entry:
                    entry.async_start_reauth(self.hass)
        except (httpx.HTTPError, CocoroAirConnectionError) as err:
            self._attr_is_on = None
            _LOGGER.debug("Cocoro Air state update failed: %s", err)
