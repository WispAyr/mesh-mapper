"""
Alert Actions — Action executors for the mesh-mapper alert flow system.

Each action class implements an `execute(config, event, flow, ctx)` method.
Actions are registered with the RuleEngine by action_type string.
"""

import json
import logging
import threading
import time
import urllib.request
import urllib.error

logger = logging.getLogger(__name__)


class BaseAction:
    """Base class for alert actions."""

    def execute(self, config: dict, event: dict, flow: dict, ctx: dict):
        """Execute the action. Must be implemented by subclasses."""
        raise NotImplementedError


# ============================================================
# UI Alert Action
# ============================================================

class UIAlertAction(BaseAction):
    """Emit a SocketIO event to connected browser clients.
    
    SocketIO event: 'alert_fired'
    """

    def __init__(self, socketio=None):
        self.socketio = socketio

    def execute(self, config: dict, event: dict, flow: dict, ctx: dict):
        if not self.socketio:
            logger.warning("UIAlertAction: no socketio instance")
            return

        from alert_engine import resolve_template

        severity = config.get("severity", flow.get("severity", "warning"))
        title = resolve_template(config.get("title", flow.get("name", "Alert")), ctx)
        message = resolve_template(config.get("message", ""), ctx)

        loc = event.get("location", {})

        alert_payload = {
            "id": f"alert_{int(time.time() * 1000)}",
            "flow_id": flow.get("id", ""),
            "flow_name": flow.get("name", ""),
            "severity": severity,
            "title": title,
            "message": message,
            "event_type": event.get("event_type", ""),
            "object_id": event.get("object_id", ""),
            "object_type": event.get("object_type", ""),
            "lat": loc.get("lat"),
            "lon": loc.get("lon"),
            "alt": loc.get("alt"),
            "timestamp": ctx.get("timestamp", ""),
            "sound": config.get("sound", "default"),
            "highlight_object": config.get("highlight_object", True),
            "fly_to": config.get("fly_to", False),
            "auto_dismiss_seconds": config.get("auto_dismiss_seconds"),
            "acknowledged": False,
        }

        try:
            self.socketio.emit("alert_fired", alert_payload)
            logger.debug(f"UIAlertAction: emitted alert_fired [{severity}] {title}")
        except Exception as e:
            logger.error(f"UIAlertAction: emit error: {e}")


# ============================================================
# Webhook Action
# ============================================================

class WebhookAction(BaseAction):
    """HTTP POST to a configurable URL."""

    def __init__(self, default_url: str = None, timeout: int = 10):
        self.default_url = default_url
        self.timeout = timeout

    def execute(self, config: dict, event: dict, flow: dict, ctx: dict):
        url = config.get("url", self.default_url)
        if not url:
            logger.warning("WebhookAction: no URL configured")
            return

        from alert_engine import resolve_template

        method = config.get("method", "POST")
        headers = config.get("headers", {})
        headers.setdefault("Content-Type", "application/json")
        timeout = config.get("timeout_seconds", self.timeout)

        # Build payload
        payload_template = config.get("payload")
        if payload_template and isinstance(payload_template, dict):
            payload = {}
            for k, v in payload_template.items():
                if isinstance(v, str):
                    payload[k] = resolve_template(v, ctx)
                else:
                    payload[k] = v
        else:
            # Default payload
            loc = event.get("location", {})
            payload = {
                "flow_id": flow.get("id", ""),
                "flow_name": flow.get("name", ""),
                "severity": flow.get("severity", "warning"),
                "event_type": event.get("event_type", ""),
                "object_id": event.get("object_id", ""),
                "object_type": event.get("object_type", ""),
                "lat": loc.get("lat"),
                "lon": loc.get("lon"),
                "timestamp": ctx.get("timestamp", ""),
                "message": config.get("message", ""),
            }

        def _send():
            try:
                data = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(url, data=data, headers=headers, method=method)
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    logger.debug(f"WebhookAction: {method} {url} → {resp.status}")
            except Exception as e:
                logger.error(f"WebhookAction: error posting to {url}: {e}")

        # Execute in background thread to avoid blocking
        threading.Thread(target=_send, daemon=True).start()


# ============================================================
# Telegram Action
# ============================================================

