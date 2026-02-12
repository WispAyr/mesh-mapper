# MMIP — Mesh Mapper Interchange Protocol

**Version:** 1.0  
**Status:** Draft  
**Date:** 2026-02-12

## Overview

MMIP is a lightweight JSON-over-MQTT protocol for exchanging situational awareness data between mesh-mapper instances and other compatible systems (e.g. World Monitor).

**Design principles:**
- **Loose coupling** — pub/sub only, no direct API calls
- **Fail-independent** — each node operates fully if peers go offline
- **Self-describing** — every message carries source identity and location
- **Extensible** — payload is freeform JSON per event_type

## Transport

- **Broker:** Any MQTT 3.1.1+ broker (Mosquitto recommended)
- **QoS:** 0 for high-frequency data (heartbeats, positions), 1 for alerts
- **Retain:** Status messages use retain=true; events do not

## Topic Structure

```
mmip/{source_id}/{event_type}
```

| Topic Pattern | Description |
|---|---|
| `mmip/{id}/heartbeat` | Periodic alive signal with summary stats |
| `mmip/{id}/detection` | New drone, aircraft, or RF detection |
| `mmip/{id}/alert` | Actionable alert (zone breach, tracker, etc.) |
| `mmip/{id}/status` | Full system status (retained) |
| `mmip/{id}/position` | Station GPS position update |
| `mmip/{id}/ble` | BLE device summary update |

### Wildcard Subscriptions

| Pattern | Use Case |
|---|---|
| `mmip/+/heartbeat` | Monitor all stations' liveness |
| `mmip/+/alert` | Receive alerts from all field units |
| `mmip/+/detection` | Aggregate all detections (World Monitor) |
| `mmip/drone-pi-kyle-rise/#` | All events from one specific station |

## Envelope Format

Every MMIP message is wrapped in a standard envelope:

```json
{
    "protocol": "mmip/1.0",
    "source": {
        "id": "drone-pi-kyle-rise",
        "type": "mesh-mapper",
        "name": "Kyle Rise",
        "location": {
            "lat": 55.4942,
            "lon": -4.5997,
            "alt": 50,
            "fix_source": "gps"
        },
        "timestamp": 1707744000.123
    },
    "event_type": "detection",
    "payload": { ... }
}
```

### Source Fields

| Field | Type | Description |
|---|---|---|
| `id` | string | Unique machine identifier (slug format) |
| `type` | string | `mesh-mapper`, `world-monitor`, `sensor`, `relay` |
| `name` | string | Human-readable station name |
| `location` | object | Station lat/lon/alt/fix_source |
| `timestamp` | float | Unix epoch of message creation |

## Event Types

### `heartbeat`

Sent every 30 seconds. Minimal payload for liveness monitoring.

```json
{
    "protocol": "mmip/1.0",
    "source": { ... },
    "event_type": "heartbeat",
    "payload": {
        "uptime": 86400,
        "gps_fix": true,
        "gps_source": "gps",
        "ble_devices": 24,
        "ble_packets": 45000,
        "active_drones": 0,
        "serial_ports": ["ttyACM0", "ttyACM1", "ttyUSB0", "ttyACM2"]
    }
}
```

### `detection`

Emitted when a new target is detected or state changes significantly.

```json
{
    "event_type": "detection",
    "payload": {
        "detection_type": "drone_wifi|drone_ble_remoteid|drone_rf|ble_tracker|ble_device",
        "mac": "AA:BB:CC:DD:EE:FF",
        "drone_lat": 55.5,
        "drone_long": -4.6,
        "drone_altitude": 100,
        "pilot_lat": 55.49,
        "pilot_long": -4.59,
        "basic_id": "RID-1234-ABCD",
        "rssi": -65,
        "source_sensor": "ble_remoteid",
        "category": "drone",
        "subcategory": "remote_id",
        "confidence": 0.95
    }
}
```

### `alert`

High-priority events requiring attention.

