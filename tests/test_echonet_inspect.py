"""Verify read-only ECHONET framing and response trust boundaries offline."""

import importlib.util
from pathlib import Path
import sys
import unittest


spec = importlib.util.spec_from_file_location(
    "echonet_inspect", Path(__file__).parents[1] / "scripts" / "echonet_inspect.py",
)
inspector = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = inspector
spec.loader.exec_module(inspector)


def response(*, tid=0x1234, seoj="013501", deoj="05FF01", esv=0x72, properties=((0x80, b"\x30"),)):
    return (
        b"\x10\x81" + tid.to_bytes(2, "big") + bytes.fromhex(seoj + deoj)
        + bytes((esv, len(properties)))
        + b"".join(bytes((epc, len(value))) + value for epc, value in properties)
    )


class EchonetInspectionTests(unittest.TestCase):
    def test_only_get_can_be_encoded_and_small_batches_are_enforced(self):
        packet = inspector.encode_get(0x1234, bytes.fromhex("013501"), (0x9F, 0x9E, 0x9D))
        self.assertEqual(packet.hex(), "1081123405ff0101350162039f009e009d00")
        with self.assertRaises(ValueError):
            inspector.encode_get(1, bytes.fromhex("013501"), (0x80, 0x81, 0x82, 0x83))

    def test_list_map(self):
        self.assertEqual(inspector.parse_property_map(bytes.fromhex("039F80F3")), [0x80, 0x9F, 0xF3])
        self.assertEqual(inspector.parse_property_map(b"\x00"), [])
        for invalid in (b"", b"\x02\x80", b"\x02\x80\x80", b"\x01\x7F", b"\x00\x80"):
            with self.subTest(invalid=invalid), self.assertRaises(inspector.ProtocolError):
                inspector.parse_property_map(invalid)

    def test_bitmap_axes_and_bit_count(self):
        # 80..8F set in bit zero of every byte; F3 in bit seven of byte 3.
        bitmap = bytearray([17] + [1] * 16)
        bitmap[1 + 3] |= 0x80
        self.assertEqual(inspector.parse_property_map(bytes(bitmap)), list(range(0x80, 0x90)) + [0xF3])
        bitmap[0] = 18
        with self.assertRaises(inspector.ProtocolError):
            inspector.parse_property_map(bytes(bitmap))
        with self.assertRaises(inspector.ProtocolError):
            inspector.parse_property_map(bytes([16] + [1] * 15))

    def test_truncated_headers_data_duplicates_and_trailing_bytes(self):
        valid = response()
        self.assertEqual(inspector.parse_frame(valid).properties, ((0x80, b"\x30"),))
        for invalid in (valid[:10], valid[:13], valid[:-1], valid + b"\x00", response(properties=((0x80, b""), (0x80, b"")))):
            with self.subTest(invalid=invalid), self.assertRaises(inspector.ProtocolError):
                inspector.parse_frame(invalid)

    def test_response_must_match_transaction_source_objects_service_and_epcs(self):
        expected = dict(tid=0x1234, target=bytes.fromhex("013501"), epcs=(0x80,), host="192.0.2.1")
        address = ("192.0.2.1", 3610)
        self.assertTrue(inspector.matches_response(inspector.parse_frame(response()), address, **expected))
        self.assertTrue(inspector.matches_response(inspector.parse_frame(response(esv=0x52)), address, **expected))
        for changed in (
            {"tid": 0x1235}, {"seoj": "013502"}, {"deoj": "05FF02"},
            {"esv": 0x73}, {"esv": 0x71}, {"properties": ((0x88, b"\x41"),)},
        ):
            with self.subTest(changed=changed):
                self.assertFalse(inspector.matches_response(inspector.parse_frame(response(**changed)), address, **expected))
        for wrong_address in (("192.0.2.2", 3610), ("192.0.2.1", 9999)):
            self.assertFalse(inspector.matches_response(inspector.parse_frame(response()), wrong_address, **expected))

    def test_instance_list_boundaries(self):
        self.assertEqual(inspector.parse_instances(bytes.fromhex("02013501013001")), [bytes.fromhex("013501"), bytes.fromhex("013001")])
        for invalid in (b"", bytes.fromhex("02013501"), bytes.fromhex("02013501013501")):
            with self.subTest(invalid=invalid), self.assertRaises(inspector.ProtocolError):
                inspector.parse_instances(invalid)

    def test_detail_reads_are_gated_by_valid_get_map(self):
        class FakeClient:
            def __init__(self, get_map):
                self.get_map = get_map
                self.calls = []

            def get(self, host, target, epcs):
                epcs = tuple(epcs)
                self.calls.append((target, epcs))
                if epcs == (0xD6,):
                    values = {"D6": "01013501"}
                elif epcs == inspector.MAP_PROPERTIES:
                    values = {"9F": self.get_map, "9E": "00", "9D": "00"}
                else:
                    values = {f"{epc:02X}": "30" for epc in epcs}
                return [{"host": host, "frame": {"properties": values}}]

        client = FakeClient("0280F3")
        result = inspector.inspect_host(client, "192.0.2.1", None)
        self.assertEqual(client.calls[-1][1], (0x80, 0xF3))
        self.assertEqual(len(client.calls), 4)
        self.assertEqual(result["objects"][0]["maps"]["9F"], ["80", "F3"])
        client = FakeClient("0280")  # Advertised count exceeds data length.
        result = inspector.inspect_host(client, "192.0.2.1", None)
        self.assertEqual(len(client.calls), 3)
        self.assertTrue(any("no detail" in error for error in result["objects"][0]["errors"]))


if __name__ == "__main__":
    unittest.main()
