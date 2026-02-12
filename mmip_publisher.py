"""
MMIP Publisher — Mesh Mapper Interchange Protocol publisher module.

Subscribes to the in-process EventBus for detections, alerts, and status,
then wraps them in MMIP/1.0 envelopes and publishes via the shared
MQTTPublisher to mmip/{source_id}/{type} topics.

Designed to run as a daemon thread inside mesh-mapper.py.

Topics:
  mmip/{source_id}/detections  — drone, aircraft, vessel, BLE, lightning
  mmip/{source_id}/alerts      — fired alerts from the rule engine
  mmip/{source_id}/status      — periodic health/stats heartbeat (every 60s)
  mmip/broadcast               — reserved for future cross-node coordination

Envelope format (MMIP/1.0):
{
    "protocol": "mmip",
    "version": "1.0",
    "source_id": "drone-pi-kyle-rise",
    "timestamp": "2026-02-12T17:50:00Z",
    "type": "detection|alert|status|heartbeat",
    "payload": { ... }
}
"""

import json
import logging
import threading
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Event types to forward as MMIP detections (only .detected, not .updated)
DETECTION_EVENTS = [
    "drone.detected",
    "aircraft.detected",
    "vessel.detected",
    "ble.detected",
    "lightning.strike",
    "aircraft.squawk_change",
]

# How often to publish status heartbeat (seconds)
STATUS_INTERVAL = 60


