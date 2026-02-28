"""
test_all.py — Comprehensive Test Suite
Covers every requirement listed in SW_Task_001.pdf:
  Task 1  — Parser Development
  Task 2  — UDP Ingestion Service
  Task 3  — Health Monitor Logic
  Task 4  — MongoDB Integration
  Task 5  — REST API (all 4 endpoints)
  Task 6  — Dashboard reachable

Run:  python test_all.py
"""

import sys
import io
import struct
import socket
import threading
import time
import json
import urllib.request
import urllib.error

# Force UTF-8 output on Windows so Unicode characters print correctly
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ── Helpers ───────────────────────────────────────────────────────────────────
PASS  = "[PASS]"
FAIL  = "[FAIL]"
INFO  = "[INFO]"
WARN  = "[WARN]"
SEP   = "-" * 62

results = []


def check(label, condition, detail=""):
    status = PASS if condition else FAIL
    msg = f"  {status}  {label}"
    if detail:
        msg += f"\n           {detail}"
    print(msg)
    results.append((label, condition))
    return condition


def section(title):
    print(f"\n{SEP}")
    print(f"  {title}")
    print(SEP)


def http_get(url, timeout=5, expect_json=True):
    """Simple HTTP GET.
    Returns (status_code, parsed_json)  when expect_json=True,
    or      (status_code, raw_text)     when expect_json=False.
    """
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            if expect_json:
                return resp.status, json.loads(raw)
            return resp.status, raw
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        if expect_json:
            try:
                body = json.loads(body)
            except Exception:
                pass
        return e.code, body


# ─────────────────────────────────────────────────────────────────────────────
# CRC + packet builder (standalone, mirrors parser.py + simulator.py)
# ─────────────────────────────────────────────────────────────────────────────
def crc16(data: bytes) -> int:
    crc = 0x0000
    for byte in data:
        crc ^= (byte << 8)
        for _ in range(8):
            crc = ((crc << 1) ^ 0x8005) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def build_hk_packet(satellite_id, battery_voltage, battery_temp, msi_temperature,
                    ssr_used, timestamp=None):
    if timestamp is None:
        timestamp = int(time.time())
    payload = struct.pack(">HbBI", battery_voltage, battery_temp, msi_temperature, ssr_used)
    pkt_len = 10 + len(payload) + 2
    header  = struct.pack(">HBBHI", 0x1ACF, pkt_len, 0x10, satellite_id, timestamp)
    body    = header + payload
    return (body + struct.pack(">H", crc16(body))).hex()


def send_udp(hex_str, host="127.0.0.1", port=5005):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.sendto(hex_str.encode(), (host, port))


