"""COCORO AIR client with an isolated, persistent cookie session."""
from __future__ import annotations

import asyncio
from html.parser import HTMLParser
import logging
import math
import time

import httpx

from .const import AI_MODES, FILTER_FIELDS, OPERATION_MODES
from .control import build_command, supported_modes

BASE = "https://cocoroplusapp.jp.sharp"
AUTH = "https://auth.cocoromembers.jp.sharp"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36"
_LOGGER = logging.getLogger(__name__)


class CocoroAirAuthError(Exception):
    """Credentials or interactive account confirmation are required."""


class CocoroAirConnectionError(Exception):
    """The service or device did not provide a usable response."""


class _StateParser(HTMLParser):
    state = None

    def handle_starttag(self, tag, attrs):
        fields = dict(attrs)
        if tag == "input" and fields.get("name") == "state":
            self.state = fields.get("value")


def _state(response):
    parser = _StateParser()
    parser.feed(response.text)
    if not parser.state:
        raise CocoroAirConnectionError("Login page is missing its state field")
    return parser.state


class CocoroAir:
    """One device and its own cookie jar; close the client on unload."""

    def __init__(self, client, email, password, device_id, model_name):
        self.client = client
        self.email = email
        self.password = password
        self.device_id = device_id
        self.model_name = model_name
        self.cache = {}
        self.available = False
        self.spec = {}
        self.supplies = {}
        self.firmware_version = None
        self._last_metadata_attempt = None
        self._lock = asyncio.Lock()
        self._last_attempt = None
        self._last_error = None

    async def login(self):
        """Follow Sharp's Auth0 identifier and password flow."""
        response = await self.client.get(f"{BASE}/v1/cocoro-air/login")
        response.raise_for_status()
        try:
            redirect = response.json()["redirectUrl"]
            url = httpx.URL(redirect)
            if url.scheme != "https" or url.host != "auth.cocoromembers.jp.sharp":
                raise ValueError("Unexpected authentication host")
        except (ValueError, KeyError) as err:
            raise CocoroAirConnectionError("Invalid login redirect") from err
        html_headers = {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}
        response = await self.client.get(redirect, follow_redirects=True, headers=html_headers)
        if response.url.host == "cocoroplusapp.jp.sharp" and response.url.path.startswith("/air"):
            response.raise_for_status()
            return
        if response.url.host != "auth.cocoromembers.jp.sharp" or response.url.path != "/u/login/identifier":
            raise CocoroAirAuthError("Unexpected login step; sign in to COCORO AIR in a browser")
        state = _state(response)
        response = await self.client.post(
            f"{AUTH}/u/login/identifier", params={"state": state},
            data={"state": state, "username": self.email, "captcha": "",
                  "js-available": "true", "webauthn-available": "false",
                  "is-brave": "false", "webauthn-platform-available": "false", "action": "default"},
            follow_redirects=True, headers=html_headers,
        )
        if response.url.host != "auth.cocoromembers.jp.sharp" or response.url.path != "/u/login/password":
            raise CocoroAirAuthError("Account identification failed; check the email or sign in in a browser")
        state = _state(response)
        response = await self.client.post(
            f"{AUTH}/u/login/password", params={"state": state},
            data={"state": state, "username": self.email, "password": self.password, "action": "default"},
            follow_redirects=True, headers=html_headers,
        )
        if response.url.path.startswith("/u/custom-prompt/"):
            raise CocoroAirAuthError("Sign in to COCORO AIR in a browser and complete the account/terms confirmation, then reload this integration")
        if (response.status_code != 200 or response.url.host != "cocoroplusapp.jp.sharp"
                or response.url.params.get("login") != "success"):
            raise CocoroAirAuthError("Login failed; check credentials or complete browser account confirmation")

    async def _request(self, method, path, **kwargs):
        """Retry once after reauthentication, outside the poll throttle."""
        for attempt in range(2):
            response = await self.client.request(method, f"{BASE}{path}", **kwargs)
            if response.status_code == 401:
                if attempt:
                    raise CocoroAirAuthError("COCORO AIR rejected the renewed session")
                await self.login()
                continue
            response.raise_for_status()
            try:
                payload = response.json()
            except ValueError as err:
                raise CocoroAirConnectionError("COCORO AIR returned a non-JSON response") from err
            if not isinstance(payload, dict) or not payload:
                raise CocoroAirConnectionError("Invalid cloud response envelope")
            statuses = self._response_statuses(payload)
            if 401 in statuses:
                if attempt:
                    raise CocoroAirAuthError("COCORO AIR rejected the renewed session")
                await self.login()
                continue
            if any(status < 200 or status >= 300 for status in statuses):
                rejected = sorted({status for status in statuses if status < 200 or status >= 300})
                raise CocoroAirConnectionError(f"Cloud request rejected (status {','.join(map(str, rejected))})")
            return payload
        raise CocoroAirAuthError("COCORO AIR rejected the renewed session")

    @staticmethod
    def _response_statuses(payload):
        """Inspect HTTP-200 business errors, adapted from original b31e216."""
        statuses = []
        for block in [payload, *(value for value in payload.values() if isinstance(value, dict))]:
            try:
                if "status" in block:
                    statuses.append(int(block["status"]))
                if isinstance(block.get("body"), dict) and "code" in block["body"]:
                    code = int(block["body"]["code"])
                    statuses.append(200 if code == 0 else code)
            except (ValueError, TypeError) as err:
                raise CocoroAirConnectionError("Invalid cloud response status") from err
        return statuses

    async def async_query_devices(self):
        """Discover devices without persisting unrelated account metadata."""
        payload = await self._request("GET", "/v1/cocoro-air/deviceinfos")
        devices = []
        found = False
        for block in payload.values():
            if not isinstance(block, dict) or not isinstance(block.get("body"), dict) or "devices" not in block["body"]:
                continue
            found = True
            rows = block["body"]["devices"]
            if not isinstance(rows, list):
                raise CocoroAirConnectionError("Invalid device discovery response")
            for item in rows:
                if not isinstance(item, dict) or not item.get("device_id") or not item.get("model_name"):
                    raise CocoroAirConnectionError("Invalid device discovery response")
                name = item.get("device_name") or item["model_name"]
                place = item.get("place")
                devices.append({"device_id": item["device_id"], "model_name": item["model_name"],
                                "label": f"{name} ({item['model_name']})" + (f" - {place}" if place else "")})
        if not found:
            raise CocoroAirConnectionError("Device discovery response is missing devices")
        return devices

    @property
    def control_status(self):
        """Translate legacy read-only names to the upstream command builder."""
        mode = self.get_sensor_data()["operation_mode"]
        aliases = {"splash_particle": "splash", "powerful_cleaning": "feel", "error_stop": "error",
                   "cleaning_while_stopped": "cleaning_stopped", "automatic_cleaning": "cleaning",
                   "ai_dog_a": "dog", "ai_dog_b": "dog", "ai_cat_a": "cat", "ai_cat_b": "cat",
                   "ai_humidification": "ai_humidity_measurement"}
        cloud = self.cache.get("k3", {}).get("s2")
        return {"current_mode": aliases.get(mode, mode),
                "cloud_enabled": bool(int(cloud, 16) & 0x80) if cloud and self.spec.get("hasCloudService") else False}

    @property
    def control_modes(self):
        aliases = {"splash": "splash_particle", "feel": "powerful_cleaning"}
        return [aliases.get(mode, mode) for mode in supported_modes({**self.spec, "model_name": self.model_name})]

    async def async_control(self, action, value):
        """Serialize status, supported command and actual device readback."""
        if not self.spec:
            raise CocoroAirConnectionError("Device capabilities are unavailable; reload the integration")
        if action == "mode":
            if value not in self.control_modes:
                raise ValueError("Mode is not supported by this device")
            value = {"splash_particle": "splash", "powerful_cleaning": "feel"}.get(value, value)
        async with self._lock:
            await self._async_update_locked(force=True)
            if self.control_status["current_mode"] is None:
                raise CocoroAirConnectionError("Device operating state is unavailable")
            commands = build_command(action, value, self.control_status, {**self.spec, "model_name": self.model_name})
            payload = await self._request("POST", "/v1/cocoro-air/sync/air-cleaner", json={
                "additional_request": int(self.spec.get("seriiesCode", 0)) >= 6,
                "deviceToken": self.device_id, "event_key": "echonet_control", "data": commands,
                "model_name": self.model_name})
            if not self._response_statuses(payload):
                raise CocoroAirConnectionError("Cloud response did not confirm the control request")
            self._last_attempt = None
            await self._async_update_locked(force=True)
            return True

    async def async_load_metadata(self):
        """Read model capabilities and consumable settings, not controls."""
        try:
            payload = await self._request("GET", "/v1/cocoro-air/deviceinfos")
            devices = [device for block in payload.values() if isinstance(block, dict)
                       for device in block.get("body", {}).get("devices", [])]
            device = next(item for item in devices if item["device_id"] == self.device_id)
            self.spec = device.get("spec") or {}
            self.firmware_version = device.get("firmware_version")
        except (httpx.HTTPError, CocoroAirConnectionError, KeyError, TypeError, StopIteration) as err:
            _LOGGER.debug("Could not read optional device metadata: %s", err)
        try:
            payload = await self._request("POST", "/v1/cocoro-air/contents/supplies", json={
                "deviceToken": self.device_id, "properties": [{"apg": "0x02", "apc": ["0x10"]}]})
            block = payload["contents_supplies_040"]
            if block.get("status", 200) != 200:
                raise ValueError("Supplies unavailable")
            supplies = block["body"]["data"][0]["0x10"]
            self.supplies = {int(item["0x00"], 16): item for item in supplies}
        except (httpx.HTTPError, CocoroAirConnectionError, KeyError, TypeError, ValueError, IndexError) as err:
            self.supplies = {}
            _LOGGER.debug("Could not read optional consumable settings: %s", err)
        self._last_metadata_attempt = time.monotonic()

    async def update(self, *, force=False):
        """Coalesce entity polling and mark failed data unavailable."""
        async with self._lock:
            return await self._async_update_locked(force=force)

    async def _async_update_locked(self, *, force=False):
        """Poll while holding the device lock (also used for control readback)."""
        if not force and self._last_attempt is not None and time.monotonic() - self._last_attempt < 20:
            if self._last_error is not None:
                raise self._last_error
            return self.cache
        try:
            if self._last_metadata_attempt is not None and time.monotonic() - self._last_metadata_attempt > 3600:
                await self.async_load_metadata()
            payload = await self._request("GET", "/v1/cocoro-air/sensors-conceal/air-cleaner", params={
                "device_id": self.device_id, "event_key": "echonet_property", "opc": "k1+k2+k3",
                "epc": "0x80+0xA0+0x86"})
            try:
                block = payload["sensors_aircleaner_021"]
                if block.get("status", 200) != 200:
                    raise ValueError("Device unreachable")
                data = block["body"]["data"]
                if not isinstance(data, list):
                    raise ValueError("Invalid sensor data")
                cache = {key: item[key] for item in data for key in ("k1", "k2", "k3", "0x80", "0xA0", "0x86") if key in item}
                if not any(key in cache for key in ("k1", "k2", "k3")) or any(
                        not isinstance(cache[key], dict) for key in ("k1", "k2", "k3") if key in cache):
                    raise ValueError("Empty sensor data")
                self.get_sensor_data(cache)
            except (KeyError, TypeError, ValueError) as err:
                raise CocoroAirConnectionError("Device is unreachable or sensor data is invalid") from err
            self.cache = cache
            self.available = True
            self._last_error = None
            return self.cache
        except (httpx.HTTPError, CocoroAirAuthError, CocoroAirConnectionError) as err:
            self.available = False
            self._last_error = err
            raise
        finally:
            self._last_attempt = time.monotonic()

    def get_sensor_data(self, data=None):
        data = self.cache if data is None else data
        def number(group, field):
            value = data.get(group, {}).get(field)
            return int(value, 16) if value else None
        def flag(group, field):
            value = data.get(group, {}).get(field)
            return str(value).lower() == "ff" if value else None
        result = {"temperature": number("k1", "s1"), "humidity": number("k1", "s2"),
                  "water_tank": flag("k2", "s6"), "humidity_mode": flag("k3", "s7")}
        mode = number("k3", "s1")
        result["operation_mode"] = AI_MODES.get(number("k3", "s10"), "ai_auto") if mode == 0x20 else OPERATION_MODES.get(mode)
        stopped = mode is not None and ((mode & 0xF0) == 0 or mode == 0x1E)
        power = data.get("0x80")
        power = int(power, 16) if power else None
        result["running"] = True if mode in (0x0E, 0x1E) else power == 0x30 if power in (0x30, 0x31) else None
        # Upstream parse_status and the vendor UI hide measurements while stopped.
        temperature = result["temperature"]
        temperature_status = "not_measuring" if stopped else "unknown" if temperature is None else "below_range" if temperature >= 128 else "above_range" if temperature >= 51 else "measured"
        humidity = result["humidity"]
        humidity_low, humidity_high = (24, 76) if self.spec.get("seriiesCode", 0) >= 6 else (6, 100)
        humidity_status = "not_measuring" if stopped else "unknown" if humidity is None else "below_range" if humidity <= humidity_low else "above_range" if humidity >= humidity_high else "measured"
        result.update(temperature=temperature if temperature_status == "measured" else None,
                      humidity=humidity if humidity_status == "measured" else None,
                      temperature_status=temperature_status, humidity_status=humidity_status)
        fan = data.get("0xA0")
        fan = int(fan, 16) if fan else None
        result["fan_speed"] = "auto" if fan == 0x41 else f"level_{fan - 0x30}" if fan is not None and 0x31 <= fan <= 0x38 else None
        pm25 = number("k1", "s7")
        pm_status = None
        if pm25 is not None and self.spec.get("hasPM25Sensor", True):
            if stopped:
                pm_status = "not_measuring"
            elif pm25 & 0x8000:
                pm_status = "measuring"
            else:
                pm25 &= 0x03FF
                pm_status = "below_range" if pm25 < 1 else "above_range" if pm25 >= 500 else "measured"
        result["pm25"] = pm25 if pm_status == "measured" else None
        result["pm25_status"] = pm_status
        odor, dust, quality = (number("k2", field) for field in ("s1", "s2", "s4"))
        # Same boundaries as Sharp's Cn/wn/Dn helpers; these are ordinal levels.
        result["odor_level"] = sum(odor > boundary for boundary in (16, 50, 75)) if odor is not None and not stopped else None
        result["dust_level"] = sum(dust >= boundary for boundary in (16, 36, 56, 76)) if dust is not None and not stopped and self.spec.get("hasDustSensor", True) else None
        result["air_quality_level"] = sum(quality > boundary for boundary in (0, 25, 50, 75)) if quality is not None and not stopped else None
        minutes = number("k1", "s3")
        result["operating_time"] = round(minutes / 60, 2) if minutes is not None else None
        result["humidifier_filter_maintenance"] = number("k2", "s10") == 1 if number("k2", "s10") is not None else None
        result["dustbox_maintenance"] = flag("k2", "s14") if self.spec.get("hasCleaningMecha", True) else None
        for key, (field, lifetime_key, type_code) in FILTER_FIELDS.items():
            used = number("k1", field)
            if key == "pci_unit_remaining" and self.spec.get("hasPciTwiceUnit"):
                second = number("k1", "s5")
                used = max(used, second) if used is not None and second is not None else None
            lifetime = self.spec.get(lifetime_key)
            supply = self.supplies.get(type_code)
            multiplier = int(supply.get("0x02", "0x000A"), 16) / 10 if supply else None
            if used is None or not lifetime or supply is None:
                result[key] = None
            else:
                result[key] = max(0, min(100, math.floor(100 * (lifetime - used) / (lifetime * (multiplier or 1)))))
        # Sharp reports an expired/missing PCI unit as 0% even if its counter remains.
        errors = data.get("0x86", "").removeprefix("0x")
        if len(errors) >= 10 and int(errors[8:10], 16) & 0x06:
            result["pci_unit_remaining"] = 0
        return result

    async def set_humidity_mode(self, mode):
        if mode not in ("on", "off"):
            raise ValueError("Mode must be on or off")
        value = "FF" if mode == "on" else "00"
        async with self._lock:
            payload = await self._request("POST", "/v1/cocoro-air/sync/air-cleaner", json={
                "additional_request": int(self.spec.get("seriiesCode", 0)) >= 6,
                "deviceToken": self.device_id, "event_key": "echonet_control",
                "data": [{"opc": "k3", "odt": {"s5": "00", "s7": value}}], "model_name": self.model_name})
            self.cache.setdefault("k3", {})["s7"] = value
            self._last_attempt = None
            return True
