/* ============================================
   Bluetooth Research Toolkit — Frontend
   Integrated tab/panel for mesh-mapper
   ============================================ */

window.BTToolkit = (function() {
    'use strict';

    // ---- State ----
    var scanDevices = {};
    var scanActive = false;
    var monitorActive = false;
    var sortField = 'rssi';
    var sortAsc = false;
    var gattConnected = false;
    var gattDevice = null;
    var gattServices = [];
    var activeTests = {};
    var hciEvents = [];
    var maxHCIEvents = 200;
    var classicDevices = [];
    var adapterInfo = [];
    var advStatus = {};
    var capabilities = {};

    // ---- Sub-panel state ----
    var activeSection = 'scanner';  // Default open section

    // ============ Init ============
    function init() {
        // Register SocketIO event handlers
        _registerSocketEvents();
        // Fetch initial state
        _fetchAdapters();
        _fetchCapabilities();
        console.log('[BTToolkit] Initialized');
    }

    // ============ Socket Events ============
    function _registerSocketEvents() {
        var sock = window.MeshSocket;
        if (!sock) return;

        sock.on('bt_device_found', function(data) {
            _updateDevice(data, true);
        });

        sock.on('bt_device_updated', function(data) {
            _updateDevice(data, false);
        });

        sock.on('bt_scan_complete', function(data) {
            scanActive = false;
            _updateScanUI();
            _showToast('Scan complete: ' + (data.device_count || 0) + ' devices found');
        });

        sock.on('bt_gatt_data', function(data) {
            _handleGATTData(data);
        });

        sock.on('bt_hci_event', function(data) {
            _addHCIEvent(data);
        });

        sock.on('bt_test_status', function(data) {
            _updateTestStatus(data);
        });
    }

    // ============ Data Handlers ============
    function _updateDevice(device, isNew) {
        if (!device || !device.address) return;
        scanDevices[device.address] = device;
        _renderScanTable();
    }

    function _handleGATTData(data) {
        if (!data) return;
        var gattLog = document.getElementById('bt-gatt-log');
        if (!gattLog) return;

        var entry = document.createElement('div');
        entry.className = 'bt-hci-line bt-hci-' + (data.type === 'notification' ? 'info' : 'debug');
        var ts = new Date().toLocaleTimeString();
        var valDisplay = data.value_hex || '';
        if (data.value_ascii && /^[\x20-\x7e]+$/.test(data.value_ascii)) {
            valDisplay += ' ("' + data.value_ascii + '")';
        }
        entry.innerHTML = '<span class="bt-hci-ts">' + ts + '</span> ' +
            '<span class="bt-hci-type">[' + (data.type || 'read').toUpperCase() + ']</span> ' +
            '<span class="bt-hci-uuid">' + (data.uuid || '') + '</span> → ' +
            '<span class="bt-hci-val">' + valDisplay + '</span>';
        gattLog.appendChild(entry);
        gattLog.scrollTop = gattLog.scrollHeight;
    }

    function _addHCIEvent(event) {
        hciEvents.push(event);
        if (hciEvents.length > maxHCIEvents) hciEvents.shift();
        _renderHCILog();
    }

    function _updateTestStatus(data) {
        if (!data) return;
        if (data.status === 'completed') {
            delete activeTests[data.test_id || data.type];
        } else {
            activeTests[data.type || data.test_id] = data;
        }
        _renderTestStatus();
    }

    // ============ API Calls ============
    function _fetchAdapters() {
        MeshAPI.get('/api/bt/adapters').then(function(data) {
            if (data && data.adapters) {
                adapterInfo = data.adapters;
                _renderAdapterStatus();
            }
        });
    }

    function _fetchCapabilities() {
        MeshAPI.get('/api/bt/adapters').then(function(data) {
            if (data && data.capabilities) {
                capabilities = data.capabilities;
            }
        });
    }

    function startScan() {
        if (scanActive) return;
        scanActive = true;

        var duration = parseFloat(document.getElementById('bt-scan-duration')?.value) || 30;
        var active = document.getElementById('bt-scan-active')?.checked !== false;

        scanDevices = {};
        _renderScanTable();
        _updateScanUI();

        MeshAPI.get('/api/bt/scan/start?duration=' + duration + '&active=' + active).then(function(data) {
            if (!data || data.status === 'error') {
                scanActive = false;
                _updateScanUI();
                _showToast('Scan failed: ' + (data?.message || 'unknown error'), 'error');
            }
        });
    }

    function stopScan() {
        MeshAPI.get('/api/bt/scan/stop').then(function() {
            scanActive = false;
            _updateScanUI();
        });
    }

    function startAdvertise() {
        var nameEl = document.getElementById('bt-adv-name');
        var presetEl = document.getElementById('bt-adv-preset');

        var config = {
            name: nameEl ? nameEl.value : 'MeshMapper',
            preset: presetEl ? presetEl.value : '',
        };

        // Parse service UUIDs
        var uuidsEl = document.getElementById('bt-adv-uuids');
        if (uuidsEl && uuidsEl.value.trim()) {
            config.service_uuids = uuidsEl.value.split(',').map(function(s) { return s.trim(); });
        }

        // Parse manufacturer data
        var mfrEl = document.getElementById('bt-adv-mfr');
        if (mfrEl && mfrEl.value.trim()) {
            try {
                config.manufacturer_data = JSON.parse(mfrEl.value);
            } catch(e) {
                _showToast('Invalid manufacturer data JSON', 'error');
                return;
            }
        }

        MeshAPI.post('/api/bt/advertise/start', config).then(function(data) {
            if (data && data.status === 'ok') {
                _showToast('Advertisement started');
                _fetchAdvStatus();
            } else {
                _showToast('Advertise failed: ' + (data?.message || ''), 'error');
            }
        });
    }

    function stopAdvertise() {
        MeshAPI.post('/api/bt/advertise/stop').then(function() {
            advStatus = {};
            _renderAdvStatus();
            _showToast('Advertising stopped');
        });
    }

    function _fetchAdvStatus() {
        MeshAPI.get('/api/bt/advertise/status').then(function(data) {
            if (data) {
                advStatus = data;
                _renderAdvStatus();
            }
        });
    }

    function gattConnect(address) {
        _showToast('Connecting to ' + address + '…');
        MeshAPI.post('/api/bt/gatt/connect', { address: address }).then(function(data) {
            if (data && data.status === 'ok') {
                gattConnected = true;
                gattDevice = address;
                _showToast('Connected to ' + address);
                gattEnumerateServices();
                _renderGATTPanel();
            } else {
                _showToast('Connect failed: ' + (data?.message || ''), 'error');
            }
        });
    }

    function gattDisconnect() {
        MeshAPI.post('/api/bt/gatt/disconnect').then(function() {
            gattConnected = false;
            gattDevice = null;
            gattServices = [];
            _renderGATTPanel();
            _showToast('Disconnected');
        });
    }

    function gattEnumerateServices() {
        MeshAPI.get('/api/bt/gatt/services').then(function(data) {
            if (data && data.services) {
                gattServices = data.services;
                _renderGATTServices();
            }
        });
    }

    function gattReadChar(charUuid) {
        MeshAPI.post('/api/bt/gatt/read', { char_uuid: charUuid }).then(function(data) {
            if (data && data.status === 'ok') {
                // Handled by socket event bt_gatt_data
                _handleGATTData(data);
            }
        });
    }

    function gattWriteChar(charUuid) {
        var input = document.getElementById('bt-gatt-write-' + charUuid.replace(/[^a-z0-9]/gi, '_'));
        if (!input || !input.value.trim()) {
            _showToast('Enter hex value to write', 'error');
            return;
        }
        MeshAPI.post('/api/bt/gatt/write', {
            char_uuid: charUuid,
            value_hex: input.value.trim()
        }).then(function(data) {
            if (data && data.status === 'ok') {
                _showToast('Written ' + data.length + ' bytes');
            } else {
                _showToast('Write failed: ' + (data?.message || ''), 'error');
            }
        });
    }

    function gattSubscribe(charUuid) {
        MeshAPI.post('/api/bt/gatt/subscribe', { char_uuid: charUuid }).then(function(data) {
            if (data && data.status === 'ok') {
                _showToast('Subscribed to notifications on ' + charUuid);
            } else {
                _showToast('Subscribe failed: ' + (data?.message || ''), 'error');
            }
        });
    }

    function classicDiscover() {
        _showToast('Starting classic BT discovery…');
        var btn = document.getElementById('bt-classic-scan-btn');
        if (btn) { btn.disabled = true; btn.textContent = 'Scanning…'; }

        MeshAPI.get('/api/bt/classic/discover').then(function(data) {
            if (btn) { btn.disabled = false; btn.textContent = '🔍 Discover'; }
            if (data && data.devices) {
                classicDevices = data.devices;
                _renderClassicDevices();
                _showToast('Found ' + data.devices.length + ' classic BT devices');
            }
        });
    }

    function classicSDP(address) {
        _showToast('SDP lookup: ' + address);
        MeshAPI.get('/api/bt/classic/sdp/' + encodeURIComponent(address)).then(function(data) {
            if (data && data.services) {
                _renderSDPResults(address, data.services);
            }
        });
    }

    function startMonitor() {
        if (monitorActive) return;
        monitorActive = true;
        hciEvents = [];
        _renderHCILog();
        _updateMonitorUI();

        var filters = [];
        document.querySelectorAll('.bt-monitor-filter:checked').forEach(function(cb) {
            filters.push(cb.value);
        });

        MeshAPI.get('/api/bt/monitor/start?filters=' + filters.join(',')).then(function(data) {
            if (!data || data.status === 'error') {
                monitorActive = false;
                _updateMonitorUI();
            }
        });
    }

    function stopMonitor() {
        MeshAPI.get('/api/bt/monitor/stop').then(function() {
            monitorActive = false;
            _updateMonitorUI();
        });
    }

    function startTest(testType) {
        var config = {};
        var timeoutEl = document.getElementById('bt-test-timeout');
        config.timeout = parseInt(timeoutEl?.value) || 30;

        if (testType === 'stress') {
            var targetEl = document.getElementById('bt-test-target');
            if (!targetEl || !targetEl.value.trim()) {
                _showToast('Enter target address for stress test', 'error');
                return;
            }
            config.target = targetEl.value.trim();
        }

        MeshAPI.post('/api/bt/test/' + testType, config).then(function(data) {
            if (data && data.status === 'ok') {
                _showToast('Test ' + testType + ' started');
                _renderTestStatus();
            } else {
                _showToast('Test failed: ' + (data?.message || ''), 'error');
            }
        });
    }

    function stopTests() {
        MeshAPI.post('/api/bt/test/stop').then(function() {
            activeTests = {};
            _renderTestStatus();
            _showToast('All tests stopped');
        });
    }

    function configureAdapter(adapterId) {
        var powered = document.getElementById('bt-adapter-power-' + adapterId)?.checked;
        var discoverable = document.getElementById('bt-adapter-disc-' + adapterId)?.checked;
        var pairable = document.getElementById('bt-adapter-pair-' + adapterId)?.checked;

        MeshAPI.post('/api/bt/adapters/' + adapterId + '/config', {
            powered: powered,
            discoverable: discoverable,
            pairable: pairable,
        }).then(function(data) {
            if (data && data.results) {
                _showToast('Adapter configured');
                setTimeout(_fetchAdapters, 500);
            }
        });
    }

    // ============ Rendering ============

    function renderPanel() {
        /**
         * Build the complete BT Toolkit panel HTML.
         * Called once when the tab is activated.
         */
        return '' +
        '<div id="bt-toolkit-panel" class="bt-toolkit">' +

        // ---- Flash / Toast ----
        '<div id="bt-toast" class="bt-toast" style="display:none"></div>' +

        // ---- Sub-navigation ----
        '<div class="bt-subnav">' +
            '<button class="bt-subnav-btn active" data-section="scanner" title="BLE Scanner">📡 Scanner</button>' +
            '<button class="bt-subnav-btn" data-section="advertiser" title="BLE Advertiser">📢 Advertise</button>' +
            '<button class="bt-subnav-btn" data-section="gatt" title="GATT Explorer">🔗 GATT</button>' +
            '<button class="bt-subnav-btn" data-section="classic" title="Classic BT">📻 Classic</button>' +
            '<button class="bt-subnav-btn" data-section="monitor" title="HCI Monitor">📊 Monitor</button>' +
            '<button class="bt-subnav-btn" data-section="tests" title="Resilience Tests">⚡ Tests</button>' +
        '</div>' +

        // ---- Adapter Status (always visible) ----
        '<div class="bt-section bt-adapter-section">' +
            '<div id="bt-adapter-cards" class="bt-adapter-cards"><div class="empty-state">Loading adapters…</div></div>' +
        '</div>' +

        // ---- Scanner Section ----
        '<div id="bt-section-scanner" class="bt-section bt-section-content">' +
            '<div class="bt-controls">' +
                '<button id="bt-scan-start" class="bt-btn bt-btn-primary" onclick="BTToolkit.startScan()">▶ Start Scan</button>' +
                '<button id="bt-scan-stop" class="bt-btn bt-btn-secondary" onclick="BTToolkit.stopScan()" disabled>⏹ Stop</button>' +
                '<label class="bt-control-label">Duration: <input type="number" id="bt-scan-duration" value="30" min="5" max="120" class="bt-input bt-input-sm"> s</label>' +
                '<label class="bt-control-label"><input type="checkbox" id="bt-scan-active" checked> Active scan</label>' +
                '<span id="bt-scan-status" class="bt-status-badge bt-status-idle">Idle</span>' +
                '<span id="bt-scan-count" class="bt-count-badge">0 devices</span>' +
            '</div>' +
            '<div class="bt-table-wrap">' +
                '<table class="bt-table" id="bt-scan-table">' +
                    '<thead><tr>' +
                        '<th class="bt-sortable" data-sort="address">Address</th>' +
                        '<th class="bt-sortable" data-sort="name">Name</th>' +
                        '<th class="bt-sortable bt-sort-active" data-sort="rssi">RSSI</th>' +
                        '<th>MFR</th>' +
                        '<th>Services</th>' +
                        '<th class="bt-sortable" data-sort="last_seen">Last Seen</th>' +
                        '<th>Actions</th>' +
                    '</tr></thead>' +
                    '<tbody id="bt-scan-tbody"><tr><td colspan="7" class="empty-state">No devices — start a scan</td></tr></tbody>' +
                '</table>' +
            '</div>' +
        '</div>' +

        // ---- Advertiser Section ----
        '<div id="bt-section-advertiser" class="bt-section bt-section-content" style="display:none">' +
            '<div class="bt-form-grid">' +
                '<div class="bt-form-group">' +
                    '<label>Device Name</label>' +
                    '<input type="text" id="bt-adv-name" class="bt-input" value="MeshMapper" placeholder="Advertised name">' +
                '</div>' +
                '<div class="bt-form-group">' +
                    '<label>Preset</label>' +
                    '<select id="bt-adv-preset" class="bt-input">' +
                        '<option value="">None (Custom)</option>' +
                        '<option value="ibeacon">iBeacon</option>' +
                        '<option value="eddystone">Eddystone-UID</option>' +
                    '</select>' +
                '</div>' +
                '<div class="bt-form-group">' +
                    '<label>Service UUIDs (comma-separated)</label>' +
                    '<input type="text" id="bt-adv-uuids" class="bt-input" placeholder="0000180f-0000-1000-8000-00805f9b34fb">' +
                '</div>' +
                '<div class="bt-form-group">' +
                    '<label>Manufacturer Data (JSON: {"company_id": "hex"})</label>' +
                    '<input type="text" id="bt-adv-mfr" class="bt-input" placeholder=\'{"76":"0215..."}\'>' +
                '</div>' +
            '</div>' +
            '<div class="bt-controls">' +
                '<button class="bt-btn bt-btn-primary" onclick="BTToolkit.startAdvertise()">📢 Start Advertising</button>' +
                '<button class="bt-btn bt-btn-danger" onclick="BTToolkit.stopAdvertise()">⏹ Stop All</button>' +
            '</div>' +
            '<div id="bt-adv-status" class="bt-status-box"></div>' +
        '</div>' +

        // ---- GATT Explorer Section ----
        '<div id="bt-section-gatt" class="bt-section bt-section-content" style="display:none">' +
            '<div class="bt-controls">' +
                '<span id="bt-gatt-device" class="bt-device-badge">Not connected</span>' +
                '<button id="bt-gatt-disconnect-btn" class="bt-btn bt-btn-secondary" onclick="BTToolkit.gattDisconnect()" disabled>Disconnect</button>' +
            '</div>' +
            '<div id="bt-gatt-services" class="bt-gatt-tree"><div class="empty-state">Connect to a device from the Scanner tab</div></div>' +
            '<div class="bt-gatt-log-header">GATT Log</div>' +
            '<div id="bt-gatt-log" class="bt-log-view"></div>' +
        '</div>' +

        // ---- Classic BT Section ----
        '<div id="bt-section-classic" class="bt-section bt-section-content" style="display:none">' +
            '<div class="bt-controls">' +
                '<button id="bt-classic-scan-btn" class="bt-btn bt-btn-primary" onclick="BTToolkit.classicDiscover()">🔍 Discover</button>' +
                '<span class="bt-hint">Scans for BR/EDR devices (~8s)</span>' +
            '</div>' +
            '<div id="bt-classic-devices" class="bt-classic-list"><div class="empty-state">No classic BT devices</div></div>' +
        '</div>' +

        // ---- HCI Monitor Section ----
        '<div id="bt-section-monitor" class="bt-section bt-section-content" style="display:none">' +
            '<div class="bt-controls">' +
                '<button id="bt-monitor-start" class="bt-btn bt-btn-primary" onclick="BTToolkit.startMonitor()">▶ Start Monitor</button>' +
                '<button id="bt-monitor-stop" class="bt-btn bt-btn-secondary" onclick="BTToolkit.stopMonitor()" disabled>⏹ Stop</button>' +
                '<span id="bt-monitor-status" class="bt-status-badge bt-status-idle">Idle</span>' +
            '</div>' +
            '<div class="bt-monitor-filters">' +
                '<label><input type="checkbox" class="bt-monitor-filter" value="connection" checked> Connections</label>' +
                '<label><input type="checkbox" class="bt-monitor-filter" value="advertising" checked> Advertising</label>' +
                '<label><input type="checkbox" class="bt-monitor-filter" value="error" checked> Errors</label>' +
                '<label><input type="checkbox" class="bt-monitor-filter" value="command"> Commands</label>' +
                '<label><input type="checkbox" class="bt-monitor-filter" value="event"> Events</label>' +
                '<label><input type="checkbox" class="bt-monitor-filter" value="acl_data"> ACL Data</label>' +
            '</div>' +
            '<div id="bt-hci-log" class="bt-log-view"></div>' +
        '</div>' +

        // ---- Resilience Tests Section ----
        '<div id="bt-section-tests" class="bt-section bt-section-content" style="display:none">' +
            '<div class="bt-test-warning">⚠️ Resilience tests are for testing YOUR OWN infrastructure. Use responsibly. All tests have automatic safety timeouts.</div>' +
            '<div class="bt-controls">' +
                '<label class="bt-control-label">Timeout: <input type="number" id="bt-test-timeout" value="30" min="5" max="60" class="bt-input bt-input-sm"> s (max 60)</label>' +
                '<label class="bt-control-label">Target (stress): <input type="text" id="bt-test-target" class="bt-input" placeholder="AA:BB:CC:DD:EE:FF"></label>' +
            '</div>' +
            '<div class="bt-test-grid">' +
                '<div class="bt-test-card">' +
                    '<div class="bt-test-title">📡 Adv Flood</div>' +
                    '<div class="bt-test-desc">Rapidly cycle random BLE advertisements</div>' +
                    '<button class="bt-btn bt-btn-warning" onclick="BTToolkit.startTest(\'adv-flood\')">Start</button>' +
                '</div>' +
                '<div class="bt-test-card">' +
                    '<div class="bt-test-title">🏷️ Name Rotation</div>' +
                    '<div class="bt-test-desc">Rapidly change BLE device name</div>' +
                    '<button class="bt-btn bt-btn-warning" onclick="BTToolkit.startTest(\'name-rotate\')">Start</button>' +
                '</div>' +
                '<div class="bt-test-card">' +
                    '<div class="bt-test-title">🔄 Conn Stress</div>' +
                    '<div class="bt-test-desc">Rapid connect/disconnect to target</div>' +
                    '<button class="bt-btn bt-btn-warning" onclick="BTToolkit.startTest(\'stress\')">Start</button>' +
                '</div>' +
                '<div class="bt-test-card">' +
                    '<div class="bt-test-title">📶 Channel Map</div>' +
                    '<div class="bt-test-desc">Read local RF channel assessment</div>' +
                    '<button class="bt-btn bt-btn-primary" onclick="BTToolkit._fetchChannelAssessment()">Read</button>' +
                '</div>' +
            '</div>' +
            '<div class="bt-controls" style="margin-top:8px">' +
                '<button class="bt-btn bt-btn-danger" onclick="BTToolkit.stopTests()">⏹ Stop All Tests</button>' +
            '</div>' +
            '<div id="bt-test-status" class="bt-status-box"></div>' +
            '<div id="bt-channel-map" class="bt-status-box" style="display:none"></div>' +
        '</div>' +

        '</div>';  // end bt-toolkit-panel
    }

    function attachEvents() {
        /**
         * Attach DOM event handlers after panel HTML is injected.
         */

        // Sub-navigation
        document.querySelectorAll('.bt-subnav-btn').forEach(function(btn) {
            btn.addEventListener('click', function() {
                var section = this.getAttribute('data-section');
                _switchSection(section);
            });
        });

        // Sortable table headers
        document.querySelectorAll('.bt-sortable').forEach(function(th) {
            th.addEventListener('click', function() {
                var field = this.getAttribute('data-sort');
                if (sortField === field) {
                    sortAsc = !sortAsc;
                } else {
                    sortField = field;
                    sortAsc = field === 'name' || field === 'address';  // alpha asc, rssi desc
                }
                document.querySelectorAll('.bt-sortable').forEach(function(h) { h.classList.remove('bt-sort-active'); });
                this.classList.add('bt-sort-active');
                _renderScanTable();
            });
        });
    }

    // ============ Section Switching ============
    function _switchSection(section) {
        activeSection = section;
        document.querySelectorAll('.bt-section-content').forEach(function(el) {
            el.style.display = 'none';
        });
        var target = document.getElementById('bt-section-' + section);
        if (target) target.style.display = '';

        document.querySelectorAll('.bt-subnav-btn').forEach(function(btn) {
            btn.classList.toggle('active', btn.getAttribute('data-section') === section);
        });
    }

    // ============ Render Functions ============

    function _renderAdapterStatus() {
        var container = document.getElementById('bt-adapter-cards');
        if (!container) return;

        if (!adapterInfo.length) {
            container.innerHTML = '<div class="empty-state">No Bluetooth adapters found</div>';
            return;
        }

        var html = '';
        adapterInfo.forEach(function(a) {
            var id = a.id || 'hci0';
            var powerClass = a.powered ? 'bt-status-active' : 'bt-status-idle';
            html += '<div class="bt-adapter-card">' +
                '<div class="bt-adapter-header">' +
                    '<span class="bt-adapter-id">' + _esc(id) + '</span>' +
                    '<span class="bt-status-badge ' + powerClass + '">' + (a.powered ? 'ON' : 'OFF') + '</span>' +
                '</div>' +
                '<div class="bt-adapter-info">' +
                    '<span>' + _esc(a.address || '—') + '</span>' +
                    '<span>' + _esc(a.name || '—') + '</span>' +
                    (a.le ? '<span class="bt-tag">LE</span>' : '') +
                    (a.bredr ? '<span class="bt-tag">BR/EDR</span>' : '') +
                '</div>' +
                '<div class="bt-adapter-controls">' +
                    '<label><input type="checkbox" id="bt-adapter-power-' + id + '"' + (a.powered ? ' checked' : '') + '> Power</label>' +
                    '<label><input type="checkbox" id="bt-adapter-disc-' + id + '"' + (a.discoverable ? ' checked' : '') + '> Discov</label>' +
                    '<label><input type="checkbox" id="bt-adapter-pair-' + id + '"' + (a.pairable ? ' checked' : '') + '> Pair</label>' +
                    '<button class="bt-btn bt-btn-sm" onclick="BTToolkit.configureAdapter(\'' + id + '\')">Apply</button>' +
                '</div>' +
            '</div>';
        });
        container.innerHTML = html;
    }

    function _renderScanTable() {
        var tbody = document.getElementById('bt-scan-tbody');
        if (!tbody) return;

        var devices = Object.values(scanDevices);
        var countEl = document.getElementById('bt-scan-count');
        if (countEl) countEl.textContent = devices.length + ' devices';

        if (!devices.length) {
            tbody.innerHTML = '<tr><td colspan="7" class="empty-state">' +
                (scanActive ? 'Scanning…' : 'No devices — start a scan') + '</td></tr>';
            return;
        }

        // Sort
        devices.sort(function(a, b) {
            var va = a[sortField] || '', vb = b[sortField] || '';
            if (typeof va === 'number' && typeof vb === 'number') {
                return sortAsc ? va - vb : vb - va;
            }
            va = String(va).toLowerCase();
            vb = String(vb).toLowerCase();
            return sortAsc ? va.localeCompare(vb) : vb.localeCompare(va);
        });

        var html = '';
        devices.forEach(function(d) {
            var rssiClass = d.rssi > -50 ? 'bt-rssi-strong' : d.rssi > -70 ? 'bt-rssi-medium' : 'bt-rssi-weak';
            var mfrStr = '';
            if (d.manufacturer_data) {
                Object.keys(d.manufacturer_data).forEach(function(cid) {
                    mfrStr += '<span class="bt-tag bt-tag-mfr">' + cid + '</span> ';
                });
            }
            var svcStr = (d.service_uuids || []).map(function(u) {
                return '<span class="bt-tag bt-tag-svc">' + _shortUUID(u) + '</span>';
            }).join(' ');

            var lastSeen = d.last_seen ? _timeAgo(d.last_seen) : '—';

            html += '<tr class="bt-device-row" data-address="' + _esc(d.address) + '">' +
                '<td class="bt-addr">' + _esc(d.address) + '</td>' +
                '<td class="bt-name">' + _esc(d.name || '—') + '</td>' +
                '<td class="' + rssiClass + '">' + (d.rssi || '—') + ' dBm</td>' +
                '<td>' + (mfrStr || '—') + '</td>' +
                '<td>' + (svcStr || '—') + '</td>' +
                '<td class="bt-time">' + lastSeen + '</td>' +
                '<td class="bt-actions">' +
                    '<button class="bt-btn bt-btn-xs" onclick="BTToolkit.gattConnect(\'' + _esc(d.address) + '\')" title="GATT Connect">🔗</button>' +
                '</td>' +
            '</tr>';
        });
        tbody.innerHTML = html;
    }

    function _updateScanUI() {
        var startBtn = document.getElementById('bt-scan-start');
        var stopBtn = document.getElementById('bt-scan-stop');
        var statusEl = document.getElementById('bt-scan-status');

        if (startBtn) startBtn.disabled = scanActive;
        if (stopBtn) stopBtn.disabled = !scanActive;
        if (statusEl) {
            statusEl.textContent = scanActive ? 'Scanning…' : 'Idle';
            statusEl.className = 'bt-status-badge ' + (scanActive ? 'bt-status-active' : 'bt-status-idle');
        }
    }

    function _renderAdvStatus() {
        var container = document.getElementById('bt-adv-status');
        if (!container) return;

        if (!advStatus.advertisements || !Object.keys(advStatus.advertisements).length) {
            container.innerHTML = '<div class="empty-state">No active advertisements</div>';
            return;
        }

        var html = '<div class="bt-adv-list">';
        Object.keys(advStatus.advertisements).forEach(function(id) {
            var adv = advStatus.advertisements[id];
            html += '<div class="bt-adv-item">' +
                '<span class="bt-tag bt-tag-svc">' + _esc(id) + '</span> ' +
                '<span>' + _esc(adv.name || '—') + '</span> ' +
                '<span class="bt-time">' + _esc(adv.type || '') + '</span>' +
            '</div>';
        });
        html += '</div>';
        container.innerHTML = html;
    }

    function _renderGATTPanel() {
        var deviceEl = document.getElementById('bt-gatt-device');
        var disconnBtn = document.getElementById('bt-gatt-disconnect-btn');

        if (deviceEl) {
            deviceEl.textContent = gattConnected ? '🔗 ' + gattDevice : 'Not connected';
            deviceEl.className = 'bt-device-badge' + (gattConnected ? ' bt-device-connected' : '');
        }
        if (disconnBtn) disconnBtn.disabled = !gattConnected;
    }

    function _renderGATTServices() {
        var container = document.getElementById('bt-gatt-services');
        if (!container) return;

        if (!gattServices.length) {
            container.innerHTML = '<div class="empty-state">No services found</div>';
            return;
        }

        var html = '';
        gattServices.forEach(function(svc, si) {
            html += '<div class="bt-gatt-service">' +
                '<div class="bt-gatt-svc-header" onclick="this.parentElement.classList.toggle(\'open\')">' +
                    '<span class="bt-gatt-arrow">▶</span> ' +
                    '<span class="bt-gatt-uuid">' + _esc(svc.uuid) + '</span>' +
                    ' <span class="bt-tag bt-tag-svc">' + svc.characteristics.length + ' chars</span>' +
                '</div>' +
                '<div class="bt-gatt-chars">';

            svc.characteristics.forEach(function(ch) {
                var props = (ch.properties || []).join(', ');
                var safeId = ch.uuid.replace(/[^a-z0-9]/gi, '_');
                html += '<div class="bt-gatt-char">' +
                    '<div class="bt-gatt-char-header">' +
                        '<span class="bt-gatt-uuid">' + _esc(ch.uuid) + '</span>' +
                        '<span class="bt-tag">' + _esc(props) + '</span>' +
                    '</div>' +
                    '<div class="bt-gatt-char-actions">';

                if (props.indexOf('read') >= 0) {
                    html += '<button class="bt-btn bt-btn-xs" onclick="BTToolkit.gattReadChar(\'' + _esc(ch.uuid) + '\')">Read</button>';
                }
                if (props.indexOf('write') >= 0) {
                    html += '<input type="text" id="bt-gatt-write-' + safeId + '" class="bt-input bt-input-xs" placeholder="hex">' +
                        '<button class="bt-btn bt-btn-xs" onclick="BTToolkit.gattWriteChar(\'' + _esc(ch.uuid) + '\')">Write</button>';
                }
                if (props.indexOf('notify') >= 0 || props.indexOf('indicate') >= 0) {
                    html += '<button class="bt-btn bt-btn-xs" onclick="BTToolkit.gattSubscribe(\'' + _esc(ch.uuid) + '\')">🔔 Sub</button>';
                }

                html += '</div></div>';
            });

            html += '</div></div>';
        });
        container.innerHTML = html;
    }

    function _renderClassicDevices() {
        var container = document.getElementById('bt-classic-devices');
        if (!container) return;

        if (!classicDevices.length) {
            container.innerHTML = '<div class="empty-state">No classic BT devices found</div>';
            return;
        }

        var html = '';
        classicDevices.forEach(function(d) {
            var dc = d.device_class || {};
            html += '<div class="bt-classic-device">' +
                '<div class="bt-classic-header">' +
                    '<span class="bt-addr">' + _esc(d.address) + '</span>' +
                    '<span class="bt-name">' + _esc(d.name || 'Unknown') + '</span>' +
                '</div>' +
                '<div class="bt-classic-info">' +
                    '<span class="bt-tag">' + _esc(dc.major || '?') + '</span>' +
                    '<span class="bt-tag">' + _esc(dc.minor || '?') + '</span>' +
                    (dc.services || []).map(function(s) { return '<span class="bt-tag bt-tag-svc">' + _esc(s) + '</span>'; }).join('') +
                '</div>' +
                '<div class="bt-classic-actions">' +
                    '<button class="bt-btn bt-btn-xs" onclick="BTToolkit.classicSDP(\'' + _esc(d.address) + '\')">SDP Lookup</button>' +
                '</div>' +
                '<div id="bt-sdp-' + d.address.replace(/:/g, '') + '" class="bt-sdp-results"></div>' +
            '</div>';
        });
        container.innerHTML = html;
    }

    function _renderSDPResults(address, services) {
        var container = document.getElementById('bt-sdp-' + address.replace(/:/g, ''));
        if (!container) return;

        if (!services.length) {
            container.innerHTML = '<div class="empty-state" style="padding:4px">No SDP services found</div>';
            return;
        }

        var html = '<div class="bt-sdp-list">';
        services.forEach(function(s) {
            html += '<div class="bt-sdp-item">' +
                '<span class="bt-tag bt-tag-svc">' + _esc(s.name || 'Unknown') + '</span> ' +
                (s.protocol ? '<span class="bt-tag">' + _esc(s.protocol) + '</span>' : '') +
                (s.channel ? ' Ch:' + _esc(s.channel) : '') +
                (s.description ? '<br><span class="bt-hint">' + _esc(s.description) + '</span>' : '') +
            '</div>';
        });
        html += '</div>';
        container.innerHTML = html;
    }

    function _renderHCILog() {
        var container = document.getElementById('bt-hci-log');
        if (!container) return;

        if (!hciEvents.length) {
            container.innerHTML = '<div class="empty-state">No HCI events</div>';
            return;
        }

        var html = '';
        hciEvents.forEach(function(evt) {
            var cls = 'bt-hci-' + (evt.severity || 'info');
            var dirIcon = evt.direction === 'out' ? '→' : evt.direction === 'in' ? '←' : '◆';
            var ts = new Date(evt.timestamp * 1000).toLocaleTimeString();
            html += '<div class="bt-hci-line ' + cls + '">' +
                '<span class="bt-hci-ts">' + ts + '</span> ' +
                '<span class="bt-hci-dir">' + dirIcon + '</span> ' +
                '<span class="bt-hci-type">[' + _esc(evt.type || '') + ']</span> ' +
                '<span class="bt-hci-content">' + _esc(evt.content || '') + '</span>' +
            '</div>';
        });
        container.innerHTML = html;
        container.scrollTop = container.scrollHeight;
    }

    function _updateMonitorUI() {
        var startBtn = document.getElementById('bt-monitor-start');
        var stopBtn = document.getElementById('bt-monitor-stop');
        var statusEl = document.getElementById('bt-monitor-status');

        if (startBtn) startBtn.disabled = monitorActive;
        if (stopBtn) stopBtn.disabled = !monitorActive;
        if (statusEl) {
            statusEl.textContent = monitorActive ? 'Monitoring…' : 'Idle';
            statusEl.className = 'bt-status-badge ' + (monitorActive ? 'bt-status-active' : 'bt-status-idle');
        }
    }

    function _renderTestStatus() {
        var container = document.getElementById('bt-test-status');
        if (!container) return;

        var keys = Object.keys(activeTests);
        if (!keys.length) {
            container.innerHTML = '<div class="empty-state">No active tests</div>';
            return;
        }

        var html = '';
        keys.forEach(function(k) {
            var t = activeTests[k];
            html += '<div class="bt-test-running">' +
                '<span class="bt-status-badge bt-status-active">⚡ ' + _esc(t.type || k) + '</span>' +
                ' Iterations: <strong>' + (t.iterations || 0) + '</strong>' +
                ' Elapsed: <strong>' + Math.round(t.elapsed || 0) + 's</strong>' +
                (t.current_name ? ' Name: ' + _esc(t.current_name) : '') +
                (t.last_error ? ' <span class="bt-hint text-danger">Error: ' + _esc(t.last_error) + '</span>' : '') +
            '</div>';
        });
        container.innerHTML = html;
    }

    function _fetchChannelAssessment() {
        MeshAPI.get('/api/bt/test/channel-assessment').then(function(data) {
            var container = document.getElementById('bt-channel-map');
            if (!container) return;
            container.style.display = '';

            if (data && data.status === 'ok') {
                container.innerHTML = '<div class="bt-channel-header">📶 Channel Assessment — ' + _esc(data.adapter || 'hci0') + '</div>' +
                    '<pre class="bt-pre">' + _esc(data.afh_response || 'N/A') + '</pre>' +
                    '<pre class="bt-pre">' + _esc(data.le_channel_response || 'N/A') + '</pre>';
            } else {
                container.innerHTML = '<div class="empty-state">Channel assessment failed: ' + _esc(data?.message || '') + '</div>';
            }
        });
    }

    // ============ Utilities ============
    function _esc(s) {
        if (!s) return '';
        var div = document.createElement('div');
        div.textContent = String(s);
        return div.innerHTML;
    }

    function _shortUUID(uuid) {
        if (!uuid) return '';
        // Show short form for standard 16-bit UUIDs
        var m = uuid.match(/^0000([0-9a-f]{4})-0000-1000-8000-00805f9b34fb$/i);
        return m ? '0x' + m[1].toUpperCase() : uuid.substring(0, 8) + '…';
    }

    function _timeAgo(ts) {
        var diff = Math.floor(Date.now() / 1000 - ts);
        if (diff < 5) return 'now';
        if (diff < 60) return diff + 's ago';
        if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
        return Math.floor(diff / 3600) + 'h ago';
    }

    function _showToast(msg, type) {
        var el = document.getElementById('bt-toast');
        if (!el) return;
        el.textContent = msg;
        el.className = 'bt-toast bt-toast-' + (type || 'ok');
        el.style.display = 'block';
        clearTimeout(el._timer);
        el._timer = setTimeout(function() { el.style.display = 'none'; }, 3000);
    }

    // ============ Public API ============
    return {
        init: init,
        renderPanel: renderPanel,
        attachEvents: attachEvents,
        startScan: startScan,
        stopScan: stopScan,
        startAdvertise: startAdvertise,
        stopAdvertise: stopAdvertise,
        gattConnect: gattConnect,
        gattDisconnect: gattDisconnect,
        gattReadChar: gattReadChar,
        gattWriteChar: gattWriteChar,
        gattSubscribe: gattSubscribe,
        classicDiscover: classicDiscover,
        classicSDP: classicSDP,
        startMonitor: startMonitor,
        stopMonitor: stopMonitor,
        startTest: startTest,
        stopTests: stopTests,
        configureAdapter: configureAdapter,
        _fetchChannelAssessment: _fetchChannelAssessment,
    };
})();
