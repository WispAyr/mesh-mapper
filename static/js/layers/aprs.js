/* ============================================
   APRS Stations Layer
   ============================================ */

window.AprsLayer = (function() {
    'use strict';

    let aprsData = {};
    let visible = true;

    function init() {
        var map = MeshMap.getMap();

        // Load APRS icon from SVG, then add layer
        var svgString = MeshIcons.aprs('#44ff44');
        var img = new Image(20, 20);
        img.onload = function() {
            if (!map.hasImage('aprs-icon')) {
                map.addImage('aprs-icon', img, { sdf: false });
            }
            addLayers();
        };
        img.onerror = function() {
            console.warn('[AprsLayer] Failed to load APRS icon, falling back to circle');
            addLayersFallback();
        };
        img.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svgString);
    }

    function addLayers() {
        var map = MeshMap.getMap();

        // APRS symbol layer with icon
        map.addLayer({
            id: 'aprs-layer',
            type: 'symbol',
            source: 'aprs',
            layout: {
                'icon-image': 'aprs-icon',
                'icon-size': [
                    'interpolate', ['linear'], ['zoom'],
                    4, 0.6,
                    8, 0.9,
                    12, 1.2
                ],
                'icon-allow-overlap': true,
                'icon-ignore-placement': true,
                'text-field': ['step', ['zoom'], '', 8, ['get', 'callsign']],
                'text-font': ['Open Sans Regular'],
                'text-size': 10,
                'text-offset': [0, 1.5],
                'text-anchor': 'top',
                'text-optional': true
            },
            paint: {
                'text-color': '#44ff44',
                'text-halo-color': '#0a0e1a',
                'text-halo-width': 1
            }
        });

        attachHandlers();
        console.log('[AprsLayer] Initialized with icon');
    }

    function addLayersFallback() {
        var map = MeshMap.getMap();

        // Fallback: circle layer
        map.addLayer({
            id: 'aprs-layer',
            type: 'circle',
            source: 'aprs',
            paint: {
                'circle-radius': [
                    'interpolate', ['linear'], ['zoom'],
                    4, 3,
                    8, 5,
                    12, 7
                ],
                'circle-color': '#44ff44',
                'circle-opacity': 0.85,
                'circle-stroke-width': 1,
                'circle-stroke-color': '#228822'
            }
        });

        map.addLayer({
            id: 'aprs-labels',
            type: 'symbol',
            source: 'aprs',
            minzoom: 8,
            layout: {
                'text-field': ['get', 'callsign'],
                'text-font': ['Open Sans Regular'],
                'text-size': 10,
                'text-offset': [0, 1.5],
                'text-anchor': 'top',
                'text-optional': true
            },
            paint: {
                'text-color': '#44ff44',
                'text-halo-color': '#0a0e1a',
                'text-halo-width': 1
            }
        });

        attachHandlers();
        console.log('[AprsLayer] Initialized with circle fallback');
    }

    function attachHandlers() {
        var map = MeshMap.getMap();

        map.on('click', 'aprs-layer', function(e) {
            if (e.features && e.features.length) {
                showAprsPopup(e.lngLat, e.features[0].properties);
            }
        });

        map.on('mouseenter', 'aprs-layer', function() {
            map.getCanvas().style.cursor = 'pointer';
        });
        map.on('mouseleave', 'aprs-layer', function() {
            map.getCanvas().style.cursor = '';
        });

        MeshSocket.on('aprs_stations', handleAprs);
    }

    function handleAprs(data) {
        if (!data) return;

        var stations = data.stations || data;
        if (Array.isArray(stations)) {
            aprsData = {};
            stations.forEach(function(s) {
                var key = s.callsign || s.name || s.id;
                if (key) aprsData[key] = s;
            });
        } else if (typeof stations === 'object') {
            aprsData = stations;
        }

        updateMap();
        updateCount();
    }

    function updateMap() {
        var features = [];

        Object.keys(aprsData).forEach(function(key) {
            var s = aprsData[key];
            var lat = s.lat || s.latitude;
            var lng = s.lng || s.lon || s.longitude;

            if (!lat || !lng) return;

            features.push({
                type: 'Feature',
                geometry: {
                    type: 'Point',
                    coordinates: [lng, lat]
                },
                properties: {
                    callsign: s.callsign || s.name || key,
                    comment: s.comment || '',
                    symbol: s.symbol || '',
                    path: s.path || '',
                    speed: s.speed || 0,
                    course: s.course || 0,
                    altitude: s.altitude || 0,
                    lasttime: s.lasttime || s.last_time || ''
                }
            });
        });

        MeshMap.updateSource('aprs', {
            type: 'FeatureCollection',
            features: features
        });
    }

    function showAprsPopup(lngLat, props) {
        var html = '<div class="popup-title aprs">' + (props.callsign || 'APRS Station') + '</div>';

        if (props.comment) {
            html += '<div class="popup-row"><span class="popup-label">Comment</span>' +
                '<span class="popup-value">' + props.comment + '</span></div>';
        }
        if (props.altitude) {
            html += '<div class="popup-row"><span class="popup-label">Altitude</span>' +
                '<span class="popup-value">' + props.altitude + ' m</span></div>';
        }
        if (props.speed) {
            html += '<div class="popup-row"><span class="popup-label">Speed</span>' +
                '<span class="popup-value">' + props.speed + ' km/h</span></div>';
        }
        if (props.course) {
            html += '<div class="popup-row"><span class="popup-label">Course</span>' +
                '<span class="popup-value">' + props.course + '°</span></div>';
        }
        if (props.lasttime) {
            html += '<div class="popup-row"><span class="popup-label">Last seen</span>' +
                '<span class="popup-value">' + props.lasttime + '</span></div>';
        }

        MeshMap.showPopup(lngLat, html);
    }

    function setVisible(vis) {
        visible = vis;
        MeshMap.setLayerVisibility('aprs-layer', vis);
        MeshMap.setLayerVisibility('aprs-labels', vis);
    }

    function updateCount() {
        var el = document.getElementById('count-aprs');
        if (el) el.textContent = Object.keys(aprsData).length;
    }

    function getData() { return aprsData; }

    return {
        init: init,
        setVisible: setVisible,
        getData: getData,
        updateCount: updateCount
    };
})();
