"""
Alert Templates — Pre-built flow templates for the mesh-mapper alert system.

Each template is a complete flow definition that users can activate and customise.
Templates define nodes (trigger → conditions → actions) and edges connecting them.
"""

TEMPLATES = {

    # ================================================================
    # 1. Drone Detected — Any drone detection → UI alert + log
    # ================================================================
    "tpl_drone_detected": {
        "id": "tpl_drone_detected",
        "name": "Drone Detected",
        "description": "Alert when any drone is detected by the sensors. Basic notification for all drone activity.",
        "category": "drone",
        "severity": "warning",
        "icon": "🛸",
        "sort_order": 1,
        "parameters": {
            "match_new_only": {
                "type": "boolean",
                "default": False,
                "label": "New drones only",
                "description": "Only alert on first detection of a MAC address"
            },
            "cooldown_seconds": {
                "type": "integer",
                "default": 300,
                "label": "Cooldown (seconds)",
                "description": "Minimum time between alerts for the same drone"
            }
        },
        "flow": {
            "cooldown_seconds": 300,
            "nodes": [
                {
                    "id": "t1",
                    "type": "trigger",
                    "trigger_type": "drone.detected",
                    "config": {
                        "event": "detected",
                        "match_new_only": False
                    },
                    "position": {"x": 100, "y": 200}
                },
                {
                    "id": "a1",
                    "type": "action",
                    "action_type": "ui_alert",
                    "config": {
                        "severity": "warning",
                        "title": "🛸 Drone Detected",
                        "message": "Drone {{object_id}} detected. RSSI: {{rssi}}",
                        "sound": "alert-warning",
                        "highlight_object": True,
                        "fly_to": False
                    },
                    "position": {"x": 400, "y": 150}
                },
                {
                    "id": "a2",
                    "type": "action",
                    "action_type": "db_log",
                    "config": {
                        "include_full_event": True,
                        "retention_days": 90
                    },
                    "position": {"x": 400, "y": 300}
                }
            ],
            "edges": [
                {"from": "t1", "to": "a1"},
                {"from": "t1", "to": "a2"}
            ]
        }
    },

    # ================================================================
    # 2. Drone in Zone — Drone + geofence → UI + Telegram + log
    # ================================================================
    "tpl_drone_in_zone": {
        "id": "tpl_drone_in_zone",
        "name": "Drone in Zone",
        "description": "Alert when a drone enters a defined airspace zone. Sends UI alert and Telegram notification.",
        "category": "drone",
        "severity": "critical",
        "icon": "🚨",
        "sort_order": 2,
        "parameters": {
            "zone_id": {
                "type": "string",
                "default": None,
                "label": "Zone",
                "description": "Select the zone to monitor"
            },
            "cooldown_seconds": {
                "type": "integer",
                "default": 300,
                "label": "Cooldown (seconds)",
                "description": "Minimum time between alerts for the same drone in this zone"
            }
        },
        "flow": {
            "cooldown_seconds": 300,
            "nodes": [
                {
                    "id": "t1",
                    "type": "trigger",
                    "trigger_type": "drone.zone_entry",
                    "config": {
                        "event": "zone_entry"
                    },
                    "position": {"x": 100, "y": 200}
                },
                {
                    "id": "a1",
                    "type": "action",
                    "action_type": "ui_alert",
                    "config": {
                        "severity": "critical",
                        "title": "🚨 Drone Zone Intrusion",
                        "message": "Drone {{object_id}} entered zone {{zone_name}}",
                        "sound": "alert-critical",
                        "highlight_object": True,
                        "fly_to": True
                    },
                    "position": {"x": 400, "y": 100}
                },
                {
                    "id": "a2",
                    "type": "action",
                    "action_type": "telegram_push",
                    "config": {
                        "message": "🚨 *DRONE ZONE ALERT*\n\nDrone `{{object_id}}` entered *{{zone_name}}*\nTime: {{timestamp}}",
                        "include_map_link": True,
                        "include_details": True
                    },
                    "position": {"x": 400, "y": 250}
                },
                {
                    "id": "a3",
                    "type": "action",
                    "action_type": "db_log",
                    "config": {
                        "include_full_event": True,
                        "retention_days": 365
                    },
                    "position": {"x": 400, "y": 400}
                }
            ],
            "edges": [
                {"from": "t1", "to": "a1"},
                {"from": "t1", "to": "a2"},
                {"from": "t1", "to": "a3"}
            ]
        }
    },

    # ================================================================
    # 3. Aircraft Low Altitude — Below threshold in zone → alert
    # ================================================================
    "tpl_aircraft_low_altitude": {
        "id": "tpl_aircraft_low_altitude",
        "name": "Aircraft Low Altitude",
        "description": "Alert when an aircraft is below a defined altitude threshold. Useful for monitoring low-flying aircraft near sensitive areas.",
        "category": "aircraft",
        "severity": "warning",
        "icon": "✈️",
        "sort_order": 3,
        "parameters": {
            "altitude_threshold_ft": {
                "type": "number",
                "default": 1000,
                "label": "Altitude threshold (ft)",
                "description": "Alert when aircraft is below this altitude"
            },
            "zone_id": {
                "type": "string",
                "default": None,
                "label": "Zone (optional)",
                "description": "Limit to aircraft within this zone"
            },
            "cooldown_seconds": {
                "type": "integer",
                "default": 300,
                "label": "Cooldown (seconds)",
                "description": "Minimum time between alerts for the same aircraft"
            }
        },
        "flow": {
            "cooldown_seconds": 300,
            "nodes": [
                {
                    "id": "t1",
                    "type": "trigger",
                    "trigger_type": "aircraft.updated",
                    "config": {
                        "event": "updated"
                    },
                    "position": {"x": 100, "y": 200}
                },
                {
                    "id": "c1",
                    "type": "condition",
                    "condition_type": "threshold",
                    "config": {
                        "field": "data.altitude_ft",
                        "operator": "lt",
                        "value": 1000,
                        "unit": "ft"
                    },
                    "position": {"x": 300, "y": 200}
                },
                {
                    "id": "a1",
                    "type": "action",
                    "action_type": "ui_alert",
                    "config": {
                        "severity": "warning",
                        "title": "✈️ Low-Flying Aircraft",
                        "message": "Aircraft {{callsign}} ({{object_id}}) at {{alt}}ft",
                        "sound": "alert-warning",
                        "highlight_object": True,
                        "fly_to": False
                    },
                    "position": {"x": 500, "y": 150}
                },
                {
                    "id": "a2",
                    "type": "action",
                    "action_type": "db_log",
                    "config": {
                        "include_full_event": True,
                        "retention_days": 90
                    },
                    "position": {"x": 500, "y": 300}
                }
            ],
            "edges": [
                {"from": "t1", "to": "c1"},
                {"from": "c1", "to": "a1"},
                {"from": "c1", "to": "a2"}
            ]
        }
    },

    # ================================================================
    # 4. Vessel Loitering — Speed < threshold for duration → alert
    # ================================================================
    "tpl_vessel_loitering": {
        "id": "tpl_vessel_loitering",
        "name": "Vessel Loitering",
        "description": "Alert when a vessel's speed drops below a threshold for an extended period, indicating possible loitering.",
        "category": "vessel",
        "severity": "info",
        "icon": "⛴️",
        "sort_order": 4,
        "parameters": {
            "speed_threshold_kts": {
                "type": "number",
                "default": 1.0,
                "label": "Speed threshold (kts)",
                "description": "Alert when vessel speed is below this"
            },
            "duration_minutes": {
                "type": "integer",
                "default": 30,
                "label": "Duration (minutes)",
                "description": "Vessel must be below speed for this long"
            },
            "zone_id": {
                "type": "string",
                "default": None,
                "label": "Zone (optional)",
                "description": "Limit to vessels within this zone"
            },
            "cooldown_seconds": {
                "type": "integer",
                "default": 1800,
                "label": "Cooldown (seconds)",
                "description": "Minimum time between alerts for the same vessel"
            }
        },
        "flow": {
            "cooldown_seconds": 1800,
            "nodes": [
                {
                    "id": "t1",
                    "type": "trigger",
                    "trigger_type": "vessel.updated",
                    "config": {
                        "event": "updated"
                    },
                    "position": {"x": 100, "y": 200}
                },
                {
                    "id": "c1",
                    "type": "condition",
                    "condition_type": "threshold",
                    "config": {
                        "field": "data.speed",
                        "operator": "lt",
                        "value": 1.0,
                        "unit": "kts"
                    },
                    "position": {"x": 300, "y": 200}
                },
                {
                    "id": "c2",
                    "type": "condition",
                    "condition_type": "duration",
                    "config": {
                        "min_duration_seconds": 1800,
                        "check": "below_speed",
                        "speed_threshold": 1.0
                    },
                    "position": {"x": 500, "y": 200}
                },
                {
                    "id": "a1",
                    "type": "action",
                    "action_type": "ui_alert",
                    "config": {
                        "severity": "info",
                        "title": "⛴️ Vessel Loitering",
                        "message": "Vessel {{callsign}} ({{object_id}}) loitering at {{speed}} kts",
                        "sound": "alert-info",
                        "highlight_object": True,
                        "fly_to": False
                    },
                    "position": {"x": 700, "y": 150}
                },
                {
                    "id": "a2",
                    "type": "action",
                    "action_type": "db_log",
                    "config": {
                        "include_full_event": True,
                        "retention_days": 90
                    },
                    "position": {"x": 700, "y": 300}
                }
            ],
            "edges": [
                {"from": "t1", "to": "c1"},
                {"from": "c1", "to": "c2"},
                {"from": "c2", "to": "a1"},
                {"from": "c2", "to": "a2"}
            ]
        }
    },

    # ================================================================
    # 5. Lightning Proximity — Lightning within radius → alert
    # ================================================================
    "tpl_lightning_proximity": {
        "id": "tpl_lightning_proximity",
        "name": "Lightning Proximity",
        "description": "Alert when lightning strikes within a configurable distance. Escalates severity as strikes get closer.",
        "category": "weather",
        "severity": "warning",
        "icon": "⚡",
        "sort_order": 5,
        "parameters": {
            "distance_km": {
                "type": "number",
                "default": 25,
                "label": "Alert radius (km)",
                "description": "Alert when lightning is within this distance"
            },
            "reference_lat": {
                "type": "number",
                "default": None,
                "label": "Centre latitude",
                "description": "Reference point latitude (null = system centre)"
            },
            "reference_lon": {
                "type": "number",
                "default": None,
                "label": "Centre longitude",
                "description": "Reference point longitude (null = system centre)"
            },
            "cooldown_seconds": {
                "type": "integer",
                "default": 600,
                "label": "Cooldown (seconds)",
                "description": "Minimum time between lightning alerts"
            }
        },
        "flow": {
            "cooldown_seconds": 600,
            "nodes": [
                {
                    "id": "t1",
                    "type": "trigger",
                    "trigger_type": "lightning.strike",
                    "config": {
                        "event": "strike",
                        "max_distance_km": 25
                    },
                    "position": {"x": 100, "y": 200}
                },
                {
                    "id": "c1",
                    "type": "condition",
                    "condition_type": "rate_limit",
                    "config": {
                        "max_fires": 1,
                        "window_minutes": 10,
                        "per_object": False
                    },
                    "position": {"x": 300, "y": 200}
                },
                {
                    "id": "a1",
                    "type": "action",
                    "action_type": "ui_alert",
                    "config": {
                        "severity": "warning",
                        "title": "⚡ Lightning Nearby",
                        "message": "Lightning strike {{distance_km}}km away",
                        "sound": "alert-warning",
                        "highlight_object": False,
                        "fly_to": False,
                        "auto_dismiss_seconds": 30
                    },
                    "position": {"x": 500, "y": 150}
                },
                {
                    "id": "a2",
                    "type": "action",
                    "action_type": "db_log",
                    "config": {
                        "include_full_event": True,
                        "retention_days": 30
                    },
                    "position": {"x": 500, "y": 300}
                }
            ],
            "edges": [
                {"from": "t1", "to": "c1"},
                {"from": "c1", "to": "a1"},
                {"from": "c1", "to": "a2"}
            ]
        }
    },

    # ================================================================
    # 6. Emergency Squawk — Aircraft emergency squawk → alert
    # ================================================================
    "tpl_emergency_squawk": {
        "id": "tpl_emergency_squawk",
        "name": "Emergency Squawk",
        "description": "Alert when an aircraft transmits an emergency squawk code (7500 hijack, 7600 comms failure, 7700 emergency).",
        "category": "aircraft",
        "severity": "emergency",
        "icon": "🆘",
        "sort_order": 6,
        "parameters": {
            "cooldown_seconds": {
                "type": "integer",
                "default": 60,
                "label": "Cooldown (seconds)",
                "description": "Minimum time between alerts for the same aircraft"
            }
        },
        "flow": {
            "cooldown_seconds": 60,
            "nodes": [
                {
                    "id": "t1",
                    "type": "trigger",
                    "trigger_type": "aircraft.updated",
                    "config": {
                        "event": "updated",
                        "emergency_only": True
                    },
                    "position": {"x": 100, "y": 200}
                },
                {
                    "id": "a1",
                    "type": "action",
                    "action_type": "ui_alert",
                    "config": {
                        "severity": "emergency",
                        "title": "🆘 EMERGENCY SQUAWK",
                        "message": "Aircraft {{callsign}} ({{object_id}}) squawking {{squawk}}",
                        "sound": "alert-emergency",
                        "highlight_object": True,
                        "fly_to": True
                    },
                    "position": {"x": 400, "y": 100}
                },
                {
                    "id": "a2",
                    "type": "action",
                    "action_type": "telegram_push",
                    "config": {
                        "message": "🆘 *EMERGENCY SQUAWK*\n\nAircraft `{{callsign}}` ({{object_id}})\nSquawk: {{squawk}}\nAlt: {{alt}}ft\nTime: {{timestamp}}",
                        "include_map_link": True,
                        "include_details": True
                    },
                    "position": {"x": 400, "y": 250}
                },
                {
                    "id": "a3",
                    "type": "action",
                    "action_type": "db_log",
                    "config": {
                        "include_full_event": True,
                        "retention_days": 365
                    },
                    "position": {"x": 400, "y": 400}
                }
            ],
            "edges": [
                {"from": "t1", "to": "a1"},
                {"from": "t1", "to": "a2"},
                {"from": "t1", "to": "a3"}
            ]
        }
    },

    # ================================================================
    # 7. System Health — Feed stale or disconnect → alert
    # ================================================================
    "tpl_system_health": {
        "id": "tpl_system_health",
        "name": "System Health Monitor",
        "description": "Alert when a data feed becomes stale or a system component disconnects.",
        "category": "system",
        "severity": "warning",
        "icon": "⚙️",
        "sort_order": 7,
        "parameters": {
            "cooldown_seconds": {
                "type": "integer",
                "default": 900,
                "label": "Cooldown (seconds)",
                "description": "Minimum time between system alerts"
            }
        },
        "flow": {
            "cooldown_seconds": 900,
            "nodes": [
                {
                    "id": "t1",
                    "type": "trigger",
                    "trigger_type": "system.feed_stale",
                    "config": {
                        "event": "feed_stale"
                    },
                    "position": {"x": 100, "y": 200}
                },
                {
                    "id": "c1",
                    "type": "condition",
                    "condition_type": "rate_limit",
                    "config": {
                        "max_fires": 1,
                        "window_minutes": 15,
                        "per_object": True
                    },
                    "position": {"x": 300, "y": 200}
                },
                {
                    "id": "a1",
                    "type": "action",
                    "action_type": "ui_alert",
                    "config": {
                        "severity": "warning",
                        "title": "⚙️ System Alert",
                        "message": "Feed {{object_id}} is stale or disconnected",
                        "sound": "alert-info",
                        "highlight_object": False,
                        "fly_to": False,
                        "auto_dismiss_seconds": 60
                    },
                    "position": {"x": 500, "y": 200}
                }
            ],
            "edges": [
                {"from": "t1", "to": "c1"},
                {"from": "c1", "to": "a1"}
            ]
        }
    },
}


