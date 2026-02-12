/* ============================================
   SocketIO Connection Management
   ============================================ */

window.MeshSocket = (function() {
    'use strict';

    let socket = null;
    let connected = false;
    let reconnectAttempts = 0;
    const maxReconnectAttempts = 50;
    const handlers = {};
    const connectCallbacks = [];
    const disconnectCallbacks = [];

    function init() {
        const protocol = window.location.protocol;
        const host = window.location.host;

        socket = io(protocol + '//' + host, {
            transports: ['websocket', 'polling'],
            reconnection: true,
            reconnectionDelay: 1000,
            reconnectionDelayMax: 10000,
            reconnectionAttempts: maxReconnectAttempts,
            timeout: 10000
        });

        socket.on('connect', function() {
            connected = true;
            reconnectAttempts = 0;
            console.log('[Socket] Connected:', socket.id);
            updateConnectionUI(true);
            connectCallbacks.forEach(function(cb) { cb(); });
        });

        socket.on('disconnect', function(reason) {
            connected = false;
            console.log('[Socket] Disconnected:', reason);
            updateConnectionUI(false);
            disconnectCallbacks.forEach(function(cb) { cb(reason); });
        });

        socket.on('reconnect_attempt', function(attempt) {
            reconnectAttempts = attempt;
            updateLoadingStatus('Reconnecting… (attempt ' + attempt + ')');
        });

        socket.on('reconnect_failed', function() {
            console.error('[Socket] Reconnection failed after', maxReconnectAttempts, 'attempts');
            updateConnectionUI(false, 'Connection failed');
        });

        // Register all event handlers
        registerCoreEvents();

        return socket;
    }

    function registerCoreEvents() {
        var events = [
            'connected', 'detection', 'detections', 'aliases',
            'serial_status', 'paths', 'cumulative_log', 'faa_cache',
            'adsb_aircraft', 'ais_vessels', 'ais_vessel_update',
            'aprs_stations', 'weather_data', 'webcams_data',
            'metoffice_warnings', 'lightning_strike', 'lightning_alert',
            'zones_updated', 'zone_event', 'new_incident', 'ports',
            'bt_device_found', 'bt_device_updated', 'bt_scan_complete',
            'bt_gatt_data', 'bt_hci_event', 'bt_test_status'
        ];

        events.forEach(function(event) {
            socket.on(event, function(data) {
                if (handlers[event]) {
                    handlers[event].forEach(function(cb) {
                        try { cb(data); }
                        catch (e) { console.error('[Socket] Handler error for', event, e); }
                    });
                }
            });
        });
    }

    function on(event, callback) {
        if (!handlers[event]) handlers[event] = [];
        handlers[event].push(callback);
    }

    function off(event, callback) {
        if (!handlers[event]) return;
        if (callback) {
            handlers[event] = handlers[event].filter(function(cb) { return cb !== callback; });
        } else {
            delete handlers[event];
        }
    }

    function onConnect(cb) { connectCallbacks.push(cb); }
    function onDisconnect(cb) { disconnectCallbacks.push(cb); }

    function emit(event, data) {
        if (socket && connected) {
            socket.emit(event, data);
        }
    }

    function isConnected() { return connected; }

    function updateConnectionUI(isConnected, message) {
        var el = document.getElementById('connection-status');
        var wsStatus = document.getElementById('websocket-status');
        if (!el) return;

        if (isConnected) {
            el.className = 'status-indicator connected';
            el.querySelector('.status-text').textContent = 'Connected';
            if (wsStatus) {
                wsStatus.innerHTML = '<span class="status-dot online"></span>WS: Connected';
            }
        } else {
            el.className = 'status-indicator disconnected';
            el.querySelector('.status-text').textContent = message || 'Disconnected';
            if (wsStatus) {
                wsStatus.innerHTML = '<span class="status-dot offline"></span>WS: Disconnected';
            }
        }
    }

    function updateLoadingStatus(text) {
        var el = document.getElementById('loading-status');
        if (el) el.textContent = text;
    }

    return {
        init: init,
        on: on,
        off: off,
        emit: emit,
        onConnect: onConnect,
        onDisconnect: onDisconnect,
        isConnected: isConnected
    };
})();
