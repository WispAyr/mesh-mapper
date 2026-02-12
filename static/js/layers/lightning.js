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

        map.on('click', 'lightning-layer', function(e) {
            if (e.features && e.features.length) {
                showLightningPopup(e.lngLat, e.features[0].properties);
            }
        });

        // Socket events
        MeshSocket.on('lightning_strike', handleStrike);
        MeshSocket.on('lightning_alert', handleAlert);

        // Fade timer
        setInterval(updateFade, 10000);

        console.log('[LightningLayer] Initialized');
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
