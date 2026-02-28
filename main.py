"""
main.py — Application Entry Point

Starts three components in the correct order:

  1. UDPService  — background daemon thread listening on UDP_PORT
  2. FastAPI app — served by uvicorn on API_PORT (blocks the main thread)

The web dashboard is available at  http://127.0.0.1:5000/
Swagger API docs are at           http://127.0.0.1:5000/docs
"""

import logging
import signal
import sys
import webbrowser
from threading import Timer

import uvicorn

from config import UDP_HOST, UDP_PORT, API_HOST, API_PORT
from udp_service import UDPService

# ── Logging setup ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  [%(levelname)s]  %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def open_browser() -> None:
    """Open the dashboard in the default browser after a short delay."""
    url = f"http://127.0.0.1:{API_PORT}/"
    logger.info("Opening dashboard: %s", url)
    webbrowser.open(url)


def main() -> None:
    # ── Banner ────────────────────────────────────────────────────────────
    print()
    print("=" * 60)
    print("  Satellite Telemetry Monitor")
    print("=" * 60)
    print(f"  UDP listener : {UDP_HOST}:{UDP_PORT}")
    print(f"  Dashboard    : http://127.0.0.1:{API_PORT}/")
    print(f"  API docs     : http://127.0.0.1:{API_PORT}/docs")
    print("=" * 60)
    print()

    # ── UDP ingestion service ─────────────────────────────────────────────
    udp_service = UDPService(host=UDP_HOST, port=UDP_PORT)
    udp_service.start()

    # ── Graceful shutdown on Ctrl-C ───────────────────────────────────────
    def _shutdown(sig, frame):
        print("\nShutting down…")
        udp_service.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)

    # ── Auto-open browser (1 second after uvicorn is ready) ───────────────
    Timer(1.0, open_browser).start()

    # ── FastAPI via uvicorn (blocking — runs on the main thread) ──────────
    # Import here to avoid circular imports at module level
    from api import app as fastapi_app

    uvicorn.run(
        fastapi_app,
        host=API_HOST,
        port=API_PORT,
        log_level="warning",   # keep uvicorn noise low; app uses Python logging
    )


if __name__ == "__main__":
    main()
