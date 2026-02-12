/* ============================================
   Aircraft & Ships Panel (Left Panel Data Lists)
   ============================================ */

window.AircraftPanel = (function() {
    'use strict';

    function init() {
        MeshSocket.on('adsb_aircraft', renderAircraft);
        MeshSocket.on('ais_vessels', renderVessels);
        MeshSocket.on('aprs_stations', renderAprs);
        MeshSocket.on('metoffice_warnings', renderWeather);
        console.log('[AircraftPanel] Initialized');
    }

    function renderAircraft() {
        var data = AircraftLayer.getData();
        var container = document.getElementById('aircraft-list');
        if (!container) return;

        var keys = Object.keys(data);
        if (keys.length === 0) {
            container.innerHTML = '<div class="empty-state">No aircraft data</div>';
            return;
        }

        // Sort by altitude descending, show top 50
        keys.sort(function(a, b) {
            var altA = data[a].alt_baro || data[a].altitude || 0;
            var altB = data[b].alt_baro || data[b].altitude || 0;
            return altB - altA;
        });

        var html = '';
        keys.slice(0, 50).forEach(function(key) {
            var ac = data[key];
            var callsign = (ac.flight || ac.callsign || '').trim();
            var alt = ac.alt_baro || ac.alt_geom || ac.altitude || 0;
            var lat = ac.lat || ac.latitude;
            var lng = ac.lon || ac.lng || ac.longitude;

            html += '<div class="entity-item" onclick="AircraftPanel.selectAircraft(\'' + key + '\')">';
            html += '<span class="entity-icon">✈️</span>';
            html += '<span class="entity-name">' + (callsign || ac.hex || key) + '</span>';
            html += '<span class="entity-detail">' + formatAlt(alt) + '</span>';
            html += '</div>';
        });

        if (keys.length > 50) {
            html += '<div class="empty-state">+ ' + (keys.length - 50) + ' more</div>';
        }

        container.innerHTML = html;
    }

    function renderVessels() {
        var data = VesselLayer.getData();
        var container = document.getElementById('vessel-list');
        if (!container) return;

        var keys = Object.keys(data);
        if (keys.length === 0) {
            container.innerHTML = '<div class="empty-state">No vessel data</div>';
            return;
        }

        var html = '';
        keys.slice(0, 50).forEach(function(key) {
            var v = data[key];
            var name = v.name || v.ship_name || ('MMSI: ' + (v.mmsi || key));
            var speed = v.speed || v.sog || 0;

            html += '<div class="entity-item" onclick="AircraftPanel.selectVessel(\'' + key + '\')">';
            html += '<span class="entity-icon">🚢</span>';
            html += '<span class="entity-name">' + name + '</span>';
            html += '<span class="entity-detail">' + (speed ? speed + ' kts' : '—') + '</span>';
            html += '</div>';
        });

        if (keys.length > 50) {
            html += '<div class="empty-state">+ ' + (keys.length - 50) + ' more</div>';
        }

        container.innerHTML = html;
    }

    function renderAprs() {
        var data = AprsLayer.getData();
        var container = document.getElementById('aprs-list');
        if (!container) return;

        var keys = Object.keys(data);
        if (keys.length === 0) {
            container.innerHTML = '<div class="empty-state">No APRS data</div>';
            return;
        }

        var html = '';
        keys.forEach(function(key) {
            var s = data[key];
            html += '<div class="entity-item" onclick="AircraftPanel.selectAprs(\'' + key + '\')">';
            html += '<span class="entity-icon">📡</span>';
            html += '<span class="entity-name">' + (s.callsign || s.name || key) + '</span>';
            html += '<span class="entity-detail">' + (s.comment || '').substring(0, 20) + '</span>';
            html += '</div>';
        });

        container.innerHTML = html;
    }

    function renderWeather() {
        var warnings = WeatherLayer.getWarnings();
        var container = document.getElementById('weather-list');
        if (!container) return;

        if (!Array.isArray(warnings) || warnings.length === 0) {
            container.innerHTML = '<div class="empty-state">No warnings</div>';
            return;
        }

        var html = '';
        warnings.forEach(function(w) {
            var severity = (w.severity || w.level || '').toLowerCase();
            var icon = '🟡';
            if (severity.includes('red')) icon = '🔴';
            else if (severity.includes('amber')) icon = '🟠';

            html += '<div class="entity-item">';
            html += '<span class="entity-icon">' + icon + '</span>';
            html += '<span class="entity-name">' + (w.title || w.headline || 'Warning') + '</span>';
            html += '</div>';
        });

        container.innerHTML = html;
    }

    function selectAircraft(key) {
        var data = AircraftLayer.getData();
        var ac = data[key];
        if (!ac) return;
        var lat = ac.lat || ac.latitude;
        var lng = ac.lon || ac.lng || ac.longitude;
        if (lat && lng) MeshMap.flyTo(lng, lat, 12);
    }

    function selectVessel(key) {
        var data = VesselLayer.getData();
        var v = data[key];
        if (!v) return;
        var lat = v.lat || v.latitude;
        var lng = v.lon || v.lng || v.longitude;
        if (lat && lng) MeshMap.flyTo(lng, lat, 12);
    }

    function selectAprs(key) {
        var data = AprsLayer.getData();
        var s = data[key];
        if (!s) return;
        var lat = s.lat || s.latitude;
        var lng = s.lng || s.lon || s.longitude;
        if (lat && lng) MeshMap.flyTo(lng, lat, 12);
    }

    function formatAlt(alt) {
        if (!alt || alt === 'ground') return 'GND';
        return Number(alt).toLocaleString() + 'ft';
    }

    return {
        init: init,
        renderAircraft: renderAircraft,
        renderVessels: renderVessels,
        renderAprs: renderAprs,
        renderWeather: renderWeather,
        selectAircraft: selectAircraft,
        selectVessel: selectVessel,
        selectAprs: selectAprs
    };
})();
