/* ============================================
   BLE Radar Layer
   ============================================ */

window.BLELayer = (function() {
    'use strict';

    let bleDevices = {};
    let bleStats = {};
    let visible = true;

    // Category colors
    var COLORS = {
        drone:    '#ff3333',
        phone:    '#00aaff',
        tracker:  '#ff9900',
        vehicle:  '#22cc44',
        beacon:   '#aa66ff',
        wearable: '#ff66aa',
        audio:    '#66ddff',
        unknown:  '#888888'
    };

    // Category icons
    var ICONS = {
        drone:    '🛩️',
        phone:    '📱',
        tracker:  '📍',
        vehicle:  '🚗',
        beacon:   '📡',
        wearable: '⌚',
        audio:    '🎧',
        unknown:  '❓'
    };

    function init() {
        var map = MeshMap.getMap();

        // BLE device circle layer
        map.addLayer({
            id: 'ble-layer',
            type: 'circle',
            source: 'ble-devices',
            paint: {
                'circle-radius': [
                    'interpolate', ['linear'], ['zoom'],
                    4, 3,
                    10, 5,
                    14, 8
                ],
                'circle-color': ['match', ['get', 'category'],
                    'drone',    COLORS.drone,
                    'phone',    COLORS.phone,
                    'tracker',  COLORS.tracker,
                    'vehicle',  COLORS.vehicle,
                    'beacon',   COLORS.beacon,
                    'wearable', COLORS.wearable,
                    'audio',    COLORS.audio,
                    COLORS.unknown
                ],
                'circle-opacity': 0.8,
                'circle-stroke-width': 1.5,
                'circle-stroke-color': ['match', ['get', 'category'],
                    'drone',  '#cc0000',
                    'tracker','#cc7700',
                    '#444444'
                ]
            }
        });

        // BLE labels at higher zoom
        map.addLayer({
            id: 'ble-labels',
            type: 'symbol',
            source: 'ble-devices',
            minzoom: 10,
            layout: {
                'text-field': ['coalesce', ['get', 'name'], ['get', 'subcategory'], ['get', 'category']],
                'text-font': ['Open Sans Regular'],
                'text-size': 9,
                'text-offset': [0, 1.5],
                'text-anchor': 'top',
                'text-optional': true,
                'text-max-width': 10
            },
            paint: {
                'text-color': '#cccccc',
                'text-halo-color': '#0a0e1a',
                'text-halo-width': 1
            }
        });

        // Click handler
        map.on('click', 'ble-layer', function(e) {
            if (e.features && e.features.length) {
                var props = e.features[0].properties;
                showBLEPopup(e.lngLat, props);
            }
        });

        map.on('mouseenter', 'ble-layer', function() {
            map.getCanvas().style.cursor = 'pointer';
        });
        map.on('mouseleave', 'ble-layer', function() {
            map.getCanvas().style.cursor = '';
        });

        // GPS station marker layer
        map.addLayer({
            id: 'gps-station-layer',
            type: 'circle',
            source: 'ble-devices',
            filter: ['==', ['get', 'is_station'], true],
            paint: {
                'circle-radius': 10,
                'circle-color': '#00ff88',
                'circle-opacity': 0.9,
                'circle-stroke-width': 3,
                'circle-stroke-color': '#ffffff'
            }
        });

        // Socket events
        MeshSocket.on('ble_devices', handleBLEDevices);
        MeshSocket.on('ble_device_new', handleNewDevice);
        MeshSocket.on('ble_stats', handleBLEStats);
        MeshSocket.on('gps_update', handleGPSUpdate);

        // Also fetch initial GPS data via REST
        fetch('/api/gps').then(function(r) { return r.json(); }).then(function(data) {
            if (data && data.fix) handleGPSUpdate(data);
        }).catch(function() {});

        // Fetch initial BLE data via REST
        fetch('/api/ble_devices').then(function(r) { return r.json(); }).then(function(data) {
            if (data && data.devices) handleBLEDevices(data);
        }).catch(function() {});

        fetch('/api/ble_stats').then(function(r) { return r.json(); }).then(function(data) {
            if (data) handleBLEStats(data);
        }).catch(function() {});

        console.log('[BLELayer] Initialized');
    }

    function handleBLEDevices(data) {
        if (!data) return;

        var devices = data.devices || data;
        if (typeof devices === 'object' && !Array.isArray(devices)) {
            bleDevices = devices;
        } else if (Array.isArray(devices)) {
            bleDevices = {};
            devices.forEach(function(d) {
                if (d.mac) bleDevices[d.mac] = d;
            });
        }

        updateMap();
        updateCount();
        updateStatsPanel();
    }

    function handleNewDevice(data) {
        if (!data || !data.mac) return;
        bleDevices[data.mac] = data;
        updateMap();
        updateCount();
    }

    function handleBLEStats(data) {
        if (!data) return;
        bleStats = data;
        updateStatsPanel();
    }

    function updateMap() {
        var features = [];
        // Station GPS position (injected by backend into each device, or from gps_update)
        var stationLat = window._stationGPS ? window._stationGPS.lat : 0;
        var stationLon = window._stationGPS ? window._stationGPS.lon : 0;

        Object.keys(bleDevices).forEach(function(mac) {
            var dev = bleDevices[mac];
            var lat = 0, lng = 0;

            // If this is a drone with Remote ID GPS data, use its own position
            if (dev.remote_id) {
                lat = dev.remote_id.lat || (dev.drone_lat || 0);
                lng = dev.remote_id.lon || (dev.drone_long || 0);
            }

            // Fallback: use station GPS position for proximity-detected devices
            if ((!lat || lat === 0) && (!lng || lng === 0)) {
                lat = dev.station_lat || stationLat;
                lng = dev.station_lon || stationLon;
            }

            // Only add to map if we have valid coordinates
            if (lat && lng && lat !== 0 && lng !== 0) {
                // Apply small random offset for non-GPS devices to prevent stacking
                if (!dev.remote_id) {
                    var rssiOffset = Math.abs(dev.rssi || -70) * 0.000005;
                    var hashOffset = (mac.charCodeAt(0) + mac.charCodeAt(3)) * 0.00001;
                    lat += (Math.sin(mac.charCodeAt(1) * 2.5) * rssiOffset) + hashOffset;
                    lng += (Math.cos(mac.charCodeAt(4) * 2.5) * rssiOffset) - hashOffset;
                }

                features.push({
                    type: 'Feature',
                    geometry: {
                        type: 'Point',
                        coordinates: [lng, lat]
                    },
                    properties: {
                        mac: dev.mac || mac,
                        category: dev.category || 'unknown',
                        subcategory: dev.subcategory || '',
                        company: dev.company || '',
                        name: dev.name || '',
                        rssi: dev.rssi || 0,
                        advert_count: dev.advert_count || 0
                    }
                });
            }
        });

        MeshMap.updateSource('ble-devices', {
            type: 'FeatureCollection',
            features: features
        });
    }

    function showBLEPopup(lngLat, props) {
        var cat = props.category || 'unknown';
        var icon = ICONS[cat] || '❓';
        var color = COLORS[cat] || '#888';

        var html = '<div class="popup-title" style="color:' + color + '">' +
            icon + ' ' + (props.name || props.subcategory || cat) + '</div>';

        html += '<div class="popup-row"><span class="popup-label">MAC</span>' +
            '<span class="popup-value" style="font-family:monospace;font-size:11px">' +
            (props.mac || '—') + '</span></div>';
        html += '<div class="popup-row"><span class="popup-label">Category</span>' +
            '<span class="popup-value">' + cat + '</span></div>';

        if (props.subcategory) {
            html += '<div class="popup-row"><span class="popup-label">Type</span>' +
                '<span class="popup-value">' + props.subcategory + '</span></div>';
        }
        if (props.company) {
            html += '<div class="popup-row"><span class="popup-label">Company</span>' +
                '<span class="popup-value">' + props.company + '</span></div>';
        }
        html += '<div class="popup-row"><span class="popup-label">RSSI</span>' +
            '<span class="popup-value">' + props.rssi + ' dBm</span></div>';
        html += '<div class="popup-row"><span class="popup-label">Adverts</span>' +
            '<span class="popup-value">' + (props.advert_count || 0) + '</span></div>';

        MeshMap.showPopup(lngLat, html);
    }

    function setVisible(vis) {
        visible = vis;
        MeshMap.setLayerVisibility('ble-layer', vis);
        MeshMap.setLayerVisibility('ble-labels', vis);
    }

    function updateCount() {
        var el = document.getElementById('count-ble');
        if (el) el.textContent = Object.keys(bleDevices).length;
    }

    function updateStatsPanel() {
        var container = document.getElementById('ble-stats-content');
        if (!container) return;

        var stats = bleStats || {};
        var cats = stats.by_category || {};
        var total = stats.total_devices || Object.keys(bleDevices).length;
        var rate = stats.scan_rate || 0;

        var html = '<div class="ble-stat-row">' +
            '<span class="ble-stat-label">Total Devices</span>' +
            '<span class="ble-stat-value">' + total + '</span></div>';
        html += '<div class="ble-stat-row">' +
            '<span class="ble-stat-label">Scan Rate</span>' +
            '<span class="ble-stat-value">' + rate.toFixed(0) + ' pkt/s</span></div>';

        // Category breakdown
        var catOrder = ['drone', 'phone', 'tracker', 'vehicle', 'beacon', 'wearable', 'audio', 'unknown'];
        catOrder.forEach(function(cat) {
            var count = cats[cat] || 0;
            if (count > 0 || cat === 'drone') {
                var icon = ICONS[cat] || '❓';
                var color = COLORS[cat] || '#888';
                html += '<div class="ble-stat-row">' +
                    '<span class="ble-stat-label">' + icon + ' ' + cat + '</span>' +
                    '<span class="ble-stat-value" style="color:' + color + '">' + count + '</span></div>';
            }
        });

        container.innerHTML = html;
    }

    function handleGPSUpdate(data) {
        if (!data) return;
        window._stationGPS = data;

        // Update GPS info in stats panel
        var gpsEl = document.getElementById('gps-status');
        if (gpsEl) {
            if (data.fix) {
                gpsEl.innerHTML = '<span style="color:#00ff88">● FIX</span> ' +
                    data.lat.toFixed(5) + ', ' + data.lon.toFixed(5) +
                    ' <span style="color:#888">' + data.satellites + ' sats</span>';
            } else {
                gpsEl.innerHTML = '<span style="color:#ff6666">● NO FIX</span> ' +
                    '<span style="color:#888">' + (data.satellites || 0) + ' sats</span>';
            }
        }

        // Update station marker on map if we have a fix
        if (data.fix && data.lat && data.lon) {
            updateStationMarker(data.lat, data.lon);
        }

        // Re-render BLE devices (they use station position)
        updateMap();
    }

    function updateStationMarker(lat, lon) {
        // We inject the station marker into the ble-devices source during updateMap
        // by using a special feature – but simpler to use a dedicated source
        var map = MeshMap.getMap();
        if (!map) return;

        // Use a separate source for the station marker
        if (!map.getSource('gps-station')) {
            map.addSource('gps-station', {
                type: 'geojson',
                data: { type: 'FeatureCollection', features: [] }
            });
            // Pulsing outer ring
            map.addLayer({
                id: 'gps-station-ring',
                type: 'circle',
                source: 'gps-station',
                paint: {
                    'circle-radius': 14,
                    'circle-color': 'transparent',
                    'circle-stroke-width': 2,
                    'circle-stroke-color': '#00ff88',
                    'circle-stroke-opacity': 0.5
                }
            });
            // Station dot
            map.addLayer({
                id: 'gps-station-dot',
                type: 'circle',
                source: 'gps-station',
                paint: {
                    'circle-radius': 7,
                    'circle-color': '#00ff88',
                    'circle-opacity': 0.9,
                    'circle-stroke-width': 2,
                    'circle-stroke-color': '#ffffff'
                }
            });
            // Station label
            map.addLayer({
                id: 'gps-station-label',
                type: 'symbol',
                source: 'gps-station',
                layout: {
                    'text-field': '📡 Station',
                    'text-font': ['Open Sans Regular'],
                    'text-size': 10,
                    'text-offset': [0, 2],
                    'text-anchor': 'top'
                },
                paint: {
                    'text-color': '#00ff88',
                    'text-halo-color': '#0a0e1a',
                    'text-halo-width': 1
                }
            });
        }

        MeshMap.updateSource('gps-station', {
            type: 'FeatureCollection',
            features: [{
                type: 'Feature',
                geometry: { type: 'Point', coordinates: [lon, lat] },
                properties: { is_station: true }
            }]
        });
    }

    function getData() { return bleDevices; }
    function getStats() { return bleStats; }

    return {
        init: init,
        setVisible: setVisible,
        getData: getData,
        getStats: getStats,
        updateCount: updateCount,
        updateStatsPanel: updateStatsPanel,
        handleBLEDevices: handleBLEDevices,
        handleGPSUpdate: handleGPSUpdate
    };
})();
