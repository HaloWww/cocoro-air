"""The Cocoro Air integration."""
import httpx

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.httpx_client import create_async_httpx_client

from .api import CocoroAir, CocoroAirAuthError, CocoroAirConnectionError, USER_AGENT

DOMAIN = "cocoro_air"
PLATFORMS = [Platform.SENSOR, Platform.SWITCH]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})
    client = create_async_httpx_client(hass, timeout=30, headers={"User-Agent": USER_AGENT})
    api = CocoroAir(client, entry.data["email"], entry.data["password"],
                    entry.data["device_id"], entry.data["model_name"])
    api.device_info = DeviceInfo(identifiers={(DOMAIN, api.device_id)},
                                name=f"Cocoro Air {api.model_name}", manufacturer="Sharp", model=api.model_name)
    try:
        await api.login()
        await api.update()
    except CocoroAirAuthError as err:
        await httpx.AsyncClient.aclose(client)
        raise ConfigEntryAuthFailed(str(err)) from err
    except (httpx.HTTPError, CocoroAirConnectionError) as err:
        await httpx.AsyncClient.aclose(client)
        raise ConfigEntryNotReady(f"Could not connect to Cocoro Air: {err}") from err
    hass.data[DOMAIN][entry.entry_id] = {"cocoro_air_api": api}
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        await httpx.AsyncClient.aclose(client)
        raise
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        # This integration owns the isolated client, so close it directly.
        # HA wraps instance aclose() to warn against closing its shared client.
        await httpx.AsyncClient.aclose(hass.data[DOMAIN].pop(entry.entry_id)["cocoro_air_api"].client)
    return unload_ok
