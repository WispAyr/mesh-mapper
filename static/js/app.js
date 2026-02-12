/* ============================================
   Mesh Mapper — Main Application
   ============================================ */

(function() {
    'use strict';

    // ============ State ============
    var startTime = Date.now();
    var layerModules = {
        drones: null,
        aircraft: null,
        vessels: null,
        aprs: null,
        weather: null,
        lightning: null,
        airspace: null,
        webcams: null,
        ble: null
    };

    // ============ Initialization ============
    function init() {
        console.log('[MeshMapper] Initializing…');
        updateLoadingStatus('Connecting to server…');

        // 1. Initialize Socket connection
        MeshSocket.init();

        // 2. Initialize Map
        var map = MeshMap.init();

        map.on('load', function() {
            console.log('[MeshMapper] Map loaded, initializing layers…');
            updateLoadingStatus('Loading data layers…');

            // 3. Initialize all layers
            DroneLayer.init();
            AircraftLayer.init();
            VesselLayer.init();
            AprsLayer.init();
            WeatherLayer.init();
            LightningLayer.init();
            AirspaceLayer.init();
            WebcamLayer.init();
            if (window.BLELayer) BLELayer.init();

            // 4. Initialize panels
            DronesPanel.init();
            AircraftPanel.init();
            SettingsPanel.init();

            // 5. Store layer module references
            layerModules = {
                drones: DroneLayer,
                aircraft: AircraftLayer,
                vessels: VesselLayer,
                aprs: AprsLayer,
                weather: WeatherLayer,
                lightning: LightningLayer,
                airspace: AirspaceLayer,
                webcams: WebcamLayer,
                ble: window.BLELayer || null
            };

            // 6. Setup UI handlers
            setupPanels();
            setupLayerToggles();
            setupKeyboardShortcuts();
            setupSectionToggles();
            setupTopBarButtons();

            // 7. Fetch initial data via REST
            fetchInitialData();

            // 8. Hide loading overlay
            setTimeout(function() {
                var overlay = document.getElementById('loading-overlay');
                if (overlay) overlay.classList.add('hidden');
            }, 500);

            // 9. Start update timer for bottom bar
            setInterval(updateBottomBar, 5000);

            console.log('[MeshMapper] ✓ Ready');
        });

        // Connection events
        MeshSocket.onConnect(function() {
            MeshAudio.connectionAlert();
            updateLoadingStatus('Connected — loading data…');
        });

        MeshSocket.onDisconnect(function() {
            MeshAudio.disconnectAlert();
        });
    }

    // ============ Fetch Initial Data via REST ============
    function fetchInitialData() {
        // Load recent data (fast bulk load)
        MeshAPI.getRecentData().then(function(data) {
            if (!data) return;
            if (data.detections) {
                MeshSocket.on('_internal_init', function(){});
                // Trigger handlers manually
                var handlers = {
                    detections: DroneLayer,
                    aliases: DroneLayer
                };
            }
            SettingsPanel.updateLastUpdate();
        });

        // Load zones via REST and feed directly to airspace layer
        MeshAPI.getZones().then(function(data) {
            if (data && data.zones) {
                AirspaceLayer.handleZones(data);
            }
        });
    }

    // ============ Panel Management ============
    function setupPanels() {
        // Left panel toggle
        document.getElementById('btn-left-panel').addEventListener('click', function() {
            togglePanel('left');
        });

        // Right panel toggle
        document.getElementById('btn-right-panel').addEventListener('click', function() {
            togglePanel('right');
        });

        // Close buttons
        document.querySelectorAll('.panel-close').forEach(function(btn) {
            btn.addEventListener('click', function() {
                var panelId = this.getAttribute('data-panel');
                if (panelId === 'left-panel') togglePanel('left', false);
                else if (panelId === 'right-panel') togglePanel('right', false);
            });
        });

        // Start with both panels open on desktop
        if (window.innerWidth > 768) {
            togglePanel('left', true);
            togglePanel('right', true);
        }
    }

    function togglePanel(side, forceState) {
        var panel, btnId, bodyClass;
        if (side === 'left') {
            panel = document.getElementById('left-panel');
            btnId = 'btn-left-panel';
            bodyClass = 'left-open';
        } else {
            panel = document.getElementById('right-panel');
            btnId = 'btn-right-panel';
            bodyClass = 'right-open';
        }

        if (!panel) return;

        var isCollapsed = panel.classList.contains('collapsed');
        var shouldOpen = forceState !== undefined ? forceState : isCollapsed;

        if (shouldOpen) {
            panel.classList.remove('collapsed');
            document.body.classList.add(bodyClass);
            document.getElementById(btnId).classList.add('active');
        } else {
            panel.classList.add('collapsed');
            document.body.classList.remove(bodyClass);
            document.getElementById(btnId).classList.remove('active');
        }

        // Trigger map resize after animation
        setTimeout(function() {
            var map = MeshMap.getMap();
            if (map) map.resize();
        }, 300);
    }

    // ============ Layer Toggles ============
    function setupLayerToggles() {
        document.querySelectorAll('.layer-toggle').forEach(function(toggle) {
            var layerName = toggle.getAttribute('data-layer');
            var checkbox = toggle.querySelector('input[type="checkbox"]');

            if (checkbox) {
                checkbox.addEventListener('change', function() {
                    var module = layerModules[layerName];
                    if (module && module.setVisible) {
                        module.setVisible(this.checked);
                    }
                });
            }
        });
    }

    // ============ Keyboard Shortcuts ============
    function setupKeyboardShortcuts() {
        document.addEventListener('keydown', function(e) {
            // Don't trigger if typing in an input
            if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;

            var key = e.key.toLowerCase();

            // Number keys 1-8 toggle layers
            var layerOrder = ['drones', 'aircraft', 'vessels', 'aprs', 'weather', 'lightning', 'airspace', 'webcams', 'ble'];
            var num = parseInt(key);
            if (num >= 1 && num <= 9) {
                var layerName = layerOrder[num - 1];
                var toggle = document.querySelector('.layer-toggle[data-layer="' + layerName + '"] input');
                if (toggle) {
                    toggle.checked = !toggle.checked;
                    toggle.dispatchEvent(new Event('change'));
                }
                return;
            }

            switch (key) {
                case 'l':
                    togglePanel('left');
                    break;
                case 'd':
                    togglePanel('right');
                    break;
                case 'a':
                    MeshAudio.toggle();
                    break;
                case 'f':
                    toggleFullscreen();
                    break;
                case 'escape':
                    MeshMap.removePopup();
                    break;
                case 'h':
                    // Home — fly to default center
                    MeshMap.flyTo(MeshMap.DEFAULT_CENTER[0], MeshMap.DEFAULT_CENTER[1], 6);
                    break;
            }
        });
    }

    // ============ Section Toggles ============
    function setupSectionToggles() {
        document.querySelectorAll('.section-title[data-toggle]').forEach(function(title) {
            title.addEventListener('click', function() {
                var targetId = this.getAttribute('data-toggle');
                var target = document.getElementById(targetId);
                if (target) {
                    target.classList.toggle('collapsed');
                    this.classList.toggle('collapsed');
                }
            });
        });
    }

    // ============ Top Bar Buttons ============
    function setupTopBarButtons() {
        // Audio toggle
        document.getElementById('btn-audio-toggle').addEventListener('click', function() {
            MeshAudio.toggle();
        });

        // Fullscreen
        document.getElementById('btn-fullscreen').addEventListener('click', function() {
            toggleFullscreen();
        });
    }

    function toggleFullscreen() {
        if (!document.fullscreenElement) {
            document.documentElement.requestFullscreen().catch(function(e) {
                console.warn('[App] Fullscreen not available:', e);
            });
        } else {
            document.exitFullscreen();
        }
    }

    // ============ Bottom Bar Updates ============
    function updateBottomBar() {
        // Uptime
        var elapsed = Math.floor((Date.now() - startTime) / 1000);
        var uptimeEl = document.getElementById('uptime-display');
        if (uptimeEl) {
            uptimeEl.textContent = 'Session: ' + formatDuration(elapsed);
        }

        // Last update
        SettingsPanel.updateLastUpdate();
    }

    function formatDuration(seconds) {
        var h = Math.floor(seconds / 3600);
        var m = Math.floor((seconds % 3600) / 60);
        var s = seconds % 60;
        if (h > 0) return h + 'h ' + m + 'm';
        if (m > 0) return m + 'm ' + s + 's';
        return s + 's';
    }

    // ============ Loading Status ============
    function updateLoadingStatus(text) {
        var el = document.getElementById('loading-status');
        if (el) el.textContent = text;
    }

    // ============ Start ============
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