def get_all_templates() -> list:
    """Return all templates as a sorted list."""
    return sorted(
        TEMPLATES.values(),
        key=lambda t: t.get("sort_order", 99)
    )


def get_template(template_id: str) -> dict | None:
    """Get a specific template by ID."""
    return TEMPLATES.get(template_id)


def create_flow_from_template(template_id: str, name: str = None,
                              parameters: dict = None) -> dict | None:
    """Create a flow definition from a template with custom parameters.
    
    Args:
        template_id: Template ID to instantiate
        name: Custom name for the flow (uses template name if not provided)
        parameters: Dict of parameter overrides
        
    Returns:
        A flow dict ready to be saved via FlowStorage.create_flow()
    """
    template = get_template(template_id)
    if not template:
        return None

    parameters = parameters or {}
    flow_def = template["flow"].copy()
    nodes = [n.copy() for n in flow_def.get("nodes", [])]
    edges = flow_def.get("edges", [])

    # Apply parameter overrides
    for node in nodes:
        config = node.get("config", {})

        # Trigger config overrides
        if node["type"] == "trigger":
            if "match_new_only" in parameters:
                config["match_new_only"] = parameters["match_new_only"]
            if "zone_id" in parameters and "zone_id" in config:
                config["zone_id"] = parameters["zone_id"]
            if "max_distance_km" in parameters:
                config["max_distance_km"] = parameters["max_distance_km"]
            if "reference_lat" in parameters and "reference_lon" in parameters:
                config["reference_point"] = {
                    "lat": parameters["reference_lat"],
                    "lon": parameters["reference_lon"],
                }

        # Condition config overrides
        elif node["type"] == "condition":
            ctype = node.get("condition_type", "")
            if ctype == "geofence" and "zone_id" in parameters:
                config["zone_id"] = parameters["zone_id"]
            if ctype == "threshold":
                if "altitude_threshold_ft" in parameters and config.get("field") == "data.altitude_ft":
                    config["value"] = parameters["altitude_threshold_ft"]
                if "speed_threshold_kts" in parameters and config.get("unit") == "kts":
                    config["value"] = parameters["speed_threshold_kts"]
            if ctype == "duration":
                if "duration_minutes" in parameters:
                    config["min_duration_seconds"] = parameters["duration_minutes"] * 60
                if "speed_threshold_kts" in parameters:
                    config["speed_threshold"] = parameters["speed_threshold_kts"]
            if ctype == "rate_limit":
                if "rate_limit_fires" in parameters:
                    config["max_fires"] = parameters["rate_limit_fires"]
                if "rate_limit_window" in parameters:
                    config["window_minutes"] = parameters["rate_limit_window"]

        node["config"] = config

    # Build the flow
    cooldown = parameters.get("cooldown_seconds", flow_def.get("cooldown_seconds", 300))

    return {
        "name": name or template["name"],
        "description": template.get("description", ""),
        "enabled": True,
        "severity": template.get("severity", "warning"),
        "template_id": template_id,
        "cooldown_seconds": cooldown,
        "nodes": nodes,
        "edges": edges,
    }
