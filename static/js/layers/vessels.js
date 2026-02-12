/* ============================================
   AIS Maritime Vessel Layer
   ============================================ */

window.VesselLayer = (function() {
    'use strict';

    let vesselData = {};
    let visible = true;

    function init() {
        var map = MeshMap.getMap();

        // Load vessel icon from SVG, then add layer
        var svgString = MeshIcons.vessel('#4a90d9', 0);
        var img = new Image(24, 24);
        img.onload = function() {
            if (!map.hasImage('vessel-icon')) {
                map.addImage('vessel-icon', img, { sdf: false });
            }
            addLayers();
        };
        img.onerror = function() {
            console.warn('[VesselLayer] Failed to load vessel icon, falling back to circle');
            addLayersFallback();
        };
        img.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svgString);
    }

    function addLayers() {
        var map = MeshMap.getMap();

        // Vessel symbol layer with icon
        map.addLayer({
            id: 'vessel-layer',
            type: 'symbol',
            source: 'vessels',
            layout: {
                'icon-image': 'vessel-icon',
                'icon-size': [
                    'interpolate', ['linear'], ['zoom'],
                    4, 0.5,
                    8, 0.8,
                    12, 1.2
                ],
                'icon-rotate': ['coalesce', ['get', 'heading'], ['get', 'course'], 0],
                'icon-rotation-alignment': 'map',
                'icon-allow-overlap': true,
                'icon-ignore-placement': true,
                'text-field': ['step', ['zoom'], '', 9, ['get', 'name']],
                'text-font': ['Open Sans Regular'],
                'text-size': 10,
                'text-offset': [0, 1.5],
                'text-anchor': 'top',
                'text-optional': true
            },
            paint: {
                'text-color': '#4a90d9',
                'text-halo-color': '#0a0e1a',
                'text-halo-width': 1
            }
        });

        attachHandlers();
        console.log('[VesselLayer] Initialized with icon');
    }

    function addLayersFallback() {
        var map = MeshMap.getMap();

        // Fallback: circle layer if icon fails to load
        map.addLayer({
            id: 'vessel-layer',
            type: 'circle',
            source: 'vessels',
            paint: {
                'circle-radius': [
                    'interpolate', ['linear'], ['zoom'],
                    4, 2,
                    8, 5,
                    12, 7
                ],
                'circle-color': '#4a90d9',
                'circle-opacity': 0.85,
                'circle-stroke-width': 1,
                'circle-stroke-color': '#2a5090'
            }
        });

        // Vessel labels
        map.addLayer({
            id: 'vessel-labels',
            type: 'symbol',
            source: 'vessels',
            minzoom: 9,
            layout: {
                'text-field': ['get', 'name'],
                'text-font': ['Open Sans Regular'],
                'text-size': 10,
                'text-offset': [0, 1.5],
                'text-anchor': 'top',
                'text-optional': true
            },
            paint: {
                'text-color': '#4a90d9',
                'text-halo-color': '#0a0e1a',
                'text-halo-width': 1
            }
        });

        attachHandlers();
        console.log('[VesselLayer] Initialized with circle fallback');
    }

    function attachHandlers() {
        var map = MeshMap.getMap();

        // Click handler
        map.on('click', 'vessel-layer', function(e) {
            if (e.features && e.features.length) {
                var props = e.features[0].properties;
                showVesselPopup(e.lngLat, props);
            }
        });

        map.on('mouseenter', 'vessel-layer', function() {
            map.getCanvas().style.cursor = 'pointer';
        });
        map.on('mouseleave', 'vessel-layer', function() {
            map.getCanvas().style.cursor = '';
        });

        // Socket events
        MeshSocket.on('ais_vessels', handleVessels);
        MeshSocket.on('ais_vessel_update', handleVesselUpdate);
    }

    function handleVessels(data) {
        if (!data) return;

        var vessels = data.vessels || data;
        if (Array.isArray(vessels)) {
            vesselData = {};
            vessels.forEach(function(v) {
                var key = v.mmsi || v.id;
                if (key) vesselData[key] = v;
            });
        } else if (typeof vessels === 'object') {
            vesselData = vessels;
        }

        updateMap();
        updateCount();
    }

    function handleVesselUpdate(data) {
        if (!data) return;
        var key = data.mmsi || data.id;
        if (key) {
            vesselData[key] = data;
            updateMap();
            updateCount();
        }
    }

    function updateMap() {
        var features = [];

        Object.keys(vesselData).forEach(function(key) {
            var v = vesselData[key];
            var lat = v.lat || v.latitude;
            var lng = v.lon || v.lng || v.longitude;

            if (!lat || !lng) return;

            features.push({
                type: 'Feature',
                geometry: {
                    type: 'Point',
                    coordinates: [lng, lat]
                },
                properties: {
                    mmsi: v.mmsi || key,
                    name: v.name || v.ship_name || '',
                    type: v.ship_type || v.vessel_type || v.type || '',
                    speed: v.speed || v.sog || 0,
                    course: v.course || v.cog || 0,
                    heading: v.heading || v.true_heading || 0,
                    destination: v.destination || '',
                    flag: v.flag || v.country || '',
                    length: v.length || 0,
                    status: v.nav_status || v.status || ''
                }
            });
        });

        MeshMap.updateSource('vessels', {
            type: 'FeatureCollection',
            features: features
        });
    }

    function showVesselPopup(lngLat, props) {
        var html = '<div class="popup-title vessel">' +
            (props.name || 'MMSI: ' + props.mmsi) + '</div>';

        html += '<div class="popup-row"><span class="popup-label">MMSI</span>' +
            '<span class="popup-value">' + (props.mmsi || '—') + '</span></div>';

        if (props.type) {
            html += '<div class="popup-row"><span class="popup-label">Type</span>' +
                '<span class="popup-value">' + props.type + '</span></div>';
        }
        if (props.flag) {
            html += '<div class="popup-row"><span class="popup-label">Flag</span>' +
                '<span class="popup-value">' + props.flag + '</span></div>';
        }
        html += '<div class="popup-row"><span class="popup-label">Speed</span>' +
            '<span class="popup-value">' + (props.speed ? props.speed + ' kts' : '—') + '</span></div>';
        html += '<div class="popup-row"><span class="popup-label">Course</span>' +
            '<span class="popup-value">' + (props.course ? props.course + '°' : '—') + '</span></div>';

        if (props.destination) {
            html += '<div class="popup-row"><span class="popup-label">Destination</span>' +
                '<span class="popup-value">' + props.destination + '</span></div>';
        }
        if (props.length) {
            html += '<div class="popup-row"><span class="popup-label">Length</span>' +
                '<span class="popup-value">' + props.length + ' m</span></div>';
        }
        if (props.status) {
            html += '<div class="popup-row"><span class="popup-label">Nav Status</span>' +
                '<span class="popup-value">' + props.status + '</span></div>';
        }

        MeshMap.showPopup(lngLat, html);
    }

    function setVisible(vis) {
        visible = vis;
        MeshMap.setLayerVisibility('vessel-layer', vis);
        MeshMap.setLayerVisibility('vessel-labels', vis);
    }

    function updateCount() {
        var el = document.getElementById('count-vessels');
        if (el) el.textContent = Object.keys(vesselData).length;
    }

    function getData() { return vesselData; }

    return {
        init: init,
        setVisible: setVisible,
        getData: getData,
        updateCount: updateCount
    };
})();
