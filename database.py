"""
database.py — MongoDB Integration

Collections
───────────
  telemetry  — Stores every valid Housekeeping packet received.
  alerts     — Stores every triggered health alert.

Indexing Strategy
─────────────────
  telemetry:
    { satellite_id: 1, timestamp: -1 }   compound — fast per-satellite queries
    { timestamp: -1 }                    — fast time-range scans

  alerts:
    { satellite_id: 1, timestamp: -1 }   — fast per-satellite alert queries
    { satellite_id: 1, field: 1,          UNIQUE — deduplication key:
      packet_timestamp: 1 }               one alert per (satellite, field, packet)

Duplicate Alert Handling (Design Decision)
──────────────────────────────────────────
  A unique compound index on (satellite_id, field, packet_timestamp) ensures
  that if the same packet is reprocessed — due to a retransmit, restart, or
  bug — the duplicate alert is silently discarded via a DuplicateKeyError catch.
  This approach is atomic and requires no extra read-before-write logic.
"""

import logging
import time
from typing import Optional, List, Dict, Any

import certifi

from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError

from config import MONGODB_URI, DB_NAME, TELEMETRY_COLLECTION, ALERTS_COLLECTION

logger = logging.getLogger(__name__)

# ── Lazy-initialised module singletons ───────────────────────────────────────
_client: Optional[MongoClient] = None
_db = None


def get_db():
    """Return the MongoDB database handle; creates it on first call."""
    global _client, _db
    if _client is None:
        _client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000, tls=True, tlsCAFile=certifi.where())
        _db = _client[DB_NAME]
        _create_indexes()
    return _db


def _create_indexes() -> None:
    """Create all required indexes (idempotent — safe to call on every start)."""
    try:
        db = _db

        # ── telemetry ─────────────────────────────────────────────────────
        db[TELEMETRY_COLLECTION].create_index(
            [("satellite_id", ASCENDING), ("timestamp", DESCENDING)],
            name="sat_ts_compound",
        )
        db[TELEMETRY_COLLECTION].create_index(
            [("timestamp", DESCENDING)],
            name="ts_desc",
        )

        # ── alerts ────────────────────────────────────────────────────────
        db[ALERTS_COLLECTION].create_index(
            [("satellite_id", ASCENDING), ("timestamp", DESCENDING)],
            name="alert_sat_ts",
        )
        # Unique deduplication index
        db[ALERTS_COLLECTION].create_index(
            [
                ("satellite_id", ASCENDING),
                ("field", ASCENDING),
                ("packet_timestamp", ASCENDING),
            ],
            unique=True,
            name="alert_dedup",
        )

        logger.info("MongoDB indexes verified / created.")
    except Exception as exc:
        logger.error(f"Index creation error: {exc}")


# ── Write operations ──────────────────────────────────────────────────────────

def store_telemetry(packet: Dict[str, Any]) -> Optional[str]:
    """
    Persist a validated Housekeeping packet.

    Returns the inserted _id as a hex string, or None on failure.
    """
    try:
        doc = {**packet, "received_at": time.time()}
        result = get_db()[TELEMETRY_COLLECTION].insert_one(doc)
        return str(result.inserted_id)
    except Exception as exc:
        logger.error(f"store_telemetry: {exc}")
        return None


def store_alert(alert: Dict[str, Any]) -> bool:
    """
    Persist a health alert.

    Silently ignores duplicates (same satellite_id + field + packet_timestamp).
    Returns True if stored, False if duplicate or on error.
    """
    try:
        doc = {**alert, "created_at": time.time(), "active": True}
        get_db()[ALERTS_COLLECTION].insert_one(doc)
        return True
    except DuplicateKeyError:
        logger.debug(
            "Duplicate alert suppressed — sat=%s field=%s ts=%s",
            alert.get("satellite_id"),
            alert.get("field"),
            alert.get("packet_timestamp"),
        )
        return False
    except Exception as exc:
        logger.error(f"store_alert: {exc}")
        return False


# ── Read operations ───────────────────────────────────────────────────────────

