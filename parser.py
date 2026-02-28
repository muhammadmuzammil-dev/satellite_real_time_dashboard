"""
parser.py — Telemetry Packet Parser

Packet Structure (big-endian byte layout):
  Offset  Size  Field
  ------  ----  -----
  0       2     Sync Header   (0x1ACF)
  2       1     Packet Length (total bytes, including this header and CRC)
  3       1     Packet Type   (0x10 = Housekeeping, 0x20 = Payload Data)
  4       2     Satellite ID
  6       4     Timestamp     (Unix epoch, uint32)
  10      var   Payload Data
  -2      2     CRC-16        (computed over all bytes except the CRC itself)

Housekeeping Payload (Type 0x10) — 8 bytes:
  Offset  Size  Type    Field
  0       2     uint16  battery_voltage  (mV)
  2       1     int8    battery_temp     (°C, signed)
  3       1     uint8   msi_temperature  (°C)
  4       4     uint32  ssr_used         (MB)
"""

import struct
from typing import Dict, Any

SYNC_HEADER = 0x1ACF
PACKET_TYPE_HK = 0x10
PACKET_TYPE_PAYLOAD = 0x20

HEADER_SIZE = 10   # Sync(2) + Length(1) + Type(1) + SatID(2) + Timestamp(4)
CRC_SIZE = 2
HK_PAYLOAD_SIZE = 8
MIN_PACKET_SIZE = HEADER_SIZE + CRC_SIZE  # 12 bytes absolute minimum


class ParseError(Exception):
    """Raised when a packet is malformed, incomplete, or has an invalid CRC."""
    pass


def crc16(data: bytes) -> int:
    """
    CRC-16 using polynomial 0x8005, initial value 0x0000, no reflection.
    Computed over all packet bytes except the trailing CRC field.
    Both the parser and simulator use this exact function so they stay in sync.
    """
    crc = 0x0000
    for byte in data:
        crc ^= (byte << 8)
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x8005) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


def parse_packet(hex_string: str) -> Dict[str, Any]:
    """
    Parse a hex-encoded telemetry packet.

    Args:
        hex_string: Hex-encoded packet string (spaces, '0x' prefix and newlines
                    are stripped automatically).

    Returns:
        A dictionary with all parsed fields. The 'packet_type' key will be
        either 'HOUSEKEEPING' or 'PAYLOAD_DATA'.

    Raises:
        ParseError: On any validation failure (bad sync, length mismatch,
                    CRC error, unknown type, or truncated payload).
    """
    # ── Normalise input ──────────────────────────────────────────────────────
    cleaned = hex_string.strip().replace(" ", "").replace("0x", "").replace("\n", "")
    if not cleaned:
        raise ParseError("Empty packet string")
    if len(cleaned) % 2 != 0:
        raise ParseError(
            f"Hex string has an odd number of characters ({len(cleaned)}); "
            "each byte requires exactly two hex digits"
        )
    try:
        data = bytes.fromhex(cleaned)
    except ValueError as exc:
        raise ParseError(f"Invalid hex encoding: {exc}") from exc

    # ── Minimum length ───────────────────────────────────────────────────────
    if len(data) < MIN_PACKET_SIZE:
        raise ParseError(
            f"Packet too short: {len(data)} bytes "
            f"(minimum is {MIN_PACKET_SIZE})"
        )

    # ── Sync header ──────────────────────────────────────────────────────────
    sync = struct.unpack_from(">H", data, 0)[0]
    if sync != SYNC_HEADER:
        raise ParseError(
            f"Sync header mismatch: got 0x{sync:04X}, "
            f"expected 0x{SYNC_HEADER:04X}"
        )

    # ── Packet length ────────────────────────────────────────────────────────
    declared_length = data[2]
    if declared_length != len(data):
        raise ParseError(
            f"Length field says {declared_length} bytes "
            f"but actual data is {len(data)} bytes"
        )
    if declared_length < MIN_PACKET_SIZE:
        raise ParseError(
            f"Declared packet length {declared_length} is below "
            f"minimum {MIN_PACKET_SIZE}"
        )

    # ── CRC-16 ───────────────────────────────────────────────────────────────
    received_crc = struct.unpack_from(">H", data, -CRC_SIZE)[0]
    calculated_crc = crc16(data[:-CRC_SIZE])
    if received_crc != calculated_crc:
        raise ParseError(
            f"CRC mismatch: packet carries 0x{received_crc:04X}, "
            f"calculated 0x{calculated_crc:04X}"
        )

    # ── Fixed header fields ──────────────────────────────────────────────────
    packet_type = data[3]
    satellite_id = struct.unpack_from(">H", data, 4)[0]
    timestamp = struct.unpack_from(">I", data, 6)[0]

    # Payload sits between the fixed header and the CRC
    payload = data[HEADER_SIZE:-CRC_SIZE]

    # ── Dispatch by type ─────────────────────────────────────────────────────
    if packet_type == PACKET_TYPE_HK:
        return _parse_housekeeping(satellite_id, timestamp, payload, cleaned)
    elif packet_type == PACKET_TYPE_PAYLOAD:
        return {
            "packet_type": "PAYLOAD_DATA",
            "satellite_id": satellite_id,
            "timestamp": timestamp,
            "payload_hex": payload.hex(),
            "payload_length": len(payload),
            "raw_hex": cleaned,
        }
    else:
        raise ParseError(f"Unknown packet type: 0x{packet_type:02X}")


def _parse_housekeeping(
    satellite_id: int,
    timestamp: int,
    payload: bytes,
    raw_hex: str,
) -> Dict[str, Any]:
    """Unpack the 8-byte Housekeeping payload."""
    if len(payload) < HK_PAYLOAD_SIZE:
        raise ParseError(
            f"Housekeeping payload too short: {len(payload)} bytes, "
            f"expected {HK_PAYLOAD_SIZE}"
        )

    battery_voltage = struct.unpack_from(">H", payload, 0)[0]   # uint16, mV
    battery_temp    = struct.unpack_from(">b", payload, 2)[0]   # int8, °C (signed)
    msi_temperature = struct.unpack_from(">B", payload, 3)[0]   # uint8, °C
    ssr_used        = struct.unpack_from(">I", payload, 4)[0]   # uint32, MB

    return {
        "packet_type":   "HOUSEKEEPING",
        "satellite_id":  satellite_id,
        "timestamp":     timestamp,
        "battery_voltage":  battery_voltage,
        "battery_temp":     battery_temp,
        "msi_temperature":  msi_temperature,
        "ssr_used":         ssr_used,
        "raw_hex":          raw_hex,
    }
