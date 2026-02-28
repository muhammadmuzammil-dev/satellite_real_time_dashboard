"""
api.py — FastAPI REST API

Endpoints
─────────
  GET /                                  → Serves the web dashboard
  GET /telemetry/latest?satellite_id=ID  → Latest HK record for a satellite
  GET /telemetry/history?satellite_id=ID&from=TS&to=TS
                                         → HK records within a time range
  GET /alerts?satellite_id=ID            → Active alerts for a satellite
  GET /stats/satellite/{id}              → Aggregated statistics (uses aggregation pipeline)

Interactive API docs are available automatically at:
  http://127.0.0.1:5000/docs   (Swagger UI)
  http://127.0.0.1:5000/redoc  (ReDoc)
"""

import os
import logging
from typing import List, Optional

from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

import database as db

logger = logging.getLogger(__name__)

# ── Application ───────────────────────────────────────────────────────────────
app = FastAPI(
    title="Satellite Telemetry API",
    description=(
        "Real-time telemetry processing system for satellite ground stations.\n\n"
        "Parses incoming UDP telemetry frames, stores them in MongoDB, monitors "
        "health thresholds, and exposes this REST API for querying."
    ),
    version="1.0.0",
)

# ── Pydantic response models (for Swagger docs) ───────────────────────────────
class HousekeepingRecord(BaseModel):
    packet_type:     str
    satellite_id:    int
    timestamp:       int
    battery_voltage: int
    battery_temp:    int
    msi_temperature: int
    ssr_used:        int
    raw_hex:         str
    received_at:     Optional[float] = None

    class Config:
        extra = "allow"  # MongoDB may return extra fields


class AlertRecord(BaseModel):
    level:            str
    field:            str
    value:            float
    message:          str
    satellite_id:     int
    timestamp:        int
    packet_timestamp: int
    active:           bool
    created_at:       Optional[float] = None

    class Config:
        extra = "allow"


class TelemetryHistoryResponse(BaseModel):
    satellite_id: int
    count:        int
    records:      List[HousekeepingRecord]


class AlertsResponse(BaseModel):
    satellite_id: int
    count:        int
    alerts:       List[AlertRecord]


class StatsResponse(BaseModel):
    satellite_id:            int
    avg_battery_voltage_mv:  Optional[float]
    min_battery_voltage_mv:  Optional[float]
    max_battery_voltage_mv:  Optional[float]
    avg_msi_temperature_c:   Optional[float]
    max_msi_temperature_c:   Optional[float]
    avg_battery_temp_c:      Optional[float]
    max_ssr_used_mb:         Optional[float]
    total_packets:           int
    first_packet_ts:         Optional[int]
    last_packet_ts:          Optional[int]

    class Config:
        extra = "allow"


# ── Static files & dashboard ──────────────────────────────────────────────────
_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


@app.get("/", include_in_schema=False)
def dashboard():
    """Serve the web dashboard."""
    html_path = os.path.join(_STATIC_DIR, "dashboard.html")
    if not os.path.exists(html_path):
        raise HTTPException(status_code=404, detail="Dashboard not found")
    return FileResponse(html_path, media_type="text/html")


@app.get("/health/db", include_in_schema=False)
def db_health():
    """Ping MongoDB and return connection status, latency, and document counts."""
    result = db.ping_db()
    if result["status"] == "disconnected":
        return JSONResponse(status_code=503, content=result)
    return result


@app.get("/globe", include_in_schema=False)
def globe():
    """Serve the 3D satellite globe view."""
    html_path = os.path.join(_STATIC_DIR, "globe.html")
    if not os.path.exists(html_path):
        raise HTTPException(status_code=404, detail="Globe not found")
    return FileResponse(html_path, media_type="text/html")


# ── Telemetry endpoints ───────────────────────────────────────────────────────

@app.get(
    "/telemetry/latest",
    response_model=HousekeepingRecord,
    summary="Latest telemetry",
    description="Returns the most recent Housekeeping record for the given satellite.",
)
def get_latest_telemetry(
    satellite_id: int = Query(..., description="Satellite ID", examples=[1]),
):
    data = db.get_latest_telemetry(satellite_id)
    if data is None:
        raise HTTPException(
            status_code=404,
            detail=f"No telemetry found for satellite_id={satellite_id}",
        )
    return data


@app.get(
    "/telemetry/history",
    response_model=TelemetryHistoryResponse,
    summary="Telemetry history",
    description="Returns Housekeeping records for a satellite within a Unix timestamp range.",
)
def get_telemetry_history(
    satellite_id: int = Query(..., description="Satellite ID", examples=[1]),
    from_ts: int = Query(..., alias="from", description="Start Unix timestamp", examples=[1700000000]),
    to_ts:   int = Query(..., alias="to",   description="End Unix timestamp",   examples=[1700003600]),
):
    if from_ts > to_ts:
        raise HTTPException(
            status_code=400,
            detail="'from' timestamp must be less than or equal to 'to' timestamp",
        )
    records = db.get_telemetry_history(satellite_id, from_ts, to_ts)
    return {"satellite_id": satellite_id, "count": len(records), "records": records}


# ── Alerts endpoint ───────────────────────────────────────────────────────────

@app.get(
    "/alerts",
    response_model=AlertsResponse,
    summary="Active alerts",
    description="Returns all active health alerts for a satellite, newest first.",
)
def get_alerts(
    satellite_id: int = Query(..., description="Satellite ID", examples=[1]),
):
    alerts = db.get_active_alerts(satellite_id)
    return {"satellite_id": satellite_id, "count": len(alerts), "alerts": alerts}


# ── Statistics endpoint ───────────────────────────────────────────────────────

@app.get(
    "/stats/satellite/{satellite_id}",
    response_model=StatsResponse,
    summary="Satellite statistics",
    description=(
        "Returns aggregated statistics for a satellite computed via "
        "MongoDB aggregation pipeline: avg/min/max battery voltage, "
        "avg/max MSI temperature, max SSR used, total packet count."
    ),
)
def get_satellite_stats(satellite_id: int):
    data = db.get_satellite_stats(satellite_id)
    if data is None:
        raise HTTPException(
            status_code=404,
            detail=f"No data found for satellite_id={satellite_id}",
        )
    return data
