/**
 * Alert Dashboard — Live alert feed panel for mesh-mapper.
 * 
 * Listens for SocketIO 'alert_fired' events and displays them in a
 * sliding panel with severity badges and auto-dismiss support.
 * 
 * Integrates into the existing map UI as a collapsible side panel.
 */

(function() {
    'use strict';

    // ============================================================
    // Configuration
    // ============================================================
    const MAX_ALERTS_DISPLAYED = 50;
    const STATS_UPDATE_INTERVAL = 30000; // 30 seconds

    const SEVERITY_CONFIG = {
        emergency: { color: '#ff0000', bg: '#3a0000', icon: '🆘', label: 'EMERGENCY', priority: 4 },
        critical:  { color: '#ff4444', bg: '#2a0a0a', icon: '🚨', label: 'CRITICAL',  priority: 3 },
        warning:   { color: '#ffb347', bg: '#2a1f0a', icon: '⚠️', label: 'WARNING',   priority: 2 },
        info:      { color: '#4a9eff', bg: '#0a1a2a', icon: 'ℹ️', label: 'INFO',      priority: 1 },
        system:    { color: '#888888', bg: '#1a1a1a', icon: '⚙️', label: 'SYSTEM',    priority: 0 },
    };

    // ============================================================
    // State
    // ============================================================
    let alerts = [];
    let panelVisible = false;
    let unackedCount = 0;
    let socket = null;

    // ============================================================
    // Initialise
    // ============================================================
    function init() {
        createPanelHTML();
        createToggleButton();
        connectSocketIO();
        loadRecentAlerts();
        startStatsUpdater();

        console.log('[AlertDashboard] Initialised');
    }

    // ============================================================
    // SocketIO Connection
    // ============================================================
    function connectSocketIO() {
        // Use existing socket if available, otherwise create new
        if (typeof io !== 'undefined') {
            socket = io.connect ? io.connect() : io();

            socket.on('alert_fired', function(alert) {
                addAlert(alert);
                showToast(alert);
                playSound(alert);
            });

            socket.on('alert_acknowledged', function(data) {
                markAcknowledged(data.alert_id);
            });

            socket.on('alert_cleared', function(data) {
                if (data.alert_ids) {
                    data.alert_ids.forEach(function(id) {
                        removeAlert(id);
                    });
                }
            });

            socket.on('alert_stats_update', function(stats) {
                updateStatsDisplay(stats);
            });

            console.log('[AlertDashboard] SocketIO connected');
        } else {
            console.warn('[AlertDashboard] SocketIO not available, retrying in 2s');
            setTimeout(connectSocketIO, 2000);
        }
    }

    // ============================================================
    // Panel HTML
    // ============================================================
    function createPanelHTML() {
        const panel = document.createElement('div');
        panel.id = 'alert-dashboard-panel';
        panel.innerHTML = `
            <div class="alert-panel-header">
                <div class="alert-panel-title">
                    <span class="alert-panel-icon">🔔</span>
                    <span>Alerts</span>
                    <span id="alert-unacked-badge" class="alert-badge" style="display:none;">0</span>
                </div>
                <div class="alert-panel-actions">
                    <button id="alert-ack-all-btn" class="alert-btn" title="Acknowledge all">✓ All</button>
                    <button id="alert-close-btn" class="alert-btn" title="Close panel">✕</button>
                </div>
            </div>
            <div class="alert-panel-stats" id="alert-stats-bar">
                <span class="alert-stat" data-severity="emergency">🆘 0</span>
                <span class="alert-stat" data-severity="critical">🚨 0</span>
                <span class="alert-stat" data-severity="warning">⚠️ 0</span>
                <span class="alert-stat" data-severity="info">ℹ️ 0</span>
            </div>
            <div class="alert-panel-filters" id="alert-filters">
                <button class="alert-filter-btn active" data-filter="all">All</button>
                <button class="alert-filter-btn" data-filter="emergency">Emergency</button>
                <button class="alert-filter-btn" data-filter="critical">Critical</button>
                <button class="alert-filter-btn" data-filter="warning">Warning</button>
                <button class="alert-filter-btn" data-filter="info">Info</button>
            </div>
            <div class="alert-panel-feed" id="alert-feed">
                <div class="alert-empty">No alerts yet</div>
            </div>
        `;
        document.body.appendChild(panel);

        // Inject styles
        const style = document.createElement('style');
        style.textContent = getStyles();
        document.head.appendChild(style);

        // Event listeners
        document.getElementById('alert-close-btn').addEventListener('click', togglePanel);
        document.getElementById('alert-ack-all-btn').addEventListener('click', acknowledgeAll);

        // Filter buttons
        document.querySelectorAll('.alert-filter-btn').forEach(function(btn) {
            btn.addEventListener('click', function() {
                document.querySelectorAll('.alert-filter-btn').forEach(function(b) { b.classList.remove('active'); });
                btn.classList.add('active');
                filterAlerts(btn.dataset.filter);
            });
        });
    }

    function createToggleButton() {
        const btn = document.createElement('button');
        btn.id = 'alert-toggle-btn';
        btn.innerHTML = '🔔 <span id="alert-toggle-badge" style="display:none;">0</span>';
        btn.title = 'Toggle Alert Dashboard';
        btn.addEventListener('click', togglePanel);
        document.body.appendChild(btn);
    }

    // ============================================================
    // Panel Control
    // ============================================================
    function togglePanel() {
        panelVisible = !panelVisible;
        const panel = document.getElementById('alert-dashboard-panel');
        if (panel) {
            panel.classList.toggle('visible', panelVisible);
        }
    }

    // ============================================================
    // Alert Management
    // ============================================================
    function addAlert(alert) {
        // De-duplicate
        if (alerts.find(function(a) { return a.id === alert.id; })) return;

        alerts.unshift(alert);
        if (alerts.length > MAX_ALERTS_DISPLAYED) {
            alerts = alerts.slice(0, MAX_ALERTS_DISPLAYED);
        }

        if (!alert.acknowledged) {
            unackedCount++;
            updateBadge();
        }

        renderAlertItem(alert, true);
        updateEmptyState();
    }

    function removeAlert(alertId) {
        alerts = alerts.filter(function(a) { return a.id !== alertId; });
        var el = document.getElementById('alert-item-' + alertId);
        if (el) el.remove();
        updateEmptyState();
    }

    function markAcknowledged(alertId) {
        var alert = alerts.find(function(a) { return a.id === alertId; });
        if (alert && !alert.acknowledged) {
            alert.acknowledged = true;
            unackedCount = Math.max(0, unackedCount - 1);
            updateBadge();
        }
        var el = document.getElementById('alert-item-' + alertId);
        if (el) {
            el.classList.add('acknowledged');
        }
    }

    function acknowledgeAll() {
        fetch('/api/alerts/history/acknowledge-all', { method: 'POST' })
            .then(function(r) { return r.json(); })
            .then(function() {
                alerts.forEach(function(a) { a.acknowledged = true; });
                unackedCount = 0;
                updateBadge();
                document.querySelectorAll('.alert-item').forEach(function(el) {
                    el.classList.add('acknowledged');
                });
            })
            .catch(function(e) { console.error('[AlertDashboard] Error acknowledging:', e); });
    }

    function filterAlerts(filter) {
        document.querySelectorAll('.alert-item').forEach(function(el) {
            if (filter === 'all' || el.dataset.severity === filter) {
                el.style.display = '';
            } else {
                el.style.display = 'none';
            }
        });
    }

    // ============================================================
    // Rendering
    // ============================================================
    function renderAlertItem(alert, prepend) {
        var feed = document.getElementById('alert-feed');
        if (!feed) return;

        var sev = SEVERITY_CONFIG[alert.severity] || SEVERITY_CONFIG.info;
        var item = document.createElement('div');
        item.id = 'alert-item-' + alert.id;
        item.className = 'alert-item' + (alert.acknowledged ? ' acknowledged' : '');
        item.dataset.severity = alert.severity;
        item.style.borderLeftColor = sev.color;
        item.style.backgroundColor = sev.bg;

        var timeStr = alert.timestamp ? new Date(alert.timestamp).toLocaleTimeString() : '';

        item.innerHTML =
            '<div class="alert-item-header">' +
                '<span class="alert-severity-badge" style="background:' + sev.color + ';">' +
                    sev.icon + ' ' + sev.label +
                '</span>' +
                '<span class="alert-time">' + timeStr + '</span>' +
            '</div>' +
            '<div class="alert-item-title">' + escapeHtml(alert.title || '') + '</div>' +
            '<div class="alert-item-message">' + escapeHtml(alert.message || '') + '</div>' +
            '<div class="alert-item-meta">' +
                (alert.object_id ? '<span class="alert-meta-tag">' + escapeHtml(alert.object_id) + '</span>' : '') +
                (alert.event_type ? '<span class="alert-meta-tag">' + escapeHtml(alert.event_type) + '</span>' : '') +
            '</div>' +
            '<div class="alert-item-actions">' +
                '<button class="alert-item-btn alert-ack-btn" data-id="' + alert.id + '" title="Acknowledge">✓</button>' +
                (alert.lat && alert.lon ?
                    '<button class="alert-item-btn alert-goto-btn" data-lat="' + alert.lat + '" data-lon="' + alert.lon + '" title="Go to location">📍</button>'
                    : '') +
            '</div>';

        if (prepend) {
            feed.insertBefore(item, feed.firstChild);
        } else {
            feed.appendChild(item);
        }

        // Acknowledge button
        item.querySelector('.alert-ack-btn').addEventListener('click', function() {
            var id = this.dataset.id;
            fetch('/api/alerts/history/' + id + '/acknowledge', { method: 'POST' })
                .then(function() { markAcknowledged(id); })
                .catch(function(e) { console.error('[AlertDashboard] Ack error:', e); });
        });

        // Go-to button
        var gotoBtn = item.querySelector('.alert-goto-btn');
        if (gotoBtn) {
            gotoBtn.addEventListener('click', function() {
                var lat = parseFloat(this.dataset.lat);
                var lon = parseFloat(this.dataset.lon);
                if (window.map && lat && lon) {
                    window.map.flyTo({ center: [lon, lat], zoom: 14 });
                }
            });
        }

        // Auto-dismiss
        if (alert.auto_dismiss_seconds) {
            setTimeout(function() {
                var el = document.getElementById('alert-item-' + alert.id);
                if (el) {
                    el.style.opacity = '0.4';
                }
            }, alert.auto_dismiss_seconds * 1000);
        }
    }

    function updateEmptyState() {
        var feed = document.getElementById('alert-feed');
        var empty = feed.querySelector('.alert-empty');
        if (alerts.length === 0) {
            if (!empty) {
                var div = document.createElement('div');
                div.className = 'alert-empty';
                div.textContent = 'No alerts yet';
                feed.appendChild(div);
            }
        } else if (empty) {
            empty.remove();
        }
    }

    function updateBadge() {
        var badge = document.getElementById('alert-unacked-badge');
        var toggleBadge = document.getElementById('alert-toggle-badge');

        if (badge) {
            badge.textContent = unackedCount;
            badge.style.display = unackedCount > 0 ? '' : 'none';
        }
        if (toggleBadge) {
            toggleBadge.textContent = unackedCount;
            toggleBadge.style.display = unackedCount > 0 ? '' : 'none';
        }
    }

    function updateStatsDisplay(stats) {
        var bar = document.getElementById('alert-stats-bar');
        if (!bar) return;
        var spans = bar.querySelectorAll('.alert-stat');
        spans.forEach(function(span) {
            var sev = span.dataset.severity;
            var cfg = SEVERITY_CONFIG[sev];
            if (cfg && stats[sev] !== undefined) {
                span.textContent = cfg.icon + ' ' + stats[sev];
            }
        });
    }

    // ============================================================
    // Toast Notifications
    // ============================================================
    function showToast(alert) {
        var sev = SEVERITY_CONFIG[alert.severity] || SEVERITY_CONFIG.info;

        var toast = document.createElement('div');
        toast.className = 'alert-toast';
        toast.style.borderLeftColor = sev.color;
        toast.style.backgroundColor = sev.bg;
        toast.innerHTML =
            '<div class="alert-toast-header">' +
                '<span style="color:' + sev.color + ';">' + sev.icon + ' ' + sev.label + '</span>' +
                '<button class="alert-toast-close">✕</button>' +
            '</div>' +
            '<div class="alert-toast-title">' + escapeHtml(alert.title || '') + '</div>' +
            '<div class="alert-toast-message">' + escapeHtml(alert.message || '') + '</div>';

        var container = document.getElementById('alert-toast-container');
        if (!container) {
            container = document.createElement('div');
            container.id = 'alert-toast-container';
            document.body.appendChild(container);
        }
        container.appendChild(toast);

        // Animate in
        requestAnimationFrame(function() {
            toast.classList.add('visible');
        });

        // Close button
        toast.querySelector('.alert-toast-close').addEventListener('click', function() {
            toast.classList.remove('visible');
            setTimeout(function() { toast.remove(); }, 300);
        });

        // Auto-dismiss toast
        var dismissTime = (alert.auto_dismiss_seconds || 10) * 1000;
        setTimeout(function() {
            toast.classList.remove('visible');
            setTimeout(function() { toast.remove(); }, 300);
        }, dismissTime);
    }

    // ============================================================
    // Sound
    // ============================================================
    function playSound(alert) {
        var sound = alert.sound || 'default';
        if (sound === 'none') return;

        // Simple beep using Web Audio API
        try {
            var audioCtx = new (window.AudioContext || window.webkitAudioContext)();
            var oscillator = audioCtx.createOscillator();
            var gainNode = audioCtx.createGain();

            oscillator.connect(gainNode);
            gainNode.connect(audioCtx.destination);

            var freqMap = {
                'alert-emergency': 880,
                'alert-critical': 660,
                'alert-warning': 440,
                'alert-info': 330,
                'default': 440,
            };

            oscillator.frequency.value = freqMap[sound] || 440;
            oscillator.type = 'sine';
            gainNode.gain.value = 0.1;

            oscillator.start();
            oscillator.stop(audioCtx.currentTime + 0.15);
        } catch (e) {
            // Audio not available
        }
    }

    // ============================================================
    // API
    // ============================================================
    function loadRecentAlerts() {
        fetch('/api/alerts/history?limit=50')
            .then(function(r) { return r.json(); })
            .then(function(data) {
                var items = data.alerts || data || [];
                items.reverse().forEach(function(alert) {
                    addAlert(alert);
                });
            })
            .catch(function(e) {
                console.warn('[AlertDashboard] Could not load recent alerts:', e);
            });
    }

    function startStatsUpdater() {
        setInterval(function() {
            fetch('/api/alerts/stats')
                .then(function(r) { return r.json(); })
                .then(function(stats) {
                    updateStatsDisplay(stats);
                })
                .catch(function() {});
        }, STATS_UPDATE_INTERVAL);
    }

    // ============================================================
    // Utilities
    // ============================================================
    function escapeHtml(str) {
        var div = document.createElement('div');
        div.appendChild(document.createTextNode(str));
        return div.innerHTML;
    }

    // ============================================================
    // Styles
    // ============================================================
    function getStyles() {
        return `
            /* Alert Toggle Button */
            #alert-toggle-btn {
                position: fixed;
                top: 10px;
                right: 60px;
                z-index: 1000;
                background: rgba(20, 20, 30, 0.9);
                border: 1px solid rgba(74, 158, 255, 0.3);
                color: #fff;
                padding: 8px 14px;
                border-radius: 6px;
                cursor: pointer;
                font-size: 16px;
                backdrop-filter: blur(10px);
                transition: all 0.2s;
            }
            #alert-toggle-btn:hover {
                background: rgba(30, 30, 50, 0.95);
                border-color: rgba(74, 158, 255, 0.6);
            }
            #alert-toggle-badge {
                background: #ff4444;
                color: #fff;
                border-radius: 10px;
                padding: 1px 6px;
                font-size: 11px;
                margin-left: 4px;
                vertical-align: top;
            }

            /* Alert Panel */
            #alert-dashboard-panel {
                position: fixed;
                top: 0;
                right: -380px;
                width: 370px;
                height: 100vh;
                z-index: 999;
                background: rgba(15, 15, 25, 0.95);
                border-left: 1px solid rgba(74, 158, 255, 0.2);
                backdrop-filter: blur(20px);
                display: flex;
                flex-direction: column;
                transition: right 0.3s ease;
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                color: #e0e0e0;
            }
            #alert-dashboard-panel.visible {
                right: 0;
            }

            .alert-panel-header {
                display: flex;
                justify-content: space-between;
                align-items: center;
                padding: 12px 16px;
                border-bottom: 1px solid rgba(255,255,255,0.1);
            }
            .alert-panel-title {
                font-size: 16px;
                font-weight: 600;
                display: flex;
                align-items: center;
                gap: 8px;
            }
            .alert-panel-icon { font-size: 20px; }
            .alert-badge {
                background: #ff4444;
                color: #fff;
                border-radius: 10px;
                padding: 1px 7px;
                font-size: 11px;
            }
            .alert-panel-actions { display: flex; gap: 6px; }
            .alert-btn {
                background: rgba(255,255,255,0.08);
                border: 1px solid rgba(255,255,255,0.15);
                color: #ccc;
                padding: 4px 10px;
                border-radius: 4px;
                cursor: pointer;
                font-size: 12px;
            }
            .alert-btn:hover { background: rgba(255,255,255,0.15); color: #fff; }

            /* Stats Bar */
            .alert-panel-stats {
                display: flex;
                gap: 12px;
                padding: 8px 16px;
                border-bottom: 1px solid rgba(255,255,255,0.06);
                font-size: 12px;
            }
            .alert-stat { opacity: 0.7; }

            /* Filter Buttons */
            .alert-panel-filters {
                display: flex;
                gap: 4px;
                padding: 8px 16px;
                border-bottom: 1px solid rgba(255,255,255,0.06);
                flex-wrap: wrap;
            }
            .alert-filter-btn {
                background: rgba(255,255,255,0.05);
                border: 1px solid rgba(255,255,255,0.1);
                color: #999;
                padding: 3px 10px;
                border-radius: 12px;
                cursor: pointer;
                font-size: 11px;
            }
            .alert-filter-btn.active {
                background: rgba(74, 158, 255, 0.2);
                border-color: rgba(74, 158, 255, 0.4);
                color: #4a9eff;
            }

            /* Feed */
            .alert-panel-feed {
                flex: 1;
                overflow-y: auto;
                padding: 8px;
            }
            .alert-empty {
                text-align: center;
                color: #666;
                padding: 40px;
                font-size: 14px;
            }

            /* Alert Items */
            .alert-item {
                border-left: 3px solid #4a9eff;
                border-radius: 4px;
                margin-bottom: 6px;
                padding: 10px 12px;
                transition: opacity 0.3s;
                position: relative;
            }
            .alert-item.acknowledged { opacity: 0.5; }
            .alert-item-header {
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-bottom: 4px;
            }
            .alert-severity-badge {
                font-size: 10px;
                font-weight: 700;
                padding: 2px 8px;
                border-radius: 3px;
                color: #fff;
                text-transform: uppercase;
                letter-spacing: 0.5px;
            }
            .alert-time {
                font-size: 11px;
                color: #888;
            }
            .alert-item-title {
                font-size: 13px;
                font-weight: 600;
                margin-bottom: 2px;
            }
            .alert-item-message {
                font-size: 12px;
                color: #aaa;
                margin-bottom: 4px;
            }
            .alert-item-meta {
                display: flex;
                gap: 6px;
                flex-wrap: wrap;
            }
            .alert-meta-tag {
                font-size: 10px;
                background: rgba(255,255,255,0.06);
                padding: 1px 6px;
                border-radius: 3px;
                color: #999;
            }
            .alert-item-actions {
                position: absolute;
                top: 8px;
                right: 8px;
                display: flex;
                gap: 4px;
                opacity: 0;
                transition: opacity 0.2s;
            }
            .alert-item:hover .alert-item-actions { opacity: 1; }
            .alert-item-btn {
                background: rgba(255,255,255,0.1);
                border: none;
                color: #ccc;
                width: 24px;
                height: 24px;
                border-radius: 4px;
                cursor: pointer;
                font-size: 12px;
                display: flex;
                align-items: center;
                justify-content: center;
            }
            .alert-item-btn:hover { background: rgba(255,255,255,0.2); color: #fff; }

            /* Toast Container */
            #alert-toast-container {
                position: fixed;
                top: 60px;
                right: 16px;
                z-index: 10000;
                display: flex;
                flex-direction: column;
                gap: 8px;
                max-width: 360px;
            }
            .alert-toast {
                border-left: 4px solid #4a9eff;
                border-radius: 6px;
                padding: 12px 16px;
                backdrop-filter: blur(20px);
                box-shadow: 0 4px 20px rgba(0,0,0,0.5);
                opacity: 0;
                transform: translateX(100px);
                transition: all 0.3s ease;
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                color: #e0e0e0;
            }
            .alert-toast.visible {
                opacity: 1;
                transform: translateX(0);
            }
            .alert-toast-header {
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-bottom: 4px;
                font-size: 11px;
                font-weight: 700;
                text-transform: uppercase;
            }
            .alert-toast-close {
                background: none;
                border: none;
                color: #888;
                cursor: pointer;
                font-size: 14px;
            }
            .alert-toast-title {
                font-size: 13px;
                font-weight: 600;
                margin-bottom: 2px;
            }
            .alert-toast-message {
                font-size: 12px;
                color: #aaa;
            }

            /* Scrollbar */
            .alert-panel-feed::-webkit-scrollbar { width: 4px; }
            .alert-panel-feed::-webkit-scrollbar-track { background: transparent; }
            .alert-panel-feed::-webkit-scrollbar-thumb {
                background: rgba(255,255,255,0.15);
                border-radius: 2px;
            }
        `;
    }

    // ============================================================
    // Auto-init when DOM is ready
    // ============================================================
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    // Expose API for external use
    window.AlertDashboard = {
        toggle: togglePanel,
        addAlert: addAlert,
        acknowledgeAll: acknowledgeAll,
        getAlerts: function() { return alerts; },
    };

})();
