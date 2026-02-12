/* ============================================
   Drone Detections Panel
   ============================================ */

window.DronesPanel = (function() {
    'use strict';

    function init() {
        // Re-render on new detection data
        MeshSocket.on('detections', render);
        MeshSocket.on('detection', render);
        MeshSocket.on('aliases', render);
        console.log('[DronesPanel] Initialized');
    }

    function render() {
        var droneData = DroneLayer.getData();
        var aliases = DroneLayer.getAliases();
        var container = document.getElementById('drone-list');
        if (!container) return;

        var macs = Object.keys(droneData);
        if (macs.length === 0) {
            container.innerHTML = '<div class="empty-state">No drone detections</div>';
            return;
        }

        // Sort: active first, then by most recent
        macs.sort(function(a, b) {
            var da = droneData[a];
            var db = droneData[b];
            var aActive = da.status !== 'inactive' && da.active !== false;
            var bActive = db.status !== 'inactive' && db.active !== false;
            if (aActive !== bActive) return bActive - aActive;

            var aTime = da.last_seen || da.timestamp || 0;
            var bTime = db.last_seen || db.timestamp || 0;
            return bTime - aTime;
        });

        var html = '';
        macs.forEach(function(mac) {
            var d = droneData[mac];
            var alias = aliases[mac] || '';
            var isActive = d.status !== 'inactive' && d.active !== false;

            html += '<div class="drone-card ' + (isActive ? 'active' : 'inactive') +
                '" onclick="DronesPanel.selectDrone(\'' + mac + '\')">';

            html += '<div class="drone-card-header">';
            html += '<div>';
            html += '<div class="drone-mac">' + mac + '</div>';
            if (alias) html += '<div class="drone-alias">' + alias + '</div>';
            html += '</div>';
            html += '<span class="drone-status ' + (isActive ? 'active' : 'inactive') + '">' +
                (isActive ? 'ACTIVE' : 'INACTIVE') + '</span>';
            html += '</div>';

            html += '<div class="drone-card-body">';
            if (d.rssi) {
                html += '<div class="drone-detail">RSSI: <span>' + d.rssi + '</span></div>';
            }
            if (d.drone_altitude) {
                html += '<div class="drone-detail">Alt: <span>' + Number(d.drone_altitude).toFixed(0) + 'm</span></div>';
            }
            if (d.drone_lat && d.drone_long) {
                html += '<div class="drone-detail">Pos: <span>' +
                    Number(d.drone_lat).toFixed(3) + ', ' + Number(d.drone_long).toFixed(3) + '</span></div>';
            }
            if (d.basic_id) {
                html += '<div class="drone-detail">ID: <span>' + d.basic_id + '</span></div>';
            }
            html += '</div>';

            html += '</div>';
        });

        container.innerHTML = html;
    }

    function selectDrone(mac) {
        var droneData = DroneLayer.getData();
        var d = droneData[mac];
        if (!d) return;

        var lat = d.drone_lat || d.lat;
        var lng = d.drone_long || d.lng || d.lon;

        if (lat && lng) {
            MeshMap.flyTo(lng, lat, 15);
            DroneLayer.showDronePopup(mac);
        }
    }

    return {
        init: init,
        render: render,
        selectDrone: selectDrone
    };
})();
