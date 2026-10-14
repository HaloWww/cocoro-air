"""Regression coverage without installing Home Assistant."""
import asyncio
import importlib.util
from pathlib import Path
import unittest
import sys
import types

import httpx

package = types.ModuleType("cocoro_air")
package.__path__ = [str(Path(__file__).parents[1]/"custom_components/cocoro_air")]
sys.modules.setdefault("cocoro_air", package)
spec = importlib.util.spec_from_file_location("cocoro_air.api", Path(__file__).parents[1]/"custom_components/cocoro_air/api.py")
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

    async def test_read_extra_epcs_in_same_poll(self):
        def handler(request):
            self.assertEqual(request.url.params['epc'], '0x80+0xA0+0x86')
            return httpx.Response(200, json={"sensors_aircleaner_021": {"body": {"data": [
                {"k1": {"s1": "19", "s2": "18"}}, {"0x80": "0x30"}, {"0xA0": "0x41"},
            ]}}})
        api = self.api(self.client(handler))
        await api.update()
        self.assertEqual(api.get_sensor_data()['fan_speed'], 'auto')
        self.assertTrue(api.get_sensor_data()['running'])

    async def test_fan_settings_are_levels_not_rpm_or_percentage(self):
        api = self.api(self.client(lambda r: httpx.Response(200)))
        for value, expected in [('0x41','auto'), ('0x31','level_1'), ('0x38','level_8'), ('0x39',None)]:
            self.assertEqual(api.get_sensor_data({'0xA0':value})['fan_speed'], expected)

    async def test_pm25_masks_status_and_handles_ranges(self):
        api = self.api(self.client(lambda r: httpx.Response(200)))
        for raw, expected, status in [('4019',25,'measured'), ('8019',None,'measuring'),
                                      ('0000',None,'below_range'), ('01f4',None,'above_range')]:
            data = api.get_sensor_data({'k1':{'s7':raw}, 'k3':{'s1':'30'}})
            self.assertEqual(data['pm25'], expected)
            self.assertEqual(data['pm25_status'], status)
        self.assertIsNone(api.get_sensor_data({'k1':{'s7':'0019'},'k3':{'s1':'00'}})['pm25'])
        api.spec['hasPM25Sensor'] = False
        self.assertIsNone(api.get_sensor_data({'k1':{'s7':'0019'}})['pm25'])

    async def test_vendor_pollution_level_boundaries(self):
        api = self.api(self.client(lambda r: httpx.Response(200)))
        for raw, odor, dust, quality in [(0,0,0,0),(16,0,1,1),(17,1,1,1),(26,1,1,2),
                                        (36,1,2,2),(51,2,2,3),(56,2,3,3),(75,2,3,3),(76,3,4,4)]:
            value = f'{raw:02x}'
            data = api.get_sensor_data({'k2':{'s1':value,'s2':value,'s4':value},'k3':{'s1':'30'}})
            self.assertEqual((data['odor_level'],data['dust_level'],data['air_quality_level']), (odor,dust,quality))
        self.assertIsNone(api.get_sensor_data({'k2':{'s1':'10'},'k3':{'s1':'00'}})['odor_level'])

    async def test_metadata_capabilities_and_supplies_are_read_only(self):
        def handler(request):
            if request.method == 'GET':
                return httpx.Response(200,json={'device_infos':{'body':{'devices':[
                    {'device_id':'device','firmware_version':'1.2','spec':{'hasPM25Sensor':True}}]}}})
            self.assertEqual(request.url.path, '/v1/cocoro-air/contents/supplies')
            self.assertIn(b'"apg":"0x02"',request.content)
            return httpx.Response(200,json={'contents_supplies_040':{'body':{'data':[
                {'0x10':[{'0x00':'0x00','0x01':'test-filter','0x02':'0x000A'}]}]}}})
        api = self.api(self.client(handler))
        await api.async_load_metadata()
        self.assertTrue(api.spec['hasPM25Sensor'])
        self.assertEqual(api.firmware_version,'1.2')
        self.assertEqual(api.supplies[0]['0x01'],'test-filter')

    async def test_filter_percentage_matches_lifetime_and_multiplier(self):
        api = self.api(self.client(lambda r: httpx.Response(200)))
        api.spec = {'dustFilterLimit':3000, 'pciUnitLimit':19000, 'hasPciTwiceUnit':True}
        api.supplies = {0x00:{'0x02':'0x000A'},0x11:{'0x02':'0x000A'}}
        cache = {'k1':{'s8':'03a0','s4':'0100','s5':'3ba3'}}
        self.assertEqual(api.get_sensor_data(cache)['dust_filter_remaining'],69)
        self.assertEqual(api.get_sensor_data(cache)['pci_unit_remaining'],19)
        api.supplies[0]['0x02'] = '0x0014'
        self.assertEqual(api.get_sensor_data(cache)['dust_filter_remaining'],34)
        cache['0x86'] = '0x000000000200'
        self.assertEqual(api.get_sensor_data(cache)['pci_unit_remaining'],0)
        api.supplies = {}
        self.assertIsNone(api.get_sensor_data(cache)['dust_filter_remaining'])

    async def test_runtime_modes_and_maintenance(self):
        api = self.api(self.client(lambda r: httpx.Response(200)))
        cache = {'k1':{'s3':'0078'},'k2':{'s10':'01','s14':'ff'},'k3':{'s1':'30'}}
        data = api.get_sensor_data(cache)
        self.assertEqual(data['operating_time'],2)
        self.assertEqual(data['operation_mode'],'splash_particle')
        self.assertTrue(data['humidifier_filter_maintenance'])
        self.assertTrue(data['dustbox_maintenance'])
        cache['k3'] = {'s1':'20','s10':'81'}
        self.assertEqual(api.get_sensor_data(cache)['operation_mode'],'ai_dog_a')


if __name__ == "__main__":
    unittest.main()