def get_latest_telemetry(satellite_id: int) -> Optional[Dict[str, Any]]:
    """Return the most recent Housekeeping record for a satellite.
    Secondary sort on received_at breaks ties when multiple packets share the
    same Unix-epoch second timestamp.
    """
    try:
        return get_db()[TELEMETRY_COLLECTION].find_one(
            {"satellite_id": satellite_id},
            sort=[("timestamp", DESCENDING), ("received_at", DESCENDING)],
            projection={"_id": 0},
        )
    except Exception as exc:
        logger.error(f"get_latest_telemetry: {exc}")
        return None


def get_telemetry_history(
    satellite_id: int, from_ts: int, to_ts: int
) -> List[Dict[str, Any]]:
    """Return Housekeeping records for a satellite within [from_ts, to_ts]."""
    try:
        cursor = get_db()[TELEMETRY_COLLECTION].find(
            {
                "satellite_id": satellite_id,
                "timestamp": {"$gte": from_ts, "$lte": to_ts},
            },
            sort=[("timestamp", ASCENDING)],
            projection={"_id": 0},
        )
        return list(cursor)
    except Exception as exc:
        logger.error(f"get_telemetry_history: {exc}")
        return []


def get_active_alerts(satellite_id: int) -> List[Dict[str, Any]]:
    """Return all active alerts for a satellite, newest first."""
    try:
        return list(
            get_db()[ALERTS_COLLECTION].find(
                {"satellite_id": satellite_id, "active": True},
                sort=[("timestamp", DESCENDING)],
                projection={"_id": 0},
            )
        )
    except Exception as exc:
        logger.error(f"get_active_alerts: {exc}")
        return []


def ping_db() -> Dict[str, Any]:
    """
    Ping MongoDB and return connection health metrics.

    Returns a dict with status, round-trip latency in ms, and estimated
    document counts for both collections.
    """
    try:
        database = get_db()
        t0 = time.time()
        database.command("ping")
        latency_ms = round((time.time() - t0) * 1000, 1)
        tel_count = database[TELEMETRY_COLLECTION].estimated_document_count()
        alt_count = database[ALERTS_COLLECTION].estimated_document_count()
        return {
            "status": "connected",
            "latency_ms": latency_ms,
            "telemetry_docs": tel_count,
            "alert_docs": alt_count,
        }
    except Exception as exc:
        logger.warning("ping_db failed: %s", exc)
        return {"status": "disconnected", "error": str(exc)}


def get_satellite_stats(satellite_id: int) -> Optional[Dict[str, Any]]:
    """
    Return aggregated statistics for a satellite using MongoDB aggregation pipeline.

    Computes per-satellite: avg/min/max battery voltage, avg/max MSI temperature,
    avg battery temp, max SSR used, total packet count, first/last seen timestamps.
    """
    try:
        pipeline = [
            {"$match": {"satellite_id": satellite_id}},
            {
                "$group": {
                    "_id": "$satellite_id",
                    "avg_battery_voltage_mv": {"$avg": "$battery_voltage"},
                    "min_battery_voltage_mv": {"$min": "$battery_voltage"},
                    "max_battery_voltage_mv": {"$max": "$battery_voltage"},
                    "avg_msi_temperature_c":  {"$avg": "$msi_temperature"},
                    "max_msi_temperature_c":  {"$max": "$msi_temperature"},
                    "avg_battery_temp_c":     {"$avg": "$battery_temp"},
                    "max_ssr_used_mb":        {"$max": "$ssr_used"},
                    "total_packets":          {"$sum": 1},
                    "first_packet_ts":        {"$min": "$timestamp"},
                    "last_packet_ts":         {"$max": "$timestamp"},
                }
            },
        ]
        results = list(get_db()[TELEMETRY_COLLECTION].aggregate(pipeline))
        if not results:
            return None
        doc = results[0]
        doc.pop("_id", None)
        doc["satellite_id"] = satellite_id
        return doc
    except Exception as exc:
        logger.error(f"get_satellite_stats: {exc}")
        return None
