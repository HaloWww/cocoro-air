"""Refresh every existing entity after a confirmed device command."""
from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect


def update_signal(api):
    return f"cocoro_air_{api.device_id}_updated"


class CocoroAirRefreshMixin:
    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(async_dispatcher_connect(self.hass, update_signal(self._api), self._schedule_refresh))

    @callback
    def _schedule_refresh(self):
        self.async_schedule_update_ha_state(force_refresh=True)