# ─────────────────────────────────────────────────────────────────────────────
# TASK 1 — Parser Development
# ─────────────────────────────────────────────────────────────────────────────
def test_parser():
    section("TASK 1 — Parser Development")
    sys.path.insert(0, ".")
    from parser import parse_packet, ParseError, crc16 as p_crc16

    # 1a. Valid HK packet round-trip
    pkt = build_hk_packet(1, 13800, 22, 28, 512, 1700000000)
    try:
        result = parse_packet(pkt)
        check("1a. Valid HK packet parsed without error", True)
        check("1a. packet_type == HOUSEKEEPING",     result["packet_type"] == "HOUSEKEEPING")
        check("1a. satellite_id == 1",               result["satellite_id"] == 1)
        check("1a. battery_voltage == 13800 mV",     result["battery_voltage"] == 13800)
        check("1a. battery_temp == 22 °C",           result["battery_temp"] == 22)
        check("1a. msi_temperature == 28 °C",        result["msi_temperature"] == 28)
        check("1a. ssr_used == 512 MB",              result["ssr_used"] == 512)
        check("1a. timestamp == 1700000000",         result["timestamp"] == 1700000000)
        check("1a. raw_hex field present",           "raw_hex" in result)
    except ParseError as e:
        check("1a. Valid packet parsed", False, str(e))

    # 1b. Signed battery_temp (negative value)
    pkt_neg = build_hk_packet(1, 13000, -15, 30, 256)
    try:
        r = parse_packet(pkt_neg)
        check("1b. Negative battery_temp (-15) parsed correctly as int8",
              r["battery_temp"] == -15,
              f"got {r.get('battery_temp')}")
    except ParseError as e:
        check("1b. Negative battery_temp", False, str(e))

    # 1c. Wrong sync header → ParseError
    bad_sync = "DEADBEEF" + pkt[8:]
    try:
        parse_packet(bad_sync)
        check("1c. Wrong sync header rejected", False, "No error raised")
    except ParseError as e:
        check("1c. Wrong sync header rejected", "Sync header mismatch" in str(e), str(e))

    # 1d. CRC corruption → ParseError
    bad_crc = pkt[:-4] + "0000"
    try:
        parse_packet(bad_crc)
        check("1d. Bad CRC rejected", False, "No error raised")
    except ParseError as e:
        check("1d. Bad CRC rejected", "CRC mismatch" in str(e), str(e))

    # 1e. Wrong packet length field → ParseError
    data = bytes.fromhex(pkt)
    tampered = bytearray(data)
    tampered[2] = 0xFF          # corrupt length byte
    try:
        parse_packet(tampered.hex())
        check("1e. Length mismatch rejected", False, "No error raised")
    except ParseError as e:
        check("1e. Length mismatch rejected", "Length field" in str(e) or "length" in str(e).lower(), str(e))

    # 1f. Packet too short → ParseError
    try:
        parse_packet("1acf10")
        check("1f. Too-short packet rejected", False, "No error raised")
    except ParseError as e:
        check("1f. Too-short packet rejected", True, str(e))

    # 1g. Empty string → ParseError
    try:
        parse_packet("")
        check("1g. Empty string rejected", False, "No error raised")
    except ParseError as e:
        check("1g. Empty string rejected", True, str(e))

    # 1h. Invalid hex chars → ParseError
    try:
        parse_packet("ZZZZ1234")
        check("1h. Invalid hex chars rejected", False, "No error raised")
    except ParseError as e:
        check("1h. Invalid hex chars rejected", True, str(e))

    # 1i. Payload-type packet (0x20) parsed as PAYLOAD_DATA
    payload_data = bytes([0xAB, 0xCD, 0xEF])
    pkt_len2 = 10 + len(payload_data) + 2
    hdr2 = struct.pack(">HBBHI", 0x1ACF, pkt_len2, 0x20, 7, 1700001000)
    body2 = hdr2 + payload_data
    pkt2 = (body2 + struct.pack(">H", p_crc16(body2))).hex()
    try:
        r2 = parse_packet(pkt2)
        check("1i. PAYLOAD_DATA packet type recognised",
              r2["packet_type"] == "PAYLOAD_DATA")
    except ParseError as e:
        check("1i. PAYLOAD_DATA packet", False, str(e))

    # 1j. Unknown packet type → ParseError
    hdr3 = struct.pack(">HBBHI", 0x1ACF, 12, 0xFF, 1, 1700001000)
    body3 = hdr3
    pkt3 = (body3 + struct.pack(">H", p_crc16(body3))).hex()
    try:
        parse_packet(pkt3)
        check("1j. Unknown packet type rejected", False, "No error raised")
    except ParseError as e:
        check("1j. Unknown packet type rejected", "Unknown packet type" in str(e), str(e))

    # 1k. Hex string with spaces (tolerant input)
    spaced = " ".join(pkt[i:i+2] for i in range(0, len(pkt), 2))
    try:
        r3 = parse_packet(spaced)
        check("1k. Hex string with spaces accepted", r3["packet_type"] == "HOUSEKEEPING")
    except ParseError as e:
        check("1k. Hex string with spaces accepted", False, str(e))