```json
{
    "event_type": "alert",
    "payload": {
        "alert_type": "zone_breach|new_drone|tracker_detected|gps_lost|sensor_offline",
        "severity": "info|warning|critical",
        "title": "Drone entered restricted zone",
        "description": "MAC AA:BB:CC entered Zone Alpha at 15:30",
        "detection_mac": "AA:BB:CC:DD:EE:FF",
        "zone_id": "zone-alpha",
        "expires": 1707745000
    }
}
```

### `status`

Full system status, published with MQTT retain flag.

```json
{
    "event_type": "status",
    "payload": {
        "version": "1.0.0",
        "uptime": 86400,
        "gps": { "fix": true, "lat": 55.49, "lon": -4.60, "satellites": 8 },
        "ble": { "enabled": true, "devices": 24, "packets_total": 45000 },
        "serial_ports": { "ttyACM0": "connected", "ttyUSB0": "connected" },
        "layers": { "drones": 0, "aircraft": 12, "vessels": 3, "ble": 24 },
        "mqtt": { "connected": true, "publish_count": 1234 }
    }
}
```

### `position`

Station GPS position update (same as `mesh-mapper/gps/position`, bridged to MMIP).

```json
{
    "event_type": "position",
    "payload": {
        "lat": 55.4942,
        "lon": -4.5997,
        "alt": 50.3,
        "speed_kmh": 0,
        "heading": 0,
        "fix_source": "gps",
        "satellites": 8,
        "hdop": 1.2
    }
}
```

## World Monitor Integration

### Architecture

```
┌──────────────┐          MQTT Broker          ┌──────────────┐
│  Mesh-Mapper │  ───publish──►  mmip/...  ◄──subscribe── │World Monitor │
│  (Field Pi)  │  ◄─subscribe──  mmip/...  ───publish───► │   (NOC)      │
└──────────────┘                                └──────────────┘
```

### Mesh-Mapper → World Monitor

Mesh-mapper publishes:
- `mmip/{id}/detection` — drone & RF detections
- `mmip/{id}/heartbeat` — periodic liveness
- `mmip/{id}/status` — full system snapshot (retained)
- `mmip/{id}/alert` — zone breaches, tracker alerts

World Monitor subscribes to `mmip/+/detection` and `mmip/+/alert` to:
- Show field sensor positions on its global map
- Aggregate drone detections from multiple sites
- Display alerts in its notification feed

### World Monitor → Mesh-Mapper

World Monitor publishes:
- `mmip/world-monitor/alert` — flash alerts (earthquakes, weather, service outages)
- `mmip/world-monitor/status` — NOC system health

Mesh-mapper subscribes to `mmip/+/alert` and `mmip/world-monitor/status` to:
- Display relevant global alerts in its alert log
- Show NOC connectivity status

### Failure Modes

| Scenario | Mesh-Mapper | World Monitor |
|---|---|---|
| MQTT broker down | Continues detecting, queues messages | Continues monitoring, marks sensors offline |
| Field Pi offline | N/A | Detects missing heartbeat after 90s, marks sensor offline |
| World Monitor offline | Continues publishing, no effect | N/A |
| Internet down | Local MQTT still works if on same LAN | Loses remote sensors, local feeds continue |

## Configuration

```json
{
    "mmip": {
        "enabled": true,
        "source_id": "drone-pi-kyle-rise",
        "source_type": "mesh-mapper",
        "publish": true,
        "subscribe": true,
        "subscribe_filters": [
            "mmip/+/alerts",
            "mmip/+/status"
        ]
    }
}
```

## Source ID Convention

Format: `{function}-{platform}-{location}`

Examples:
- `drone-pi-kyle-rise` — Drone detection Pi at Kyle Rise
- `drone-pi-greenford` — Drone detection Pi at Greenford
- `world-monitor-noc` — World Monitor at the NOC
- `sensor-van-mobile` — Mobile van sensor
