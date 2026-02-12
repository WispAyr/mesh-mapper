/* ============================================
   Weather Overlays Layer
   ============================================ */

window.WeatherLayer = (function() {
    'use strict';

    let weatherData = {};
    let warningsData = [];
    let visible = true;

    function init() {
        var map = MeshMap.getMap();

        // Met Office weather warning polygons
        map.addLayer({
            id: 'weather-warnings-fill',
            type: 'fill',
            source: 'weather-warnings',
            paint: {
                'fill-color': ['get', 'color'],
                'fill-opacity': 0.15
            }
        });

        map.addLayer({
            id: 'weather-warnings-outline',
            type: 'line',
            source: 'weather-warnings',
            paint: {
                'line-color': ['get', 'color'],
                'line-width': 2,
                'line-opacity': 0.6
            }
        });

        // Click handler for warnings
        map.on('click', 'weather-warnings-fill', function(e) {
            if (e.features && e.features.length) {
                showWarningPopup(e.lngLat, e.features[0].properties);
            }
        });

        map.on('mouseenter', 'weather-warnings-fill', function() {
            map.getCanvas().style.cursor = 'pointer';
        });
        map.on('mouseleave', 'weather-warnings-fill', function() {
            map.getCanvas().style.cursor = '';
        });

        // Socket events
        MeshSocket.on('weather_data', handleWeather);
        MeshSocket.on('metoffice_warnings', handleWarnings);

        console.log('[WeatherLayer] Initialized');
    }

    function handleWeather(data) {
        if (!data) return;
        weatherData = data.weather || data;
        updateCount();
    }

    function handleWarnings(data) {
        if (!data) return;
        warningsData = data.warnings || data;
        if (Array.isArray(warningsData)) {
            updateWarningsMap();
        }
        updateCount();

        // Update panel
        if (window.AircraftPanel) AircraftPanel.renderWeather();
    }

    function updateWarningsMap() {
        var features = [];

        warningsData.forEach(function(warning, idx) {
            var geometry = null;

            // Handle different geometry formats
            if (warning.geometry) {
                geometry = warning.geometry;
            } else if (warning.coordinates) {
                geometry = {
                    type: 'Polygon',
                    coordinates: warning.coordinates
                };
            } else if (warning.polygon) {
                geometry = {
                    type: 'Polygon',
                    coordinates: [warning.polygon]
                };
            }

            if (!geometry) return;

            // Color by severity
            var color = '#8899aa';
            var severity = (warning.severity || warning.level || '').toLowerCase();
            if (severity.includes('red') || severity.includes('extreme')) color = '#ff2222';
            else if (severity.includes('amber') || severity.includes('severe')) color = '#ff9900';
            else if (severity.includes('yellow') || severity.includes('moderate')) color = '#ffdd00';

            features.push({
                type: 'Feature',
                geometry: geometry,
                properties: {
                    title: warning.title || warning.headline || 'Weather Warning',
                    severity: warning.severity || warning.level || '',
                    description: warning.description || warning.message || '',
                    color: color,
                    valid_from: warning.valid_from || warning.onset || '',
                    valid_to: warning.valid_to || warning.expires || '',
                    type: warning.type || warning.event || ''
                }
            });
        });

        MeshMap.updateSource('weather-warnings', {
            type: 'FeatureCollection',
            features: features
        });
    }

    function showWarningPopup(lngLat, props) {
        var html = '<div class="popup-title" style="color:#ff9900">' +
            '⚠️ ' + (props.title || 'Weather Warning') + '</div>';

        if (props.severity) {
            html += '<div class="popup-row"><span class="popup-label">Severity</span>' +
                '<span class="popup-value" style="color:' + props.color + '">' + props.severity + '</span></div>';
        }
        if (props.type) {
            html += '<div class="popup-row"><span class="popup-label">Type</span>' +
                '<span class="popup-value">' + props.type + '</span></div>';
        }
        if (props.valid_from) {
            html += '<div class="popup-row"><span class="popup-label">From</span>' +
                '<span class="popup-value">' + props.valid_from + '</span></div>';
        }
        if (props.valid_to) {
            html += '<div class="popup-row"><span class="popup-label">To</span>' +
                '<span class="popup-value">' + props.valid_to + '</span></div>';
        }
        if (props.description) {
            html += '<div style="margin-top:6px;font-size:11px;color:#94a3b8;max-height:100px;overflow-y:auto">' +
                props.description + '</div>';
        }

        MeshMap.showPopup(lngLat, html);
    }

    function setVisible(vis) {
        visible = vis;
        MeshMap.setLayerVisibility('weather-warnings-fill', vis);
        MeshMap.setLayerVisibility('weather-warnings-outline', vis);
    }

    function updateCount() {
        var el = document.getElementById('count-weather');
        if (!el) return;

        var wCount = 0;
        if (typeof weatherData === 'object' && !Array.isArray(weatherData)) {
            wCount = Object.keys(weatherData).length;
        }
        var totalCount = wCount + (Array.isArray(warningsData) ? warningsData.length : 0);
        el.textContent = totalCount;
    }

    function getData() { return weatherData; }
    function getWarnings() { return warningsData; }

    return {
        init: init,
        setVisible: setVisible,
        getData: getData,
        getWarnings: getWarnings,
        updateCount: updateCount
    };
})();