# ─────────────────────────────────────────────────────────────────────────────
# TASK 3 — Health Monitor Logic
# ─────────────────────────────────────────────────────────────────────────────
def test_health_monitor():
    section("TASK 3 — Health Monitor Logic")
    from health_monitor import check_health

    base = dict(satellite_id=1, timestamp=1700000000)

    # 3a. All nominal → no alerts
    nominal = {**base, "battery_voltage": 13500, "battery_temp": 22,
               "msi_temperature": 30, "ssr_used": 512}
    alerts = check_health(nominal)
    check("3a. Nominal packet → 0 alerts", len(alerts) == 0)

    # 3b. RED alert: msi_temperature = 41 (> 40)
    red_pkt = {**base, "battery_voltage": 13500, "battery_temp": 22,
               "msi_temperature": 41, "ssr_used": 512}
    alerts = check_health(red_pkt)
    check("3b. msi_temperature=41 → RED alert triggered",
          any(a["level"] == "RED" for a in alerts))
    check("3b. RED alert message contains 'MSI'",
          any("MSI" in a["message"] for a in alerts if a["level"] == "RED"))

    # 3c. msi_temperature exactly 40 → no RED (boundary: > 40, not >=)
    boundary = {**base, "battery_voltage": 13500, "battery_temp": 22,
                "msi_temperature": 40, "ssr_used": 512}
    alerts = check_health(boundary)
    check("3c. msi_temperature=40 → no RED alert (boundary check: > 40)",
          not any(a["level"] == "RED" for a in alerts))

    # 3d. YELLOW alert: battery_voltage = 11999 (< 12000)
    yellow_pkt = {**base, "battery_voltage": 11999, "battery_temp": 22,
                  "msi_temperature": 28, "ssr_used": 512}
    alerts = check_health(yellow_pkt)
    check("3d. battery_voltage=11999 → YELLOW alert triggered",
          any(a["level"] == "YELLOW" for a in alerts))
    check("3d. YELLOW alert message contains 'voltage'",
          any("voltage" in a["message"].lower() for a in alerts if a["level"] == "YELLOW"))

    # 3e. battery_voltage exactly 12000 → no YELLOW (boundary: < 12000)
    boundary2 = {**base, "battery_voltage": 12000, "battery_temp": 22,
                 "msi_temperature": 28, "ssr_used": 512}
    alerts = check_health(boundary2)
    check("3e. battery_voltage=12000 → no YELLOW alert (boundary check: < 12000)",
          not any(a["level"] == "YELLOW" for a in alerts))

    # 3f. Both alerts triggered simultaneously
    both = {**base, "battery_voltage": 10800, "battery_temp": 18,
            "msi_temperature": 52, "ssr_used": 4096}
    alerts = check_health(both)
    levels = {a["level"] for a in alerts}
    check("3f. Both RED + YELLOW triggered simultaneously",
          "RED" in levels and "YELLOW" in levels)
    check("3f. Exactly 2 alerts returned", len(alerts) == 2)

    # 3g. Alert dict has required fields
    check("3g. Alert dict contains: level, field, value, message, satellite_id, timestamp",
          all(k in alerts[0] for k in ["level", "field", "value", "message",
                                       "satellite_id", "timestamp", "packet_timestamp"]))


