/* ============================================
   ADS-B Aircraft Layer
   ============================================ */

window.AircraftLayer = (function() {
    'use strict';

    let aircraftData = {};
    let markers = {};
    let visible = true;

    function init() {
        var map = MeshMap.getMap();

        // Aircraft icon layer using symbols
        map.addLayer({
            id: 'aircraft-layer',
            type: 'circle',
            source: 'aircraft',
            paint: {
                'circle-radius': [
                    'interpolate', ['linear'], ['zoom'],
                    4, 2,
                    8, 4,
                    12, 6
                ],
                'circle-color': '#00d4ff',
                'circle-opacity': 0.85,
                'circle-stroke-width': 1,
                'circle-stroke-color': '#006688'
            }
        });

        // Aircraft labels at higher zoom
        map.addLayer({
            id: 'aircraft-labels',
            type: 'symbol',
            source: 'aircraft',
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
                'text-color': '#00d4ff',
                'text-halo-color': '#0a0e1a',
                'text-halo-width': 1
            }
        });

        // Click handler
        map.on('click', 'aircraft-layer', function(e) {
            if (e.features && e.features.length) {
                var props = e.features[0].properties;
                showAircraftPopup(e.lngLat, props);
            }
        });

        map.on('mouseenter', 'aircraft-layer', function() {
            map.getCanvas().style.cursor = 'pointer';
        });
        map.on('mouseleave', 'aircraft-layer', function() {
            map.getCanvas().style.cursor = '';
        });

        // Socket events
        MeshSocket.on('adsb_aircraft', handleAircraft);

        console.log('[AircraftLayer] Initialized');
    }

    function handleAircraft(data) {
        if (!data) return;

        var aircraft = data.aircraft || data;
        if (Array.isArray(aircraft)) {
            aircraftData = {};
            aircraft.forEach(function(ac) {
                var key = ac.hex || ac.icao || ac.id;
                if (key) aircraftData[key] = ac;
            });
        } else if (typeof aircraft === 'object') {
            aircraftData = aircraft;
        }

        updateMap();
        updateCount();
    }

    function updateMap() {
        var features = [];

        Object.keys(aircraftData).forEach(function(key) {
            var ac = aircraftData[key];
            var lat = ac.lat || ac.latitude;
            var lng = ac.lon || ac.lng || ac.longitude;

            if (!lat || !lng) return;

            features.push({
                type: 'Feature',
                geometry: {
                    type: 'Point',
                    coordinates: [lng, lat]
                },
                properties: {
                    hex: ac.hex || key,
                    callsign: (ac.flight || ac.callsign || '').trim(),
                    registration: ac.r || ac.registration || '',
                    type: ac.t || ac.type || '',
                    altitude: ac.alt_baro || ac.alt_geom || ac.altitude || 0,
                    speed: ac.gs || ac.speed || 0,
                    track: ac.track || ac.heading || 0,
                    squawk: ac.squawk || '',
                    category: ac.category || '',
                    vert_rate: ac.baro_rate || ac.vert_rate || 0
                }
            });
        });

        MeshMap.updateSource('aircraft', {
            type: 'FeatureCollection',
            features: features
        });
    }

    function showAircraftPopup(lngLat, props) {
        var html = '<div class="popup-title aircraft">' +
            (props.callsign || props.hex || 'Unknown') + '</div>';

        if (props.registration) {
            html += '<div class="popup-row"><span class="popup-label">Registration</span>' +
                '<span class="popup-value">' + props.registration + '</span></div>';
        }
        if (props.type) {
            html += '<div class="popup-row"><span class="popup-label">Type</span>' +
                '<span class="popup-value">' + props.type + '</span></div>';
        }
        html += '<div class="popup-row"><span class="popup-label">ICAO</span>' +
            '<span class="popup-value">' + (props.hex || '—') + '</span></div>';
        html += '<div class="popup-row"><span class="popup-label">Altitude</span>' +
            '<span class="popup-value">' + formatAlt(props.altitude) + '</span></div>';
        html += '<div class="popup-row"><span class="popup-label">Speed</span>' +
            '<span class="popup-value">' + (props.speed ? props.speed + ' kts' : '—') + '</span></div>';
        html += '<div class="popup-row"><span class="popup-label">Heading</span>' +
            '<span class="popup-value">' + (props.track ? props.track + '°' : '—') + '</span></div>';

        if (props.squawk) {
            html += '<div class="popup-row"><span class="popup-label">Squawk</span>' +
                '<span class="popup-value">' + props.squawk + '</span></div>';
        }
        if (props.vert_rate) {
            html += '<div class="popup-row"><span class="popup-label">V/S</span>' +
                '<span class="popup-value">' + props.vert_rate + ' ft/min</span></div>';
        }

        MeshMap.showPopup(lngLat, html);
    }

    function formatAlt(alt) {
        if (!alt || alt === 'ground') return 'GND';
        return Number(alt).toLocaleString() + ' ft';
    }

    function setVisible(vis) {
        visible = vis;
        MeshMap.setLayerVisibility('aircraft-layer', vis);
        MeshMap.setLayerVisibility('aircraft-labels', vis);
    }

    function updateCount() {
        var el = document.getElementById('count-aircraft');
        if (el) el.textContent = Object.keys(aircraftData).length;
    }

    function getData() { return aircraftData; }

    return {
        init: init,
        setVisible: setVisible,
        getData: getData,
        updateCount: updateCount
    };
})();