class TelegramAction(BaseAction):
    """Send a message via Telegram Bot API.
    
    Uses direct Bot API calls (not webhook relay).
    """

    BOT_TOKEN = "8274828622:AAGDckVBXNOeNJdOT5M5fkxJH6b6I1IhXV0"
    DEFAULT_CHAT_ID = "614811138"
    API_BASE = "https://api.telegram.org/bot{token}/sendMessage"

    def __init__(self, bot_token: str = None, chat_id: str = None):
        self.bot_token = bot_token or self.BOT_TOKEN
        self.chat_id = chat_id or self.DEFAULT_CHAT_ID

    def execute(self, config: dict, event: dict, flow: dict, ctx: dict):
        from alert_engine import resolve_template

        message = resolve_template(config.get("message", ""), ctx)
        if not message:
            message = f"🔔 Alert: {flow.get('name', 'Alert')}\n{event.get('event_type', '')}"

        chat_id = config.get("chat_id", self.chat_id)
        include_map_link = config.get("include_map_link", True)
        include_details = config.get("include_details", True)

        # Append map link
        loc = event.get("location", {})
        lat = loc.get("lat")
        lon = loc.get("lon")

        if include_map_link and lat and lon:
            message += f"\n\n📍 https://www.google.com/maps?q={lat},{lon}"

        if include_details:
            details = []
            obj_id = event.get("object_id", "")
            obj_type = event.get("object_type", "")
            if obj_id:
                details.append(f"ID: {obj_id}")
            if obj_type:
                details.append(f"Type: {obj_type}")
            if lat and lon:
                details.append(f"Position: {lat:.5f}, {lon:.5f}")
            data = event.get("data", {})
            if data.get("callsign"):
                details.append(f"Callsign: {data['callsign']}")
            if data.get("squawk"):
                details.append(f"Squawk: {data['squawk']}")
            if data.get("rssi"):
                details.append(f"RSSI: {data['rssi']}")
            if details:
                message += "\n\n" + "\n".join(details)

        def _send():
            try:
                url = self.API_BASE.format(token=self.bot_token)
                payload = {
                    "chat_id": chat_id,
                    "text": message,
                    "parse_mode": "Markdown",
                    "disable_web_page_preview": True,
                }
                data = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(
                    url, data=data,
                    headers={"Content-Type": "application/json"},
                    method="POST"
                )
                with urllib.request.urlopen(req, timeout=15) as resp:
                    logger.debug(f"TelegramAction: sent to {chat_id} → {resp.status}")
            except Exception as e:
                logger.error(f"TelegramAction: error sending to {chat_id}: {e}")

        threading.Thread(target=_send, daemon=True).start()


# ============================================================
# MQTT Action
# ============================================================

class MQTTAction(BaseAction):
    """Publish a message to an MQTT topic.
    
    Uses the existing MQTTPublisher if available.
    """

    def __init__(self, mqtt_publisher=None):
        self.mqtt = mqtt_publisher

    def execute(self, config: dict, event: dict, flow: dict, ctx: dict):
        if not self.mqtt:
            logger.warning("MQTTAction: no MQTT publisher available")
            return

        from alert_engine import resolve_template

        topic = config.get("topic", "alerts/fired")
        qos = config.get("qos", 1)
        retain = config.get("retain", False)

        # Build payload
        payload_template = config.get("payload")
        if payload_template and isinstance(payload_template, dict):
            payload = {}
            for k, v in payload_template.items():
                if isinstance(v, str):
                    payload[k] = resolve_template(v, ctx)
                else:
                    payload[k] = v
        else:
            loc = event.get("location", {})
            payload = {
                "flow_id": flow.get("id", ""),
                "flow_name": flow.get("name", ""),
                "severity": flow.get("severity", "warning"),
                "event_type": event.get("event_type", ""),
                "object_id": event.get("object_id", ""),
                "object_type": event.get("object_type", ""),
                "lat": loc.get("lat"),
                "lon": loc.get("lon"),
                "timestamp": ctx.get("timestamp", ""),
            }

        try:
            # Use the existing publish_message method if available
            if hasattr(self.mqtt, 'publish_message'):
                self.mqtt.publish_message(topic, payload, "alerts")
            elif hasattr(self.mqtt, 'client') and self.mqtt.client:
                import json as _json
                self.mqtt.client.publish(topic, _json.dumps(payload), qos=qos, retain=retain)
            logger.debug(f"MQTTAction: published to {topic}")
        except Exception as e:
            logger.error(f"MQTTAction: error publishing to {topic}: {e}")


# ============================================================
# Log Action
# ============================================================

class LogAction(BaseAction):
    """Write alert to the alert_history table.
    
    Note: The RuleEngine always logs alerts anyway. This action allows
    customising retention or adding extra metadata.
    """

    def __init__(self, storage=None):
        self.storage = storage

    def execute(self, config: dict, event: dict, flow: dict, ctx: dict):
        # The RuleEngine handles core logging.
        # This action can be used for custom retention or extra fields.
        retention_days = config.get("retention_days", 90)
        include_full_event = config.get("include_full_event", True)

        # Log is handled by the engine; this is mostly a no-op but allows 
        # the action to appear in the flow for visibility.
        logger.debug(f"LogAction: alert logged for flow {flow.get('id', '')} "
                     f"(retention: {retention_days}d)")


# ============================================================
# Sound Action (Placeholder for AI Horn TTS)
# ============================================================

class SoundAction(BaseAction):
    """Placeholder for AI Horn TTS integration.
    
    When the AI Horn system is connected, this will call the TTS
    announce endpoint. For now, logs the intent.
    """

    HORN_SCRIPT = "/Users/noc/clawd/scripts/horn-announce.sh"

    def __init__(self):
        self._available = False
        try:
            import os
            self._available = os.path.exists(self.HORN_SCRIPT)
        except Exception:
            pass

    def execute(self, config: dict, event: dict, flow: dict, ctx: dict):
        from alert_engine import resolve_template

        message = resolve_template(config.get("message", "Alert"), ctx)
        volume = config.get("volume", 50)
        voice = config.get("voice", "en-GB-RyanNeural")

        if self._available:
            import subprocess
            try:
                subprocess.Popen(
                    [self.HORN_SCRIPT, message, str(volume)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                logger.debug(f"SoundAction: announcing '{message}' at volume {volume}")
            except Exception as e:
                logger.error(f"SoundAction: error running horn script: {e}")
        else:
            logger.debug(f"SoundAction: [placeholder] would announce: '{message}' "
                        f"(volume={volume}, voice={voice})")
