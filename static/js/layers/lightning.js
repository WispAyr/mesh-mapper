/* ============================================
   Lightning Strikes Layer
   ============================================ */

window.LightningLayer = (function() {
    'use strict';

    let strikes = [];
    let visible = true;
    const MAX_STRIKES = 500;
    const STRIKE_LIFETIME_MS = 300000; // 5 minutes

    function init() {
        var map = MeshMap.getMap();

        // Load lightning bolt icon
        var svgString = MeshIcons.lightning('#ffee00');
        var img = new Image(18, 18);
        img.onload = function() {
            if (!map.hasImage('lightning-icon')) {
                map.addImage('lightning-icon', img, { sdf: false });
            }
            _addLightningLayer(map);
        };
        img.onerror = function() {
            console.warn('[LightningLayer] Icon load failed, using circle fallback');
            _addLightningLayerFallback(map);
        };
        img.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svgString);

        map.on('click', 'lightning-layer', function(e) {
            if (e.features && e.features.length) {
                showLightningPopup(e.lngLat, e.features[0].properties);
            }
        });

        map.on('mouseenter', 'lightning-layer', function() {
            map.getCanvas().style.cursor = 'pointer';
        });
        map.on('mouseleave', 'lightning-layer', function() {
            map.getCanvas().style.cursor = '';
        });

        // Socket events
        MeshSocket.on('lightning_strike', handleStrike);
        MeshSocket.on('lightning_alert', handleAlert);

        // Fade timer
        setInterval(updateFade, 10000);

        console.log('[LightningLayer] Initialized');
    }

    function _addLightningLayer(map) {
        map.addLayer({
            id: 'lightning-layer',
            type: 'symbol',
            source: 'lightning',
            layout: {
                'icon-image': 'lightning-icon',
                'icon-size': [
                    'interpolate', ['linear'], ['get', 'age'],
                    0, 1.4,
                    1, 0.5
                ],
                'icon-allow-overlap': true,
                'icon-ignore-placement': true,
                'text-field': ['step', ['zoom'], '', 10, ['get', 'time']],
                'text-font': ['Open Sans Regular'],
                'text-size': 9,
                'text-offset': [0, 1.5],
                'text-anchor': 'top',
                'text-optional': true
            },
            paint: {
                'icon-opacity': [
                    'interpolate', ['linear'], ['get', 'age'],
                    0, 1.0,
                    1, 0.15
                ],
                'text-color': '#ffee00',
                'text-opacity': [
                    'interpolate', ['linear'], ['get', 'age'],
                    0, 0.8,
                    1, 0.1
                ],
                'text-halo-color': '#0a0e1a',
                'text-halo-width': 1
            }
        });
    }

    function _addLightningLayerFallback(map) {
        map.addLayer({
            id: 'lightning-layer',
            type: 'circle',
            source: 'lightning',
            paint: {
                'circle-radius': [
                    'interpolate', ['linear'], ['get', 'age'],
                    0, 6,
                    1, 2
                ],
                'circle-color': '#ffee00',
                'circle-opacity': [
                    'interpolate', ['linear'], ['get', 'age'],
                    0, 0.9,
                    1, 0.1
                ],
                'circle-stroke-width': 1,
                'circle-stroke-color': '#ff8800',
                'circle-stroke-opacity': [
                    'interpolate', ['linear'], ['get', 'age'],
                    0, 0.6,
                    1, 0
                ]
            }
        });
    }

    function handleStrike(data) {
        if (!data) return;

        var lat = data.lat || data.latitude;
        var lng = data.lon || data.lng || data.longitude;
        if (!lat || !lng) return;

        strikes.push({
            lat: lat,
            lng: lng,
            time: Date.now(),
            distance: data.distance || null,
            polarity: data.pol || data.polarity || 0
        });

        // Trim old strikes
        var cutoff = Date.now() - STRIKE_LIFETIME_MS;
        strikes = strikes.filter(function(s) { return s.time > cutoff; });
        if (strikes.length > MAX_STRIKES) {
            strikes = strikes.slice(-MAX_STRIKES);
        }

        updateMap();
        updateCount();
    }

    function handleAlert(data) {
        if (!data) return;
        MeshAudio.lightningAlert();
        DroneLayer.addAlert('⚡ Lightning alert: ' + (data.message || 'Strike nearby'), 'danger');
    }

    function updateFade() {
        var cutoff = Date.now() - STRIKE_LIFETIME_MS;
        var before = strikes.length;
        strikes = strikes.filter(function(s) { return s.time > cutoff; });
        if (before !== strikes.length) {
            updateMap();
            updateCount();
        }
    }

    function updateMap() {
        var now = Date.now();
        var features = strikes.map(function(s) {
            var age = Math.min(1, (now - s.time) / STRIKE_LIFETIME_MS);
            return {
                type: 'Feature',
                geometry: {
                    type: 'Point',
                    coordinates: [s.lng, s.lat]
                },
                properties: {
                    age: age,
                    time: new Date(s.time).toLocaleTimeString(),
                    distance: s.distance,
                    polarity: s.polarity
                }
            };
        });

        MeshMap.updateSource('lightning', {
            type: 'FeatureCollection',
            features: features
        });
    }

    function showLightningPopup(lngLat, props) {
        var html = '<div class="popup-title" style="color:#ffee00">⚡ Lightning Strike</div>';
        html += '<div class="popup-row"><span class="popup-label">Time</span>' +
            '<span class="popup-value">' + (props.time || '—') + '</span></div>';
        if (props.distance) {
            html += '<div class="popup-row"><span class="popup-label">Distance</span>' +
                '<span class="popup-value">' + Number(props.distance).toFixed(1) + ' km</span></div>';
        }
        MeshMap.showPopup(lngLat, html);
    }

    function setVisible(vis) {
        visible = vis;
        MeshMap.setLayerVisibility('lightning-layer', vis);
    }

    function updateCount() {
        var el = document.getElementById('count-lightning');
        if (el) el.textContent = strikes.length;
    }

    function getData() { return strikes; }

    return {
        init: init,
        setVisible: setVisible,
        getData: getData,
        updateCount: updateCount
    };
})();
