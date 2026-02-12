/* ============================================
   REST API Client
   ============================================ */

window.MeshAPI = (function() {
    'use strict';

    const BASE = '';

    async function get(path) {
        try {
            const response = await fetch(BASE + path);
            if (!response.ok) throw new Error('HTTP ' + response.status);
            return await response.json();
        } catch (e) {
            console.error('[API] GET', path, 'failed:', e.message);
            return null;
        }
    }

    async function post(path, data) {
        try {
            const response = await fetch(BASE + path, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(data || {})
            });
            if (!response.ok) throw new Error('HTTP ' + response.status);
            return await response.json();
        } catch (e) {
            console.error('[API] POST', path, 'failed:', e.message);
            return null;
        }
    }

    // Core Detection
    function getDetections() { return get('/api/detections'); }
    function getRecentData() { return get('/api/recent_data'); }

    // ADS-B Aircraft
    function getAircraft() { return get('/api/adsb_aircraft'); }
    function getAdsbSettings() { return get('/api/adsb_settings'); }
    function setAdsbDetection(enabled) { return post('/api/adsb_detection', { enabled: enabled }); }

    // AIS Vessels
    function getVessels() { return get('/api/ais_vessels'); }
    function setAisDetection(enabled) { return post('/api/ais_detection', { enabled: enabled }); }

    // APRS
    function getAprsStations() { return get('/api/aprs_stations'); }
    function getAprsConfig() { return get('/api/aprs_config'); }
    function setAprsDetection(enabled) { return post('/api/aprs_detection', { enabled: enabled }); }
    function updateAprs(config) { return post('/api/aprs_update', config); }

    // Weather
    function getWeather() { return get('/api/weather'); }
    function getWeatherConfig() { return get('/api/weather_config'); }
    function setWeatherDetection(enabled) { return post('/api/weather_detection', { enabled: enabled }); }

    // Met Office Warnings
    function getMetOfficeWarnings() { return get('/api/metoffice_warnings'); }
    function setMetOfficeDetection(enabled) { return post('/api/metoffice_warnings_detection', { enabled: enabled }); }

    // Webcams
    function getWebcams() { return get('/api/webcams'); }
    function setWebcamsDetection(enabled) { return post('/api/webcams_detection', { enabled: enabled }); }

    // Zones
    function getZones() { return get('/api/zones'); }
    function createZone(zone) { return post('/api/zones', zone); }

    // Aliases
    function getAliases() { return get('/api/aliases'); }
    function setAlias(mac, alias) { return post('/api/set_alias', { mac: mac, alias: alias }); }

    // Serial / Ports
    function getSerialStatus() { return get('/api/serial_status'); }
    function getSelectedPorts() { return get('/api/selected_ports'); }

    // Diagnostics
    function getDiagnostics() { return get('/api/diagnostics'); }

    return {
        get: get,
        post: post,
        getDetections: getDetections,
        getRecentData: getRecentData,
        getAircraft: getAircraft,
        getAdsbSettings: getAdsbSettings,
        setAdsbDetection: setAdsbDetection,
        getVessels: getVessels,
        setAisDetection: setAisDetection,
        getAprsStations: getAprsStations,
        getAprsConfig: getAprsConfig,
        setAprsDetection: setAprsDetection,
        updateAprs: updateAprs,
        getWeather: getWeather,
        getWeatherConfig: getWeatherConfig,
        setWeatherDetection: setWeatherDetection,
        getMetOfficeWarnings: getMetOfficeWarnings,
        setMetOfficeDetection: setMetOfficeDetection,
        getWebcams: getWebcams,
        setWebcamsDetection: setWebcamsDetection,
        getZones: getZones,
        createZone: createZone,
        getAliases: getAliases,
        setAlias: setAlias,
        getSerialStatus: getSerialStatus,
        getSelectedPorts: getSelectedPorts,
        getDiagnostics: getDiagnostics
    };
})();
