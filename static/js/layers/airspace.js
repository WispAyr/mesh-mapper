/* ============================================
   Airspace Zones Layer (OpenAir + NOTAM)
   ============================================ */

window.AirspaceLayer = (function() {
    'use strict';

    let zonesData = [];
    let visible = true;

    // Color by zone type
    var ZONE_COLORS = {
        'P': '#ff2222',      // Prohibited
        'R': '#ff4444',      // Restricted
        'D': '#ff6600',      // Danger
        'FRZ': '#ff0000',    // Flight Restriction Zone
        'CTR': '#ff9900',    // Control Zone
        'ATZ': '#ffaa00',    // Air Traffic Zone
        'MATZ': '#ffbb22',   // Military ATZ
        'TMZ': '#ddaa00',    // Transponder Mandatory Zone
        'RMZ': '#ccaa00',    // Radio Mandatory Zone
        'A': '#ff3333',      // Class A
        'C': '#ff6600',      // Class C
        'D_CLASS': '#ff8800', // Class D
        'E': '#ffaa00',      // Class E
        'NOTAM': '#ff00ff',  // NOTAM
        'default': '#ff9900'
    };

    function init() {
        var map = MeshMap.getMap();

        // Zone fill
        map.addLayer({
            id: 'airspace-fill',
            type: 'fill',
            source: 'airspace',
            paint: {
                'fill-color': ['get', 'color'],
                'fill-opacity': 0.08
            }
        });

        // Zone outline
        map.addLayer({
            id: 'airspace-outline',
            type: 'line',
            source: 'airspace-outline',
            paint: {
                'line-color': ['get', 'color'],
                'line-width': 1.5,
                'line-opacity': 0.5,
                'line-dasharray': [3, 3]
            }
        });

        // Zone labels
        map.addLayer({
            id: 'airspace-labels',
            type: 'symbol',
            source: 'airspace',
            minzoom: 8,
            layout: {
                'text-field': ['get', 'name'],
                'text-font': ['Open Sans Regular'],
                'text-size': 9,
                'text-optional': true
            },
            paint: {
                'text-color': '#ff9900',
                'text-halo-color': '#0a0e1a',
                'text-halo-width': 1,
                'text-opacity': 0.7
            }
        });

        // Click handler
        map.on('click', 'airspace-fill', function(e) {
            if (e.features && e.features.length) {
                showZonePopup(e.lngLat, e.features[0].properties);
            }
        });

        map.on('mouseenter', 'airspace-fill', function() {
            map.getCanvas().style.cursor = 'pointer';
        });
        map.on('mouseleave', 'airspace-fill', function() {
            map.getCanvas().style.cursor = '';
        });

        // Socket events
        MeshSocket.on('zones_updated', handleZones);
        MeshSocket.on('zone_event', handleZoneEvent);

        console.log('[AirspaceLayer] Initialized');
    }

    function handleZones(data) {
        if (!data) return;
        zonesData = data.zones || data;
        if (!Array.isArray(zonesData)) zonesData = [];
        updateMap();
        updateCount();
    }

    function handleZoneEvent(data) {
        if (!data) return;
        MeshAudio.zoneAlert();
        var msg = 'Zone event: ' + (data.zone_name || data.type || 'Unknown') +
            ' — ' + (data.event || data.action || 'entry');
        DroneLayer.addAlert(msg, 'danger');
    }

    function updateMap() {
        var fillFeatures = [];
        var outlineFeatures = [];

        zonesData.forEach(function(zone, idx) {
            var geometry = null;

            if (zone.geometry) {
                geometry = zone.geometry;
            } else if (zone.coordinates) {
                // Attempt to build polygon from coordinates array
                if (Array.isArray(zone.coordinates) && zone.coordinates.length > 0) {
                    if (Array.isArray(zone.coordinates[0]) && Array.isArray(zone.coordinates[0][0])) {
                        geometry = { type: 'Polygon', coordinates: zone.coordinates };
                    } else {
                        geometry = { type: 'Polygon', coordinates: [zone.coordinates] };
                    }
                }
            } else if (zone.polygon) {
                geometry = { type: 'Polygon', coordinates: [zone.polygon] };
            }

            if (!geometry) return;

            var zoneType = (zone.type || zone.class || 'default').toUpperCase();
            var color = ZONE_COLORS[zoneType] || ZONE_COLORS['default'];

            var props = {
                id: zone.id || idx,
                name: zone.name || zone.title || ('Zone ' + idx),
                type: zoneType,
                color: color,
                ceiling: zone.ceiling || zone.upper || '',
                floor: zone.floor || zone.lower || '',
                source: zone.source || ''
            };

            fillFeatures.push({
                type: 'Feature',
                geometry: geometry,
                properties: props
            });

            outlineFeatures.push({
                type: 'Feature',
                geometry: geometry,
                properties: props
            });
        });

        MeshMap.updateSource('airspace', {
            type: 'FeatureCollection',
            features: fillFeatures
        });

        MeshMap.updateSource('airspace-outline', {
            type: 'FeatureCollection',
            features: outlineFeatures
        });
    }

    function showZonePopup(lngLat, props) {
        var html = '<div class="popup-title" style="color:' + props.color + '">' +
            '🔶 ' + (props.name || 'Airspace Zone') + '</div>';

        html += '<div class="popup-row"><span class="popup-label">Type</span>' +
            '<span class="popup-value">' + (props.type || '—') + '</span></div>';

        if (props.ceiling) {
            html += '<div class="popup-row"><span class="popup-label">Ceiling</span>' +
                '<span class="popup-value">' + props.ceiling + '</span></div>';
        }
        if (props.floor) {
            html += '<div class="popup-row"><span class="popup-label">Floor</span>' +
                '<span class="popup-value">' + props.floor + '</span></div>';
        }
        if (props.source) {
            html += '<div class="popup-row"><span class="popup-label">Source</span>' +
                '<span class="popup-value">' + props.source + '</span></div>';
        }

        MeshMap.showPopup(lngLat, html);
    }

    function setVisible(vis) {
        visible = vis;
        MeshMap.setLayerVisibility('airspace-fill', vis);
        MeshMap.setLayerVisibility('airspace-outline', vis);
        MeshMap.setLayerVisibility('airspace-labels', vis);
    }

    function updateCount() {
        var el = document.getElementById('count-airspace');
        if (el) el.textContent = zonesData.length;
    }

    function getData() { return zonesData; }

    return {
        init: init,
        setVisible: setVisible,
        getData: getData,
        updateCount: updateCount
    };
})();
