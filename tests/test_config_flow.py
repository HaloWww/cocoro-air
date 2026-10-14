"""Run flow regressions in a Home Assistant runtime; skip in lightweight checks."""
import importlib
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch

try:
    from homeassistant.core import HomeAssistant
except ModuleNotFoundError:
    HomeAssistant = None


@unittest.skipUnless(HomeAssistant is not None, "Requires the Home Assistant runtime")
class ConfigFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        package = sys.modules.get("cocoro_air")
        if package is None:
            package = types.ModuleType("cocoro_air")
            package.__path__ = [str(Path(__file__).parents[1]/"custom_components/cocoro_air")]
            sys.modules["cocoro_air"] = package
        package.DOMAIN = "cocoro_air"
        self.module = importlib.import_module("cocoro_air.config_flow")
        self.hass = HomeAssistant("/tmp/cocoro-flow-tests")
        self.addAsyncCleanup(self.hass.async_stop)
        self.flow = self.module.ConfigFlow()
        self.flow.hass = self.hass
        self.flow.handler = "cocoro_air"
        self.devices = [{"device_id":"device","model_name":"KIPX100","label":"Cleaner (KIPX100)"}]
        self.credentials = {"email":"test@example.org","password":"test"}

    async def test_credentials_discover_device_without_manual_id(self):
        with patch.object(self.module,"discover_devices",AsyncMock(return_value=self.devices)):
            result = await self.flow.async_step_user(self.credentials)
        self.assertEqual(result["step_id"],"device")
        self.assertEqual(self.flow._credentials,self.credentials)

    async def test_legacy_entry_without_unique_id_still_prevents_duplicate(self):
        self.flow._devices = self.devices
        self.flow._credentials = self.credentials
        self.flow._async_current_entries = Mock(return_value=[types.SimpleNamespace(data={"device_id":"device"})])
        result = await self.flow.async_step_device({"device_id":"device"})
        self.assertEqual(result["reason"],"already_configured")

    async def test_selected_device_keeps_existing_four_field_storage(self):
        self.flow._devices = self.devices
        self.flow._credentials = self.credentials
        self.flow._async_current_entries = Mock(return_value=[])
        self.flow.async_set_unique_id = AsyncMock()
        self.flow._abort_if_unique_id_configured = Mock()
        with patch.object(self.module,"validate_input",AsyncMock(return_value={"title":"Cleaner"})):
            result = await self.flow.async_step_device({"device_id":"device"})
        self.assertEqual(result["data"],dict(self.credentials,device_id="device",model_name="KIPX100"))

    async def test_invalid_device_selection_does_not_create_entry(self):
        self.flow._devices = self.devices
        result = await self.flow.async_step_device({"device_id":"other"})
        self.assertEqual(result["errors"],{"base":"device_not_found"})

    async def test_reconfigure_preserves_device_identity(self):
        entry = types.SimpleNamespace(data={"email":"old@example.org","password":"old","device_id":"device","model_name":"KIPX100"})
        with patch.object(self.module,"discover_devices",AsyncMock(return_value=self.devices)), patch.object(self.module,"validate_input",AsyncMock()):
            result = await self.flow._async_update_credentials(entry,dict(self.credentials,device_id="other"))
        self.assertEqual(result["device_id"],"device")
        self.assertEqual(result["email"],self.credentials["email"])

    async def test_reconfigure_rejects_account_without_original_device(self):
        entry = types.SimpleNamespace(data={"device_id":"missing"})
        with patch.object(self.module,"discover_devices",AsyncMock(return_value=self.devices)):
            result = await self.flow._async_update_credentials(entry,self.credentials)
        self.assertEqual(result,"device_not_found")

    async def test_temporary_discovery_client_closes_on_auth_failure(self):
        import httpx
        client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200)))
        with patch.object(self.module,"_client",Mock(return_value=client)), patch.object(self.module.CocoroAir,"login",AsyncMock(side_effect=self.module.CocoroAirAuthError("invalid"))):
            with self.assertRaises(self.module.CocoroAirAuthError):
                await self.module.discover_devices(self.hass,self.credentials)
        self.assertTrue(client.is_closed)
