/* ============================================
   Webcam Markers Layer
   ============================================ */

window.WebcamLayer = (function() {
    'use strict';

    let webcamData = {};
    let visible = true;

    function init() {
        var map = MeshMap.getMap();

        // Load webcam/camera icon
        var svgString = MeshIcons.webcam('#88ccff');
        var img = new Image(18, 18);
        img.onload = function() {
            if (!map.hasImage('webcam-icon')) {
                map.addImage('webcam-icon', img, { sdf: false });
            }
            _addWebcamLayers(map);
        };
        img.onerror = function() {
            console.warn('[WebcamLayer] Icon load failed, using circle fallback');
            _addWebcamLayersFallback(map);
        };
        img.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svgString);

        map.on('click', 'webcam-layer', function(e) {
            if (e.features && e.features.length) {
                showWebcamPopup(e.lngLat, e.features[0].properties);
            }
        });

        map.on('mouseenter', 'webcam-layer', function() {
            map.getCanvas().style.cursor = 'pointer';
        });
        map.on('mouseleave', 'webcam-layer', function() {
            map.getCanvas().style.cursor = '';
        });

        MeshSocket.on('webcams_data', handleWebcams);

        console.log('[WebcamLayer] Initialized');
    }

    function _addWebcamLayers(map) {
        map.addLayer({
            id: 'webcam-layer',
            type: 'symbol',
            source: 'webcams',
            layout: {
                'icon-image': 'webcam-icon',
                'icon-size': [
                    'interpolate', ['linear'], ['zoom'],
                    4, 0.7,
                    10, 1.0,
                    14, 1.3
                ],
                'icon-allow-overlap': true,
                'text-field': ['step', ['zoom'], '', 10, ['get', 'title']],
                'text-font': ['Open Sans Regular'],
                'text-size': 9,
                'text-offset': [0, 1.5],
                'text-anchor': 'top',
                'text-optional': true
            },
            paint: {
                'text-color': '#88ccff',
                'text-halo-color': '#0a0e1a',
                'text-halo-width': 1
            }
        });
    }

    function _addWebcamLayersFallback(map) {
        map.addLayer({
            id: 'webcam-layer',
            type: 'circle',
            source: 'webcams',
            paint: {
                'circle-radius': 5,
                'circle-color': '#88ccff',
                'circle-opacity': 0.8,
                'circle-stroke-width': 1,
                'circle-stroke-color': '#5588aa'
            }
        });

        map.addLayer({
            id: 'webcam-labels',
            type: 'symbol',
            source: 'webcams',
            minzoom: 10,
            layout: {
                'text-field': ['get', 'title'],
                'text-font': ['Open Sans Regular'],
                'text-size': 9,
                'text-offset': [0, 1.5],
                'text-anchor': 'top',
                'text-optional': true
            },
            paint: {
                'text-color': '#88ccff',
                'text-halo-color': '#0a0e1a',
                'text-halo-width': 1
            }
        });
    }

    function handleWebcams(data) {
        if (!data) return;
        var webcams = data.webcams || data;
        if (Array.isArray(webcams)) {
            webcamData = {};
            webcams.forEach(function(w) {
                var key = w.webcam_id || w.id || w.webcamId;
                if (key) webcamData[key] = w;
            });
        } else if (typeof webcams === 'object') {
            webcamData = webcams;
        }

        updateMap();
        updateCount();
    }

    function updateMap() {
        var features = [];

        Object.keys(webcamData).forEach(function(key) {
            var w = webcamData[key];
            var lat = w.lat || w.latitude || (w.location && w.location.latitude);
            var lng = w.lng || w.lon || w.longitude || (w.location && w.location.longitude);

            if (!lat || !lng) return;

            features.push({
                type: 'Feature',
                geometry: {
                    type: 'Point',
                    coordinates: [lng, lat]
                },
                properties: {
                    id: key,
                    title: w.title || w.name || 'Webcam',
                    image: w.image || w.thumbnail || (w.images && w.images.current && w.images.current.preview) || '',
                    status: w.status || 'active',
                    player: w.player || w.url || ''
                }
            });
        });

        MeshMap.updateSource('webcams', {
            type: 'FeatureCollection',
            features: features
        });
    }

    function showWebcamPopup(lngLat, props) {
        var html = '<div class="popup-title" style="color:#66bb6a">📷 ' +
            (props.title || 'Webcam') + '</div>';

        if (props.image) {
            html += '<img src="' + props.image + '" style="width:100%;border-radius:4px;margin:6px 0" ' +
                'onerror="this.style.display=\'none\'" />';
        }

        html += '<div class="popup-row"><span class="popup-label">Status</span>' +
            '<span class="popup-value">' + (props.status || 'active') + '</span></div>';

        if (props.player) {
            html += '<a href="' + props.player + '" target="_blank" class="popup-btn">🔗 View Feed</a>';
        }

        MeshMap.showPopup(lngLat, html);
    }

    function setVisible(vis) {
        visible = vis;
        MeshMap.setLayerVisibility('webcam-layer', vis);
        // webcam-labels only exists in fallback mode
        try { MeshMap.setLayerVisibility('webcam-labels', vis); } catch(e) {}
    }

    function updateCount() {
        var el = document.getElementById('count-webcams');
        if (el) el.textContent = Object.keys(webcamData).length;
    }

    function getData() { return webcamData; }

    return {
        init: init,
        setVisible: setVisible,
        getData: getData,
        updateCount: updateCount
    };
})();
