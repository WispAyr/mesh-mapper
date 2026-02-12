/* ============================================
   Station, BLE & GPS Settings Panel
   ============================================ */

window.StationPanel = (function() {
    'use strict';

    var config = {};
    var loaded = false;

    function init() {
        // Load config from server
        fetchConfig();

        // Wire up save buttons
        _on('click', 'btn-save-station', saveStation);
        _on('click', 'btn-save-gps', saveGPS);
        _on('click', 'btn-save-ble', saveBLE);
        _on('click', 'btn-clear-override', clearOverride);
        _on('click', 'btn-set-override', setOverride);

        // Wire up category toggles
        document.querySelectorAll('.ble-cat-toggle').forEach(function(cb) {
            cb.addEventListener('change', function() {
                // Auto-save category changes
                saveBLE();
            });
        });

        console.log('[StationPanel] Initialized');
    }

    function _on(event, id, handler) {
        var el = document.getElementById(id);
        if (el) el.addEventListener(event, handler);
    }

    function _val(id, newVal) {
        var el = document.getElementById(id);
        if (!el) return undefined;
        if (newVal !== undefined) {
            if (el.type === 'checkbox') el.checked = !!newVal;
            else el.value = newVal;
            return newVal;
        }
        if (el.type === 'checkbox') return el.checked;
        if (el.type === 'number') return parseFloat(el.value) || 0;
        return el.value;
    }

    function fetchConfig() {
        fetch('/api/ble_config').then(function(r) { return r.json(); }).then(function(data) {
            if (data && data.config) {
                config = data.config;
                populateForm();
                loaded = true;
            }
        }).catch(function(e) {
            console.warn('[StationPanel] Failed to load config:', e);
        });
    }

    function populateForm() {
        var c = config;
        var gps = c.gps || {};
        var station = c.station || {};
        var mmip = c.mmip || {};

        // Station
        _val('cfg-station-name', station.name || '');
        _val('cfg-station-label', station.label || '');
        _val('cfg-station-default-lat', station.default_lat || '');
        _val('cfg-station-default-lon', station.default_lon || '');
        _val('cfg-station-default-alt', station.default_alt || '');
        _val('cfg-station-manual', station.manual_override || false);
        _val('cfg-station-manual-lat', station.manual_lat || '');
        _val('cfg-station-manual-lon', station.manual_lon || '');
        _val('cfg-station-manual-alt', station.manual_alt || '');

        // GPS
        _val('cfg-gps-enabled', gps.enabled !== false);
        _val('cfg-gps-port', gps.serial_port || '/dev/ttyACM2');
        _val('cfg-gps-baud', gps.baud_rate || 9600);

        // BLE
        _val('cfg-ble-enabled', c.enabled !== false);
        _val('cfg-ble-port', c.serial_port || '/dev/ttyUSB0');
        _val('cfg-ble-baud', c.baud_rate || 921600);
        _val('cfg-ble-rssi', c.rssi_min || -100);
        _val('cfg-ble-stale', c.stale_timeout_seconds || 300);

        // Categories
        var cats = c.categories || {};
        ['drones','phones','trackers','vehicles','beacons','wearables','audio','unknown'].forEach(function(cat) {
            _val('cfg-cat-' + cat, cats[cat] !== false);
        });

        // MMIP
        _val('cfg-mmip-enabled', mmip.enabled || false);
        _val('cfg-mmip-source', mmip.source_id || '');
    }

    function saveStation() {
        var data = {
            station: {
                name: _val('cfg-station-name'),
                label: _val('cfg-station-label'),
                default_lat: parseFloat(_val('cfg-station-default-lat')) || 0,
                default_lon: parseFloat(_val('cfg-station-default-lon')) || 0,
                default_alt: parseFloat(_val('cfg-station-default-alt')) || 0,
                manual_override: _val('cfg-station-manual'),
                manual_lat: parseFloat(_val('cfg-station-manual-lat')) || 0,
                manual_lon: parseFloat(_val('cfg-station-manual-lon')) || 0,
                manual_alt: parseFloat(_val('cfg-station-manual-alt')) || 0,
            }
        };
        _postConfig(data, 'Station settings saved');
    }

    function saveGPS() {
        var data = {
            gps: {
                enabled: _val('cfg-gps-enabled'),
                serial_port: _val('cfg-gps-port'),
                baud_rate: parseInt(_val('cfg-gps-baud')) || 9600,
            }
        };
        _postConfig(data, 'GPS settings saved');
    }

    function saveBLE() {
        var cats = {};
        ['drones','phones','trackers','vehicles','beacons','wearables','audio','unknown'].forEach(function(cat) {
            cats[cat] = _val('cfg-cat-' + cat);
        });

        var data = {
            enabled: _val('cfg-ble-enabled'),
            serial_port: _val('cfg-ble-port'),
            baud_rate: parseInt(_val('cfg-ble-baud')) || 921600,
            rssi_min: parseInt(_val('cfg-ble-rssi')) || -100,
            stale_timeout_seconds: parseInt(_val('cfg-ble-stale')) || 300,
            categories: cats,
        };

        var mmipEnabled = _val('cfg-mmip-enabled');
        var mmipSource = _val('cfg-mmip-source');
        if (mmipEnabled !== undefined) {
            data.mmip = {
                enabled: mmipEnabled,
                source_id: mmipSource || config.mmip.source_id,
            };
        }

        _postConfig(data, 'BLE settings saved');
    }

    function setOverride() {
        var lat = parseFloat(_val('cfg-station-manual-lat'));
        var lon = parseFloat(_val('cfg-station-manual-lon'));
        var alt = parseFloat(_val('cfg-station-manual-alt')) || 0;
        if (!lat || !lon) {
            _flash('Enter lat/lon first', 'error');
            return;
        }
        fetch('/api/gps/override', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({lat: lat, lon: lon, alt: alt})
        }).then(function(r) { return r.json(); }).then(function(data) {
            _flash(data.message || 'Override set', 'ok');
            _val('cfg-station-manual', true);
        }).catch(function() { _flash('Failed', 'error'); });
    }

    function clearOverride() {
        fetch('/api/gps/override', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({clear: true})
        }).then(function(r) { return r.json(); }).then(function(data) {
            _flash(data.message || 'Override cleared', 'ok');
            _val('cfg-station-manual', false);
            _val('cfg-station-manual-lat', '');
            _val('cfg-station-manual-lon', '');
            _val('cfg-station-manual-alt', '');
        }).catch(function() { _flash('Failed', 'error'); });
    }

    function _postConfig(data, successMsg) {
        fetch('/api/ble_config', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(data)
        }).then(function(r) { return r.json(); }).then(function(resp) {
            if (resp.status === 'ok') {
                config = resp.config;
                _flash(successMsg, 'ok');
            } else {
                _flash(resp.message || 'Save failed', 'error');
            }
        }).catch(function() { _flash('Network error', 'error'); });
    }

    function _flash(msg, type) {
        var el = document.getElementById('station-flash');
        if (!el) return;
        el.textContent = msg;
        el.className = 'station-flash ' + (type || '');
        el.style.display = 'block';
        setTimeout(function() { el.style.display = 'none'; }, 3000);
    }

    return {
        init: init,
        fetchConfig: fetchConfig,
    };
})();
