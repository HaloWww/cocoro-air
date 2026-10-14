"""Regression coverage without installing Home Assistant."""
import asyncio
import importlib.util
from pathlib import Path
import unittest

import httpx

spec = importlib.util.spec_from_file_location("cocoro_api", Path(__file__).parents[1]/"custom_components/cocoro_air/api.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

DATA = {"sensors_aircleaner_021": {"status": 200, "body": {"data": [
    {"k1": {"s1": "19", "s2": "18"}}, {"k2": {"s6": "00"}}, {"k3": {"s7": "FF"}},
]}}}


class ApiTests(unittest.IsolatedAsyncioTestCase):
    def client(self, handler):
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        self.addAsyncCleanup(client.aclose)
        return client

    def api(self, client):
        return module.CocoroAir(client, "example@example.org", "test-password", "device", "KIPX100")

    async def test_auth0_and_persistent_client(self):
        calls = []
        def handler(request):
            calls.append(request)
            path = request.url.path
            if path.endswith("/login"):
                return httpx.Response(200, json={"redirectUrl": module.AUTH+"/authorize"})
            if path == "/authorize":
                return httpx.Response(302, headers={"location": "/u/login/identifier", "set-cookie": "auth_state=present; Path=/"})
            if path == "/u/login/identifier" and request.method == "GET":
                return httpx.Response(200, text='<input value="id-state" name="state">')
            if path == "/u/login/identifier":
                self.assertIn("auth_state=present", request.headers.get("cookie", ""))
                self.assertIn(b"state=id-state", request.content)
                return httpx.Response(302, headers={"location": "/u/login/password"})
            if path == "/u/login/password" and request.method == "GET":
                return httpx.Response(200, text='<input name="state" value="password-state">')
            if path == "/u/login/password":
                self.assertIn(b"state=password-state", request.content)
                self.assertIn(b"password=test-password", request.content)
                return httpx.Response(302, headers={"location": module.BASE+"/air?login=success"})
            if path == "/air":
                return httpx.Response(200)
            return httpx.Response(200, json=DATA)
        client = self.client(handler)
        api = self.api(client)
        await api.login()
        await api.update()
        self.assertFalse(client.is_closed)
        self.assertEqual(api.get_sensor_data()["temperature"], 25)
        self.assertEqual(api.get_sensor_data()["humidity"], 24)
        self.assertTrue(api.get_sensor_data()["humidity_mode"])

    async def test_401_retries_immediately_then_bounds_failure(self):
        count = 0
        async def handler(request):
            nonlocal count
            count += 1
            return httpx.Response(401 if count == 1 else 200, json=DATA)
        api = self.api(self.client(handler))
        logins = []
        async def login():
            logins.append(True)
        api.login = login
        await api.update()
        self.assertEqual(count, 2)
        self.assertEqual(len(logins), 1)
        self.assertTrue(api.available)
        failed = self.api(self.client(lambda r: httpx.Response(401)))
        failed.login = login
        with self.assertRaises(module.CocoroAirAuthError):
            await failed.update()
        self.assertFalse(failed.available)
        self.assertEqual(len(logins), 2)

    async def test_parallel_polling_coalesces(self):
        count = 0
        async def handler(request):
            nonlocal count
            count += 1
            await asyncio.sleep(0)
            return httpx.Response(200, json=DATA)
        api = self.api(self.client(handler))
        values = await asyncio.gather(*(api.update() for _ in range(4)))
        self.assertEqual(count, 1)
        self.assertTrue(all(v == values[0] for v in values))

    async def test_failure_does_not_report_stale_data(self):
        responses = iter([httpx.Response(200, json=DATA), httpx.Response(200, text="not json")])
        api = self.api(self.client(lambda r: next(responses)))
        await api.update()
        with self.assertRaises(module.CocoroAirConnectionError):
            await api.update(force=True)
        self.assertFalse(api.available)
        with self.assertRaises(module.CocoroAirConnectionError):
            await api.update()

    async def test_cloud_unreachable_and_empty_data(self):
        for payload in ({"sensors_aircleaner_021": {"status": 404, "body": {"info": "unreachable"}}},
                        {"sensors_aircleaner_021": {"status": 200, "body": {"data": []}}}):
            api = self.api(self.client(lambda r: httpx.Response(200, json=payload)))
            with self.assertRaises(module.CocoroAirConnectionError):
                await api.update()
            self.assertFalse(api.available)

    async def test_control_failure_keeps_cache_and_success_normalizes_flags(self):
        for payload in ({"sync": {"status": 404}}, {"sync": {"body": {"code": 500}}}):
            api = self.api(self.client(lambda r: httpx.Response(200, json=payload)))
            api.cache = {"k3": {"s7": "00"}}
            with self.assertRaises(module.CocoroAirConnectionError):
                await api.set_humidity_mode("on")
            self.assertFalse(api.get_sensor_data()["humidity_mode"])
        api = self.api(self.client(lambda r: httpx.Response(200, json={"sync": {"status": 200}})))
        await api.set_humidity_mode("on")
        self.assertTrue(api.get_sensor_data()["humidity_mode"])
        self.assertIsNone(api._last_attempt)

    async def test_control_401_retries(self):
        count = 0
        def handler(request):
            nonlocal count
            count += 1
            return httpx.Response(401) if count == 1 else httpx.Response(200, json={"sync": {"status": 200}})
        api = self.api(self.client(handler))
        async def login():
            pass
        api.login = login
        await api.set_humidity_mode("on")
        self.assertEqual(count, 2)

    async def test_interactive_confirmation_is_not_automatically_accepted(self):
        def handler(request):
            path = request.url.path
            if path.endswith("/login"):
                return httpx.Response(200, json={"redirectUrl": module.AUTH+"/u/login/identifier"})
            if request.method == "GET":
                return httpx.Response(200, text='<input name="state" value="state">')
            if path == "/u/login/identifier":
                return httpx.Response(302, headers={"location": "/u/login/password"})
            return httpx.Response(302, headers={"location": "/u/custom-prompt/terms"})
        api = self.api(self.client(handler))
        with self.assertRaisesRegex(module.CocoroAirAuthError, "terms"):
            await api.login()


if __name__ == "__main__":
    unittest.main()
