"""
Alert Engine — Core module for the mesh-mapper alert flow system.

Provides:
- EventBus: In-process publish/subscribe with wildcard pattern matching
- RuleEngine: Evaluates flows (trigger → condition chain → actions)
- FlowStorage: SQLite-backed CRUD for alert flows
- Alert history logging

Designed to run in-process within mesh-mapper.py on Raspberry Pi 5.
Thread-safe for use alongside ~15 daemon threads.
"""

import json
import logging
import math
import re
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


# ============================================================
# Event Bus
# ============================================================

class EventBus:
    """Lightweight in-process event bus for the alert engine.
    
    Supports hierarchical event types with wildcard matching:
    - Exact: 'drone.detected'
    - Wildcard: 'drone.*'
    - All: '*'
    """

    def __init__(self):
        self._handlers: dict[str, list] = {}
        self._lock = threading.Lock()
        self._event_count = 0
        self._last_event_time = 0.0

    def subscribe(self, event_pattern: str, handler):
        """Subscribe a handler to events matching a pattern."""
        with self._lock:
            self._handlers.setdefault(event_pattern, []).append(handler)
            logger.debug(f"EventBus: subscribed to '{event_pattern}'")

    def unsubscribe(self, event_pattern: str, handler):
        """Remove a handler from a pattern."""
        with self._lock:
            if event_pattern in self._handlers:
                try:
                    self._handlers[event_pattern].remove(handler)
                except ValueError:
                    pass

    def publish(self, event: dict):
        """Publish an event to all matching subscribers.
        
        Event must contain at minimum:
            event_type (str): Hierarchical event type (e.g. 'drone.detected')
        """
        event_type = event.get("event_type", "")
        if not event_type:
            return

        # Ensure timestamp
        if "timestamp" not in event:
            event["timestamp"] = time.time()

        self._event_count += 1
        self._last_event_time = time.time()

        with self._lock:
            handlers_to_call = []
            for pattern, handlers in self._handlers.items():
                if self._matches(event_type, pattern):
                    handlers_to_call.extend(handlers)

        for handler in handlers_to_call:
            try:
                handler(event)
            except Exception as e:
                logger.error(f"EventBus: handler error for '{event_type}': {e}")

    @staticmethod
    def _matches(event_type: str, pattern: str) -> bool:
        """Match event type against a subscription pattern."""
        if pattern == "*":
            return True
        if pattern.endswith(".*"):
            prefix = pattern[:-2]
            return event_type.startswith(prefix + ".") or event_type == prefix
        return event_type == pattern

    @property
    def stats(self) -> dict:
        """Return event bus statistics."""
        with self._lock:
            subscriber_count = sum(len(h) for h in self._handlers.values())
        return {
            "total_events": self._event_count,
            "last_event_time": self._last_event_time,
            "subscriber_count": subscriber_count,
            "pattern_count": len(self._handlers),
        }


# ============================================================
# Template Variable Resolution
# ============================================================

_TEMPLATE_RE = re.compile(r"\{\{(\w+)\}\}")


def resolve_template(template_str: str, context: dict) -> str:
    """Resolve {{variable}} placeholders from context dict."""
    if not template_str:
        return template_str

    def _replace(match):
        key = match.group(1)
        val = context.get(key)
        if val is None:
            return match.group(0)  # leave unresolved
        return str(val)

    return _TEMPLATE_RE.sub(_replace, template_str)


