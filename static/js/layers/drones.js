/* ============================================
   Drone Detection Layer
   ============================================ */

window.DroneLayer = (function() {
    'use strict';

    let droneData = {};       // tracked_pairs from server
    let aliases = {};
    let faaCache = {};
    let paths = {};
    let markers = {};         // MapLibre markers for drones
    let pilotMarkers = {};    // MapLibre markers for pilots
    let visible = true;
    let followMac = null;     // MAC to auto-follow

    function init() {
        var map = MeshMap.getMap();

        // Add drone-pilot connection lines layer
        map.addLayer({
            id: 'drone-pilot-lines',
            type: 'line',
            source: 'drone-lines',
            paint: {
                'line-color': '#ff6644',
                'line-width': 1.5,
                'line-dasharray': [4, 4],
                'line-opacity': 0.6
            }
        });

        // Register socket events
        MeshSocket.on('detection', handleDetection);
        MeshSocket.on('detections', handleDetections);
        MeshSocket.on('aliases', handleAliases);
        MeshSocket.on('faa_cache', handleFaaCache);
        MeshSocket.on('paths', handlePaths);

        console.log('[DroneLayer] Initialized');
    }

    function handleDetection(data) {
        if (!data || !data.mac) return;

        var mac = data.mac;
        var isNew = !droneData[mac];
        droneData[mac] = data;

        updateDroneMarker(mac, data);
        updateCount();

        if (isNew) {
            MeshAudio.droneAlert();
            addAlert('New drone detected: ' + (aliases[mac] || mac), 'danger');
        }

        if (followMac === mac && data.drone_lat && data.drone_long) {
            MeshMap.flyTo(data.drone_long, data.drone_lat, 15);
        }
    }

    function handleDetections(data) {
        if (!data) return;
        droneData = data;

        // Clear existing markers
        Object.keys(markers).forEach(function(mac) {
            if (!data[mac]) {
                if (markers[mac]) markers[mac].remove();
                if (pilotMarkers[mac]) pilotMarkers[mac].remove();
                delete markers[mac];
                delete pilotMarkers[mac];
            }
        });

        // Update/create markers
        Object.keys(data).forEach(function(mac) {
            updateDroneMarker(mac, data[mac]);
        });

        updateCount();
    }

    function handleAliases(data) {
        aliases = data || {};
        // Re-render panels
        if (window.DronesPanel) DronesPanel.render();
    }

    function handleFaaCache(data) {
        faaCache = data || {};
    }

    function handlePaths(data) {
        paths = data || {};
    }

    function updateDroneMarker(mac, detection) {
        var map = MeshMap.getMap();
        if (!map) return;

        var droneLat = detection.drone_lat || detection.lat;
        var droneLng = detection.drone_long || detection.lng || detection.lon;
        var pilotLat = detection.pilot_lat;
        var pilotLng = detection.pilot_long;
        var isActive = detection.status !== 'inactive' && detection.active !== false;

        // Drone marker
        if (droneLat && droneLng) {
            if (markers[mac]) {
                markers[mac].setLngLat([droneLng, droneLat]);
                // Update element style for active/inactive
                var el = markers[mac].getElement();
                if (el) {
                    el.querySelector('.drone-icon-inner').style.opacity = isActive ? '1' : '0.4';
                }
            } else {
                var el = createDroneMarkerElement(mac, isActive);
                var marker = new maplibregl.Marker({
                    element: el,
                    anchor: 'center'
                })
                .setLngLat([droneLng, droneLat])
                .addTo(map);

                el.addEventListener('click', function() {
                    showDronePopup(mac);
                });

                markers[mac] = marker;
            }

            if (!visible && markers[mac]) {
                markers[mac].getElement().style.display = 'none';
            }
        }

        // Pilot marker
        if (pilotLat && pilotLng) {
            if (pilotMarkers[mac]) {
                pilotMarkers[mac].setLngLat([pilotLng, pilotLat]);
            } else {
                var pEl = createPilotMarkerElement(mac);
                var pMarker = new maplibregl.Marker({
                    element: pEl,
                    anchor: 'center'
                })
                .setLngLat([pilotLng, pilotLat])
                .addTo(map);

                pilotMarkers[mac] = pMarker;
            }

            if (!visible && pilotMarkers[mac]) {
                pilotMarkers[mac].getElement().style.display = 'none';
            }
        }

        // Update drone-pilot connection lines
        updateConnectionLines();
    }

    function createDroneMarkerElement(mac, isActive) {
        var el = document.createElement('div');
        el.className = 'drone-marker-el';
        el.innerHTML = '<div class="pulse-ring"></div>' +
            '<div class="drone-icon-inner" style="opacity:' + (isActive ? '1' : '0.4') + '">' +
            MeshIcons.drone(isActive ? '#ff4444' : '#666') +
            '</div>';
        el.style.width = '32px';
        el.style.height = '32px';
        el.style.cursor = 'pointer';
        return el;
    }

    function createPilotMarkerElement(mac) {
        var el = document.createElement('div');
        el.innerHTML = MeshIcons.pilot('#ff8800');
        el.style.width = '20px';
        el.style.height = '20px';
        el.style.cursor = 'pointer';
        el.addEventListener('click', function() {
            showDronePopup(mac);
        });
        return el;
    }

    function updateConnectionLines() {
        var features = [];
        Object.keys(droneData).forEach(function(mac) {
            var d = droneData[mac];
            var dLat = d.drone_lat || d.lat;
            var dLng = d.drone_long || d.lng || d.lon;
            var pLat = d.pilot_lat;
            var pLng = d.pilot_long;

            if (dLat && dLng && pLat && pLng) {
                features.push({
                    type: 'Feature',
                    geometry: {
                        type: 'LineString',
                        coordinates: [[dLng, dLat], [pLng, pLat]]
                    },
                    properties: { mac: mac }
                });
            }
        });

        MeshMap.updateSource('drone-lines', {
            type: 'FeatureCollection',
            features: features
        });
    }

    function showDronePopup(mac) {
        var d = droneData[mac];
        if (!d) return;

        var alias = aliases[mac] || '';
        var faa = faaCache[mac] || {};
        var dLat = d.drone_lat || d.lat;
        var dLng = d.drone_long || d.lng || d.lon;

        if (!dLat || !dLng) return;

        var html = '<div class="popup-title drone">' +
            (alias ? alias + ' (' + mac + ')' : mac) + '</div>';

        html += '<div class="popup-row"><span class="popup-label">Status</span>' +
            '<span class="popup-value">' + (d.status || (d.active !== false ? 'Active' : 'Inactive')) + '</span></div>';

        if (d.rssi) {
            html += '<div class="popup-row"><span class="popup-label">RSSI</span>' +
                '<span class="popup-value">' + d.rssi + ' dBm</span></div>';
        }
        if (d.drone_altitude) {
            html += '<div class="popup-row"><span class="popup-label">Altitude</span>' +
                '<span class="popup-value">' + d.drone_altitude.toFixed(1) + ' m</span></div>';
        }
        if (d.basic_id) {
            html += '<div class="popup-row"><span class="popup-label">Basic ID</span>' +
                '<span class="popup-value">' + d.basic_id + '</span></div>';
        }
        if (d.remote_id) {
            html += '<div class="popup-row"><span class="popup-label">Remote ID</span>' +
                '<span class="popup-value">' + d.remote_id + '</span></div>';
        }
        if (dLat && dLng) {
            html += '<div class="popup-row"><span class="popup-label">Position</span>' +
                '<span class="popup-value">' + Number(dLat).toFixed(5) + ', ' + Number(dLng).toFixed(5) + '</span></div>';
        }
        if (d.pilot_lat && d.pilot_long) {
            html += '<div class="popup-row"><span class="popup-label">Pilot</span>' +
                '<span class="popup-value">' + Number(d.pilot_lat).toFixed(5) + ', ' + Number(d.pilot_long).toFixed(5) + '</span></div>';
        }

        if (faa && Object.keys(faa).length) {
            html += '<hr style="border:none;border-top:1px solid #1e2d4a;margin:6px 0">';
            html += '<div class="popup-row"><span class="popup-label">FAA Data</span><span class="popup-value">✓</span></div>';
        }

        html += '<button class="popup-btn" onclick="DroneLayer.followDrone(\'' + mac + '\')">📍 Follow</button>';
        html += ' <button class="popup-btn" onclick="DroneLayer.stopFollow()">✕ Unfollow</button>';

        MeshMap.showPopup([dLng, dLat], html);
    }

    function followDrone(mac) {
        followMac = mac;
        var d = droneData[mac];
        if (d) {
            var lat = d.drone_lat || d.lat;
            var lng = d.drone_long || d.lng || d.lon;
            if (lat && lng) MeshMap.flyTo(lng, lat, 15);
        }
    }

    function stopFollow() {
        followMac = null;
    }

    function setVisible(vis) {
        visible = vis;
        Object.keys(markers).forEach(function(mac) {
            if (markers[mac]) markers[mac].getElement().style.display = vis ? '' : 'none';
        });
        Object.keys(pilotMarkers).forEach(function(mac) {
            if (pilotMarkers[mac]) pilotMarkers[mac].getElement().style.display = vis ? '' : 'none';
        });
        MeshMap.setLayerVisibility('drone-pilot-lines', vis);
    }

    function updateCount() {
        var total = Object.keys(droneData).length;
        var active = 0;
        Object.keys(droneData).forEach(function(mac) {
            var d = droneData[mac];
            if (d.status !== 'inactive' && d.active !== false) active++;
        });
        var el = document.getElementById('count-drones');
        if (el) el.textContent = total;

        // Update panel counts
        var activeEl = document.getElementById('active-drones');
        var inactiveEl = document.getElementById('inactive-drones');
        var totalEl = document.getElementById('total-drones');
        if (activeEl) activeEl.textContent = active;
        if (inactiveEl) inactiveEl.textContent = total - active;
        if (totalEl) totalEl.textContent = total;
    }

    function addAlert(text, type) {
        var log = document.getElementById('alert-log');
        if (!log) return;

        var emptyState = log.querySelector('.empty-state');
        if (emptyState) emptyState.remove();

        var item = document.createElement('div');
        item.className = 'alert-item ' + (type || 'info');
        var now = new Date();
        item.innerHTML = '<span class="alert-time">' + now.toLocaleTimeString() + '</span>' +
            '<span class="alert-text">' + text + '</span>';
        log.insertBefore(item, log.firstChild);

        // Keep max 50 alerts
        while (log.children.length > 50) {
            log.removeChild(log.lastChild);
        }
    }

    function getData() { return droneData; }
    function getAliases() { return aliases; }
    function getFaaCache() { return faaCache; }

    return {
        init: init,
        setVisible: setVisible,
        getData: getData,
        getAliases: getAliases,
        getFaaCache: getFaaCache,
        followDrone: followDrone,
        stopFollow: stopFollow,
        showDronePopup: showDronePopup,
        addAlert: addAlert,
        updateCount: updateCount
    };
})();
