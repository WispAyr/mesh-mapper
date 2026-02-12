/* ============================================
   Settings & Configuration Panel
   ============================================ */

window.SettingsPanel = (function() {
    'use strict';

    function init() {
        // Serial status updates
        MeshSocket.on('serial_status', handleSerialStatus);
        MeshSocket.on('connected', handleConnected);

        console.log('[SettingsPanel] Initialized');
    }

    function handleSerialStatus(data) {
        if (!data) return;

        var el = document.getElementById('serial-status');
        if (!el) return;

        // Check if any port is connected
        var anyConnected = false;
        var portInfo = [];

        if (typeof data === 'object') {
            Object.keys(data).forEach(function(port) {
                var status = data[port];
                if (status && (status.connected || status === 'connected' || status === true)) {
                    anyConnected = true;
                    portInfo.push(port.replace('/dev/', ''));
                }
            });
        }

        if (anyConnected) {
            el.innerHTML = '<span class="status-dot online"></span>Serial: ' + portInfo.join(', ');
        } else {
            el.innerHTML = '<span class="status-dot offline"></span>Serial: No ports';
        }
    }

    function handleConnected(data) {
        // Server confirmed connection — may include initial state info
        if (data && data.uptime) {
            var el = document.getElementById('uptime-display');
            if (el) el.textContent = 'Uptime: ' + formatUptime(data.uptime);
        }
    }

    function updateLastUpdate() {
        var el = document.getElementById('last-update');
        if (el) {
            el.textContent = 'Last update: ' + new Date().toLocaleTimeString();
        }
    }

    function formatUptime(seconds) {
        if (!seconds) return '—';
        var d = Math.floor(seconds / 86400);
        var h = Math.floor((seconds % 86400) / 3600);
        var m = Math.floor((seconds % 3600) / 60);
        if (d > 0) return d + 'd ' + h + 'h';
        if (h > 0) return h + 'h ' + m + 'm';
        return m + 'm';
    }

    return {
        init: init,
        updateLastUpdate: updateLastUpdate
    };
})();
