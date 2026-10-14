"""Configure, discover and update one Cocoro Air device per entry."""
from __future__ import annotations

import httpx
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.helpers.httpx_client import create_async_httpx_client

from . import DOMAIN
from .api import CocoroAir, CocoroAirAuthError, CocoroAirConnectionError, USER_AGENT

CREDENTIAL_SCHEMA = vol.Schema({vol.Required("email"): str, vol.Required("password"): str})


def _client(hass):
    return create_async_httpx_client(hass, auto_cleanup=False, timeout=30, headers={"User-Agent": USER_AGENT})


async def discover_devices(hass: HomeAssistant, credentials):
    client = _client(hass)
    try:
        api = CocoroAir(client, credentials["email"], credentials["password"], None, None)
        await api.login()
        return await api.async_query_devices()
    finally:
        await httpx.AsyncClient.aclose(client)


async def validate_input(hass: HomeAssistant, data):
    client = _client(hass)
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
            try:
                devices = await discover_devices(self.hass, user_input)
            except CocoroAirAuthError:
                errors["base"] = "invalid_auth"
            except (httpx.HTTPError, CocoroAirConnectionError):
                errors["base"] = "cannot_connect"
            else:
                if devices:
                    self._credentials = {key: user_input[key] for key in ("email", "password")}
                    self._devices = devices
                    return await self.async_step_device()
                errors["base"] = "no_devices_found"
        return self.async_show_form(step_id="user", data_schema=CREDENTIAL_SCHEMA, errors=errors)

    async def async_step_device(self, user_input=None):
        errors = {}
        choices = {device["device_id"]: device["label"] for device in self._devices}
        if user_input is not None:
            device = next((d for d in self._devices if d["device_id"] == user_input["device_id"]), None)
            if device is None:
                errors["base"] = "device_not_found"
            elif any(entry.data.get("device_id") == device["device_id"] for entry in self._async_current_entries()):
                return self.async_abort(reason="already_configured")
            else:
                data = dict(self._credentials, device_id=device["device_id"], model_name=device["model_name"])
                try:
                    info = await validate_input(self.hass, data)
                except CocoroAirAuthError:
                    errors["base"] = "invalid_auth"
                except (httpx.HTTPError, CocoroAirConnectionError):
                    errors["base"] = "cannot_connect"
                else:
                    await self.async_set_unique_id(data["device_id"])
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(title=info["title"], data=data)
        return self.async_show_form(step_id="device", data_schema=vol.Schema({
            vol.Required("device_id"): vol.In(choices)}), errors=errors)

    async def async_step_reauth(self, entry_data):
        self._reauth_data = dict(entry_data)
        return await self.async_step_reauth_confirm()

    async def _async_update_credentials(self, entry, user_input):
        devices = await discover_devices(self.hass, user_input)
        device = next((d for d in devices if d["device_id"] == entry.data["device_id"]), None)
        if device is None:
            return "device_not_found"
        data = dict(entry.data, email=user_input["email"], password=user_input["password"], model_name=device["model_name"])
        await validate_input(self.hass, data)
        return data

    async def _async_credentials_form(self, entry, step_id, user_input):
        errors = {}
        if user_input is not None:
            try:
                result = await self._async_update_credentials(entry, user_input)
            except CocoroAirAuthError:
                errors["base"] = "invalid_auth"
            except (httpx.HTTPError, CocoroAirConnectionError):
                errors["base"] = "cannot_connect"
            else:
                if isinstance(result, str):
                    errors["base"] = result
                else:
                    # Preserve device_id and existing entities, rather than rebinding automations.
                    return self.async_update_reload_and_abort(entry, data=result, unique_id=entry.data["device_id"])
        return self.async_show_form(step_id=step_id, data_schema=vol.Schema({
            vol.Required("email", default=entry.data["email"]): str,
            vol.Required("password"): str,
        }), errors=errors)

    async def async_step_reauth_confirm(self, user_input=None):
        return await self._async_credentials_form(self._get_reauth_entry(), "reauth_confirm", user_input)

    async def async_step_reconfigure(self, user_input=None):
        return await self._async_credentials_form(self._get_reconfigure_entry(), "reconfigure", user_input)