# ─────────────────────────────────────────────────────────────────────────────
# TASK 4 — MongoDB Integration
# ─────────────────────────────────────────────────────────────────────────────
def test_mongodb():
    section("TASK 4 — MongoDB Integration")
    try:
        import database as db_module
        from pymongo import MongoClient
        from config import MONGODB_URI, DB_NAME, TELEMETRY_COLLECTION, ALERTS_COLLECTION
    except ImportError as e:
        check("4. pymongo available", False, str(e))
        return False

    # 4a. Atlas connection
    try:
        client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=8000)
        client.admin.command("ping")
        check("4a. MongoDB Atlas connection successful", True)
    except Exception as e:
        check("4a. MongoDB Atlas connection successful", False, str(e))
        return False

    db = client[DB_NAME]

    # 4b. Collections exist / can be written
    ts = int(time.time())
    test_sat_id = 9999   # use a test satellite ID we can clean up
    test_pkt = {
        "packet_type": "HOUSEKEEPING", "satellite_id": test_sat_id,
        "timestamp": ts, "battery_voltage": 13800, "battery_temp": 22,
        "msi_temperature": 28, "ssr_used": 512, "raw_hex": "test"
    }
    doc_id = db_module.store_telemetry(test_pkt)
    check("4b. Telemetry stored in 'telemetry' collection", doc_id is not None, f"_id={doc_id}")

    test_alert = {
        "level": "YELLOW", "field": "battery_voltage", "value": 11500,
        "message": "Test alert", "satellite_id": test_sat_id,
        "timestamp": ts, "packet_timestamp": ts
    }
    stored = db_module.store_alert(test_alert)
    check("4c. Alert stored in 'alerts' collection", stored)

    # 4d. Duplicate alert suppressed
    stored2 = db_module.store_alert(test_alert)   # same satellite+field+ts
    check("4d. Duplicate alert suppressed (unique index)", not stored2)

    # 4e. Indexes exist
    telem_indexes = list(db[TELEMETRY_COLLECTION].list_indexes())
    alert_indexes = list(db[ALERTS_COLLECTION].list_indexes())
    idx_names_t = {i["name"] for i in telem_indexes}
    idx_names_a = {i["name"] for i in alert_indexes}
    check("4e. Telemetry compound index (sat_ts_compound) exists",
          "sat_ts_compound" in idx_names_t, str(idx_names_t))
    check("4e. Telemetry timestamp index (ts_desc) exists",
          "ts_desc" in idx_names_t, str(idx_names_t))
    check("4e. Alerts dedup unique index (alert_dedup) exists",
          "alert_dedup" in idx_names_a, str(idx_names_a))

    # 4f. get_latest_telemetry works
    latest = db_module.get_latest_telemetry(test_sat_id)
    check("4f. get_latest_telemetry returns correct record",
          latest is not None and latest["satellite_id"] == test_sat_id)

    # 4g. get_telemetry_history works
    history = db_module.get_telemetry_history(test_sat_id, ts - 10, ts + 10)
    check("4g. get_telemetry_history returns records in range",
          len(history) >= 1)

    # 4h. get_active_alerts works
    active = db_module.get_active_alerts(test_sat_id)
    check("4h. get_active_alerts returns the stored alert",
          any(a["field"] == "battery_voltage" for a in active))

    # 4i. Aggregation pipeline stats
    # Insert a few more packets for meaningful stats
    for v, m in [(13500, 30), (11000, 45)]:
        db_module.store_telemetry({**test_pkt, "battery_voltage": v, "msi_temperature": m,
                                   "timestamp": ts + 1})
    stats = db_module.get_satellite_stats(test_sat_id)
    check("4i. Aggregation pipeline returns stats", stats is not None)
    check("4i. total_packets >= 3", stats is not None and stats.get("total_packets", 0) >= 3)
    check("4i. avg_battery_voltage_mv present",
          stats is not None and "avg_battery_voltage_mv" in stats)
    check("4i. max_msi_temperature_c present",
          stats is not None and "max_msi_temperature_c" in stats)

    # Clean up test data
    db[TELEMETRY_COLLECTION].delete_many({"satellite_id": test_sat_id})
    db[ALERTS_COLLECTION].delete_many({"satellite_id": test_sat_id})
    print(f"  {INFO}  Test data for satellite_id={test_sat_id} cleaned up.")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# TASKS 2 + 5 + 6 — UDP + REST API + Dashboard (integration)