class MMIPPublisher:
    """Bridges the EventBus to MQTT via MMIP protocol envelopes.

    Usage:
        publisher = MMIPPublisher(
            event_bus=event_bus,
            mqtt_publisher=mqtt_publisher,
            mmip_config=config['mmip'],
            station_gps_getter=lambda: dict(STATION_GPS),
            data_counts_getter=lambda: {...},
        )
        publisher.start()
    """

    def __init__(
        self,
        event_bus,
        mqtt_publisher,
        mmip_config: dict,
        station_gps_getter=None,
        data_counts_getter=None,
        socketio=None,
    ):
        self.event_bus = event_bus
        self.mqtt = mqtt_publisher
        self.config = mmip_config or {}
        self.source_id = self.config.get("source_id", "unknown")
        self.source_type = self.config.get("source_type", "mesh-mapper")
        self._station_gps_getter = station_gps_getter or (lambda: {})
        self._data_counts_getter = data_counts_getter or (lambda: {})
        self._socketio = socketio
        self._running = False
        self._start_time = time.time()
        self._stats = {
            "detections_published": 0,
            "alerts_published": 0,
            "heartbeats_published": 0,
            "errors": 0,
            "last_publish": 0,
        }
        self._heartbeat_thread = None

    # ── Envelope Builder ──────────────────────────────────────

    def _envelope(self, msg_type: str, payload: dict) -> dict:
        """Build a standard MMIP/1.0 envelope."""
        return {
            "protocol": "mmip",
            "version": "1.0",
            "source_id": self.source_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "type": msg_type,
            "payload": payload,
        }

    def _publish(self, topic: str, envelope: dict):
        """Publish an MMIP envelope to MQTT."""
        if not self.mqtt or not self.mqtt.is_connected:
            return False
        try:
            self.mqtt.client.publish(topic, json.dumps(envelope, default=str))
            self._stats["last_publish"] = time.time()
            logger.debug("MMIP published to %s", topic)
            return True
        except Exception as e:
            self._stats["errors"] += 1
            logger.warning("MMIP publish error on %s: %s", topic, e)
            return False

    # ── Detection Handler ─────────────────────────────────────

    def _on_detection(self, event: dict):
        """Handle detection events from the EventBus."""
        try:
            event_type = event.get("event_type", "")
            location = event.get("location", {})
            data = event.get("data", {})

            payload = {
                "detection_type": event_type,
                "source": event.get("source", ""),
                "object_id": event.get("object_id", ""),
                "object_type": event.get("object_type", ""),
                "location": location,
                "data": data,
            }

            topic = f"mmip/{self.source_id}/detections"
            envelope = self._envelope("detection", payload)
            if self._publish(topic, envelope):
                self._stats["detections_published"] += 1
        except Exception as e:
            self._stats["errors"] += 1
            logger.debug("MMIP detection handler error: %s", e)

    # ── Alert Handler ─────────────────────────────────────────

    def _on_alert(self, alert_data: dict):
        """Handle fired alerts. Called from the alert engine's SocketIO emit hook."""
        try:
            payload = {
                "alert_id": alert_data.get("id", ""),
                "flow_id": alert_data.get("flow_id", ""),
                "flow_name": alert_data.get("flow_name", ""),
                "severity": alert_data.get("severity", "info"),
                "title": alert_data.get("title", ""),
                "message": alert_data.get("message", ""),
                "event_type": alert_data.get("event_type", ""),
                "object_id": alert_data.get("object_id", ""),
                "object_type": alert_data.get("object_type", ""),
                "lat": alert_data.get("lat"),
                "lon": alert_data.get("lon"),
            }

            topic = f"mmip/{self.source_id}/alerts"
            envelope = self._envelope("alert", payload)
            if self._publish(topic, envelope):
                self._stats["alerts_published"] += 1
        except Exception as e:
            self._stats["errors"] += 1
            logger.debug("MMIP alert handler error: %s", e)

    # ── Status Heartbeat ──────────────────────────────────────

    def _publish_status(self):
        """Publish a rich status heartbeat."""
        try:
            gps = self._station_gps_getter()
            counts = self._data_counts_getter()

            payload = {
                "uptime_seconds": int(time.time() - self._start_time),
                "position": {
                    "lat": gps.get("lat", 0),
                    "lon": gps.get("lon", 0),
                    "alt": gps.get("alt", 0),
                    "fix": gps.get("fix", False),
                    "source": gps.get("fix_source", "none"),
                },
                "data_sources": counts,
                "mmip_stats": dict(self._stats),
                "system": {
                    "source_type": self.source_type,
                    "mmip_version": "1.0",
                },
            }

            topic = f"mmip/{self.source_id}/status"
            envelope = self._envelope("status", payload)
            if self._publish(topic, envelope):
                self._stats["heartbeats_published"] += 1
        except Exception as e:
            self._stats["errors"] += 1
            logger.debug("MMIP status heartbeat error: %s", e)

    def _heartbeat_loop(self, shutdown_event):
        """Background loop that publishes status every STATUS_INTERVAL seconds."""
        logger.info("MMIP heartbeat loop started (every %ds)", STATUS_INTERVAL)
        while not shutdown_event.is_set():
            shutdown_event.wait(STATUS_INTERVAL)
            if shutdown_event.is_set():
                break
            self._publish_status()

    # ── Start / Stop ──────────────────────────────────────────

    def start(self, shutdown_event=None):
        """Start MMIP publishing: subscribe to events and begin heartbeat loop.

        Args:
            shutdown_event: threading.Event used for graceful shutdown
        """
        if not self.config.get("enabled") or not self.config.get("publish"):
            logger.info("MMIP publisher disabled (enabled=%s, publish=%s)",
                        self.config.get("enabled"), self.config.get("publish"))
            return

        if self._running:
            logger.warning("MMIP publisher already running")
            return

        self._running = True
        self._start_time = time.time()

        # Subscribe to detection events on the EventBus
        if self.event_bus:
            for event_pattern in DETECTION_EVENTS:
                self.event_bus.subscribe(event_pattern, self._on_detection)
                logger.debug("MMIP subscribed to EventBus: %s", event_pattern)
            logger.info("MMIP publisher subscribed to %d event patterns", len(DETECTION_EVENTS))

        # Hook into SocketIO alert_fired to capture alerts
        if self._socketio:
            try:
                original_emit = self._socketio.emit

                def patched_emit(event_name, *args, **kwargs):
                    # Intercept alert_fired events
                    if event_name == "alert_fired" and args:
                        try:
                            self._on_alert(args[0])
                        except Exception:
                            pass
                    return original_emit(event_name, *args, **kwargs)

                self._socketio.emit = patched_emit
                logger.info("MMIP publisher hooked into SocketIO alert_fired")
            except Exception as e:
                logger.warning("MMIP failed to hook SocketIO: %s", e)

        # Start heartbeat thread
        if shutdown_event is None:
            shutdown_event = threading.Event()

        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            args=(shutdown_event,),
            daemon=True,
            name="MMIPHeartbeat",
        )
        self._heartbeat_thread.start()

        # Publish an initial status immediately
        self._publish_status()

        logger.info(
            "MMIP publisher started: source_id=%s, events=%d, heartbeat=%ds",
            self.source_id, len(DETECTION_EVENTS), STATUS_INTERVAL,
        )

    def stop(self):
        """Unsubscribe from events (heartbeat thread is daemon, will die with process)."""
        if not self._running:
            return
        self._running = False
        if self.event_bus:
            for event_pattern in DETECTION_EVENTS:
                try:
                    self.event_bus.unsubscribe(event_pattern, self._on_detection)
                except Exception:
                    pass
        logger.info("MMIP publisher stopped")

    @property
    def stats(self) -> dict:
        """Return MMIP publisher statistics."""
        return {
            "running": self._running,
            "source_id": self.source_id,
            "uptime": int(time.time() - self._start_time) if self._running else 0,
            **self._stats,
        }
