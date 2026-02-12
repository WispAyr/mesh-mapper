/* ============================================
   Map Setup — MapLibre GL JS
   ============================================ */

window.MeshMap = (function() {
    'use strict';

    let map = null;
    let popupInstance = null;

    // Default center: Scotland (from the architecture doc)
    const DEFAULT_CENTER = [-4.0, 56.5];
    const DEFAULT_ZOOM = 6;

    // Dark style — self-contained, no external style URL needed
    const DARK_STYLE = {
        version: 8,
        name: 'MeshMapper Dark',
        sources: {
            'osm-raster': {
                type: 'raster',
                tiles: [
                    'https://a.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}@2x.png',
                    'https://b.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}@2x.png',
                    'https://c.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}@2x.png'
                ],
                tileSize: 256,
                attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a> &copy; <a href="https://carto.com/">CARTO</a>',
                maxzoom: 19
            }
        },
        layers: [
            {
                id: 'osm-dark',
                type: 'raster',
                source: 'osm-raster',
                minzoom: 0,
                maxzoom: 22
            }
        ],
        glyphs: 'https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf'
    };

    // Fallback style for offline — plain OSM tiles
    const OFFLINE_STYLE = {
        version: 8,
        name: 'MeshMapper Offline',
        sources: {
            'osm-fallback': {
                type: 'raster',
                tiles: [
                    'https://tile.openstreetmap.org/{z}/{x}/{y}.png'
                ],
                tileSize: 256,
                attribution: '&copy; OpenStreetMap contributors',
                maxzoom: 18
            }
        },
        layers: [
            {
                id: 'osm-fallback-layer',
                type: 'raster',
                source: 'osm-fallback',
                paint: {
                    'raster-brightness-max': 0.4,
                    'raster-saturation': -0.8
                }
            }
        ]
    };

    function init() {
        map = new maplibregl.Map({
            container: 'map',
            style: DARK_STYLE,
            center: DEFAULT_CENTER,
            zoom: DEFAULT_ZOOM,
            maxZoom: 19,
            minZoom: 2,
            attributionControl: true,
            hash: true
        });

        // Navigation controls
        map.addControl(new maplibregl.NavigationControl({
            showCompass: true,
            showZoom: true,
            visualizePitch: true
        }), 'bottom-right');

        // Scale bar
        map.addControl(new maplibregl.ScaleControl({
            maxWidth: 150,
            unit: 'metric'
        }), 'bottom-left');

        // Track mouse position
        map.on('mousemove', function(e) {
            var coordsEl = document.getElementById('coords-display');
            if (coordsEl) {
                coordsEl.textContent = e.lngLat.lat.toFixed(5) + '°, ' + e.lngLat.lng.toFixed(5) + '°';
            }
        });

        // Track zoom
        map.on('zoom', function() {
            var zoomEl = document.getElementById('zoom-display');
            if (zoomEl) {
                zoomEl.textContent = 'Z: ' + map.getZoom().toFixed(1);
            }
        });

        // Handle tile load errors — try offline fallback
        map.on('error', function(e) {
            if (e.error && e.error.status === 0) {
                console.warn('[Map] Tile load failed, may be offline');
            }
        });

        map.on('load', function() {
            console.log('[Map] Loaded successfully');
            // Add empty GeoJSON sources for each layer
            addEmptySources();
        });

        return map;
    }

    function addEmptySources() {
        var emptyGeoJSON = { type: 'FeatureCollection', features: [] };

        // Drone source and layers
        if (!map.getSource('drones')) {
            map.addSource('drones', { type: 'geojson', data: emptyGeoJSON });
        }
        if (!map.getSource('drone-pilots')) {
            map.addSource('drone-pilots', { type: 'geojson', data: emptyGeoJSON });
        }
        if (!map.getSource('drone-lines')) {
            map.addSource('drone-lines', { type: 'geojson', data: emptyGeoJSON });
        }

        // Aircraft source
        if (!map.getSource('aircraft')) {
            map.addSource('aircraft', { type: 'geojson', data: emptyGeoJSON });
        }

        // Vessel source
        if (!map.getSource('vessels')) {
            map.addSource('vessels', { type: 'geojson', data: emptyGeoJSON });
        }

        // APRS source
        if (!map.getSource('aprs')) {
            map.addSource('aprs', { type: 'geojson', data: emptyGeoJSON });
        }

        // Lightning source
        if (!map.getSource('lightning')) {
            map.addSource('lightning', { type: 'geojson', data: emptyGeoJSON });
        }

        // Airspace source
        if (!map.getSource('airspace')) {
            map.addSource('airspace', { type: 'geojson', data: emptyGeoJSON });
        }
        if (!map.getSource('airspace-outline')) {
            map.addSource('airspace-outline', { type: 'geojson', data: emptyGeoJSON });
        }

        // Webcam source
        if (!map.getSource('webcams')) {
            map.addSource('webcams', { type: 'geojson', data: emptyGeoJSON });
        }

        // Weather warnings source
        if (!map.getSource('weather-warnings')) {
            map.addSource('weather-warnings', { type: 'geojson', data: emptyGeoJSON });
        }
    }

    function getMap() { return map; }

    function flyTo(lng, lat, zoom) {
        if (map) {
            map.flyTo({
                center: [lng, lat],
                zoom: zoom || 14,
                duration: 1500
            });
        }
    }

    function showPopup(lngLat, html) {
        if (popupInstance) popupInstance.remove();
        popupInstance = new maplibregl.Popup({
            closeButton: true,
            closeOnClick: true,
            maxWidth: '320px',
            offset: 15
        })
        .setLngLat(lngLat)
        .setHTML(html)
        .addTo(map);
        return popupInstance;
    }

    function removePopup() {
        if (popupInstance) {
            popupInstance.remove();
            popupInstance = null;
        }
    }

    function setLayerVisibility(layerId, visible) {
        if (map && map.getLayer(layerId)) {
            map.setLayoutProperty(layerId, 'visibility', visible ? 'visible' : 'none');
        }
    }

    function updateSource(sourceId, data) {
        if (map) {
            var source = map.getSource(sourceId);
            if (source) {
                source.setData(data);
            }
        }
    }

    function isLoaded() {
        return map && map.loaded();
    }

    function switchToOffline() {
        if (map) {
            map.setStyle(OFFLINE_STYLE);
        }
    }

    return {
        init: init,
        getMap: getMap,
        flyTo: flyTo,
        showPopup: showPopup,
        removePopup: removePopup,
        setLayerVisibility: setLayerVisibility,
        updateSource: updateSource,
        isLoaded: isLoaded,
        switchToOffline: switchToOffline,
        DEFAULT_CENTER: DEFAULT_CENTER
    };
})();
