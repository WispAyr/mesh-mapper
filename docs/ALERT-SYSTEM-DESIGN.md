# Mesh Mapper — Alert Flow System Design

> **Version:** 1.0  
> **Date:** 2025-07-11  
> **Status:** Design  
> **Author:** Skynet Engineering  

---

## Table of Contents

1. [Overview & Goals](#1-overview--goals)
2. [Architecture](#2-architecture)
3. [Node Types](#3-node-types)
4. [Flow Templates](#4-flow-templates)
5. [Data Model](#5-data-model)
6. [API Design](#6-api-design)
7. [Frontend — Flow Editor UI](#7-frontend--flow-editor-ui)
8. [Implementation Plan](#8-implementation-plan)
9. [MQTT Integration](#9-mqtt-integration)
10. [Future Considerations](#10-future-considerations)

---

## 1. Overview & Goals

### Problem Statement

Mesh Mapper currently detects and tracks drones, aircraft, vessels, weather events, lightning, APRS stations, and airspace violations across a real-time MapLibre GL web UI. It has a basic webhook notification system that fires on drone detections and a zone event system that logs incidents when drones enter/exit airspace boundaries.

**What's missing:** There is no way for operators to define custom, composable alert logic. The webhook system is hardcoded to drone events. Zone violations are logged but not actionable. There's no way to say *"if a vessel enters this polygon AND its speed drops below 1kt AND it's between 22:00-06:00, then announce it on the horn AND push to Telegram AND log it as a critical alert."*

### Goals

1. **Actionable alerts, not just notifications** — Every alert should tell the operator what happened, why it matters, and what to do. Alerts are categorised by severity (info, warning, critical, emergency) and can trigger real-world actions (audio announcements, camera movements, push notifications).

2. **Template-based** — Users pick from pre-built flow templates ("Drone Near Airfield", "Vessel Loitering", "Lightning Proximity") and customise thresholds, zones, and actions. No one should need to understand flow programming to get useful alerts running.

3. **Visual flow editor** — A simplified Node-RED-style editor where operators wire trigger → condition → action chains. Deliberately simpler than Node-RED: no JavaScript function nodes, no subflows, no complex routing. This is a domain-specific flow editor for surveillance alerts.

4. **Zone-aware** — Triggers can be scoped to GeoJSON polygon zones. "Alert me when aircraft X enters zone Y" is a first-class concept, leveraging the existing `point_in_polygon()` implementation and `zones` table.

5. **Object-aware** — Flows can target specific objects by identifier (drone MAC/OUI, aircraft ICAO hex, vessel MMSI, APRS callsign) or match by classification, type, flag state, etc.

6. **Real-time** — Alert evaluation happens on every incoming event with sub-second latency. Alerts appear instantly on the UI, are pushed to external channels, and are logged for audit.

7. **Lightweight** — Runs on a Raspberry Pi 5. No heavy dependencies, no external databases. SQLite + Python + in-memory evaluation. The rule engine must add negligible overhead to the existing ~15-thread architecture.

### Non-Goals

- This is not a general-purpose automation platform (use Node-RED for that)
- No complex data transformations or scripting within flows
- No multi-tenant isolation (single operator environment)
- No machine learning or anomaly detection (Phase 1 — deterministic rules only)

---

## 2. Architecture

### 2.1 High-Level System Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          mesh-mapper.py                                  │
│                                                                          │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌────────────┐  │
│  │ ESP32 Serial  │  │ ADS-B Poller │  │ AIS WebSocket│  │ Lightning  │  │
│  │ (drones)      │  │ (aircraft)   │  │ (vessels)    │  │ WebSocket  │  │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └─────┬──────┘  │
│         │                  │                  │                │         │
│  ┌──────┴───────┐  ┌──────┴───────┐  ┌──────┴───────┐  ┌─────┴──────┐  │
│  │ APRS Poller  │  │ Met Office   │  │ Weather API  │  │ System Mon │  │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └─────┬──────┘  │
│         │                  │                  │                │         │
│         ▼                  ▼                  ▼                ▼         │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │                      EVENT BUS (Python)                          │   │
│  │  Normalised events: {type, source, object_id, lat, lon, data}   │   │
│  └──────────────────────────────┬───────────────────────────────────┘   │
│                                 │                                        │
│                                 ▼                                        │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │                      RULE ENGINE (alert_engine.py)               │   │
│  │                                                                   │   │
│  │  For each event:                                                  │   │
│  │    1. Match against active flow triggers                         │   │
│  │    2. Evaluate condition chain (geofence, threshold, time, etc.) │   │
│  │    3. Check cooldown / rate limits                               │   │
│  │    4. Execute action nodes (parallel)                            │   │
│  │    5. Log to alert_history                                       │   │
│  └──────────────┬───────────────────────────────────┬───────────────┘   │
│                  │                                   │                   │
│          ┌───────┴────────┐                 ┌────────┴──────────┐       │
│          ▼                ▼                 ▼                    ▼       │
│  ┌────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │ SocketIO   │  │ MQTT Publish │  │ Telegram     │  │ AI Horn TTS  │  │
│  │ (UI alert) │  │ (topics)     │  │ (Clawdbot)   │  │ (announce)   │  │
│  └────────────┘  └──────────────┘  └──────────────┘  └──────────────┘  │
│                                                                          │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │  REST API (/api/alerts/*)    │  Flow Editor UI (static/alerts/) │   │
│  └──────────────────────────────┴───────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────┘
```

### 2.2 Integration Strategy: New Module, Not Separate Service

**Decision: `alert_engine.py` as an importable Python module within mesh-mapper.**

Rationale:
- Mesh-mapper is a 14k-line monolith. Adding a separate microservice would complicate deployment on a Pi (more systemd units, IPC overhead, failure modes).
- The rule engine needs direct access to in-memory state (`tracked_pairs`, `AIS_VESSELS`, `ADSB_AIRCRAFT`, `ZONES`, etc.) for fast evaluation without serialisation overhead.
- The existing `MQTTPublisher` class and Flask/SocketIO server are already in-process — the alert engine should be too.

**File structure:**

```
mesh-mapper/
├── mesh-mapper.py              # Main application (existing)
├── alert_engine.py             # Rule engine + event bus + flow evaluator
├── alert_actions.py            # Action executors (telegram, horn, webhook, etc.)
├── alert_templates.py          # Built-in flow template definitions
├── static/
│   ├── js/
│   │   ├── alerts/
│   │   │   ├── flow-editor.js  # Visual flow editor canvas
│   │   │   ├── node-palette.js # Sidebar node library
│   │   │   ├── node-config.js  # Property editor panels
│   │   │   ├── flow-validate.js# Flow validation logic
│   │   │   └── alert-dashboard.js # Live alerts + history panel
│   │   └── ...
│   └── ...
├── docs/
│   └── ALERT-SYSTEM-DESIGN.md  # This document
└── ...
```

### 2.3 Event Bus

The event bus is the bridge between data sources and the rule engine. Every data update in mesh-mapper publishes a normalised event.

**Event envelope format:**

```python
{
    "event_type": "drone.detected",      # Hierarchical type
    "source": "serial",                   # Origin subsystem
    "timestamp": 1720000000.0,            # Unix timestamp
    "object_id": "AA:BB:CC:DD:EE:FF",   # Primary identifier
    "object_type": "drone",               # drone | aircraft | vessel | aprs | lightning | weather | system
    "location": {
        "lat": 55.458,
        "lon": -4.629,
        "alt": 120.5                      # Metres (optional)
    },
    "data": {                             # Type-specific payload
        "mac": "AA:BB:CC:DD:EE:FF",
        "rssi": -65,
        "basic_id": "SERIAL_NUMBER",
        "pilot_lat": 55.457,
        "pilot_lon": -4.630,
        "is_new": true,
        "is_returning": false
    }
}
```

**Implementation:** A simple in-process publish/subscribe using callbacks. No external message broker needed.

```python
class EventBus:
    """Lightweight in-process event bus for alert engine."""
    
    def __init__(self):
        self._handlers: dict[str, list[Callable]] = {}
    
    def subscribe(self, event_pattern: str, handler: Callable):
        """Subscribe to events matching a pattern. Supports wildcards: 'drone.*'"""
        self._handlers.setdefault(event_pattern, []).append(handler)
    
    def publish(self, event: dict):
        """Publish an event to all matching subscribers."""
        event_type = event.get("event_type", "")
        for pattern, handlers in self._handlers.items():
            if self._matches(event_type, pattern):
                for handler in handlers:
                    handler(event)
    
    @staticmethod
    def _matches(event_type: str, pattern: str) -> bool:
        if pattern == "*":
            return True
        if pattern.endswith(".*"):
            return event_type.startswith(pattern[:-2])
        return event_type == pattern
```

### 2.4 Event Types Published

| Event Type | Source | Trigger Point in mesh-mapper.py |
|------------|--------|--------------------------------|
| `drone.detected` | Serial reader | `update_detection()` — new MAC first seen |
| `drone.updated` | Serial reader | `update_detection()` — existing MAC position update |
| `drone.lost` | Cleanup timer | `cleanup_timer` — MAC goes stale |
| `drone.reactivated` | Serial reader | `update_detection()` — stale MAC comes back |
| `drone.zone_entry` | Zone checker | `check_zone_events()` — entered zone |
| `drone.zone_exit` | Zone checker | `check_zone_events()` — exited zone |
| `aircraft.updated` | ADS-B poller | `adsb_updater()` — position update |
| `aircraft.new` | ADS-B poller | New hex code first seen this session |
| `aircraft.lost` | ADS-B poller | Hex code disappears from feed |
| `aircraft.squawk` | ADS-B poller | Squawk code change detected |
| `vessel.updated` | AIS WebSocket/REST | `process_ais_message()` — position update |
| `vessel.new` | AIS WebSocket/REST | New MMSI first seen |
| `vessel.lost` | AIS updater | MMSI not seen for threshold |
| `vessel.speed_change` | AIS processor | Speed crosses a threshold |
| `lightning.strike` | Blitzortung WS | `process_lightning_strike()` |
| `weather.warning` | Met Office poller | `metoffice_updater()` — new/updated warning |
| `weather.warning_expired` | Met Office poller | Warning removed from feed |
| `aprs.updated` | APRS poller | `aprs_updater()` — station position update |
| `aprs.new` | APRS poller | New callsign first seen |
| `system.feed_stale` | Status logger | Data source not updated for threshold |
| `system.mqtt_disconnect` | MQTT publisher | Broker connection lost |
| `system.serial_disconnect` | Port monitor | ESP32 USB disconnected |
| `mqtt.external` | MQTT subscriber | Message on subscribed external topic |

### 2.5 Hook Points in mesh-mapper.py

Integration requires adding `event_bus.publish()` calls at key points. Minimal, non-breaking changes:

```python
# In update_detection() at line ~4971, after tracking logic:
event_bus.publish({
    "event_type": "drone.detected" if is_new else "drone.updated",
    "source": "serial",
    "timestamp": time.time(),
    "object_id": mac,
    "object_type": "drone",
    "location": {"lat": drone_lat, "lon": drone_long, "alt": drone_alt},
    "data": detection
})

# In check_zone_events() at line ~3590, on zone entry/exit:
event_bus.publish({
    "event_type": "drone.zone_entry",
    ...
})

# In adsb_updater() at line ~1834, for each aircraft:
event_bus.publish({
    "event_type": "aircraft.updated",
    "source": "adsb",
    "timestamp": time.time(),
    "object_id": hex_id,
    "object_type": "aircraft",
    "location": {"lat": lat, "lon": lon, "alt": alt_ft},
    "data": aircraft_data
})

# Similar patterns for AIS, APRS, lightning, weather, system events
```

### 2.6 Rule Engine Evaluation Loop

```python
class AlertEngine:
    """Evaluates events against active flows and executes matching actions."""
    
    def __init__(self, db_path, event_bus, socketio, mqtt_publisher):
        self.db_path = db_path
        self.event_bus = event_bus
        self.socketio = socketio
        self.mqtt = mqtt_publisher
        self.flows = {}          # flow_id -> FlowDefinition
        self.cooldowns = {}      # (flow_id, object_id) -> last_fired_timestamp
        self.object_state = {}   # object_id -> {first_seen, last_seen, zone_history, ...}
        self.duration_timers = {} # (flow_id, object_id, condition_id) -> start_time
        self.action_executor = ActionExecutor(socketio, mqtt_publisher)
        self._load_flows()
        
        # Subscribe to all events
        event_bus.subscribe("*", self.evaluate)
    
    def evaluate(self, event: dict):
        """Main evaluation entry point. Called for every event."""
        # Update object state tracking
        self._update_object_state(event)
        
        for flow_id, flow in self.flows.items():
            if not flow.enabled:
                continue
            
            # 1. Check if any trigger node matches this event
            matched_trigger = self._match_triggers(flow, event)
            if not matched_trigger:
                continue
            
            # 2. Walk the condition chain from the matched trigger
            context = FlowContext(event=event, trigger=matched_trigger, flow=flow)
            
            if not self._evaluate_conditions(flow, matched_trigger, context):
                continue
            
            # 3. Check cooldown
            cooldown_key = (flow_id, event.get("object_id", "_global"))
            if not self._check_cooldown(flow, cooldown_key):
                continue
            
            # 4. Execute all connected action nodes
            self._execute_actions(flow, context)
            
            # 5. Update cooldown
            self.cooldowns[cooldown_key] = time.time()
            
            # 6. Log to alert_history
            self._log_alert(flow, context)
    
    def _update_object_state(self, event):
        """Track object lifecycle for state-check conditions."""
        obj_id = event.get("object_id")
        if not obj_id:
            return
        
        now = time.time()
        if obj_id not in self.object_state:
            self.object_state[obj_id] = {
                "first_seen": now,
                "last_seen": now,
                "detection_count": 1,
                "zone_ids": set()
            }
        else:
            state = self.object_state[obj_id]
            state["last_seen"] = now
            state["detection_count"] += 1
```

### 2.7 Flow Storage Format

Flows are stored as JSON, inspired by Node-RED but drastically simplified:

```json
{
    "id": "flow_abc123",
    "name": "Drone Near Airfield",
    "description": "Alert when any drone is detected within 5km of Glasgow Airport",
    "enabled": true,
    "created_at": "2025-07-11T10:00:00Z",
    "updated_at": "2025-07-11T10:00:00Z",
    "template_id": "tpl_drone_near_airfield",
    "severity": "critical",
    "cooldown_seconds": 300,
    "nodes": [
        {
            "id": "n1",
            "type": "trigger",
            "trigger_type": "drone.detected",
            "config": {
                "match_new_only": true,
                "match_oui": null,
                "match_mac": null
            },
            "position": {"x": 100, "y": 200}
        },
        {
            "id": "n2",
            "type": "condition",
            "condition_type": "geofence",
            "config": {
                "zone_id": "zone_glasgow_airport",
                "check": "object_inside",
                "radius_km": 5.0
            },
            "position": {"x": 300, "y": 200}
        },
        {
            "id": "n3",
            "type": "action",
            "action_type": "ui_alert",
            "config": {
                "severity": "critical",
                "title": "Drone Near {{zone_name}}",
                "message": "Drone {{object_id}} detected {{distance_km}}km from {{zone_name}}",
                "sound": "alert-critical",
                "auto_dismiss": false
            },
            "position": {"x": 500, "y": 150}
        },
        {
            "id": "n4",
            "type": "action",
            "action_type": "telegram_push",
            "config": {
                "message": "🚨 DRONE ALERT: {{object_id}} near {{zone_name}} at {{timestamp}}\nPosition: {{lat}}, {{lon}}\nDistance: {{distance_km}}km"
            },
            "position": {"x": 500, "y": 300}
        }
    ],
    "edges": [
        {"from": "n1", "to": "n2"},
        {"from": "n2", "to": "n3"},
        {"from": "n2", "to": "n4"}
    ]
}
```

**Template variables** (double curly braces) are resolved at execution time from the event context:

| Variable | Description |
|----------|-------------|
| `{{object_id}}` | Primary identifier (MAC, hex, MMSI, callsign) |
| `{{object_type}}` | drone, aircraft, vessel, etc. |
| `{{lat}}`, `{{lon}}`, `{{alt}}` | Object location |
| `{{timestamp}}` | ISO 8601 event time |
| `{{zone_name}}` | Zone that triggered geofence match |
| `{{distance_km}}` | Distance from zone centre / point |
| `{{speed}}`, `{{heading}}` | Object dynamics |
| `{{severity}}` | Flow severity level |
| `{{flow_name}}` | Name of the flow that fired |
| `{{alias}}` | User-assigned name (if exists) |
| `{{callsign}}` | Aircraft callsign or vessel name |
| `{{squawk}}` | Aircraft squawk code |
| `{{rssi}}` | Signal strength (drones) |

---

## 3. Node Types

### 3.1 Trigger Nodes

Trigger nodes are event matchers. Each flow must have exactly one trigger node as its entry point.

#### 3.1.1 Drone Trigger (`trigger.drone`)

| Property | Type | Default | Description |
|----------|------|---------|-------------|
| `event` | enum | `detected` | `detected`, `updated`, `lost`, `reactivated`, `zone_entry`, `zone_exit` |
| `match_mac` | string | null | Specific MAC address (exact match) |
| `match_oui` | string | null | OUI prefix (first 3 octets, e.g. `"AA:BB:CC"`) — match manufacturer |
| `match_new_only` | bool | false | Only fire for MACs not seen before in this session |
| `match_not_whitelisted` | bool | false | Only fire for MACs not in the aliases/whitelist |
| `min_rssi` | int | null | Minimum RSSI (e.g. -50 = only strong signals = close proximity) |

#### 3.1.2 Aircraft Trigger (`trigger.aircraft`)

| Property | Type | Default | Description |
|----------|------|---------|-------------|
| `event` | enum | `updated` | `new`, `updated`, `lost`, `squawk_change` |
| `match_hex` | string | null | Specific ICAO hex code |
| `match_callsign` | string | null | Callsign pattern (supports `*` wildcard) |
| `match_squawk` | string | null | Specific squawk code (e.g. `"7700"` for emergency) |
| `match_category` | string | null | Aircraft category filter |
| `emergency_only` | bool | false | Only fire for emergency squawk codes (7500, 7600, 7700) |
| `military_only` | bool | false | Only match military category aircraft |

#### 3.1.3 Vessel Trigger (`trigger.vessel`)

| Property | Type | Default | Description |
|----------|------|---------|-------------|
| `event` | enum | `updated` | `new`, `updated`, `lost`, `speed_change` |
| `match_mmsi` | string | null | Specific MMSI |
| `match_name` | string | null | Vessel name pattern (wildcard) |
| `match_type` | string | null | Vessel type code filter |
| `match_flag` | string | null | Flag state (from MMSI MID code) |

#### 3.1.4 Lightning Trigger (`trigger.lightning`)

| Property | Type | Default | Description |
|----------|------|---------|-------------|
| `event` | enum | `strike` | `strike` (only option currently) |
| `reference_point` | object | null | `{lat, lon}` — centre point for distance check. If null, uses system centre. |
| `reference_zone_id` | string | null | Zone ID — trigger if strike is within zone polygon |
| `max_distance_km` | float | 50 | Maximum distance from reference point |

#### 3.1.5 Weather Warning Trigger (`trigger.weather`)

| Property | Type | Default | Description |
|----------|------|---------|-------------|
| `event` | enum | `warning` | `warning` (new/updated), `warning_expired` |
| `min_severity` | enum | `yellow` | `yellow`, `amber`, `red` |
| `match_type` | string | null | Warning type (e.g. `"wind"`, `"rain"`, `"thunder"`, `"snow"`, `"fog"`) |
| `match_region` | string | null | Region name filter |

#### 3.1.6 APRS Trigger (`trigger.aprs`)

| Property | Type | Default | Description |
|----------|------|---------|-------------|
| `event` | enum | `updated` | `new`, `updated`, `lost` |
| `match_callsign` | string | null | Specific callsign |
| `match_symbol` | string | null | APRS symbol filter |

#### 3.1.7 System Trigger (`trigger.system`)

| Property | Type | Default | Description |
|----------|------|---------|-------------|
| `event` | enum | `feed_stale` | `feed_stale`, `mqtt_disconnect`, `serial_disconnect`, `error` |
| `match_feed` | string | null | Specific feed name (`adsb`, `ais`, `aprs`, `weather`, `lightning`) |
| `stale_threshold_seconds` | int | 300 | How long before a feed is considered stale |

#### 3.1.8 Schedule Trigger (`trigger.schedule`)

| Property | Type | Default | Description |
|----------|------|---------|-------------|
| `cron` | string | required | Cron expression (e.g. `"0 */6 * * *"` = every 6 hours) |
| `description` | string | null | Human-readable schedule description |

*Implementation: Uses `croniter` library, checked every 60 seconds by the engine's maintenance loop.*

#### 3.1.9 MQTT External Trigger (`trigger.mqtt`)

| Property | Type | Default | Description |
|----------|------|---------|-------------|
| `topic` | string | required | MQTT topic to subscribe to (supports `+` and `#` wildcards) |
| `payload_filter` | object | null | JSON path conditions on the message payload |

### 3.2 Condition Nodes

Condition nodes filter events. They sit between triggers and actions. All conditions evaluate to `true` (pass) or `false` (stop). Multiple conditions in sequence form an implicit AND chain.

#### 3.2.1 Geofence Condition (`condition.geofence`)

| Property | Type | Default | Description |
|----------|------|---------|-------------|
| `zone_id` | string | null | ID of a saved zone polygon |
| `point` | object | null | `{lat, lon}` — centre of a circle check |
| `radius_km` | float | null | Radius in km (used with `point` or zone centroid) |
| `check` | enum | `object_inside` | `object_inside`, `object_outside`, `pilot_inside` (drones only) |

*Uses the existing `point_in_polygon()` for polygon zones and Haversine for radius checks.*

#### 3.2.2 Time Filter Condition (`condition.time_filter`)

| Property | Type | Default | Description |
|----------|------|---------|-------------|
| `time_start` | string | null | `"HH:MM"` — start of active window (24h format) |
| `time_end` | string | null | `"HH:MM"` — end of active window |
| `days_of_week` | list | `[0-6]` | Active days (0=Monday, 6=Sunday) |
| `timezone` | string | `"Europe/London"` | Timezone for evaluation |
| `invert` | bool | false | If true, fires OUTSIDE the time window |

#### 3.2.3 Rate Limiter Condition (`condition.rate_limit`)

| Property | Type | Default | Description |
|----------|------|---------|-------------|
| `max_fires` | int | 1 | Maximum triggers within the window |
| `window_minutes` | int | 5 | Sliding window size |
| `per_object` | bool | true | Rate limit per object_id (vs global for the flow) |

#### 3.2.4 Threshold Condition (`condition.threshold`)

| Property | Type | Default | Description |
|----------|------|---------|-------------|
| `field` | string | required | Dot-path into event data (e.g. `"data.speed_kts"`, `"location.alt"`) |
| `operator` | enum | required | `lt`, `lte`, `gt`, `gte`, `eq`, `neq`, `between` |
| `value` | float | required | Comparison value |
| `value_max` | float | null | Upper bound (for `between` operator) |
| `unit` | string | null | Display hint: `"ft"`, `"m"`, `"kts"`, `"km"`, `"nm"` |

#### 3.2.5 Object Match Condition (`condition.object_match`)

| Property | Type | Default | Description |
|----------|------|---------|-------------|
| `field` | string | required | Field to match (e.g. `"data.mmsi"`, `"data.squawk"`, `"object_id"`) |
| `operator` | enum | `eq` | `eq`, `neq`, `in`, `not_in`, `contains`, `starts_with`, `regex` |
| `value` | any | required | Match value(s) — string, number, or list |

#### 3.2.6 Logic Combinator (`condition.logic`)

| Property | Type | Default | Description |
|----------|------|---------|-------------|
| `operator` | enum | `and` | `and`, `or`, `not` |

*In the flow graph, a logic combinator has multiple incoming edges from condition nodes and one outgoing edge. `AND` requires all inputs to pass. `OR` requires at least one. `NOT` inverts a single input.*

#### 3.2.7 State Check Condition (`condition.state_check`)

| Property | Type | Default | Description |
|----------|------|---------|-------------|
| `check` | enum | required | `first_seen`, `returning`, `already_tracked`, `in_zone`, `not_in_zone` |
| `zone_id` | string | null | For zone-related state checks |
| `timeout_seconds` | int | null | For `returning` — how long since last seen to count as returning |

*The engine maintains `object_state` tracking first-seen timestamps, zone membership history, and last-seen times. This enables "is this drone new or returning?" checks.*

#### 3.2.8 Duration Condition (`condition.duration`)

| Property | Type | Default | Description |
|----------|------|---------|-------------|
| `min_duration_seconds` | int | required | Object must match for at least this long |
| `check` | enum | `in_zone` | What state must persist: `in_zone`, `below_speed`, `stationary` |
| `zone_id` | string | null | Zone reference (if check is `in_zone`) |
| `speed_threshold` | float | null | Speed in kts (for `below_speed` or `stationary`) |

*The engine maintains a timer per (flow_id, object_id, condition_id). Passes only when timer exceeds min_duration. Resets if condition stops matching.*

### 3.3 Action Nodes

Action nodes execute side effects. Multiple action nodes can fan out in parallel from the last condition. All actions execute asynchronously via a thread pool.

#### 3.3.1 UI Alert (`action.ui_alert`)

| Property | Type | Default | Description |
|----------|------|---------|-------------|
| `severity` | enum | `warning` | `info`, `warning`, `critical`, `emergency` |
| `title` | string | required | Alert title (supports `{{template}}` variables) |
| `message` | string | required | Alert body (supports `{{template}}` variables) |
| `sound` | enum | `default` | `none`, `default`, `alert-info`, `alert-warning`, `alert-critical`, `alert-emergency` |
| `auto_dismiss_seconds` | int | null | Auto-dismiss toast (null = manual only) |
| `highlight_object` | bool | true | Pulse the object on the map |
| `fly_to` | bool | false | Pan the map to alert location |

*SocketIO event: `alert_fired` — UI renders toast + alert panel entry.*

#### 3.3.2 Telegram Push (`action.telegram_push`)

| Property | Type | Default | Description |
|----------|------|---------|-------------|
| `message` | string | required | Telegram message (supports `{{template}}` vars + Markdown) |
| `include_map_link` | bool | true | Append Google Maps link |
| `include_details` | bool | true | Append object details block |

*Implementation: POST to the Clawdbot webhook endpoint in `webhook_url.json`.*

#### 3.3.3 Audio Announcement (`action.audio_announce`)

| Property | Type | Default | Description |
|----------|------|---------|-------------|
| `message` | string | required | TTS text (supports `{{template}}` vars) |
| `volume` | int | 50 | Volume percentage (0-100) |
| `voice` | string | `"en-GB-RyanNeural"` | TTS voice |

*Calls `/Users/noc/clawd/scripts/horn-announce.sh "{message}" {volume}` via `subprocess.Popen()`.*

#### 3.3.4 Database Log (`action.db_log`)

| Property | Type | Default | Description |
|----------|------|---------|-------------|
| `include_full_event` | bool | true | Store complete event payload |
| `retention_days` | int | 90 | Auto-cleanup after N days |

*Every fired alert is always logged. This node allows customising retention.*

#### 3.3.5 MQTT Publish (`action.mqtt_publish`)

| Property | Type | Default | Description |
|----------|------|---------|-------------|
| `topic` | string | required | MQTT topic (relative to prefix, or absolute if starts with `/`) |
| `payload` | object | required | JSON payload template (supports `{{template}}` vars) |
| `qos` | int | 1 | MQTT QoS (0, 1, 2) |
| `retain` | bool | false | Retain message |

#### 3.3.6 Webhook (`action.webhook`)

| Property | Type | Default | Description |
|----------|------|---------|-------------|
| `url` | string | required | HTTP(S) endpoint URL |
| `method` | enum | `POST` | `POST`, `PUT` |
| `headers` | object | `{}` | Custom headers |
| `payload` | object | required | JSON payload template |
| `timeout_seconds` | int | 10 | Request timeout |

#### 3.3.7 Camera Trigger (`action.camera_trigger`) — *Future*

| Property | Type | Default | Description |
|----------|------|---------|-------------|
| `camera_id` | string | required | Camera identifier |
| `action` | enum | `snapshot` | `snapshot`, `ptz_preset`, `start_recording` |
| `preset_id` | string | null | PTZ preset |

#### 3.3.8 Email (`action.email`) — *Future*

| Property | Type | Default | Description |
|----------|------|---------|-------------|
| `to` | string | required | Recipient |
| `subject` | string | required | Subject template |
| `body` | string | required | Body template |

---

## 4. Flow Templates

Templates are pre-built flows that users instantiate and customise. When selected, the system clones the definition and opens it with highlighted customisable parameters.

### 4.1 Template: Drone Near Airfield

**Scenario:** Drone detected within configurable radius of an airfield zone.  
**Severity:** Critical

```
┌──────────────────┐    ┌──────────────────┐    ┌──────────────────┐
│ TRIGGER          │    │ CONDITION         │    │ ACTION           │
│ drone.detected   │───▶│ geofence          │───▶│ ui_alert         │
│ (new only)       │    │ radius: 5km       │    │ severity: crit   │
└──────────────────┘    │ zone: airfield     │    └──────────────────┘
                        └─────────┬──────────┘
                                  │            ┌──────────────────┐
                                  └───────────▶│ telegram_push    │
                                               │ + map link       │
                                               └──────────────────┘
```

**Parameters:** Zone/airfield selection, radius (5km), new-only vs all, notification channels.

### 4.2 Template: Low-Flying Aircraft

**Scenario:** Aircraft below altitude threshold in a zone.  
**Severity:** Warning

```
┌──────────────────┐   ┌──────────────┐   ┌──────────────┐   ┌──────────────┐
│ aircraft.updated │──▶│ geofence     │──▶│ threshold    │──▶│ ui_alert     │
│                  │   │ zone: custom │   │ alt < 1000ft │   │ sev: warning │
└──────────────────┘   └──────────────┘   └──────────────┘   └──────────────┘
```

**Parameters:** Zone, altitude threshold (1000ft), callsign exclusions, cooldown (5min).

### 4.3 Template: Vessel Loitering

**Scenario:** Vessel speed <1kt in zone for extended period.  
**Severity:** Info

```
┌────────────────┐  ┌──────────┐  ┌───────────┐  ┌──────────┐  ┌──────────┐
│ vessel.updated │─▶│ geofence │─▶│ threshold │─▶│ duration │─▶│ ui_alert │
│                │  │ harbour  │  │ speed<1kt │  │ 30 min   │  │ sev:info │
└────────────────┘  └──────────┘  └───────────┘  └──────────┘  └──────────┘
```

**Parameters:** Zone, speed threshold (1kt), duration (30min), vessel type filter.

### 4.4 Template: Lightning Proximity

**Scenario:** Lightning strikes at decreasing range, with escalating severity.  
**Severity:** Warning → Critical (two linked flows)

**Flow A — Distant Warning (25-50km):**
```
┌──────────────┐  ┌─────────────────┐  ┌─────────────┐  ┌──────────────┐
│ lightning    │─▶│ geofence        │─▶│ rate_limit  │─▶│ ui_alert     │
│ .strike     │  │ radius: 25-50km │  │ 1 per 10min │  │ sev: info    │
└──────────────┘  └─────────────────┘  └─────────────┘  └──────────────┘
```

**Flow B — Close Critical (<10km):**
```
┌──────────────┐  ┌──────────────┐  ┌─────────────┐  ┌──────────────┐
│ lightning    │─▶│ geofence     │─▶│ rate_limit  │─▶│ ui_alert     │
│ .strike     │  │ radius:<10km │  │ 1 per 5min  │  │ sev: crit    │
└──────────────┘  └──────────────┘  └─────────────┘  └───────┬──────┘
                                                              │
                                                     ┌────────▼───────┐
                                                     │ audio_announce │
                                                     │ "Lightning     │
                                                     │  within 10km"  │
                                                     └────────────────┘
```

**Parameters:** Reference point, distance thresholds per tier, rate limit, horn toggle.

### 4.5 Template: Met Office Weather Warning

**Scenario:** Met Office issues a warning affecting monitored area.  
**Severity:** Matches warning (yellow→warning, amber→critical, red→emergency)

```
┌──────────────────┐  ┌──────────────┐  ┌──────────────┐
│ weather.warning  │─▶│ rate_limit   │─▶│ telegram     │
│ min: yellow      │  │ 1 per warn   │  │ full details │
└──────────────────┘  └──────┬───────┘  └──────────────┘
                             │
                    ┌────────▼────────┐
                    │ ui_alert        │
                    │ severity: auto  │
                    └─────────────────┘
```

**Parameters:** Min severity, warning type filter, region filter, channels.

### 4.6 Template: New Unknown Drone

**Scenario:** First-seen MAC not in operator's whitelist (aliases).  
**Severity:** Warning

```
┌──────────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│ drone.detected   │─▶│ state_check  │─▶│ object_match │─▶│ ui_alert     │
│ (new only)       │  │ first_seen   │  │ NOT whitelist│  │ sev: warning │
└──────────────────┘  └──────────────┘  └──────────────┘  └───────┬──────┘
                                                                   │
                                                          ┌────────▼──────┐
                                                          │ audio_announce│
                                                          │ "Unknown      │
                                                          │  drone"       │
                                                          └───────────────┘
```

**Parameters:** Whitelist source, OUI manufacturer filter, horn toggle.

### 4.7 Template: AIS Loss

**Scenario:** Tracked vessel stops transmitting.  
**Severity:** Warning

```
┌──────────────┐  ┌──────────────────┐  ┌──────────────┐  ┌──────────────┐
│ vessel.lost  │─▶│ state_check      │─▶│ geofence     │─▶│ ui_alert     │
│              │  │ tracked >5min    │  │ last pos in  │  │ sev: warning │
│              │  │                  │  │ zone         │  │              │
└──────────────┘  └──────────────────┘  └──────────────┘  └──────────────┘
```

**Parameters:** Min tracking duration (5min), zone filter, loss timeout.

### 4.8 Template: System Health Monitor

**Scenario:** Data feed stale, serial disconnect, or MQTT loss.  
**Severity:** System

```
┌──────────────┐  ┌─────────────┐  ┌──────────────┐  ┌──────────────┐
│ system.*     │─▶│ rate_limit  │─▶│ ui_alert     │─▶│ mqtt_publish │
│ (any system) │  │ 1 per 15min │  │ sev: system  │  │ system/health│
└──────────────┘  └─────────────┘  └──────────────┘  └──────────────┘
```

**Parameters:** Monitored feeds, stale threshold (5min), cooldown (15min).

### 4.9 Template: Emergency Squawk

**Scenario:** Aircraft transmits emergency squawk (7500 hijack, 7600 comms, 7700 emergency).  
**Severity:** Emergency

```
┌──────────────────┐  ┌──────────────┐  ┌──────────────┐
│ aircraft.updated │─▶│ ui_alert     │─▶│ telegram     │
│ emergency_only   │  │ sev: emerg   │  │ + map link   │
└──────────────────┘  │ fly_to: true │  └──────────────┘
                      │ sound: klaxon│           │
                      └──────────────┘  ┌────────▼──────┐
                                        │ audio_announce│
                                        │ "Emergency    │
                                        │  squawk"      │
                                        └───────────────┘
```

**Parameters:** Squawk codes, zone filter, channels, auto-fly-to.

### 4.10 Template: Drone Returning

**Scenario:** Previously-seen drone reappears after absence.  
**Severity:** Info

```
┌───────────────────┐  ┌──────────────────┐  ┌──────────────┐
│ drone.reactivated │─▶│ state_check      │─▶│ ui_alert     │
│                   │  │ returning        │  │ sev: info    │
│                   │  │ timeout: 1 hour  │  │              │
└───────────────────┘  └──────────────────┘  └──────────────┘
```

**Parameters:** Absence threshold (1hr), push toggle, announce toggle.

---

## 5. Data Model

### 5.1 SQLite Schema

All tables added to the existing `mesh_mapper.db`:

```sql
-- =============================================
-- Alert Flow System Tables
-- =============================================

-- Alert Flows: user-created alert flow definitions
CREATE TABLE IF NOT EXISTS alert_flows (
    id TEXT PRIMARY KEY,                          -- UUID
    name TEXT NOT NULL,
    description TEXT,
    enabled INTEGER DEFAULT 1,                    -- 0/1 boolean
    severity TEXT DEFAULT 'warning',              -- info|warning|critical|emergency|system
    template_id TEXT,                             -- Reference to source template (null if custom)
    cooldown_seconds INTEGER DEFAULT 300,
    flow_json TEXT NOT NULL,                      -- Complete flow definition (nodes + edges)
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_fired_at TEXT,
    fire_count INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_af_enabled ON alert_flows(enabled);
CREATE INDEX IF NOT EXISTS idx_af_template ON alert_flows(template_id);

-- Alert History: log of every alert that fired
CREATE TABLE IF NOT EXISTS alert_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    flow_id TEXT NOT NULL,
    flow_name TEXT NOT NULL,                      -- Denormalised for fast queries
    severity TEXT NOT NULL,
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    event_type TEXT NOT NULL,
    object_id TEXT,
    object_type TEXT,
    lat REAL,
    lon REAL,
    alt REAL,
    event_data TEXT,                              -- Full event JSON
    actions_executed TEXT,                         -- JSON array of action results
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

-- Alert Templates: pre-built flow templates
CREATE TABLE IF NOT EXISTS alert_templates (
    id TEXT PRIMARY KEY,                          -- e.g. 'tpl_drone_near_airfield'
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    category TEXT NOT NULL,                       -- drone|aircraft|vessel|weather|system
    severity TEXT NOT NULL,
    icon TEXT,
    flow_json TEXT NOT NULL,                      -- Template flow definition
    parameters TEXT NOT NULL,                     -- JSON schema of customisable params
    sort_order INTEGER DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_at_category ON alert_templates(category);

-- Alert Zones: GeoJSON zones for geofencing
CREATE TABLE IF NOT EXISTS alert_zones (
    id TEXT PRIMARY KEY,                          -- UUID
    name TEXT NOT NULL,
    description TEXT,
    zone_type TEXT DEFAULT 'custom',              -- custom|airfield|harbour|exclusion|monitoring
    geometry TEXT NOT NULL,                        -- GeoJSON geometry object
    properties TEXT,                               -- JSON (color, opacity, etc.)
    centre_lat REAL,
    centre_lon REAL,
    radius_km REAL,                               -- For circular zones
    enabled INTEGER DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_az_enabled ON alert_zones(enabled);
CREATE INDEX IF NOT EXISTS idx_az_type ON alert_zones(zone_type);

-- Cooldown State: persistent across restarts
CREATE TABLE IF NOT EXISTS alert_cooldowns (
    flow_id TEXT NOT NULL,
    object_id TEXT NOT NULL DEFAULT '_global',
    last_fired_at TEXT NOT NULL,
    fire_count INTEGER DEFAULT 1,
    PRIMARY KEY (flow_id, object_id),
    FOREIGN KEY (flow_id) REFERENCES alert_flows(id) ON DELETE CASCADE
);

-- Object State: tracked object lifecycle
CREATE TABLE IF NOT EXISTS alert_object_state (
    object_id TEXT PRIMARY KEY,
    object_type TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    total_detections INTEGER DEFAULT 1,
    last_zone_ids TEXT,                           -- JSON array
    properties TEXT                                -- JSON bag of state
);

CREATE INDEX IF NOT EXISTS idx_aos_type ON alert_object_state(object_type);

-- Useful views
CREATE VIEW IF NOT EXISTS recent_alerts AS
SELECT * FROM alert_history
WHERE created_at > datetime('now', '-24 hours')
ORDER BY created_at DESC;

CREATE VIEW IF NOT EXISTS unacknowledged_alerts AS
SELECT * FROM alert_history
WHERE acknowledged = 0
ORDER BY created_at DESC;

CREATE VIEW IF NOT EXISTS alert_stats AS
SELECT 
    severity,
    COUNT(*) as total,
    COUNT(CASE WHEN acknowledged = 0 THEN 1 END) as unacked,
    MAX(created_at) as latest
FROM alert_history
WHERE created_at > datetime('now', '-24 hours')
GROUP BY severity;
```

### 5.2 Flow JSON Schema

```json
{
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "AlertFlow",
    "type": "object",
    "required": ["id", "name", "nodes", "edges"],
    "properties": {
        "id": {"type": "string"},
        "name": {"type": "string", "minLength": 1, "maxLength": 100},
        "description": {"type": "string", "maxLength": 500},
        "enabled": {"type": "boolean", "default": true},
        "severity": {
            "type": "string",
            "enum": ["info", "warning", "critical", "emergency", "system"]
        },
        "cooldown_seconds": {"type": "integer", "minimum": 0, "default": 300},
        "nodes": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "type", "config", "position"],
                "properties": {
                    "id": {"type": "string"},
                    "type": {"type": "string", "enum": ["trigger", "condition", "action"]},
                    "trigger_type": {"type": "string"},
                    "condition_type": {"type": "string"},
                    "action_type": {"type": "string"},
                    "config": {"type": "object"},
                    "position": {
                        "type": "object",
                        "properties": {
                            "x": {"type": "number"},
                            "y": {"type": "number"}
                        }
                    }
                }
            },
            "minItems": 2
        },
        "edges": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["from", "to"],
                "properties": {
                    "from": {"type": "string"},
                    "to": {"type": "string"}
                }
            },
            "minItems": 1
        }
    }
}
```

### 5.3 Zone Geometry Format

Standard GeoJSON (`[lon, lat]` per RFC 7946):

**Polygon zone:**
```json
{
    "type": "Polygon",
    "coordinates": [[
        [-4.431, 55.870], [-4.430, 55.868],
        [-4.427, 55.869], [-4.428, 55.871],
        [-4.431, 55.870]
    ]]
}
```

**Circular zone (Point + radius):**
```json
{
    "type": "Point",
    "coordinates": [-4.431, 55.870],
    "properties": {"radius_km": 5.0}
}
```

*Note: The existing `zones` table uses `[lat, lon]` ordering. The new `alert_zones` table uses GeoJSON `[lon, lat]`. A helper handles conversion for compatibility.*

---

## 6. API Design

### 6.1 REST Endpoints

All alert endpoints prefixed with `/api/alerts/`. No authentication in Phase 1 (consistent with existing API).

#### Flows

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/alerts/flows` | List all flows (`?enabled=true` filter) |
| `POST` | `/api/alerts/flows` | Create a new flow |
| `GET` | `/api/alerts/flows/<id>` | Get specific flow |
| `PUT` | `/api/alerts/flows/<id>` | Update a flow |
| `DELETE` | `/api/alerts/flows/<id>` | Delete a flow |
| `POST` | `/api/alerts/flows/<id>/enable` | Enable a flow |
| `POST` | `/api/alerts/flows/<id>/disable` | Disable a flow |
| `POST` | `/api/alerts/flows/<id>/test` | Test fire (dry run with sample event) |
| `POST` | `/api/alerts/flows/from-template` | Create from template |

**Create from template:**
```http
POST /api/alerts/flows/from-template
Content-Type: application/json

{
    "template_id": "tpl_drone_near_airfield",
    "name": "Drone Near Glasgow Airport",
    "parameters": {
        "zone_id": "zone_gla",
        "radius_km": 5.0,
        "match_new_only": true,
        "actions": ["ui_alert", "telegram_push"]
    }
}
```

**Response:**
```json
{
    "id": "flow_abc123",
    "name": "Drone Near Glasgow Airport",
    "enabled": true,
    "severity": "critical",
    "template_id": "tpl_drone_near_airfield",
    "nodes": [...],
    "edges": [...]
}
```

#### Templates

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/alerts/templates` | List all templates |
| `GET` | `/api/alerts/templates/<id>` | Template details + parameter schema |
| `GET` | `/api/alerts/templates/<id>/preview` | Preview the template flow |

#### Zones

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/alerts/zones` | List all alert zones |
| `POST` | `/api/alerts/zones` | Create (GeoJSON body) |
| `GET` | `/api/alerts/zones/<id>` | Get zone details |
| `PUT` | `/api/alerts/zones/<id>` | Update zone |
| `DELETE` | `/api/alerts/zones/<id>` | Delete zone |
| `POST` | `/api/alerts/zones/import` | Import from GeoJSON FeatureCollection |
| `GET` | `/api/alerts/zones/export` | Export all as GeoJSON FeatureCollection |

#### Alert History

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/alerts/history` | Query history (with filters) |
| `GET` | `/api/alerts/history/<id>` | Specific alert details |
| `POST` | `/api/alerts/history/<id>/acknowledge` | Acknowledge alert |
| `POST` | `/api/alerts/history/acknowledge-all` | Acknowledge all |
| `DELETE` | `/api/alerts/history` | Clear history (`?before=` date) |
| `GET` | `/api/alerts/stats` | Aggregate stats |

**History query parameters:** `severity`, `object_type`, `object_id`, `flow_id`, `acknowledged`, `since`, `until`, `limit` (default 100, max 1000), `offset`.

#### Engine Status

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/alerts/engine/status` | Engine stats (flows, events/sec, memory) |
| `POST` | `/api/alerts/engine/reload` | Reload all flows from database |

### 6.2 SocketIO Events

#### Server → Client

| Event | Payload | Description |
|-------|---------|-------------|
| `alert_fired` | Full alert object (see below) | Real-time alert notification |
| `alert_acknowledged` | `{alert_id, acknowledged_by, timestamp}` | Alert acknowledged |
| `alert_cleared` | `{alert_ids: [...]}` | Alerts cleared |
| `alert_flow_changed` | `{flow_id, action: "created"\|"updated"\|"deleted"\|"enabled"\|"disabled"}` | Flow config changed |
| `alert_stats_update` | `{info: N, warning: N, critical: N, emergency: N, system: N, unacked: N}` | Periodic stats (30s) |

#### Client → Server

| Event | Payload | Description |
|-------|---------|-------------|
| `acknowledge_alert` | `{alert_id}` | Acknowledge specific alert |
| `acknowledge_all` | `{severity?: string}` | Acknowledge all (optional filter) |

### 6.3 Alert Payload Example

```json
{
    "id": "alert_789xyz",
    "flow_id": "flow_abc123",
    "flow_name": "Drone Near Glasgow Airport",
    "severity": "critical",
    "title": "Drone Near Glasgow Airport",
    "message": "Drone AA:BB:CC:DD:EE:FF detected 2.3km from Glasgow Airport",
    "event_type": "drone.detected",
    "object_id": "AA:BB:CC:DD:EE:FF",
    "object_type": "drone",
    "lat": 55.870,
    "lon": -4.431,
    "alt": 120.5,
    "timestamp": "2025-07-11T14:30:00Z",
    "sound": "alert-critical",
    "highlight_object": true,
    "fly_to": false,
    "actions_executed": ["ui_alert", "telegram_push"],
    "acknowledged": false
}
```

---

## 7. Frontend — Flow Editor UI

### 7.1 Library Choice: Drawflow

**Decision: [Drawflow](https://github.com/jerosoler/Drawflow)**

Rationale:
- **Lightweight** — 10KB minified. No framework dependency. Vanilla JS.
- **Simple API** — addNode, addConnection, export/import JSON. Perfect for our simplified flow model.
- **Visual match** — Dark theme ready, minimal chrome. Fits the existing aerospace design system.
- **Active maintenance** — Well-documented, MIT licensed, 4k+ GitHub stars.
- **Not overkill** — LiteGraph.js is too complex (3D shader graphs). React Flow requires React. Flowy is abandoned. Drawflow is exactly the right size.

Alternatives considered:
| Library | Verdict |
|---------|---------|
| **React Flow** | Requires React — mesh-mapper is vanilla JS. Too heavy. |
| **LiteGraph.js** | Built for shader/audio graphs. Overkill, wrong domain. |
| **Flowy** | Unmaintained since 2020. No export format. |
| **Rete.js** | Plugin-heavy, complex setup. |
| **Custom SVG** | Maximum control but weeks of work for drag, zoom, pan, connections. |

### 7.2 Flow Editor Page Layout

```
┌──────────────────────────────────────────────────────────────────────┐
│  MESH MAPPER — ALERT FLOW EDITOR              [Save] [Test] [Back]  │
├──────────────┬───────────────────────────────────────────────────────┤
│              │                                                       │
│  NODE        │               FLOW CANVAS                             │
│  PALETTE     │                                                       │
│              │   ┌─────────┐      ┌─────────┐      ┌─────────┐     │
│  ─ TRIGGERS  │   │ Drone   │─────▶│ Geofence│─────▶│ UI Alert│     │
│  🟢 Drone    │   │ Detected│      │ Check   │      │ Critical│     │
│  ✈ Aircraft  │   └─────────┘      └─────────┘      └─────────┘     │
│  ⛴ Vessel    │                                           │          │
│  ⚡ Lightning │                                    ┌──────▼────┐     │
│  🌤 Weather   │                                    │ Telegram  │     │
│  📡 APRS     │                                    │ Push      │     │
│  ⚙ System    │                                    └───────────┘     │
│  🕐 Schedule  │                                                      │
│              │                                                       │
│  ─ CONDITIONS│                                                       │
│  📍 Geofence │                                                       │
│  🕐 Time     │                                                       │
│  ⏱ Rate Limit│                                                       │
│  📊 Threshold │                                                       │
│  🔍 Object   │                                                       │
│  🔀 Logic    │                                                       │
│  📌 State    │                                                       │
│  ⏳ Duration │                                                       │
│              │                                                       │
│  ─ ACTIONS   │                                                       │
│  🔔 UI Alert │                                                       │
│  📱 Telegram │                                                       │
│  🔊 Horn     │                                                       │
│  💾 DB Log   │                                                       │
│  📡 MQTT     │                                                       │
│  🌐 Webhook  │                                                       │
│              │                                                       │
├