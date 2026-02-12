/**
 * Flow Node Definitions — All node types for the alert flow editor.
 * 
 * Each node type defines:
 *   - display name, icon, category, color
 *   - input/output ports
 *   - configurable properties with defaults
 *   - HTML template for canvas rendering
 */

(function() {
    'use strict';

    // ============================================================
    // Category Definitions
    // ============================================================
    const CATEGORIES = {
        trigger:   { label: 'TRIGGERS',   color: '#1e88e5', accent: '#42a5f5' },
        condition: { label: 'CONDITIONS', color: '#ff8f00', accent: '#ffb74d' },
        action:    { label: 'ACTIONS',    color: '#43a047', accent: '#66bb6a' }
    };

    // ============================================================
    // Template Variable Hints
    // ============================================================
    const TEMPLATE_VARS = [
        '{{object_id}}', '{{object_type}}', '{{lat}}', '{{lon}}', '{{alt}}',
        '{{timestamp}}', '{{zone_name}}', '{{distance_km}}', '{{speed}}',
        '{{heading}}', '{{severity}}', '{{flow_name}}', '{{alias}}',
        '{{callsign}}', '{{squawk}}', '{{rssi}}', '{{event_type}}'
    ];

    // ============================================================
    // Node Type Definitions
    // ============================================================
    const NODE_TYPES = {

        // ── TRIGGERS ─────────────────────────────────────────────

        'trigger.drone': {
            name: 'Drone',
            icon: '🛸',
            category: 'trigger',
            description: 'Triggers on drone detection events',
            inputs: 0,
            outputs: 1,
            properties: {
                event_filter: {
                    type: 'select',
                    label: 'Event',
                    options: [
                        { value: 'detected', label: 'Detected' },
                        { value: 'lost', label: 'Lost' },
                        { value: 'reactivated', label: 'Reactivated' },
                        { value: 'zone_enter', label: 'Zone Enter' },
                        { value: 'zone_exit', label: 'Zone Exit' },
                        { value: 'updated', label: 'Updated' }
                    ],
                    default: 'detected'
                },
                match_new_only: {
                    type: 'checkbox',
                    label: 'New drones only',
                    default: false
                }
            }
        },

        'trigger.aircraft': {
            name: 'Aircraft',
            icon: '✈️',
            category: 'trigger',
            description: 'Triggers on ADS-B aircraft events',
            inputs: 0,
            outputs: 1,
            properties: {
                event_filter: {
                    type: 'select',
                    label: 'Event',
                    options: [
                        { value: 'updated', label: 'Updated' },
                        { value: 'new', label: 'New Aircraft' },
                        { value: 'lost', label: 'Lost' },
                        { value: 'squawk_change', label: 'Squawk Change' }
                    ],
                    default: 'updated'
                },
                squawk_filter: {
                    type: 'text',
                    label: 'Squawk Code',
                    placeholder: 'e.g. 7700, 7600',
                    default: ''
                },
                altitude_filter: {
                    type: 'text',
                    label: 'Altitude Filter',
                    placeholder: 'e.g. <5000 (ft)',
                    default: ''
                },
                callsign_filter: {
                    type: 'text',
                    label: 'Callsign Filter',
                    placeholder: 'e.g. BAW*, RYR*',
                    default: ''
                }
            }
        },

        'trigger.vessel': {
            name: 'Vessel',
            icon: '⛴️',
            category: 'trigger',
            description: 'Triggers on AIS vessel events',
            inputs: 0,
            outputs: 1,
            properties: {
                event_filter: {
                    type: 'select',
                    label: 'Event',
                    options: [
                        { value: 'updated', label: 'Updated' },
                        { value: 'new', label: 'New Vessel' },
                        { value: 'lost', label: 'Lost' },
                        { value: 'speed_change', label: 'Speed Change' }
                    ],
                    default: 'updated'
                },
                mmsi_filter: {
                    type: 'text',
                    label: 'MMSI Filter',
                    placeholder: 'e.g. 235*, 211000000',
                    default: ''
                },
                speed_filter: {
                    type: 'text',
                    label: 'Speed Filter',
                    placeholder: 'e.g. <1, >20 (kts)',
                    default: ''
                },
                type_filter: {
                    type: 'text',
                    label: 'Vessel Type',
                    placeholder: 'e.g. Cargo, Tanker',
                    default: ''
                }
            }
        },

        'trigger.lightning': {
            name: 'Lightning',
            icon: '⚡',
            category: 'trigger',
            description: 'Triggers on lightning strike events',
            inputs: 0,
            outputs: 1,
            properties: {}
        },

        'trigger.weather': {
            name: 'Weather',
            icon: '🌤️',
            category: 'trigger',
            description: 'Triggers on Met Office weather warnings',
            inputs: 0,
            outputs: 1,
            properties: {
                min_severity: {
                    type: 'select',
                    label: 'Min Severity',
                    options: [
                        { value: 'yellow', label: 'Yellow' },
                        { value: 'amber', label: 'Amber' },
                        { value: 'red', label: 'Red' }
                    ],
                    default: 'yellow'
                },
                warning_type: {
                    type: 'text',
                    label: 'Warning Type',
                    placeholder: 'e.g. wind, rain, thunder',
                    default: ''
                }
            }
        },

        'trigger.aprs': {
            name: 'APRS',
            icon: '📡',
            category: 'trigger',
            description: 'Triggers on APRS station events',
            inputs: 0,
            outputs: 1,
            properties: {
                callsign_filter: {
                    type: 'text',
                    label: 'Callsign Filter',
                    placeholder: 'e.g. MM0*, GB7*',
                    default: ''
                }
            }
        },

        'trigger.system': {
            name: 'System',
            icon: '⚙️',
            category: 'trigger',
            description: 'Triggers on system health events',
            inputs: 0,
            outputs: 1,
            properties: {
                event_filter: {
                    type: 'select',
                    label: 'Event',
                    options: [
                        { value: 'feed_stale', label: 'Feed Stale' },
                        { value: 'serial_disconnect', label: 'Serial Disconnect' },
                        { value: 'mqtt_loss', label: 'MQTT Loss' },
                        { value: 'high_cpu', label: 'High CPU' },
                        { value: 'low_disk', label: 'Low Disk' }
                    ],
                    default: 'feed_stale'
                }
            }
        },

        'trigger.schedule': {
            name: 'Schedule',
            icon: '🕐',
            category: 'trigger',
            description: 'Triggers on a cron schedule',
            inputs: 0,
            outputs: 1,
            properties: {
                cron_expression: {
                    type: 'text',
                    label: 'Cron Expression',
                    placeholder: '0 */6 * * *',
                    default: '0 * * * *'
                },
                timezone: {
                    type: 'text',
                    label: 'Timezone',
                    placeholder: 'Europe/London',
                    default: 'Europe/London'
                }
            }
        },

        'trigger.mqtt': {
            name: 'MQTT External',
            icon: '📨',
            category: 'trigger',
            description: 'Triggers on external MQTT messages',
            inputs: 0,
            outputs: 1,
            properties: {
                topic_pattern: {
                    type: 'text',
                    label: 'Topic Pattern',
                    placeholder: 'sensors/+/data',
                    default: ''
                }
            }
        },

        // ── CONDITIONS ───────────────────────────────────────────

        'condition.geofence': {
            name: 'Geofence',
            icon: '📍',
            category: 'condition',
            description: 'Check if object is inside/outside a zone',
            inputs: 1,
            outputs: 1,
            properties: {
                zone_id: {
                    type: 'zone_select',
                    label: 'Zone',
                    default: ''
                },
                radius_km: {
                    type: 'number',
                    label: 'Radius (km)',
                    placeholder: 'For point zones',
                    default: 5,
                    min: 0.1,
                    max: 500,
                    step: 0.1
                },
                inside: {
                    type: 'checkbox',
                    label: 'Object must be inside zone',
                    default: true
                }
            }
        },

        'condition.time_filter': {
            name: 'Time Filter',
            icon: '🕐',
            category: 'condition',
            description: 'Filter by time of day and day of week',
            inputs: 1,
            outputs: 1,
            properties: {
                days_of_week: {
                    type: 'multiselect',
                    label: 'Days',
                    options: [
                        { value: '0', label: 'Mon' },
                        { value: '1', label: 'Tue' },
                        { value: '2', label: 'Wed' },
                        { value: '3', label: 'Thu' },
                        { value: '4', label: 'Fri' },
                        { value: '5', label: 'Sat' },
                        { value: '6', label: 'Sun' }
                    ],
                    default: ['0','1','2','3','4','5','6']
                },
                start_time: {
                    type: 'text',
                    label: 'Start Time',
                    placeholder: '00:00',
                    default: '00:00'
                },
                end_time: {
                    type: 'text',
                    label: 'End Time',
                    placeholder: '23:59',
                    default: '23:59'
                },
                timezone: {
                    type: 'text',
                    label: 'Timezone',
                    default: 'Europe/London'
                }
            }
        },

        'condition.rate_limit': {
            name: 'Rate Limiter',
            icon: '⏱️',
            category: 'condition',
            description: 'Limit how often the flow fires',
            inputs: 1,
            outputs: 1,
            properties: {
                max_events: {
                    type: 'number',
                    label: 'Max Events',
                    default: 1,
                    min: 1,
                    max: 1000
                },
                window_seconds: {
                    type: 'number',
                    label: 'Window (seconds)',
                    default: 300,
                    min: 1,
                    max: 86400
                },
                per_object: {
                    type: 'checkbox',
                    label: 'Per object (vs global)',
                    default: true
                }
            }
        },

        'condition.threshold': {
            name: 'Threshold',
            icon: '📊',
            category: 'condition',
            description: 'Compare a numeric field against a threshold',
            inputs: 1,
            outputs: 1,
            properties: {
                field: {
                    type: 'select',
                    label: 'Field',
                    options: [
                        { value: 'location.alt', label: 'Altitude (ft)' },
                        { value: 'data.speed_kts', label: 'Speed (kts)' },
                        { value: 'data.rssi', label: 'RSSI (dBm)' },
                        { value: 'data.distance_km', label: 'Distance (km)' },
                        { value: 'data.heading', label: 'Heading (°)' },
                        { value: 'data.ground_speed', label: 'Ground Speed' }
                    ],
                    default: 'location.alt'
                },
                operator: {
                    type: 'select',
                    label: 'Operator',
                    options: [
                        { value: '<', label: '< Less than' },
                        { value: '<=', label: '≤ Less or equal' },
                        { value: '>', label: '> Greater than' },
                        { value: '>=', label: '≥ Greater or equal' },
                        { value: '==', label: '= Equals' },
                        { value: '!=', label: '≠ Not equals' }
                    ],
                    default: '<'
                },
                value: {
                    type: 'number',
                    label: 'Value',
                    default: 0,
                    step: 0.1
                }
            }
        },

        'condition.object_match': {
            name: 'Object Match',
            icon: '🔍',
            category: 'condition',
            description: 'Match objects by field value',
            inputs: 1,
            outputs: 1,
            properties: {
                field: {
                    type: 'text',
                    label: 'Field',
                    placeholder: 'e.g. data.mmsi, object_id',
                    default: 'object_id'
                },
                operator: {
                    type: 'select',
                    label: 'Operator',
                    options: [
                        { value: 'equals', label: 'Equals' },
                        { value: 'contains', label: 'Contains' },
                        { value: 'regex', label: 'Regex' },
                        { value: 'in_list', label: 'In List' },
                        { value: 'not_in_list', label: 'Not In List' }
                    ],
                    default: 'equals'
                },
                value: {
                    type: 'text',
                    label: 'Value',
                    placeholder: 'Match value or comma-separated list',
                    default: ''
                }
            }
        },

        'condition.logic': {
            name: 'Logic Gate',
            icon: '🔀',
            category: 'condition',
            description: 'Combine conditions with AND/OR/NOT',
            inputs: 3,
            outputs: 1,
            properties: {
                operator: {
                    type: 'select',
                    label: 'Operator',
                    options: [
                        { value: 'AND', label: 'AND — All must pass' },
                        { value: 'OR', label: 'OR — Any must pass' },
                        { value: 'NOT', label: 'NOT — Invert' }
                    ],
                    default: 'AND'
                }
            }
        },

        'condition.state_check': {
            name: 'State Check',
            icon: '📌',
            category: 'condition',
            description: 'Check object tracking state',
            inputs: 1,
            outputs: 1,
            properties: {
                check: {
                    type: 'select',
                    label: 'Check',
                    options: [
                        { value: 'first_seen', label: 'First Seen' },
                        { value: 'returning', label: 'Returning' },
                        { value: 'tracked_duration', label: 'Tracked Duration' },
                        { value: 'in_zone_duration', label: 'In Zone Duration' }
                    ],
                    default: 'first_seen'
                },
                operator: {
                    type: 'select',
                    label: 'Operator',
                    options: [
                        { value: '<', label: '< Less than' },
                        { value: '>', label: '> Greater than' },
                        { value: '==', label: '= Equals' }
                    ],
                    default: '>'
                },
                value: {
                    type: 'number',
                    label: 'Value (seconds)',
                    default: 300,
                    min: 0
                }
            }
        },

        'condition.duration': {
            name: 'Duration',
            icon: '⏳',
            category: 'condition',
            description: 'Object must match for a minimum duration',
            inputs: 1,
            outputs: 1,
            properties: {
                min_duration_seconds: {
                    type: 'number',
                    label: 'Min Duration (seconds)',
                    default: 60,
                    min: 1,
                    max: 86400
                }
            }
        },

        // ── ACTIONS ──────────────────────────────────────────────

        'action.ui_alert': {
            name: 'UI Alert',
            icon: '🔔',
            category: 'action',
            description: 'Show alert in the map interface',
            inputs: 1,
            outputs: 0,
            properties: {
                severity: {
                    type: 'select',
                    label: 'Severity',
                    options: [
                        { value: 'info', label: 'ℹ️ Info' },
                        { value: 'warning', label: '⚠️ Warning' },
                        { value: 'critical', label: '🚨 Critical' },
                        { value: 'emergency', label: '🆘 Emergency' }
                    ],
                    default: 'warning'
                },
                title_template: {
                    type: 'template',
                    label: 'Title',
                    placeholder: 'Alert: {{object_type}} detected',
                    default: '{{object_type}} Alert'
                },
                message_template: {
                    type: 'template',
                    label: 'Message',
                    placeholder: '{{object_id}} at {{lat}}, {{lon}}',
                    default: '{{object_type}} {{object_id}} detected at {{lat}}, {{lon}}'
                },
                sound: {
                    type: 'select',
                    label: 'Sound',
                    options: [
                        { value: 'none', label: 'None' },
                        { value: 'beep', label: 'Beep' },
                        { value: 'alert', label: 'Alert' },
                        { value: 'klaxon', label: 'Klaxon' }
                    ],
                    default: 'alert'
                },
                fly_to: {
                    type: 'checkbox',
                    label: 'Fly map to location',
                    default: false
                },
                highlight_object: {
                    type: 'checkbox',
                    label: 'Highlight object on map',
                    default: true
                }
            }
        },

        'action.telegram': {
            name: 'Telegram',
            icon: '📱',
            category: 'action',
            description: 'Send notification via Telegram',
            inputs: 1,
            outputs: 0,
            properties: {
                chat_id: {
                    type: 'text',
                    label: 'Chat ID',
                    default: '614811138'
                },
                message_template: {
                    type: 'template',
                    label: 'Message',
                    placeholder: '🚨 {{severity}}: {{object_id}} at {{lat}}, {{lon}}',
                    default: '🚨 Alert: {{object_type}} {{object_id}} detected\n📍 {{lat}}, {{lon}}\n⏰ {{timestamp}}'
                },
                include_map_link: {
                    type: 'checkbox',
                    label: 'Include map link',
                    default: true
                }
            }
        },

        'action.horn_tts': {
            name: 'Horn / TTS',
            icon: '🔊',
            category: 'action',
            description: 'Audio announcement via horn/TTS',
            inputs: 1,
            outputs: 0,
            properties: {
                message_template: {
                    type: 'template',
                    label: 'Message',
                    placeholder: 'Attention: {{object_type}} detected',
                    default: 'Attention: {{object_type}} {{object_id}} detected'
                },
                volume: {
                    type: 'number',
                    label: 'Volume (%)',
                    default: 50,
                    min: 0,
                    max: 100
                }
            }
        },

        'action.log': {
            name: 'DB Log',
            icon: '💾',
            category: 'action',
            description: 'Log alert to database (always enabled)',
            inputs: 1,
            outputs: 0,
            properties: {}
        },

        'action.mqtt': {
            name: 'MQTT Publish',
            icon: '📡',
            category: 'action',
            description: 'Publish to an MQTT topic',
            inputs: 1,
            outputs: 0,
            properties: {
                topic: {
                    type: 'text',
                    label: 'Topic',
                    placeholder: 'mesh-mapper/alerts/...',
                    default: 'mesh-mapper/alerts'
                },
                payload_template: {
                    type: 'template',
                    label: 'Payload (JSON)',
                    placeholder: '{"object_id": "{{object_id}}", "severity": "{{severity}}"}',
                    default: '{"object_id": "{{object_id}}", "type": "{{object_type}}", "lat": {{lat}}, "lon": {{lon}}}'
                },
                qos: {
                    type: 'select',
                    label: 'QoS',
                    options: [
                        { value: '0', label: '0 — At most once' },
                        { value: '1', label: '1 — At least once' },
                        { value: '2', label: '2 — Exactly once' }
                    ],
                    default: '1'
                }
            }
        },

        'action.webhook': {
            name: 'Webhook',
            icon: '🌐',
            category: 'action',
            description: 'Send HTTP request to external endpoint',
            inputs: 1,
            outputs: 0,
            properties: {
                url: {
                    type: 'text',
                    label: 'URL',
                    placeholder: 'https://example.com/webhook',
                    default: ''
                },
                method: {
                    type: 'select',
                    label: 'Method',
                    options: [
                        { value: 'POST', label: 'POST' },
                        { value: 'PUT', label: 'PUT' }
                    ],
                    default: 'POST'
                },
                headers: {
                    type: 'textarea',
                    label: 'Headers (JSON)',
                    placeholder: '{"Authorization": "Bearer ..."}',
                    default: '{}'
                },
                payload_template: {
                    type: 'template',
                    label: 'Payload (JSON)',
                    placeholder: '{"alert": "{{object_id}}"}',
                    default: '{"object_id": "{{object_id}}", "type": "{{object_type}}", "severity": "{{severity}}"}'
                }
            }
        }
    };

    // ============================================================
    // Helper: Generate node HTML for Drawflow canvas
    // ============================================================
    function generateNodeHTML(nodeType, nodeId) {
        const def = NODE_TYPES[nodeType];
        if (!def) return '<div class="flow-node">Unknown</div>';
        const cat = CATEGORIES[def.category];
        return `
            <div class="flow-node flow-node--${def.category}" data-node-type="${nodeType}">
                <div class="flow-node__header" style="background: ${cat.color}">
                    <span class="flow-node__icon">${def.icon}</span>
                    <span class="flow-node__title">${def.name}</span>
                </div>
                <div class="flow-node__body">
                    <span class="flow-node__type">${nodeType}</span>
                </div>
            </div>
        `;
    }

    // ============================================================
    // Helper: Get default config for a node type
    // ============================================================
    function getDefaultConfig(nodeType) {
        const def = NODE_TYPES[nodeType];
        if (!def) return {};
        const config = {};
        for (const [key, prop] of Object.entries(def.properties)) {
            config[key] = prop.default !== undefined ? prop.default : '';
        }
        return config;
    }

    // ============================================================
    // Helper: Get nodes grouped by category
    // ============================================================
    function getNodesByCategory() {
        const groups = { trigger: [], condition: [], action: [] };
        for (const [type, def] of Object.entries(NODE_TYPES)) {
            groups[def.category].push({ type, ...def });
        }
        return groups;
    }

    // ============================================================
    // Export
    // ============================================================
    window.FlowNodes = {
        CATEGORIES,
        NODE_TYPES,
        TEMPLATE_VARS,
        generateNodeHTML,
        getDefaultConfig,
        getNodesByCategory
    };

})();
