"""
health_monitor.py — Satellite Health Monitoring & Alert Logic

Alert Rules (from specification):
  RED    — msi_temperature > 40 °C
  YELLOW — battery_voltage  < 12 000 mV

check_health() evaluates a parsed Housekeeping packet and returns a list of
triggered alert dicts. Each dict is ready to be stored directly in MongoDB.
"""

from typing import List, Dict, Any


# ── Alert rule table ──────────────────────────────────────────────────────────
# Plain dicts so new rules can be appended without touching any logic.
ALERT_RULES: List[Dict[str, Any]] = [
    {
        "level":     "RED",
        "field":     "msi_temperature",
        "condition": lambda v: v > 40,
        "message":   lambda v: (
            f"MSI Temperature critical: {v}°C exceeds the 40°C threshold"
        ),
    },
    {
        "level":     "YELLOW",
        "field":     "battery_voltage",
        "condition": lambda v: v < 12000,
        "message":   lambda v: (
            f"Battery voltage low: {v} mV is below the 12 000 mV threshold"
        ),
    },
]


def check_health(packet: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Evaluate health thresholds for a parsed Housekeeping packet.

    Args:
        packet: A Housekeeping packet dict as returned by parser.parse_packet().

    Returns:
        List of triggered alert dicts (empty list means all nominal).

        Each alert dict contains:
          level            — 'RED' or 'YELLOW'
          field            — name of the offending telemetry field
          value            — measured value that triggered the alert
          message          — human-readable description
          satellite_id     — from the source packet
          timestamp        — packet timestamp (Unix epoch)
          packet_timestamp — same as timestamp; used as deduplication key in DB
    """
    alerts: List[Dict[str, Any]] = []

    for rule in ALERT_RULES:
        value = packet.get(rule["field"])
        if value is None:
            continue
        if rule["condition"](value):
            alerts.append(
                {
                    "level":            rule["level"],
                    "field":            rule["field"],
                    "value":            value,
                    "message":          rule["message"](value),
                    "satellite_id":     packet["satellite_id"],
                    "timestamp":        packet["timestamp"],
                    "packet_timestamp": packet["timestamp"],
                }
            )

    return alerts