def build_template_context(event: dict, flow: dict) -> dict:
    """Build a flat template context from an event and flow."""
    ctx = {
        "object_id": event.get("object_id", ""),
        "object_type": event.get("object_type", ""),
        "timestamp": datetime.fromtimestamp(
            event.get("timestamp", time.time()), tz=timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "severity": flow.get("severity", "warning"),
        "flow_name": flow.get("name", ""),
    }

    loc = event.get("location", {})
    ctx["lat"] = loc.get("lat", "")
    ctx["lon"] = loc.get("lon", "")
    ctx["alt"] = loc.get("alt", "")

    data = event.get("data", {})
    ctx["rssi"] = data.get("rssi", "")
    ctx["speed"] = data.get("speed_kts", data.get("speed", ""))
    ctx["heading"] = data.get("heading", data.get("track", ""))
    ctx["callsign"] = data.get("callsign", "")
    ctx["squawk"] = data.get("squawk", "")
    ctx["alias"] = data.get("alias", "")
    ctx["zone_name"] = data.get("zone_name", "")
    ctx["distance_km"] = data.get("distance_km", "")

    # Merge any extra data keys the user might reference
    for k, v in data.items():
        if k not in ctx:
            ctx[k] = v

    return ctx


# ============================================================
# Condition Evaluators
# ============================================================

def _get_nested(data: dict, dotpath: str):
    """Get a value from a nested dict by dot-separated path."""
    parts = dotpath.split(".")
    current = data
    for p in parts:
        if isinstance(current, dict):
            current = current.get(p)
        else:
            return None
    return current


def _haversine_km(lat1, lon1, lat2, lon2):
    """Calculate great-circle distance between two points in km."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlon / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def evaluate_condition(condition_node: dict, event: dict, engine) -> bool:
    """Evaluate a single condition node against an event.
    
    Returns True if the condition passes.
    """
    ctype = condition_node.get("condition_type", "")
    config = condition_node.get("config", {})

    if ctype == "geofence":
        return _eval_geofence(config, event, engine)
    elif ctype == "threshold":
        return _eval_threshold(config, event)
    elif ctype == "time_filter":
        return _eval_time_filter(config)
    elif ctype == "rate_limit":
        return _eval_rate_limit(config, event, engine, condition_node.get("id", ""))
    elif ctype == "object_match":
        return _eval_object_match(config, event)
    elif ctype == "state_check":
        return _eval_state_check(config, event, engine)
    elif ctype == "duration":
        return _eval_duration(config, event, engine, condition_node.get("id", ""))
    elif ctype == "logic":
        # Logic combinators are handled at the graph-walk level
        return True
    else:
        logger.warning(f"Unknown condition type: {ctype}")
        return True  # permissive default


def _eval_geofence(config: dict, event: dict, engine) -> bool:
    """Geofence condition: check if object is inside/outside a zone or radius."""
    loc = event.get("location", {})
    lat = loc.get("lat", 0)
    lon = loc.get("lon", 0)

    if lat == 0 and lon == 0:
        return False

    check = config.get("check", "object_inside")

    # Pilot location check (drones only)
    if check == "pilot_inside":
        data = event.get("data", {})
        lat = data.get("pilot_lat", 0)
        lon = data.get("pilot_lon", data.get("pilot_long", 0))
        if lat == 0 and lon == 0:
            return False

    # Point + radius check
    point = config.get("point")
    radius_km = config.get("radius_km")

    if point and radius_km:
        dist = _haversine_km(lat, lon, point["lat"], point["lon"])
        # Store distance in event data for template resolution
        event.setdefault("data", {})["distance_km"] = round(dist, 2)
        inside = dist <= radius_km
        if check in ("object_inside", "pilot_inside"):
            return inside
        return not inside

    # Zone polygon check (uses zone_id to look up from ZONES global)
    zone_id = config.get("zone_id")
    if zone_id and engine and hasattr(engine, '_get_zone'):
        zone = engine._get_zone(zone_id)
        if zone:
            polygon = zone.get("coordinates", [])
            if polygon and len(polygon) >= 3:
                inside = _point_in_polygon(lat, lon, polygon)
                # Add zone name to event context
                event.setdefault("data", {})["zone_name"] = zone.get("name", zone_id)
                if check in ("object_inside", "pilot_inside"):
                    return inside
                return not inside

            # Point zone with radius
            centre_lat = zone.get("centre_lat")
            centre_lon = zone.get("centre_lon")
            zone_radius = zone.get("radius_km", radius_km)
            if centre_lat and centre_lon and zone_radius:
                dist = _haversine_km(lat, lon, centre_lat, centre_lon)
                event.setdefault("data", {})["distance_km"] = round(dist, 2)
                event.setdefault("data", {})["zone_name"] = zone.get("name", zone_id)
                inside = dist <= zone_radius
                if check in ("object_inside", "pilot_inside"):
                    return inside
                return not inside

    return False


def _point_in_polygon(lat, lon, polygon):
    """Ray-casting point-in-polygon test. Polygon is list of [lat, lon] pairs."""
    if not polygon or len(polygon) < 3:
        return False
    n = len(polygon)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if ((yi > lat) != (yj > lat)) and (lon < (xj - xi) * (lat - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def _eval_threshold(config: dict, event: dict) -> bool:
    """Threshold condition: compare a field value against threshold."""
    field = config.get("field", "")
    operator = config.get("operator", "gt")
    value = config.get("value")
    value_max = config.get("value_max")

    if value is None:
        return True

    # Get the actual value from event
    actual = _get_nested(event, field)
    if actual is None:
        # Also check top-level location fields
        if field.startswith("location."):
            actual = _get_nested(event.get("location", {}), field.split(".", 1)[1])
        if actual is None:
            return False

    try:
        actual = float(actual)
        value = float(value)
    except (TypeError, ValueError):
        return False

    if operator == "gt":
        return actual > value
    elif operator == "gte":
        return actual >= value
    elif operator == "lt":
        return actual < value
    elif operator == "lte":
        return actual <= value
    elif operator == "eq":
        return actual == value
    elif operator == "neq":
        return actual != value
    elif operator == "between":
        if value_max is None:
            return False
        return value <= actual <= float(value_max)
    return False


def _eval_time_filter(config: dict) -> bool:
    """Time filter condition: check if current time is within window."""
    from datetime import datetime as dt
    try:
        import zoneinfo
        tz = zoneinfo.ZoneInfo(config.get("timezone", "Europe/London"))
    except Exception:
        tz = None

    now = dt.now(tz) if tz else dt.now()
    invert = config.get("invert", False)

    # Day of week check
    days = config.get("days_of_week", list(range(7)))
    if now.weekday() not in days:
        return invert

    # Time window check
    time_start = config.get("time_start")
    time_end = config.get("time_end")

    if time_start and time_end:
        start_h, start_m = map(int, time_start.split(":"))
        end_h, end_m = map(int, time_end.split(":"))
        start_minutes = start_h * 60 + start_m
        end_minutes = end_h * 60 + end_m
        now_minutes = now.hour * 60 + now.minute

        if start_minutes <= end_minutes:
            in_window = start_minutes <= now_minutes <= end_minutes
        else:
            # Overnight window (e.g. 22:00 - 06:00)
            in_window = now_minutes >= start_minutes or now_minutes <= end_minutes

        if invert:
            return not in_window
        return in_window

    return not invert  # no time bounds = always active


def _eval_rate_limit(config: dict, event: dict, engine, condition_id: str) -> bool:
    """Rate limiter condition: max N fires in a window."""
    max_fires = config.get("max_fires", 1)
    window_minutes = config.get("window_minutes", 5)
    per_object = config.get("per_object", True)

    if engine is None:
        return True

    obj_id = event.get("object_id", "_global") if per_object else "_global"
    key = (condition_id, obj_id)
    now = time.time()
    window_seconds = window_minutes * 60

    with engine._rate_limit_lock:
        timestamps = engine._rate_limit_state.get(key, [])
        # Remove expired timestamps
        timestamps = [t for t in timestamps if now - t < window_seconds]
        if len(timestamps) >= max_fires:
            engine._rate_limit_state[key] = timestamps
            return False
        timestamps.append(now)
        engine._rate_limit_state[key] = timestamps
        return True


def _eval_object_match(config: dict, event: dict) -> bool:
    """Object match condition: field comparison."""
    field = config.get("field", "object_id")
    operator = config.get("operator", "eq")
    value = config.get("value")

    if value is None:
        return True

    actual = _get_nested(event, field)
    if actual is None and "." in field:
        # Try in data sub-dict
        actual = _get_nested(event.get("data", {}), field.split(".", 1)[-1])

    if actual is None:
        actual = ""

    actual_str = str(actual)
    if operator == "eq":
        return actual_str == str(value)
    elif operator == "neq":
        return actual_str != str(value)
    elif operator == "in":
        return actual_str in (value if isinstance(value, list) else [value])
    elif operator == "not_in":
        return actual_str not in (value if isinstance(value, list) else [value])
    elif operator == "contains":
        return str(value) in actual_str
    elif operator == "starts_with":
        return actual_str.startswith(str(value))
    elif operator == "regex":
        try:
            return bool(re.search(str(value), actual_str))
        except re.error:
            return False
    return False


def _eval_state_check(config: dict, event: dict, engine) -> bool:
    """State check condition: object lifecycle checks."""
    check = config.get("check", "first_seen")
    obj_id = event.get("object_id")
    if not obj_id or engine is None:
        return False

    state = engine.object_state.get(obj_id)

    if check == "first_seen":
        # True if this is the first time we've seen this object
        return state is None or state.get("detection_count", 0) <= 1

    elif check == "returning":
        if state is None:
            return False
        timeout = config.get("timeout_seconds", 3600)
        last_seen = state.get("last_seen", 0)
        now = time.time()
        # Returning = was seen before, but had a gap > timeout
        gap = now - last_seen
        return gap > timeout and state.get("detection_count", 0) > 1

    elif check == "already_tracked":
        return state is not None and state.get("detection_count", 0) > 1

    elif check == "in_zone":
        zone_id = config.get("zone_id")
        if state and zone_id:
            return zone_id in state.get("zone_ids", set())
        return False

    elif check == "not_in_zone":
        zone_id = config.get("zone_id")
        if state and zone_id:
            return zone_id not in state.get("zone_ids", set())
        return True  # not tracked = not in zone

    return False


def _eval_duration(config: dict, event: dict, engine, condition_id: str) -> bool:
    """Duration condition: state must persist for min_duration_seconds."""
    if engine is None:
        return False

    min_duration = config.get("min_duration_seconds", 0)
    check = config.get("check", "in_zone")
    obj_id = event.get("object_id", "")
    flow_id = getattr(engine, '_current_flow_id', '')
    timer_key = (flow_id, obj_id, condition_id)

    # Determine if the underlying condition is currently met
    condition_met = False
    if check == "in_zone":
        zone_id = config.get("zone_id")
        state = engine.object_state.get(obj_id)
        condition_met = state is not None and zone_id in state.get("zone_ids", set())
    elif check == "below_speed":
        threshold = config.get("speed_threshold", 1.0)
        speed = _get_nested(event, "data.speed_kts") or _get_nested(event, "data.speed") or 0
        try:
            condition_met = float(speed) < threshold
        except (TypeError, ValueError):
            condition_met = False
    elif check == "stationary":
        threshold = config.get("speed_threshold", 0.5)
        speed = _get_nested(event, "data.speed_kts") or _get_nested(event, "data.speed") or 0
        try:
            condition_met = float(speed) < threshold
        except (TypeError, ValueError):
            condition_met = False

    now = time.time()
    with engine._duration_lock:
        if condition_met:
            if timer_key not in engine._duration_timers:
                engine._duration_timers[timer_key] = now
            elapsed = now - engine._duration_timers[timer_key]
            return elapsed >= min_duration
        else:
            # Reset timer
            engine._duration_timers.pop(timer_key, None)
            return False


# ============================================================
# Trigger Matching
# ============================================================

def match_trigger(trigger_node: dict, event: dict) -> bool:
    """Check if an event matches a trigger node's criteria."""
    ttype = trigger_node.get("trigger_type", "")
    config = trigger_node.get("config", {})
    event_type = event.get("event_type", "")

    # Match the event type to trigger type
    # trigger_type format: "drone.detected" or just a category check
    if ttype:
        # Direct event type match
        expected_events = _trigger_to_events(ttype, config)
        if event_type not in expected_events:
            return False

    # Apply trigger-specific filters
    obj_type = event.get("object_type", "")
    data = event.get("data", {})

    if obj_type == "drone" or ttype.startswith("drone"):
        if config.get("match_mac"):
            if event.get("object_id", "") != config["match_mac"]:
                return False
        if config.get("match_oui"):
            obj_id = event.get("object_id", "")
            if not obj_id.upper().startswith(config["match_oui"].upper()):
                return False
        if config.get("match_new_only") and not data.get("is_new", False):
            return False
        if config.get("match_not_whitelisted") and data.get("is_whitelisted", False):
            return False
        if config.get("min_rssi") is not None:
            rssi = data.get("rssi", -100)
            if rssi < config["min_rssi"]:
                return False

    elif obj_type == "aircraft" or ttype.startswith("aircraft"):
        if config.get("match_hex"):
            if event.get("object_id", "") != config["match_hex"]:
                return False
        if config.get("match_callsign"):
            callsign = data.get("callsign", "")
            pattern = config["match_callsign"].replace("*", ".*")
            if not re.match(pattern, callsign, re.IGNORECASE):
                return False
        if config.get("match_squawk"):
            if data.get("squawk", "") != config["match_squawk"]:
                return False
        if config.get("emergency_only"):
            squawk = data.get("squawk", "")
            if squawk not in ("7500", "7600", "7700"):
                return False
        if config.get("military_only"):
            cat = data.get("category", "")
            if "military" not in cat.lower() and cat not in ("A5", "A6", "A7"):
                return False

    elif obj_type == "vessel" or ttype.startswith("vessel"):
        if config.get("match_mmsi"):
            if event.get("object_id", "") != config["match_mmsi"]:
                return False
        if config.get("match_name"):
            name = data.get("name", "")
            pattern = config["match_name"].replace("*", ".*")
            if not re.match(pattern, name, re.IGNORECASE):
                return False
        if config.get("match_type"):
            if data.get("vessel_type", "") != config["match_type"]:
                return False
        if config.get("match_flag"):
            if data.get("flag", "") != config["match_flag"]:
                return False

    elif obj_type == "lightning" or ttype.startswith("lightning"):
        # Lightning triggers may have distance check built into trigger config
        max_dist = config.get("max_distance_km")
        if max_dist is not None:
            ref = config.get("reference_point")
            if ref:
                loc = event.get("location", {})
                dist = _haversine_km(loc.get("lat", 0), loc.get("lon", 0),
                                     ref["lat"], ref["lon"])
                event.setdefault("data", {})["distance_km"] = round(dist, 2)
                if dist > max_dist:
                    return False

    elif obj_type == "weather" or ttype.startswith("weather"):
        if config.get("min_severity"):
            severity_order = {"yellow": 1, "amber": 2, "red": 3}
            event_sev = data.get("severity", "yellow")
            min_sev = config["min_severity"]
            if severity_order.get(event_sev, 0) < severity_order.get(min_sev, 0):
                return False
        if config.get("match_type"):
            if data.get("warning_type", "") != config["match_type"]:
                return False

    elif obj_type == "system" or ttype.startswith("system"):
        if config.get("match_feed"):
            if data.get("feed", "") != config["match_feed"]:
                return False

    return True


def _trigger_to_events(trigger_type: str, config: dict) -> list:
    """Map a trigger_type to the event types it should match."""
    # Direct mapping
    parts = trigger_type.split(".")
    if len(parts) >= 2:
        category = parts[0]
        event_name = config.get("event", parts[1] if len(parts) > 1 else "")

        if event_name:
            return [f"{category}.{event_name}"]
        # Wildcard — match any event in this category
        return [f"{category}.*"]

    # Fallback: treat as exact match
    return [trigger_type]


# ============================================================
# Flow Storage (SQLite)
# ============================================================

class FlowStorage:
    """SQLite-backed storage for alert flows and history."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._lock = threading.Lock()
        self._init_tables()

    def _get_conn(self):
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_tables(self):
        """Create alert tables if they don't exist."""
        conn = self._get_conn()
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS alert_flows (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT,
                    enabled INTEGER DEFAULT 1,
                    severity TEXT DEFAULT 'warning',
                    template_id TEXT,
                    cooldown_seconds INTEGER DEFAULT 300,
                    flow_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                    last_fired_at TEXT,
                    fire_count INTEGER DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_af_enabled ON alert_flows(enabled);
                CREATE INDEX IF NOT EXISTS idx_af_template ON alert_flows(template_id);

                CREATE TABLE IF NOT EXISTS alert_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    flow_id TEXT NOT NULL,
                    flow_name TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    title TEXT NOT NULL,
                    message TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    object_id TEXT,
                    object_type TEXT,
                    lat REAL,
                    lon REAL,
                    alt REAL,
                    event_data TEXT,
                    actions_executed TEXT,
                    acknowledged INTEGER DEFAULT 0,
                    acknowledged_at TEXT,
                    acknowledged_by TEXT,
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    FOREIGN KEY (flow_id) REFERENCES alert_flows(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_ah_flow ON alert_history(flow_id);
                CREATE INDEX IF NOT EXISTS idx_ah_severity ON alert_history(severity);
                CREATE INDEX IF NOT EXISTS idx_ah_created ON alert_history(created_at);
                CREATE INDEX IF NOT EXISTS idx_ah_object ON alert_history(object_id);
                CREATE INDEX IF NOT EXISTS idx_ah_acked ON alert_history(acknowledged);

                CREATE TABLE IF NOT EXISTS alert_cooldowns (
                    flow_id TEXT NOT NULL,
                    object_id TEXT NOT NULL DEFAULT '_global',
                    last_fired_at TEXT NOT NULL,
                    fire_count INTEGER DEFAULT 1,
                    PRIMARY KEY (flow_id, object_id),
                    FOREIGN KEY (flow_id) REFERENCES alert_flows(id) ON DELETE CASCADE
                );

                CREATE VIEW IF NOT EXISTS recent_alerts AS
                SELECT * FROM alert_history
                WHERE created_at > datetime('now', '-24 hours')
                ORDER BY created_at DESC;

                CREATE VIEW IF NOT EXISTS unacknowledged_alerts AS
                SELECT * FROM alert_history
                WHERE acknowledged = 0
                ORDER BY created_at DESC;
            """)
            conn.commit()
            logger.info("Alert engine database tables initialised")
        except Exception as e:
            logger.error(f"Error initialising alert tables: {e}")
        finally:
            conn.close()

    # --- Flow CRUD ---

    def list_flows(self, enabled_only=False) -> list:
        """List all flows, optionally filtered to enabled only."""
        conn = self._get_conn()
        try:
            if enabled_only:
                rows = conn.execute(
                    "SELECT * FROM alert_flows WHERE enabled = 1 ORDER BY created_at DESC"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM alert_flows ORDER BY created_at DESC"
                ).fetchall()
            return [self._row_to_flow(r) for r in rows]
        finally:
            conn.close()

    def get_flow(self, flow_id: str) -> dict | None:
        """Get a single flow by ID."""
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT * FROM alert_flows WHERE id = ?", (flow_id,)
            ).fetchone()
            return self._row_to_flow(row) if row else None
        finally:
            conn.close()

    def create_flow(self, flow: dict) -> dict:
        """Create a new flow."""
        flow_id = flow.get("id", f"flow_{uuid.uuid4().hex[:12]}")
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        flow_data = {
            "id": flow_id,
            "name": flow.get("name", "Untitled Flow"),
            "description": flow.get("description", ""),
            "enabled": 1 if flow.get("enabled", True) else 0,
            "severity": flow.get("severity", "warning"),
            "template_id": flow.get("template_id"),
            "cooldown_seconds": flow.get("cooldown_seconds", 300),
            "flow_json": json.dumps({
                "nodes": flow.get("nodes", []),
                "edges": flow.get("edges", []),
            }),
            "created_at": now,
            "updated_at": now,
        }

        conn = self._get_conn()
        try:
            conn.execute("""
                INSERT INTO alert_flows (id, name, description, enabled, severity,
                    template_id, cooldown_seconds, flow_json, created_at, updated_at)
                VALUES (:id, :name, :description, :enabled, :severity,
                    :template_id, :cooldown_seconds, :flow_json, :created_at, :updated_at)
            """, flow_data)
            conn.commit()
            logger.info(f"Created alert flow: {flow_id} ({flow_data['name']})")
            return self.get_flow(flow_id)
        finally:
            conn.close()

    def update_flow(self, flow_id: str, updates: dict) -> dict | None:
        """Update an existing flow."""
        existing = self.get_flow(flow_id)
        if not existing:
            return None

        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        # Build SET clause dynamically
        allowed = {"name", "description", "enabled", "severity",
                   "cooldown_seconds", "nodes", "edges"}
        set_parts = ["updated_at = ?"]
        params = [now]

        for key, val in updates.items():
            if key in allowed:
                if key == "enabled":
                    set_parts.append("enabled = ?")
                    params.append(1 if val else 0)
                elif key in ("nodes", "edges"):
                    # Update within flow_json
                    pass  # handled below
                else:
                    set_parts.append(f"{key} = ?")
                    params.append(val)

        # Handle nodes/edges update
        if "nodes" in updates or "edges" in updates:
            flow_json = json.loads(existing.get("flow_json", "{}") if isinstance(existing.get("flow_json"), str) else json.dumps(existing.get("flow_json", {})))
            if isinstance(flow_json, str):
                flow_json = json.loads(flow_json)
            if "nodes" in updates:
                flow_json["nodes"] = updates["nodes"]
            if "edges" in updates:
                flow_json["edges"] = updates["edges"]
            set_parts.append("flow_json = ?")
            params.append(json.dumps(flow_json))

        params.append(flow_id)

        conn = self._get_conn()
        try:
            conn.execute(
                f"UPDATE alert_flows SET {', '.join(set_parts)} WHERE id = ?",
                params
            )
            conn.commit()
            logger.info(f"Updated alert flow: {flow_id}")
            return self.get_flow(flow_id)
        finally:
            conn.close()

    def delete_flow(self, flow_id: str) -> bool:
        """Delete a flow."""
        conn = self._get_conn()
        try:
            cursor = conn.execute("DELETE FROM alert_flows WHERE id = ?", (flow_id,))
            conn.commit()
            deleted = cursor.rowcount > 0
            if deleted:
                logger.info(f"Deleted alert flow: {flow_id}")
            return deleted
        finally:
            conn.close()

    def update_fire_count(self, flow_id: str):
        """Increment fire count and update last_fired_at."""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        conn = self._get_conn()
        try:
            conn.execute(
                "UPDATE alert_flows SET fire_count = fire_count + 1, last_fired_at = ? WHERE id = ?",
                (now, flow_id)
            )
            conn.commit()
        finally:
            conn.close()

    # --- Alert History ---

    def log_alert(self, alert: dict):
        """Log an alert to history."""
        conn = self._get_conn()
        try:
            conn.execute("""
                INSERT INTO alert_history (flow_id, flow_name, severity, title, message,
                    event_type, object_id, object_type, lat, lon, alt, event_data,
                    actions_executed)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                alert.get("flow_id", ""),
                alert.get("flow_name", ""),
                alert.get("severity", "info"),
                alert.get("title", ""),
                alert.get("message", ""),
                alert.get("event_type", ""),
                alert.get("object_id"),
                alert.get("object_type"),
                alert.get("lat"),
                alert.get("lon"),
                alert.get("alt"),
                json.dumps(alert.get("event_data", {})),
                json.dumps(alert.get("actions_executed", [])),
            ))
            conn.commit()
        except Exception as e:
            logger.error(f"Error logging alert: {e}")
        finally:
            conn.close()

    def query_history(self, filters: dict = None, limit: int = 100, offset: int = 0) -> list:
        """Query alert history with optional filters."""
        filters = filters or {}
        where_parts = []
        params = []

        if "severity" in filters:
            where_parts.append("severity = ?")
            params.append(filters["severity"])
        if "object_type" in filters:
            where_parts.append("object_type = ?")
            params.append(filters["object_type"])
        if "object_id" in filters:
            where_parts.append("object_id = ?")
            params.append(filters["object_id"])
        if "flow_id" in filters:
            where_parts.append("flow_id = ?")
            params.append(filters["flow_id"])
        if "acknowledged" in filters:
            where_parts.append("acknowledged = ?")
            params.append(1 if filters["acknowledged"] else 0)
        if "since" in filters:
            where_parts.append("created_at >= ?")
            params.append(filters["since"])
        if "until" in filters:
            where_parts.append("created_at <= ?")
            params.append(filters["until"])

        where_clause = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""

        limit = min(limit, 1000)
        params.extend([limit, offset])

        conn = self._get_conn()
        try:
            rows = conn.execute(
                f"SELECT * FROM alert_history {where_clause} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                params
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def acknowledge_alert(self, alert_id: int, by: str = "operator") -> bool:
        """Mark an alert as acknowledged."""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        conn = self._get_conn()
        try:
            cursor = conn.execute(
                "UPDATE alert_history SET acknowledged = 1, acknowledged_at = ?, acknowledged_by = ? WHERE id = ?",
                (now, by, alert_id)
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()

    def acknowledge_all(self, severity: str = None) -> int:
        """Acknowledge all unacknowledged alerts."""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        conn = self._get_conn()
        try:
            if severity:
                cursor = conn.execute(
                    "UPDATE alert_history SET acknowledged = 1, acknowledged_at = ? WHERE acknowledged = 0 AND severity = ?",
                    (now, severity)
                )
            else:
                cursor = conn.execute(
                    "UPDATE alert_history SET acknowledged = 1, acknowledged_at = ? WHERE acknowledged = 0",
                    (now,)
                )
            conn.commit()
            return cursor.rowcount
        finally:
            conn.close()

    def get_stats(self) -> dict:
        """Get alert statistics for the last 24 hours."""
        conn = self._get_conn()
        try:
            rows = conn.execute("""
                SELECT 
                    severity,
                    COUNT(*) as total,
                    COUNT(CASE WHEN acknowledged = 0 THEN 1 END) as unacked,
                    MAX(created_at) as latest
                FROM alert_history
                WHERE created_at > datetime('now', '-24 hours')
                GROUP BY severity
            """).fetchall()
            stats = {
                "info": 0, "warning": 0, "critical": 0,
                "emergency": 0, "system": 0, "unacked": 0,
            }
            for row in rows:
                sev = row["severity"]
                stats[sev] = row["total"]
                stats["unacked"] += row["unacked"]
            return stats
        finally:
            conn.close()

    def cleanup_old_alerts(self, retention_days: int = 90):
        """Remove alerts older than retention_days."""
        conn = self._get_conn()
        try:
            cursor = conn.execute(
                "DELETE FROM alert_history WHERE created_at < datetime('now', ?)",
                (f"-{retention_days} days",)
            )
            conn.commit()
            if cursor.rowcount > 0:
                logger.info(f"Cleaned up {cursor.rowcount} old alerts (>{retention_days} days)")
        finally:
            conn.close()

    # --- Cooldown persistence ---

    def load_cooldowns(self) -> dict:
        """Load persisted cooldowns."""
        conn = self._get_conn()
        try:
            rows = conn.execute("SELECT * FROM alert_cooldowns").fetchall()
            cooldowns = {}
            for row in rows:
                key = (row["flow_id"], row["object_id"])
                try:
                    ts = datetime.fromisoformat(row["last_fired_at"]).timestamp()
                except (ValueError, TypeError):
                    ts = 0
                cooldowns[key] = ts
            return cooldowns
        finally:
            conn.close()

    def save_cooldown(self, flow_id: str, object_id: str, timestamp: float):
        """Persist a cooldown entry."""
        ts_str = datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        conn = self._get_conn()
        try:
            conn.execute("""
                INSERT OR REPLACE INTO alert_cooldowns (flow_id, object_id, last_fired_at, fire_count)
                VALUES (?, ?, ?, COALESCE(
                    (SELECT fire_count + 1 FROM alert_cooldowns WHERE flow_id = ? AND object_id = ?), 1
                ))
            """, (flow_id, object_id, ts_str, flow_id, object_id))
            conn.commit()
        except Exception as e:
            logger.error(f"Error saving cooldown: {e}")
        finally:
            conn.close()

    # --- Helpers ---

    @staticmethod
    def _row_to_flow(row) -> dict:
        """Convert a database row to a flow dict."""
        if row is None:
            return None
        flow = dict(row)
        # Parse flow_json
        if "flow_json" in flow and flow["flow_json"]:
            try:
                parsed = json.loads(flow["flow_json"])
                flow["nodes"] = parsed.get("nodes", [])
                flow["edges"] = parsed.get("edges", [])
            except (json.JSONDecodeError, TypeError):
                flow["nodes"] = []
                flow["edges"] = []
        flow["enabled"] = bool(flow.get("enabled", 0))
        return flow


# ============================================================
# Rule Engine
# ============================================================

class RuleEngine:
    """Evaluates events against active flows and executes matching actions.
    
    Subscribes to all events on the EventBus and runs evaluation for each.
    """

    def __init__(self, storage: FlowStorage, event_bus: EventBus,
                 socketio=None, mqtt_publisher=None, zones_getter=None):
        """
        Args:
            storage: FlowStorage instance for persistence
            event_bus: EventBus instance to subscribe to
            socketio: Flask-SocketIO instance for UI alerts
            mqtt_publisher: MQTTPublisher instance for MQTT actions
            zones_getter: Callable that returns current zones list
        """
        self.storage = storage
        self.event_bus = event_bus
        self.socketio = socketio
        self.mqtt = mqtt_publisher
        self._zones_getter = zones_getter

        # In-memory flow cache
        self._flows: dict[str, dict] = {}
        self._flows_lock = threading.Lock()

        # Object state tracking
        self.object_state: dict[str, dict] = {}
        self._state_lock = threading.Lock()

        # Cooldowns
        self._cooldowns: dict[tuple, float] = {}
        self._cooldown_lock = threading.Lock()

        # Rate limiting state
        self._rate_limit_state: dict[tuple, list] = {}
        self._rate_limit_lock = threading.Lock()

        # Duration timers
        self._duration_timers: dict[tuple, float] = {}
        self._duration_lock = threading.Lock()

        # Action executors (registered externally)
        self._action_executors: dict[str, object] = {}

        # Stats
        self._eval_count = 0
        self._fire_count = 0
        self._last_eval_time = 0.0
        self._current_flow_id = ""  # used by duration eval

        # Running flag
        self._running = False

        # Load flows and cooldowns
        self._load_flows()
        self._cooldowns = self.storage.load_cooldowns()

        logger.info(f"RuleEngine initialised with {len(self._flows)} flows")

    def start(self):
        """Start the rule engine — subscribe to event bus."""
        if self._running:
            return
        self._running = True
        self.event_bus.subscribe("*", self._on_event)

        # Start maintenance thread for cleanup
        self._maintenance_thread = threading.Thread(
            target=self._maintenance_loop, daemon=True
        )
        self._maintenance_thread.start()
        logger.info("RuleEngine started")

    def stop(self):
        """Stop the rule engine."""
        self._running = False
        self.event_bus.unsubscribe("*", self._on_event)
        logger.info("RuleEngine stopped")

    def register_action(self, action_type: str, executor):
        """Register an action executor for a given action type."""
        self._action_executors[action_type] = executor
        logger.debug(f"Registered action executor: {action_type}")

    def reload_flows(self):
        """Reload all flows from database."""
        self._load_flows()
        logger.info(f"Reloaded {len(self._flows)} flows")

    def _load_flows(self):
        """Load all enabled flows from storage into memory."""
        try:
            flows = self.storage.list_flows(enabled_only=False)
            with self._flows_lock:
                self._flows = {f["id"]: f for f in flows}
        except Exception as e:
            logger.error(f"Error loading flows: {e}")

    def _on_event(self, event: dict):
        """Main evaluation entry point — called for every event."""
        if not self._running:
            return

        self._eval_count += 1
        self._last_eval_time = time.time()

        # Update object state
        self._update_object_state(event)

        # Evaluate against all enabled flows
        with self._flows_lock:
            flows = {k: v for k, v in self._flows.items() if v.get("enabled", False)}

        for flow_id, flow in flows.items():
            try:
                self._evaluate_flow(flow, event)
            except Exception as e:
                logger.error(f"Error evaluating flow {flow_id}: {e}")

    def _evaluate_flow(self, flow: dict, event: dict):
        """Evaluate a single flow against an event."""
        flow_id = flow["id"]
        self._current_flow_id = flow_id

        nodes = flow.get("nodes", [])
        edges = flow.get("edges", [])

        if not nodes or not edges:
            return

        # Find trigger nodes
        trigger_nodes = [n for n in nodes if n.get("type") == "trigger"]
        if not trigger_nodes:
            return

        # Check if any trigger matches
        matched_trigger = None
        for trigger in trigger_nodes:
            if match_trigger(trigger, event):
                matched_trigger = trigger
                break

        if not matched_trigger:
            return

        # Walk the condition chain from trigger
        # Build adjacency list
        adj = {}
        for edge in edges:
            adj.setdefault(edge["from"], []).append(edge["to"])

        node_map = {n["id"]: n for n in nodes}

        # Walk from trigger through conditions
        if not self._walk_conditions(matched_trigger["id"], adj, node_map, event):
            return

        # Check flow-level cooldown
        cooldown_secs = flow.get("cooldown_seconds", 300)
        obj_id = event.get("object_id", "_global")
        cooldown_key = (flow_id, obj_id)

        with self._cooldown_lock:
            last_fired = self._cooldowns.get(cooldown_key, 0)
            if time.time() - last_fired < cooldown_secs:
                return
            self._cooldowns[cooldown_key] = time.time()

        # Save cooldown to DB
        try:
            self.storage.save_cooldown(flow_id, obj_id, time.time())
        except Exception:
            pass

        # Build template context
        ctx = build_template_context(event, flow)

        # Collect action nodes
        action_nodes = [n for n in nodes if n.get("type") == "action"]

        # Find which action nodes are reachable from the trigger
        reachable_actions = set()
        self._find_reachable_actions(matched_trigger["id"], adj, node_map, reachable_actions)

        # Execute all reachable action nodes
        actions_executed = []
        for action_node in action_nodes:
            if action_node["id"] not in reachable_actions:
                continue

            action_type = action_node.get("action_type", "")
            action_config = action_node.get("config", {})

            # Resolve template variables in action config
            resolved_config = self._resolve_config(action_config, ctx)

            executor = self._action_executors.get(action_type)
            if executor:
                try:
                    executor.execute(resolved_config, event, flow, ctx)
                    actions_executed.append(action_type)
                except Exception as e:
                    logger.error(f"Action {action_type} error in flow {flow_id}: {e}")
            else:
                logger.warning(f"No executor for action type: {action_type}")

        # Log the alert
        if actions_executed:
            self._fire_count += 1

            # Build alert title/message from the first ui_alert action if present,
            # otherwise from flow name
            title = flow.get("name", "Alert")
            message = ""
            for action_node in action_nodes:
                if action_node.get("action_type") == "ui_alert" and action_node["id"] in reachable_actions:
                    ac = action_node.get("config", {})
                    title = resolve_template(ac.get("title", title), ctx)
                    message = resolve_template(ac.get("message", ""), ctx)
                    break

            if not message:
                message = f"{event.get('event_type', '')} — {event.get('object_id', '')}"

            loc = event.get("location", {})
            alert_record = {
                "flow_id": flow["id"],
                "flow_name": flow.get("name", ""),
                "severity": flow.get("severity", "warning"),
                "title": title,
                "message": message,
                "event_type": event.get("event_type", ""),
                "object_id": event.get("object_id"),
                "object_type": event.get("object_type"),
                "lat": loc.get("lat"),
                "lon": loc.get("lon"),
                "alt": loc.get("alt"),
                "event_data": event,
                "actions_executed": actions_executed,
            }

            try:
                self.storage.log_alert(alert_record)
                self.storage.update_fire_count(flow["id"])
            except Exception as e:
                logger.error(f"Error logging alert: {e}")

            logger.info(
                f"🔔 Alert fired: [{flow.get('severity', 'info').upper()}] "
                f"{title} — {', '.join(actions_executed)}"
            )

    def _walk_conditions(self, node_id: str, adj: dict, node_map: dict,
                         event: dict) -> bool:
        """Walk from a node through connected condition nodes.
        
        Returns True if all conditions in the chain pass.
        """
        next_ids = adj.get(node_id, [])
        if not next_ids:
            return True  # No conditions = pass

        for next_id in next_ids:
            node = node_map.get(next_id)
            if not node:
                continue

            if node.get("type") == "condition":
                if not evaluate_condition(node, event, self):
                    return False
                # Recurse through downstream conditions
                if not self._walk_conditions(next_id, adj, node_map, event):
                    return False
            # If it's an action node, we've passed all conditions

        return True

    def _find_reachable_actions(self, node_id: str, adj: dict,
                                node_map: dict, result: set):
        """Find all action node IDs reachable from a starting node."""
        for next_id in adj.get(node_id, []):
            node = node_map.get(next_id)
            if not node:
                continue
            if node.get("type") == "action":
                result.add(next_id)
            else:
                self._find_reachable_actions(next_id, adj, node_map, result)

    def _resolve_config(self, config: dict, ctx: dict) -> dict:
        """Resolve template variables in an action config dict."""
        resolved = {}
        for k, v in config.items():
            if isinstance(v, str):
                resolved[k] = resolve_template(v, ctx)
            elif isinstance(v, dict):
                resolved[k] = self._resolve_config(v, ctx)
            else:
                resolved[k] = v
        return resolved

    def _update_object_state(self, event: dict):
        """Track object lifecycle for state-check conditions."""
        obj_id = event.get("object_id")
        if not obj_id:
            return

        now = time.time()
        with self._state_lock:
            if obj_id not in self.object_state:
                self.object_state[obj_id] = {
                    "first_seen": now,
                    "last_seen": now,
                    "detection_count": 1,
                    "zone_ids": set(),
                    "object_type": event.get("object_type", ""),
                }
            else:
                state = self.object_state[obj_id]
                state["last_seen"] = now
                state["detection_count"] += 1

            # Update zone membership from zone events
            event_type = event.get("event_type", "")
            if "zone_entry" in event_type:
                zone_id = event.get("data", {}).get("zone_id")
                if zone_id:
                    self.object_state[obj_id]["zone_ids"].add(zone_id)
            elif "zone_exit" in event_type:
                zone_id = event.get("data", {}).get("zone_id")
                if zone_id:
                    self.object_state[obj_id]["zone_ids"].discard(zone_id)

    def _get_zone(self, zone_id: str) -> dict | None:
        """Look up a zone by ID using the zones getter."""
        if not self._zones_getter:
            return None
        try:
            zones = self._zones_getter()
            for zone in zones:
                if zone.get("id") == zone_id:
                    return zone
        except Exception:
            pass
        return None

    def _maintenance_loop(self):
        """Periodic maintenance: cleanup stale state, old alerts."""
        while self._running:
            try:
                # Cleanup old object state (not seen for >24h)
                cutoff = time.time() - 86400
                with self._state_lock:
                    stale = [k for k, v in self.object_state.items()
                             if v.get("last_seen", 0) < cutoff]
                    for k in stale:
                        del self.object_state[k]

                # Cleanup old rate limit state
                now = time.time()
                with self._rate_limit_lock:
                    stale_keys = []
                    for key, timestamps in self._rate_limit_state.items():
                        self._rate_limit_state[key] = [
                            t for t in timestamps if now - t < 3600
                        ]
                        if not self._rate_limit_state[key]:
                            stale_keys.append(key)
                    for key in stale_keys:
                        del self._rate_limit_state[key]

                # Cleanup old alerts (every run = every 5 min)
                self.storage.cleanup_old_alerts(retention_days=90)

            except Exception as e:
                logger.error(f"Maintenance loop error: {e}")

            # Run every 5 minutes
            for _ in range(300):
                if not self._running:
                    break
                time.sleep(1)

    @property
    def stats(self) -> dict:
        """Return engine statistics."""
        with self._flows_lock:
            enabled_count = sum(1 for f in self._flows.values() if f.get("enabled"))
            total_count = len(self._flows)

        return {
            "total_flows": total_count,
            "enabled_flows": enabled_count,
            "total_evaluations": self._eval_count,
            "total_fires": self._fire_count,
            "last_eval_time": self._last_eval_time,
            "tracked_objects": len(self.object_state),
            "action_executors": list(self._action_executors.keys()),
            "event_bus": self.event_bus.stats,
        }
