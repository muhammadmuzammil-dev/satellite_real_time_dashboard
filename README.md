# NASTP Satellite Telemetry System

Ground station software I built for receiving, parsing, and monitoring live housekeeping telemetry from satellites over UDP. It stores everything in MongoDB, flags health issues automatically, and gives you a web dashboard plus a 3D globe view to watch satellites in orbit.

---

## What it does

Satellites send hex-encoded telemetry packets over UDP every few seconds. This system:

- Listens on UDP port 5005 and parses incoming packets (validates CRC-16, unpacks fields)
- Checks battery voltage and MSI temperature against thresholds and raises alerts
- Stores every packet and alert in MongoDB Atlas
- Exposes a REST API so the frontend can query telemetry, alerts, and stats
- Serves two UIs — a classic dashboard and an interactive 3D globe

---

## Quick start

**1. Install dependencies**
```bash
pip install -r requirements.txt
```

**2. (Optional) Seed historical data**

If you don't have a live simulator running yet, seed the database with 24 hours of realistic data first:
```bash
python seed_data.py
```

**3. Start the server**
```bash
python main.py
```

Opens the dashboard automatically at `http://127.0.0.1:5000/`

The 3D globe is at `http://127.0.0.1:5000/globe`

**4. Send live packets (separate terminal)**
```bash
python simulator.py
```

The simulator cycles through five scenarios — nominal, slightly warm, battery low (YELLOW alert), MSI overtemp (RED alert), and both at once. Use `--sat-id 2` to target a second satellite.

---

## Project layout

```
NASTP/
├── main.py            starts everything (UDP listener + FastAPI)
├── config.py          ports, MongoDB URI, collection names
├── api.py             REST endpoints + serves the web UIs
├── parser.py          hex packet parser + CRC-16 check
├── health_monitor.py  alert threshold rules (RED / YELLOW)
├── database.py        MongoDB read/write + aggregation stats
├── udp_service.py     background thread that receives UDP packets
├── simulator.py       sends fake telemetry packets for testing
├── seed_data.py       inserts 24h of historical data into MongoDB
├── static/
│   ├── dashboard.html  classic telemetry dashboard
│   └── globe.html      3D interactive globe with satellite orbits
└── requirements.txt
```

---

## Packet format

Every packet starts with sync header `0x1ACF`. The housekeeping packet (type `0x10`) is 20 bytes total:

```
[0-1]   Sync header      0x1ACF
[2]     Packet length    20
[3]     Packet type      0x10
[4-5]   Satellite ID
[6-9]   Timestamp        Unix epoch (uint32)
[10-11] Battery voltage  mV (uint16)
[12]    Battery temp     °C signed (int8)
[13]    MSI temperature  °C (uint8)
[14-17] SSR used         MB (uint32)
[18-19] CRC-16
```

---

## Alert rules

| Level  | Trigger |
|--------|---------|
| YELLOW | battery_voltage < 12,000 mV |
| RED    | msi_temperature > 40°C |

Duplicate alerts (same satellite + field + packet timestamp) are automatically dropped by a unique MongoDB index — no extra logic needed.

---

## API endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/telemetry/latest?satellite_id=1` | Latest HK packet for a satellite |
| GET | `/telemetry/history?satellite_id=1&from=TS&to=TS` | HK packets within a time window |
| GET | `/alerts?satellite_id=1` | Active alerts for a satellite |
| GET | `/stats/satellite/1` | Aggregated stats (avg/min/max voltage, max MSI temp, etc.) |

Full interactive docs at `http://127.0.0.1:5000/docs`

---

## Configuration

Everything lives in `config.py`:

```python
UDP_HOST    = "0.0.0.0"
UDP_PORT    = 5005
API_HOST    = "0.0.0.0"
API_PORT    = 5000
MONGODB_URI = "mongodb+srv://..."   # Atlas connection string
DB_NAME     = "telemetry_db"
```

---

## Notes

- The MongoDB connection uses `certifi` for TLS — needed on Python 3.11+ where the system CA bundle sometimes doesn't work with Atlas SRV connections.
- `seed_data.py` inserts ~48 packets per satellite spanning the last 24 hours, with a battery anomaly and MSI thermal spike phase baked in so the alert system has something to flag.
- The globe tracks 5 satellites. NASTP-01 and NASTP-02 pull live data from the API. The other three are visual-only for now.
