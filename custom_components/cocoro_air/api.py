"""COCORO AIR client with an isolated, persistent cookie session."""
from __future__ import annotations

import asyncio
from html.parser import HTMLParser
import time

import httpx

BASE = "https://cocoroplusapp.jp.sharp"
AUTH = "https://auth.cocoromembers.jp.sharp"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36"


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
        response = await self.client.get(redirect, follow_redirects=True)
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
            follow_redirects=True,
        )
        if response.url.host != "auth.cocoromembers.jp.sharp" or response.url.path != "/u/login/password":
            raise CocoroAirAuthError("Account identification failed; check the email or sign in in a browser")
        state = _state(response)
        response = await self.client.post(
            f"{AUTH}/u/login/password", params={"state": state},
            data={"state": state, "username": self.email, "password": self.password, "action": "default"},
            follow_redirects=True,
        )
        if response.url.path.startswith("/u/custom-prompt/"):
            raise CocoroAirAuthError("Sign in to COCORO AIR in a browser and complete the account/terms confirmation, then reload this integration")
        if (response.status_code != 200 or response.url.host != "cocoroplusapp.jp.sharp"
                or response.url.params.get("login") != "success"):
            raise CocoroAirAuthError("Login failed; check credentials or complete browser account confirmation")

    async def _request(self, method, path, **kwargs):
        """Retry once after reauthentication, outside the poll throttle."""
        response = await self.client.request(method, f"{BASE}{path}", **kwargs)
        if response.status_code == 401:
            await self.login()
            response = await self.client.request(method, f"{BASE}{path}", **kwargs)
            if response.status_code == 401:
                raise CocoroAirAuthError("COCORO AIR rejected the renewed session")
        response.raise_for_status()
        try:
            return response.json()
        except ValueError as err:
            raise CocoroAirConnectionError("COCORO AIR returned a non-JSON response") from err

    async def update(self, *, force=False):
        """Coalesce entity polling and mark failed data unavailable."""
        async with self._lock:
            if not force and self._last_attempt is not None and time.monotonic() - self._last_attempt < 20:
                if self._last_error is not None:
                    raise self._last_error
                return self.cache
            try:
                payload = await self._request("GET", "/v1/cocoro-air/sensors-conceal/air-cleaner", params={
                    "device_id": self.device_id, "event_key": "echonet_property", "opc": "k1+k2+k3"})
                try:
                    block = payload["sensors_aircleaner_021"]
                    if block.get("status", 200) != 200:
                        raise ValueError("Device unreachable")
                    data = block["body"]["data"]
                    if not isinstance(data, list):
                        raise ValueError("Invalid sensor data")
                    cache = {key: item[key] for item in data for key in ("k1", "k2", "k3") if key in item}
                    if not cache or any(not isinstance(value, dict) for value in cache.values()):
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
        return {"temperature": number("k1", "s1"), "humidity": number("k1", "s2"),
                "water_tank": flag("k2", "s6"), "humidity_mode": flag("k3", "s7")}

    async def set_humidity_mode(self, mode):
        if mode not in ("on", "off"):
            raise ValueError("Mode must be on or off")
        value = "FF" if mode == "on" else "00"
        async with self._lock:
            payload = await self._request("POST", "/v1/cocoro-air/sync/air-cleaner", json={
                "additional_request": False, "deviceToken": self.device_id, "event_key": "echonet_control",
                "data": [{"opc": "k3", "odt": {"s5": "00", "s7": value}}], "model_name": self.model_name})
            for block in payload.values() if isinstance(payload, dict) else []:
                if isinstance(block, dict):
                    if block.get("status", 200) != 200 or block.get("body", {}).get("code", 200) != 200:
                        raise CocoroAirConnectionError("Device rejected the humidity command")
            self.cache.setdefault("k3", {})["s7"] = value
            self._last_attempt = None
            return True
