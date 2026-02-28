"""
seed_data.py — Populate MongoDB with Realistic Historical Telemetry

Inserts 48 Housekeeping packets per satellite (one every 30 minutes = 24 hours
of history) for satellites 1 and 2, each with a realistic operational story:

  Phase A  0 – 30 %   Charging: voltage rises from ~12 500 to ~14 000 mV
  Phase B 30 – 55 %   Nominal cruise: stable high voltage, warm MSI
  Phase C 55 – 75 %   Battery anomaly: voltage drops < 12 000 mV (YELLOW alerts)
  Phase D 75 – 88 %   MSI thermal spike: temperature > 40 °C (RED alerts)
  Phase E 88 – 100 %  Recovery: voltage and temperature return to nominal

Usage:
    python seed_data.py                        # satellites 1 & 2, 48 packets each
    python seed_data.py --sat-ids 1 2 3        # add satellite 3
    python seed_data.py --packets 96           # denser data (96 × 15 min = 24 h)
    python seed_data.py --clear                # wipe existing data first, then seed
"""

import argparse
import io
import logging
import math
import random
import sys
import time

# Windows terminal UTF-8 fix
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

import database as db
from health_monitor import check_health

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  [SEED]  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── Random seed for reproducibility ───────────────────────────────────────────
random.seed(42)


# ── Telemetry series generator ────────────────────────────────────────────────

def _make_series(satellite_id: int, count: int, end_ts: int) -> list:
    """
    Return a list of telemetry dicts for one satellite.

    Values follow a deterministic story with noise; each phase is described in
    the module docstring above.
    """
    interval = (24 * 3600) // count          # e.g. 1800 s for 48 packets
    start_ts = end_ts - (count - 1) * interval
    rng = random.Random(satellite_id * 1000)  # per-satellite RNG for variety

    series = []
    for i in range(count):
        ts = start_ts + i * interval
        t  = i / max(count - 1, 1)           # normalised progress 0.0 → 1.0

        # ── Battery voltage (mV) ─────────────────────────────────────────
        if t < 0.30:
            bv = int(12_500 + 1_500 * (t / 0.30))
        elif t < 0.55:
            bv = 14_000
        elif t < 0.75:
            bv = int(14_000 - 2_900 * ((t - 0.55) / 0.20))
        elif t < 0.88:
            bv = rng.randint(10_800, 11_800)  # deep discharge window
        else:
            bv = int(11_000 + 3_000 * ((t - 0.88) / 0.12))
        bv += rng.randint(-150, 150)
        bv  = max(9_000, min(15_000, bv))

        # ── Battery temperature (°C, signed) ────────────────────────────
        bt = int(22 + 5 * math.sin(2 * math.pi * t)) + rng.randint(-2, 2)
        bt = max(10, min(45, bt))

        # ── MSI temperature (°C) ─────────────────────────────────────────
        if 0.74 <= t <= 0.88:
            mt = rng.randint(42, 56)          # thermal spike window
        else:
            mt = int(31 + 6 * math.sin(2 * math.pi * t * 3)) + rng.randint(-2, 2)
            mt = max(24, min(38, mt))

        # ── SSR used (MB) — gradual fill ─────────────────────────────────
        ssr = int(512 + 7_168 * t) + rng.randint(-256, 256)
        ssr = max(256, min(8_192, ssr))

        series.append({
            "packet_type":    "HOUSEKEEPING",
            "satellite_id":   satellite_id,
            "timestamp":      ts,
            "battery_voltage": bv,
            "battery_temp":   bt,
            "msi_temperature": mt,
            "ssr_used":       ssr,
            "raw_hex":        f"seed_{satellite_id}_{i:04d}",
        })

    return series


# ── Per-satellite seed function ────────────────────────────────────────────────

def seed_satellite(satellite_id: int, count: int, end_ts: int) -> None:
    series   = _make_series(satellite_id, count, end_ts)
    tel_ok   = 0
    alert_ok = 0

    for rec in series:
        if db.store_telemetry(rec):
            tel_ok += 1
        for alert in check_health(rec):
            if db.store_alert(alert):
                alert_ok += 1

    logger.info(
        "  Satellite %-3d  telemetry: %3d inserted   alerts: %2d inserted",
        satellite_id, tel_ok, alert_ok,
    )


# ── Optional clear helper ──────────────────────────────────────────────────────

def clear_satellite(satellite_id: int) -> None:
    """Delete all existing telemetry + alert docs for one satellite."""
    from config import TELEMETRY_COLLECTION, ALERTS_COLLECTION
    mongo_db = db.get_db()
    t_del = mongo_db[TELEMETRY_COLLECTION].delete_many({"satellite_id": satellite_id})
    a_del = mongo_db[ALERTS_COLLECTION].delete_many({"satellite_id": satellite_id})
    logger.info(
        "  Satellite %-3d  cleared %d telemetry, %d alerts",
        satellite_id, t_del.deleted_count, a_del.deleted_count,
    )


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(
        description="Seed MongoDB Atlas with realistic satellite telemetry data"
    )
    ap.add_argument(
        "--sat-ids", type=int, nargs="+", default=[1, 2],
        help="Satellite IDs to seed (default: 1 2)",
    )
    ap.add_argument(
        "--packets", type=int, default=48,
        help="Packets per satellite spanning the last 24 h (default: 48)",
    )
    ap.add_argument(
        "--clear", action="store_true",
        help="Delete existing data for the target satellites before inserting",
    )
    args = ap.parse_args()

    end_ts = int(time.time())

    print()
    print("=" * 56)
    print("  Satellite Telemetry — Seed Data Script")
    print("=" * 56)
    print(f"  Satellites : {args.sat_ids}")
    interval_min = 24 * 3600 // args.packets // 60
    print(f"  Packets    : {args.packets} per satellite  (~{interval_min} min intervals)")
    print(f"  Clear first: {'yes' if args.clear else 'no'}")
    print("=" * 56)
    print()

    for sat_id in args.sat_ids:
        if args.clear:
            logger.info("Clearing existing data for satellite %d…", sat_id)
            clear_satellite(sat_id)
        logger.info("Seeding satellite %d (%d packets)…", sat_id, args.packets)
        seed_satellite(sat_id, args.packets, end_ts)

    print()
    logger.info("Done.  Open the dashboard to see the data:")
    logger.info("  http://127.0.0.1:5000/")
    print()


if __name__ == "__main__":
    main()
