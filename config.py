import os

# config.py — Central configuration for all services

UDP_HOST = "0.0.0.0"
UDP_PORT = 5005

API_HOST = "0.0.0.0"
# Render (and most PaaS) inject PORT; fall back to 5000 for local dev
API_PORT = int(os.getenv("PORT", 5000))

# Set MONGODB_URI as an environment variable in production (Render / GitHub secret).
# Falls back to the dev Atlas URI so local development keeps working without extra setup.
MONGODB_URI = os.getenv(
    "MONGODB_URI",
    "mongodb+srv://muzammildev46_db_user:ebBW99iYFm7a8sXj@cluster0.itir1uu.mongodb.net/",
)

DB_NAME               = "telemetry_db"
TELEMETRY_COLLECTION  = "telemetry"
ALERTS_COLLECTION     = "alerts"