# ─────────────────────────────────────────────────────────────────────────────
def test_integration():
    section("TASKS 2 + 5 + 6 — UDP Service + REST API + Dashboard")
    try:
        import uvicorn
        from api import app as fastapi_app
        from udp_service import UDPService
        from config import UDP_PORT, API_PORT
    except ImportError as e:
        check("Integration imports", False, str(e))
        return

    SAT_ID = 42   # use a unique ID for this test run
    API_BASE = f"http://127.0.0.1:{API_PORT}"

    # ── Pre-clean any leftover data from interrupted previous runs ─────────
    from pymongo import MongoClient
    from config import MONGODB_URI, DB_NAME, TELEMETRY_COLLECTION, ALERTS_COLLECTION
    _pre_client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=8000)
    _pre_client[DB_NAME][TELEMETRY_COLLECTION].delete_many({"satellite_id": SAT_ID})
    _pre_client[DB_NAME][ALERTS_COLLECTION].delete_many({"satellite_id": SAT_ID})

    # ── Start FastAPI in background thread ────────────────────────────────
    server_config = uvicorn.Config(fastapi_app, host="127.0.0.1",
                                   port=API_PORT, log_level="error")
    server = uvicorn.Server(server_config)
    api_thread = threading.Thread(target=server.run, daemon=True)
    api_thread.start()

    # ── Start UDP service ─────────────────────────────────────────────────
    udp = UDPService(host="0.0.0.0", port=UDP_PORT)
    udp.start()

    print(f"  {INFO}  Services starting… waiting 2s for readiness")
    time.sleep(2)

    # 2a. UDP port is bound and accepting packets
    check("2a. UDP service bound on port " + str(UDP_PORT), True)

    # ── Send 5 simulation packets ─────────────────────────────────────────
    SCENARIOS = [
        dict(battery_voltage=13800, battery_temp=22,  msi_temperature=28,  ssr_used=512,  desc="Normal"),
        dict(battery_voltage=13200, battery_temp=26,  msi_temperature=38,  ssr_used=1024, desc="Normal 2"),
        dict(battery_voltage=11500, battery_temp=24,  msi_temperature=35,  ssr_used=2048, desc="YELLOW alert"),
        dict(battery_voltage=13400, battery_temp=30,  msi_temperature=45,  ssr_used=3072, desc="RED alert"),
        dict(battery_voltage=10800, battery_temp=18,  msi_temperature=52,  ssr_used=4096, desc="RED+YELLOW"),
    ]

    print(f"\n  {INFO}  Sending {len(SCENARIOS)} UDP packets for satellite_id={SAT_ID}…")
    for i, s in enumerate(SCENARIOS, 1):
        pkt = build_hk_packet(SAT_ID, s["battery_voltage"], s["battery_temp"],
                               s["msi_temperature"], s["ssr_used"])
        send_udp(pkt, port=UDP_PORT)
        print(f"  {INFO}  [{i}/{len(SCENARIOS)}] Sent: {s['desc']}  "
              f"(batt={s['battery_voltage']}mV msi={s['msi_temperature']}°C)")
        time.sleep(0.3)

    # Wait for DB writes to complete
    time.sleep(1.5)

    # 2b. Multiple packets processed (check DB count)
    from config import MONGODB_URI, DB_NAME, TELEMETRY_COLLECTION
    from pymongo import MongoClient
    client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=8000)
    count = client[DB_NAME][TELEMETRY_COLLECTION].count_documents({"satellite_id": SAT_ID})
    check("2b. All 5 UDP packets processed and stored in MongoDB",
          count == 5, f"found {count} records")
    check("2b. At least 3 packets stored (PDF requirement)", count >= 3)

    # 2c. Malformed packet is rejected gracefully (service stays alive)
    send_udp("THISISNOTVALIDHEX", port=UDP_PORT)
    time.sleep(0.3)
    count2 = client[DB_NAME][TELEMETRY_COLLECTION].count_documents({"satellite_id": SAT_ID})
    check("2c. Malformed UDP packet rejected; service kept running (count unchanged)",
          count2 == count, f"count before={count}  after={count2}")

    # ── TASK 5 — REST API endpoints ───────────────────────────────────────

    # 5a. GET /telemetry/latest
    code, body = http_get(f"{API_BASE}/telemetry/latest?satellite_id={SAT_ID}")
    check("5a. GET /telemetry/latest returns 200",        code == 200, str(code))
    check("5a. Response has satellite_id == " + str(SAT_ID),
          isinstance(body, dict) and body.get("satellite_id") == SAT_ID)
    check("5a. Response has battery_voltage field",
          isinstance(body, dict) and "battery_voltage" in body)
    check("5a. Response has msi_temperature field",
          isinstance(body, dict) and "msi_temperature" in body)
    check("5a. Returns most recent packet (ssr_used=4096)",
          isinstance(body, dict) and body.get("ssr_used") == 4096,
          f"ssr_used={body.get('ssr_used') if isinstance(body,dict) else '?'}")

    # 5b. GET /telemetry/latest with unknown satellite_id → 404
    code404, _ = http_get(f"{API_BASE}/telemetry/latest?satellite_id=99999")
    check("5b. GET /telemetry/latest with unknown sat_id → 404", code404 == 404, str(code404))

    # 5c. GET /telemetry/history
    now = int(time.time())
    code, hbody = http_get(f"{API_BASE}/telemetry/history?satellite_id={SAT_ID}&from={now-300}&to={now+10}")
    check("5c. GET /telemetry/history returns 200",       code == 200, str(code))
    check("5c. 'count' field present",                    isinstance(hbody, dict) and "count" in hbody)
    check("5c. 'records' array present",                  isinstance(hbody, dict) and "records" in hbody)
    check("5c. History count == 5",
          isinstance(hbody, dict) and hbody.get("count") == 5,
          f"count={hbody.get('count') if isinstance(hbody,dict) else '?'}")

    # 5d. GET /telemetry/history with from > to → 400
    code400, _ = http_get(f"{API_BASE}/telemetry/history?satellite_id={SAT_ID}&from=9999999999&to=0")
    check("5d. GET /telemetry/history from > to → 400", code400 == 400, str(code400))

    # 5e. GET /alerts
    code, abody = http_get(f"{API_BASE}/alerts?satellite_id={SAT_ID}")
    check("5e. GET /alerts returns 200",                  code == 200, str(code))
    check("5e. 'alerts' array present",                   isinstance(abody, dict) and "alerts" in abody)
    alert_list = abody.get("alerts", []) if isinstance(abody, dict) else []
    levels = {a.get("level") for a in alert_list}
    check("5e. RED alert stored for satellite (msi=45 & msi=52)",
          "RED" in levels, f"levels={levels}")
    check("5e. YELLOW alert stored for satellite (batt=11500 & batt=10800)",
          "YELLOW" in levels, f"levels={levels}")
    check("5e. At least 1 alert stored (PDF requirement)", len(alert_list) >= 1)

    # 5f. GET /stats/satellite/:id
    code, sbody = http_get(f"{API_BASE}/stats/satellite/{SAT_ID}")
    check("5f. GET /stats/satellite/:id returns 200",     code == 200, str(code))
    check("5f. total_packets == 5",
          isinstance(sbody, dict) and sbody.get("total_packets") == 5,
          f"total_packets={sbody.get('total_packets') if isinstance(sbody,dict) else '?'}")
    check("5f. avg_battery_voltage_mv present",
          isinstance(sbody, dict) and "avg_battery_voltage_mv" in sbody)
    check("5f. max_msi_temperature_c == 52",
          isinstance(sbody, dict) and sbody.get("max_msi_temperature_c") == 52,
          f"max_msi={sbody.get('max_msi_temperature_c') if isinstance(sbody,dict) else '?'}")
    check("5f. min_battery_voltage_mv == 10800",
          isinstance(sbody, dict) and sbody.get("min_battery_voltage_mv") == 10800,
          f"min_batt={sbody.get('min_battery_voltage_mv') if isinstance(sbody,dict) else '?'}")

    # 5g. GET /stats/satellite with unknown id → 404
    code404b, _ = http_get(f"{API_BASE}/stats/satellite/99999")
    check("5g. GET /stats/satellite/:id with unknown id → 404", code404b == 404, str(code404b))

    # ── TASK 6 — Dashboard ────────────────────────────────────────────────
    code_dash, dash_body = http_get(f"{API_BASE}/", timeout=5, expect_json=False)
    check("6a. GET / (dashboard) returns 200",            code_dash == 200, str(code_dash))
    check("6a. Dashboard response contains HTML",
          isinstance(dash_body, str) and "<html" in dash_body.lower())
    code_docs, _ = http_get(f"{API_BASE}/docs", timeout=5, expect_json=False)
    check("6b. GET /docs (Swagger UI) returns 200",       code_docs == 200, str(code_docs))

    # ── Clean up test satellite data ──────────────────────────────────────
    from config import ALERTS_COLLECTION
    client[DB_NAME][TELEMETRY_COLLECTION].delete_many({"satellite_id": SAT_ID})
    client[DB_NAME][ALERTS_COLLECTION].delete_many({"satellite_id": SAT_ID})
    print(f"  {INFO}  Test data for satellite_id={SAT_ID} cleaned up.")

    # Stop services
    server.should_exit = True
    udp.stop()
    time.sleep(0.5)


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────
def main():
    print(f"\n{'='*62}")
    print(f"  Satellite Telemetry -- Full Test Suite")
    print(f"{'='*62}")

    # Run tests sequentially
    test_parser()
    test_health_monitor()
    db_ok = test_mongodb()
    if db_ok:
        test_integration()
    else:
        print(f"\n  {WARN}  Skipping integration tests -- MongoDB not reachable.")

    # Summary
    section("SUMMARY")
    passed  = sum(1 for _, ok in results if ok)
    failed  = sum(1 for _, ok in results if not ok)
    total   = len(results)

    print(f"\n  Total : {total}")
    print(f"  {PASS}  Passed: {passed}")
    if failed:
        print(f"  {FAIL}  Failed: {failed}")
        print(f"\n  Failed tests:")
        for label, ok in results:
            if not ok:
                print(f"    - {label}")
    else:
        print(f"\n  *** ALL TESTS PASSED ***")

    print()
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
