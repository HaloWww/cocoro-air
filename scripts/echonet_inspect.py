#!/usr/bin/env python3
"""Bounded, read-only ECHONET Lite inspection using only the standard library.

Only Get (0x62) packets are constructed. No control, reset, cleaning, or other
write operation is implemented. Run on the same LAN as the appliance.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import ipaddress
import json
import secrets
import socket
import sys
import time
from typing import Iterable


PORT = 3610
MULTICAST = "224.0.23.0"
CONTROLLER = bytes.fromhex("05ff01")
NODE_PROFILE = bytes.fromhex("0ef001")
AIR_CLEANER_CLASS = bytes.fromhex("0135")
MAP_PROPERTIES = (0x9F, 0x9E, 0x9D)
IDENTITY_PROPERTIES = (0x8A, 0x8C, 0x82)
DETAIL_PROPERTIES = (
    0x80, 0x86, 0x88, 0xA0, 0xC0, 0xF0, 0xF1, 0xF2, 0xF3, 0xF4, 0xFC, 0xFD,
)
MAX_PACKETS_PER_ATTEMPT = 128
MAX_OBJECTS = 16


class ProtocolError(ValueError):
    """Malformed or internally inconsistent response."""


@dataclass(frozen=True)
class Frame:
    tid: int
    seoj: bytes
    deoj: bytes
    esv: int
    properties: tuple[tuple[int, bytes], ...]


def encode_get(tid: int, target: bytes, epcs: Iterable[int]) -> bytes:
    """Build a Get request; deliberately offer no arbitrary ESV argument."""
    epcs = tuple(epcs)
    if not 0 <= tid <= 0xFFFF or len(target) != 3:
        raise ValueError("Invalid transaction ID or target object")
    if not 1 <= len(epcs) <= 3 or len(set(epcs)) != len(epcs):
        raise ValueError("A request must contain one to three unique properties")
    if any(not 0x80 <= epc <= 0xFF for epc in epcs):
        raise ValueError("Invalid property code")
    return (
        b"\x10\x81" + tid.to_bytes(2, "big") + CONTROLLER + target
        + bytes((0x62, len(epcs)))
        + b"".join(bytes((epc, 0)) for epc in epcs)
    )


def parse_frame(data: bytes) -> Frame:
    """Parse an entire format-1 frame, rejecting truncation and extra bytes."""
    if len(data) < 12 or data[:2] != b"\x10\x81":
        raise ProtocolError("Invalid ECHONET Lite header")
    count = data[11]
    if count == 0:
        raise ProtocolError("Empty property list")
    properties = []
    seen = set()
    offset = 12
    for _ in range(count):
        if offset + 2 > len(data):
            raise ProtocolError("Truncated property header")
        epc, length = data[offset:offset + 2]
        offset += 2
        if offset + length > len(data):
            raise ProtocolError("Truncated property data")
        if epc in seen:
            raise ProtocolError("Duplicate property code")
        seen.add(epc)
        properties.append((epc, data[offset:offset + length]))
        offset += length
    if offset != len(data):
        raise ProtocolError("Unexpected trailing bytes")
    return Frame(
        int.from_bytes(data[2:4], "big"), data[4:7], data[7:10],
        data[10], tuple(properties),
    )


def matches_response(
    frame: Frame, address: tuple[str, int], *, tid: int, target: bytes,
    epcs: Iterable[int], host: str | None,
) -> bool:
    """Associate a response with this request, never with unsolicited traffic."""
    return (
        address[1] == PORT
        and (host is None or address[0] == host)
        and frame.tid == tid
        and frame.seoj == target
        and frame.deoj == CONTROLLER
        and frame.esv in (0x72, 0x52)  # Get_Res or Get_SNA, never INF/Set responses
        and tuple(epc for epc, _ in frame.properties) == tuple(epcs)
    )


def parse_property_map(data: bytes) -> list[int]:
    """Decode list form or 16-byte bitmap form without guessing malformed maps."""
    if not data:
        raise ProtocolError("Empty property map")
    count = data[0]
    if count <= 15:
        if len(data) != count + 1:
            raise ProtocolError("Property-map list length does not match count")
        properties = list(data[1:])
        if any(epc < 0x80 for epc in properties):
            raise ProtocolError("Property-map code is outside 0x80..0xFF")
        if len(set(properties)) != count:
            raise ProtocolError("Duplicate property in map")
        return sorted(properties)
    if count > 128 or len(data) != 17:
        raise ProtocolError("Invalid property-map bitmap length or count")
    properties = [
        (high << 4) | low
        for low, mask in enumerate(data[1:])
        for high in range(8, 16)
        if mask & (1 << (high - 8))
    ]
    if len(properties) != count:
        raise ProtocolError("Property-map bitmap count does not match set bits")
    return sorted(properties)


def parse_instances(data: bytes) -> list[bytes]:
    if not data or len(data) != 1 + data[0] * 3:
        raise ProtocolError("Instance-list length does not match count")
    instances = [data[offset:offset + 3] for offset in range(1, len(data), 3)]
    if len(set(instances)) != len(instances):
        raise ProtocolError("Duplicate object in instance list")
    return instances


def frame_json(frame: Frame) -> dict:
    return {
        "tid": f"{frame.tid:04X}", "seoj": frame.seoj.hex().upper(),
        "deoj": frame.deoj.hex().upper(), "esv": f"{frame.esv:02X}",
        "properties": {f"{epc:02X}": value.hex().upper() for epc, value in frame.properties},
    }


class Inspector:
    def __init__(self, interface: str | None, timeout: float, attempts: int):
        self.interface = interface
        self.timeout = timeout
        self.attempts = attempts
        self.tid = secrets.randbelow(65536)
        self.sock: socket.socket | None = None

    def __enter__(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            # Do not use SO_REUSEADDR/PORT: another ECHONET client must not share
            # and steal replies. Windows requires an explicit exclusive bind.
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            sock.bind((self.interface or "0.0.0.0", PORT))
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
            if self.interface:
                sock.setsockopt(
                    socket.IPPROTO_IP, socket.IP_MULTICAST_IF,
                    socket.inet_aton(self.interface),
                )
            self.sock = sock
            return self
        except BaseException:
            sock.close()
            raise

    def __exit__(self, *_):
        if self.sock is not None:
            self.sock.close()

    def get(self, host: str | None, target: bytes, epcs: Iterable[int]) -> list[dict]:
        """Send bounded Get requests and retain only fully correlated responses."""
        if self.sock is None:
            raise RuntimeError("Inspector socket is not open")
        epcs = tuple(epcs)
        replies: dict[str, dict] = {}
        for _ in range(self.attempts):
            self.tid = (self.tid + 1) & 0xFFFF
            tid = self.tid
            self.sock.sendto(encode_get(tid, target, epcs), (host or MULTICAST, PORT))
            deadline = time.monotonic() + self.timeout
            for _ in range(MAX_PACKETS_PER_ATTEMPT):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self.sock.settimeout(remaining)
                try:
                    data, address = self.sock.recvfrom(65535)
                except socket.timeout:
                    break
                try:
                    frame = parse_frame(data)
                except ProtocolError:
                    continue
                if not matches_response(
                    frame, address, tid=tid, target=target, epcs=epcs, host=host,
                ):
                    continue
                reply = {"host": address[0], "frame": frame_json(frame)}
                replies[address[0]] = reply
                if host is not None:
                    return [reply]
            if host is not None and replies:
                break
        return [replies[ip] for ip in sorted(replies)]


def inspect_host(client: Inspector, host: str, eoj: bytes | None) -> dict:
    result: dict = {"host": host, "instances": [], "objects": [], "errors": []}
    replies = client.get(host, NODE_PROFILE, (0xD6,))
    if not replies:
        result["errors"].append("No matching node-profile D6 response before timeout")
        return result
    result["instance_response"] = replies[0]["frame"]
    data = bytes.fromhex(replies[0]["frame"]["properties"]["D6"])
    try:
        instances = parse_instances(data)
    except ProtocolError as exc:
        result["errors"].append(str(exc))
        return result
    result["instances"] = [value.hex().upper() for value in instances]
    targets = [value for value in instances if value[:2] == AIR_CLEANER_CLASS]
    if eoj is not None:
        if eoj not in instances and eoj != NODE_PROFILE:
            result["errors"].append("Requested EOJ was not advertised in the D6 instance list")
            return result
        targets = [eoj]
    if not targets:
        result["errors"].append("No air-cleaner (0x0135) instance was advertised")
    if len(targets) > MAX_OBJECTS:
        result["errors"].append(f"Inspection limited to the first {MAX_OBJECTS} objects")
    for target in targets[:MAX_OBJECTS]:
        item: dict = {"eoj": target.hex().upper(), "responses": [], "maps": {}, "errors": []}
        result["objects"].append(item)
        get_map: set[int] | None = None
        for properties in (MAP_PROPERTIES, IDENTITY_PROPERTIES):
            responses = client.get(host, target, properties)
            if not responses:
                item["errors"].append("Timed out reading " + ",".join(f"{p:02X}" for p in properties))
                continue
            frame = responses[0]["frame"]
            item["responses"].append(frame)
            if properties == MAP_PROPERTIES:
                for key, raw in frame["properties"].items():
                    try:
                        values = parse_property_map(bytes.fromhex(raw))
                    except ProtocolError as exc:
                        item["errors"].append(f"Map {key}: {exc}")
                        continue
                    item["maps"][key] = [f"{value:02X}" for value in values]
                    if key == "9F":
                        get_map = set(values)
        if get_map is None:
            item["errors"].append("Valid Get map unavailable; no detail properties were requested")
            continue
        allowed = [epc for epc in DETAIL_PROPERTIES if epc in get_map]
        item["detail_properties_not_advertised"] = [
            f"{epc:02X}" for epc in DETAIL_PROPERTIES if epc not in get_map
        ]
        for offset in range(0, len(allowed), 3):
            properties = allowed[offset:offset + 3]
            responses = client.get(host, target, properties)
            if responses:
                item["responses"].append(responses[0]["frame"])
            else:
                item["errors"].append("Timed out reading " + ",".join(f"{p:02X}" for p in properties))
    return result


def ipv4(value: str) -> str:
    try:
        address = ipaddress.IPv4Address(value)
    except ipaddress.AddressValueError as exc:
        raise argparse.ArgumentTypeError("Provide a literal IPv4 address") from exc
    if address.is_multicast or address.is_unspecified or str(address) == "255.255.255.255":
        raise argparse.ArgumentTypeError("Provide a unicast IPv4 address")
    return str(address)


def object_id(value: str) -> bytes:
    try:
        result = bytes.fromhex(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("EOJ must contain six hexadecimal digits") from exc
    if len(result) != 3:
        raise argparse.ArgumentTypeError("EOJ must contain six hexadecimal digits")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--discover", action="store_true", help="Read node instance lists over LAN multicast")
    mode.add_argument("--host", type=ipv4, help="Inspect one appliance at a literal IPv4 address")
    parser.add_argument("--interface", type=ipv4, help="Local IPv4 interface to bind and send from")
    parser.add_argument("--eoj", type=object_id, help="Inspect one D6-advertised EOJ (default: air-cleaner objects)")
    parser.add_argument("--timeout", type=float, default=2.0, help="Seconds per attempt, 0.1 to 10 (default: 2)")
    parser.add_argument("--attempts", type=int, default=1, help="Attempts per request, 1 to 3 (default: 1)")
    args = parser.parse_args(argv)
    if not 0.1 <= args.timeout <= 10:
        parser.error("--timeout must be between 0.1 and 10 seconds")
    if not 1 <= args.attempts <= 3:
        parser.error("--attempts must be between 1 and 3")
    if args.eoj is not None and args.host is None:
        parser.error("--eoj requires --host")
    result: dict = {"read_only": True, "local_port": PORT, "interface": args.interface}
    try:
        with Inspector(args.interface, args.timeout, args.attempts) as client:
            if args.discover:
                result["discovery"] = []
                for response in client.get(None, NODE_PROFILE, (0xD6,)):
                    item = {"host": response["host"], "response": response["frame"]}
                    try:
                        instances = parse_instances(bytes.fromhex(response["frame"]["properties"]["D6"]))
                        item["instances"] = [eoj.hex().upper() for eoj in instances]
                    except ProtocolError as exc:
                        item["error"] = str(exc)
                    result["discovery"].append(item)
            else:
                result["inspection"] = inspect_host(client, args.host, args.eoj)
    except OSError as exc:
        result["error"] = str(exc)
        result["hint"] = (
            "UDP 3610 must be available exclusively. Stop another ECHONET client "
            "if it owns this port; check the selected LAN interface and firewall."
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
