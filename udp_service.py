"""
udp_service.py — UDP Ingestion Service

Listens on a configurable UDP port for hex-encoded telemetry frames.
Each received datagram is parsed, stored in MongoDB, and health-checked.

Design notes:
  • Runs in a dedicated daemon thread so the main thread is free for uvicorn.
  • SO_REUSEADDR is set so the port can be re-bound quickly after a restart.
  • A 1-second socket timeout allows the loop to notice a stop() call promptly.
  • Multiple datagrams in a stream are each processed independently; UDP frames
    are already delimited by the datagram boundary.
  • Errors in one packet never affect processing of subsequent packets.
"""

import logging
import socket
import threading
from typing import Callable, List, Optional

from parser import parse_packet, ParseError
from health_monitor import check_health
import database as db

logger = logging.getLogger(__name__)


class UDPService:
    """
    UDP telemetry ingestion service.

    Usage:
        service = UDPService(port=5005, on_packet=my_callback)
        service.start()
        ...
        service.stop()

    on_packet callback signature (optional):
        fn(packet: dict, alerts: list[dict]) -> None
    """

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 5005,
        on_packet: Optional[Callable] = None,
    ):
        self.host = host
        self.port = port
        self.on_packet = on_packet          # optional external notification hook

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._sock: Optional[socket.socket] = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Bind the socket and start the listener thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._listen_loop,
            daemon=True,
            name="UDPService",
        )
        self._thread.start()
        logger.info("UDP service started on %s:%d", self.host, self.port)

    def stop(self) -> None:
        """Signal the listener thread to exit and close the socket."""
        self._running = False
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
        logger.info("UDP service stopped.")

    # ── Listener loop ─────────────────────────────────────────────────────────

    def _listen_loop(self) -> None:
        """Main receive loop — runs in the background thread."""
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._sock.bind((self.host, self.port))
            self._sock.settimeout(1.0)   # allows stop() to be noticed
        except OSError as exc:
            logger.error("Failed to bind UDP socket on %s:%d — %s", self.host, self.port, exc)
            self._running = False
            return

        logger.info("UDP socket bound; listening for packets.")

        while self._running:
            try:
                raw_data, addr = self._sock.recvfrom(65535)
            except socket.timeout:
                continue                         # check self._running again
            except OSError:
                break                            # socket was closed by stop()
            except Exception as exc:
                logger.error("Unexpected socket error: %s", exc)
                continue

            # Decode and process — errors in one packet must not drop the loop
            try:
                hex_str = raw_data.decode("utf-8", errors="ignore").strip()
                self._process(hex_str, addr)
            except Exception as exc:
                logger.error("Unhandled processing error from %s: %s", addr, exc)

        # Clean-up
        try:
            self._sock.close()
        except OSError:
            pass

    # ── Packet processing ─────────────────────────────────────────────────────

    def _process(self, hex_str: str, addr) -> None:
        """Parse one received datagram, store it, and run health checks."""
        try:
            packet = parse_packet(hex_str)
        except ParseError as exc:
            logger.warning("Parse error from %s: %s", addr, exc)
            return

        alerts: List[dict] = []

        if packet["packet_type"] == "HOUSEKEEPING":
            db.store_telemetry(packet)
            alerts = check_health(packet)
            for alert in alerts:
                stored = db.store_alert(alert)
                if stored:
                    logger.warning(
                        "[%s ALERT] Sat %s: %s",
                        alert["level"],
                        alert["satellite_id"],
                        alert["message"],
                    )
            logger.info(
                "HK packet — sat=%s ts=%s batt=%smV msi=%s°C",
                packet["satellite_id"],
                packet["timestamp"],
                packet["battery_voltage"],
                packet["msi_temperature"],
            )
        else:
            logger.info(
                "%s packet — sat=%s ts=%s len=%s bytes",
                packet["packet_type"],
                packet["satellite_id"],
                packet["timestamp"],
                packet.get("payload_length", "?"),
            )

        # Notify external listener (e.g. WebSocket broadcaster)
        if self.on_packet:
            try:
                self.on_packet(packet, alerts)
            except Exception as exc:
                logger.error("on_packet callback error: %s", exc)
