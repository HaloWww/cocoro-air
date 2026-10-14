"""Configure or reauthenticate Cocoro Air."""
from __future__ import annotations

import httpx
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.helpers.httpx_client import create_async_httpx_client

from . import DOMAIN
from .api import CocoroAir, CocoroAirAuthError, CocoroAirConnectionError, USER_AGENT

STEP_USER_DATA_SCHEMA = vol.Schema({
    vol.Required("email"): str,
    vol.Required("password"): str,
    vol.Required("device_id"): str,
    vol.Required("model_name", default="KILS50"): str,
})


async def validate_input(hass: HomeAssistant, data):
    client = create_async_httpx_client(hass, timeout=30, headers={"User-Agent": USER_AGENT})
    try:
        api = CocoroAir(client, data["email"], data["password"], data["device_id"], data["model_name"])
        await api.login()
        await api.update()
    finally:
        await httpx.AsyncClient.aclose(client)
    return {"title": f"Cocoro Air {data['model_name']} ({data['device_id']})"}


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            if any(entry.data.get("device_id") == user_input["device_id"] for entry in self._async_current_entries()):
                return self.async_abort(reason="already_configured")
            try:
                info = await validate_input(self.hass, user_input)
            except CocoroAirAuthError:
                errors["base"] = "invalid_auth"
            except (httpx.HTTPError, CocoroAirConnectionError):
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(user_input["device_id"])
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=info["title"], data=user_input)
        return self.async_show_form(step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors)

    async def async_step_reauth(self, entry_data):
        self._reauth_data = dict(entry_data)
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        errors = {}
        if user_input is not None:
            data = dict(self._reauth_data, **user_input)
            try:
                await validate_input(self.hass, data)
            except CocoroAirAuthError:
                errors["base"] = "invalid_auth"
            except (httpx.HTTPError, CocoroAirConnectionError):
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(self._get_reauth_entry(), data=data)
        return self.async_show_form(step_id="reauth_confirm", data_schema=vol.Schema({
            vol.Required("email", default=self._reauth_data["email"]): str,
            vol.Required("password"): str,
        }), errors=errors)
