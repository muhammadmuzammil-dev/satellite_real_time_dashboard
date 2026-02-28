"""
simulator.py — Telemetry Packet Simulator

Builds well-formed hex-encoded satellite telemetry packets and sends them
over UDP to the ingestion service, demonstrating all required scenarios:

  Packet 1 — Normal: all values nominal
  Packet 2 — Normal: slightly elevated but within thresholds
  Packet 3 — YELLOW alert: battery voltage below 12 000 mV
  Packet 4 — RED alert: MSI temperature above 40°C
  Packet 5 — Both alerts active simultaneously

The CRC-16 calculation here is identical to parser.py so every packet
passes the parser's integrity check.

Usage:
  python simulator.py
  python simulator.py --host 127.0.0.1 --port 5005 --sat-id 1 --interval 1.5
"""

import argparse
import logging
import socket
import struct
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [SIMULATOR] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── Constants (must match parser.py) ─────────────────────────────────────────
SYNC_HEADER     = 0x1ACF
PACKET_TYPE_HK  = 0x10


# ── CRC-16 (identical copy from parser.py) ────────────────────────────────────
def crc16(data: bytes) -> int:
    """CRC-16, polynomial 0x8005, init 0x0000, no reflection."""
    crc = 0x0000
    for byte in data:
        crc ^= (byte << 8)
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x8005) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


# ── Packet builder ────────────────────────────────────────────────────────────
def build_hk_packet(
    satellite_id: int,
    battery_voltage: int,   # uint16, mV
    battery_temp: int,      # int8,  °C (signed, range −128..+127)
    msi_temperature: int,   # uint8, °C
    ssr_used: int,          # uint32, MB
    timestamp: int = None,
) -> str:
    """
    Build a Housekeeping telemetry packet and return it as a lowercase hex string.

    Packet layout (20 bytes total):
      Offset  Size  Field
      0       2     Sync Header  (0x1ACF)
      2       1     Length       (20)
      3       1     Type         (0x10)
      4       2     Satellite ID
      6       4     Timestamp
      10      2     battery_voltage
      12      1     battery_temp  (signed)
      13      1     msi_temperature
      14      4     ssr_used
      18      2     CRC-16
    """
    if timestamp is None:
        timestamp = int(time.time())

    # HK payload: 2 + 1 + 1 + 4 = 8 bytes
    payload = struct.pack(">HbBI", battery_voltage, battery_temp, msi_temperature, ssr_used)

    # Fixed header: Sync(2) + Length(1) + Type(1) + SatID(2) + TS(4) = 10 bytes
    # Total with CRC = 10 + 8 + 2 = 20
    packet_length = 10 + len(payload) + 2

    header = struct.pack(
        ">HBBHI",
        SYNC_HEADER,
        packet_length,
        PACKET_TYPE_HK,
        satellite_id,
        timestamp,
    )

    body = header + payload
    crc  = crc16(body)
    full = body + struct.pack(">H", crc)
    return full.hex()


# ── UDP sender ────────────────────────────────────────────────────────────────
def send_packet(hex_str: str, host: str, port: int) -> None:
    """Encode the hex string as UTF-8 and send it as a UDP datagram."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.sendto(hex_str.encode("utf-8"), (host, port))


# ── Simulation scenarios ──────────────────────────────────────────────────────
SCENARIOS = [
    {
        "name":            "Normal — all nominal",
        "battery_voltage": 13_800,
        "battery_temp":    22,
        "msi_temperature": 28,
        "ssr_used":        512,
    },
    {
        "name":            "Normal — slightly warm",
        "battery_voltage": 13_200,
        "battery_temp":    26,
        "msi_temperature": 38,
        "ssr_used":        1_024,
    },
    {
        "name":            "YELLOW ALERT — battery low (11 500 mV < 12 000 mV)",
        "battery_voltage": 11_500,
        "battery_temp":    24,
        "msi_temperature": 35,
        "ssr_used":        2_048,
    },
    {
        "name":            "RED ALERT — MSI temperature high (45°C > 40°C)",
        "battery_voltage": 13_400,
        "battery_temp":    30,
        "msi_temperature": 45,
        "ssr_used":        3_072,
    },
    {
        "name":            "RED + YELLOW — both thresholds breached",
        "battery_voltage": 10_800,
        "battery_temp":    18,
        "msi_temperature": 52,
        "ssr_used":        4_096,
    },
]


def run_simulation(
    host: str = "127.0.0.1",
    port: int = 5005,
    satellite_id: int = 1,
    interval: float = 1.5,
) -> None:
    """
    Send all scenarios sequentially, logging each packet.
    The main service (main.py) must be running before calling this.
    """
    logger.info(
        "Starting simulation — %d packets to satellite_id=%d → %s:%d",
        len(SCENARIOS), satellite_id, host, port,
    )
    logger.info("Interval between packets: %.1f s\n", interval)

    for i, scenario in enumerate(SCENARIOS, start=1):
        hex_pkt = build_hk_packet(
            satellite_id    = satellite_id,
            battery_voltage = scenario["battery_voltage"],
            battery_temp    = scenario["battery_temp"],
            msi_temperature = scenario["msi_temperature"],
            ssr_used        = scenario["ssr_used"],
        )

        logger.info(
            "[%d/%d] %s\n"
            "        battery_voltage=%d mV  "
            "msi_temperature=%d°C  "
            "battery_temp=%d°C  "
            "ssr_used=%d MB\n"
            "        hex → %s",
            i, len(SCENARIOS),
            scenario["name"],
            scenario["battery_voltage"],
            scenario["msi_temperature"],
            scenario["battery_temp"],
            scenario["ssr_used"],
            hex_pkt,
        )

        send_packet(hex_pkt, host, port)
        time.sleep(interval)

    logger.info("Simulation complete.  Check the dashboard at http://127.0.0.1:5000/")


# ── CLI entry point ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Send simulated satellite telemetry packets over UDP"
    )
    ap.add_argument("--host",     default="127.0.0.1", help="Target host (default: 127.0.0.1)")
    ap.add_argument("--port",     type=int, default=5005, help="UDP port (default: 5005)")
    ap.add_argument("--sat-id",   type=int, default=1,    help="Satellite ID (default: 1)")
    ap.add_argument("--interval", type=float, default=1.5, help="Seconds between packets (default: 1.5)")
    args = ap.parse_args()

    run_simulation(
        host       = args.host,
        port       = args.port,
        satellite_id = args.sat_id,
        interval   = args.interval,
    )
