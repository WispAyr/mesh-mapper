/**
 * Flow List — Management panel for all alert flows.
 * 
 * Displays a table of flows with enable/disable, edit, delete, duplicate.
 * Includes a template picker for creating new flows from templates.
 */

(function() {
    'use strict';

    const API_BASE = '/api/alerts';
    let flows = [];
    let templates = [];
    let listContainer = null;
    let isVisible = false;

    // ============================================================
    // Severity badges
    // ============================================================
    const SEVERITY_COLORS = {
        info:      { bg: '#0a1a2a', border: '#3b82f6', text: '#60a5fa' },
        warning:   { bg: '#2a1f0a', border: '#f59e0b', text: '#fbbf24' },
        critical:  { bg: '#2a0a0a', border: '#ef4444', text: '#f87171' },
        emergency: { bg: '#3a0000', border: '#ff0000', text: '#ff4444' },
        system:    { bg: '#1a1a1a', border: '#666',    text: '#999' }
    };

    // ============================================================
    // Initialise
    // ============================================================
    function init(containerEl) {
        listContainer = containerEl || document.getElementById('flow-list-panel');
        if (!listContainer) return;
        refresh();
    }

    // ============================================================
    // Refresh flow list
    // ============================================================
    async function refresh() {
        try {
            const [flowResp, tplResp] = await Promise.all([
                fetch(`${API_BASE}/flows`),
                fetch(`${API_BASE}/templates`)
            ]);

            if (flowResp.ok) {
                const data = await flowResp.json();
                flows = data.flows || [];
            }
            if (tplResp.ok) {
                const data = await tplResp.json();
                templates = data.templates || data || [];
                if (typeof templates === 'object' && !Array.isArray(templates)) {
                    templates = Object.values(templates);
                }
            }
        } catch (err) {
            console.error('[FlowList] Refresh error:', err);
        }

        render();
    }

    // ============================================================
    // Render
    // ============================================================
    function render() {
        if (!listContainer) return;

        let html = `
            <div class="flow-list">
                <div class="flow-list__header">
                    <h2 class="flow-list__title">
                        <span class="flow-list__icon">⚡</span>
                        Alert Flows
                        <span class="flow-list__count">${flows.length}</span>
                    </h2>
                    <div class="flow-list__actions">
                        <button class="fl-btn fl-btn--primary" onclick="FlowList.openNewFlow()">
                            + New Flow
                        </button>
                        <button class="fl-btn fl-btn--secondary" onclick="FlowList.showTemplatePicker()">
                            📋 From Template
                        </button>
                        <button class="fl-btn fl-btn--ghost" onclick="FlowList.refresh()">
                            ↻ Refresh
                        </button>
                    </div>
                </div>

                <div class="flow-list__table-wrap">
                    <table class="flow-list__table">
                        <thead>
                            <tr>
                                <th>Status</th>
                                <th>Name</th>
                                <th>Severity</th>
                                <th>Last Fired</th>
                                <th>Fires</th>
                                <th>Actions</th>
                            </tr>
                        </thead>
                        <tbody>
        `;

        if (flows.length === 0) {
            html += `
                <tr>
                    <td colspan="6" class="flow-list__empty">
                        <div class="flow-list__empty-content">
                            <span class="flow-list__empty-icon">📋</span>
                            <p>No alert flows configured</p>
                            <p class="flow-list__empty-hint">Create a new flow or start from a template</p>
                        </div>
                    </td>
                </tr>
            `;
        } else {
            for (const flow of flows) {
                const sev = SEVERITY_COLORS[flow.severity] || SEVERITY_COLORS.info;
                const lastFired = flow.last_fired_at
                    ? formatRelativeTime(flow.last_fired_at)
                    : '—';
                const fireCount = flow.fire_count || 0;

                html += `
                    <tr class="flow-list__row ${flow.enabled ? '' : 'flow-list__row--disabled'}">
                        <td>
                            <label class="flow-toggle" title="${flow.enabled ? 'Enabled' : 'Disabled'}">
                                <input type="checkbox" ${flow.enabled ? 'checked' : ''} 
                                    onchange="FlowList.toggleFlow('${flow.id}', this.checked)">
                                <span class="flow-toggle__track"></span>
                            </label>
                        </td>
                        <td>
                            <a href="/flow-editor/${flow.id}" class="flow-list__name">${escapeHtml(flow.name)}</a>
                            ${flow.template_id ? '<span class="flow-list__badge">template</span>' : ''}
                        </td>
                        <td>
                            <span class="severity-badge" style="background:${sev.bg};border-color:${sev.border};color:${sev.text}">
                                ${(flow.severity || 'info').toUpperCase()}
                            </span>
                        </td>
                        <td class="flow-list__time">${lastFired}</td>
                        <td class="flow-list__count-cell">${fireCount}</td>
                        <td class="flow-list__cell-actions">
                            <a href="/flow-editor/${flow.id}" class="fl-btn fl-btn--sm" title="Edit">✏️</a>
                            <button class="fl-btn fl-btn--sm" onclick="FlowList.duplicate('${flow.id}')" title="Duplicate">📋</button>
                            <button class="fl-btn fl-btn--sm fl-btn--danger" onclick="FlowList.deleteFlow('${flow.id}')" title="Delete">🗑️</button>
                        </td>
                    </tr>
                `;
            }
        }

        html += `
                        </tbody>
                    </table>
                </div>
            </div>
            <div id="template-picker-overlay" class="tpl-overlay" style="display:none"></div>
        `;

        listContainer.innerHTML = html;
    }

    // ============================================================
    // Template Picker Modal
    // ============================================================
    function showTemplatePicker() {
        const overlay = document.getElementById('template-picker-overlay') || createOverlay();

        // Group templates by category
        const grouped = {};
        for (const tpl of templates) {
            const cat = tpl.category || 'other';
            if (!grouped[cat]) grouped[cat] = [];
            grouped[cat].push(tpl);
        }

        const categoryIcons = {
            drone: '🛸', aircraft: '✈️', vessel: '⛴️',
            weather: '🌤️', system: '⚙️', other: '📋'
        };

        let html = `
            <div class="tpl-picker">
                <div class="tpl-picker__header">
                    <h3>Create Flow from Template</h3>
                    <button class="tpl-picker__close" onclick="FlowList.hideTemplatePicker()">✕</button>
                </div>
                <div class="tpl-picker__body">
        `;

        if (templates.length === 0) {
            html += '<div class="tpl-picker__empty">No templates available</div>';
        } else {
            for (const [cat, tpls] of Object.entries(grouped)) {
                html += `
                    <div class="tpl-category">
                        <h4 class="tpl-category__title">${categoryIcons[cat] || '📋'} ${cat.toUpperCase()}</h4>
                        <div class="tpl-category__grid">
                `;
                for (const tpl of tpls) {
                    const sev = SEVERITY_COLORS[tpl.severity] || SEVERITY_COLORS.info;
                    html += `
                        <div class="tpl-card" onclick="FlowList.useTemplate('${tpl.id}')">
                            <div class="tpl-card__icon">${tpl.icon || '📋'}</div>
                            <div class="tpl-card__info">
                                <div class="tpl-card__name">${escapeHtml(tpl.name)}</div>
                                <div class="tpl-card__desc">${escapeHtml(tpl.description || '')}</div>
                            </div>
                            <span class="severity-badge severity-badge--sm" 
                                style="background:${sev.bg};border-color:${sev.border};color:${sev.text}">
                                ${(tpl.severity || 'info').toUpperCase()}
                            </span>
                        </div>
                    `;
                }
                html += '</div></div>';
            }
        }

        html += '</div></div>';
        overlay.innerHTML = html;
        overlay.style.display = 'flex';
    }

    function hideTemplatePicker() {
        const overlay = document.getElementById('template-picker-overlay');
        if (overlay) overlay.style.display = 'none';
    }

    function createOverlay() {
        const overlay = document.createElement('div');
        overlay.id = 'template-picker-overlay';
        overlay.className = 'tpl-overlay';
        overlay.addEventListener('click', (e) => {
            if (e.target === overlay) hideTemplatePicker();
        });
        document.body.appendChild(overlay);
        return overlay;
    }

    async function useTemplate(templateId) {
        const tpl = templates.find(t => t.id === templateId);
        const name = prompt('Flow name:', tpl ? tpl.name : 'New Flow');
        if (!name) return;

        const flow = await FlowManager.createFromTemplate(templateId, name, {});
        if (flow) {
            hideTemplatePicker();
            // Navigate to the editor
            window.location.href = `/flow-editor/${flow.id}`;
        }
    }

    // ============================================================
    // Actions
    // ============================================================
    async function toggleFlow(flowId, enabled) {
        const ok = await FlowManager.toggleFlow(flowId, enabled);
        if (ok) refresh();
    }

    async function deleteFlow(flowId) {
        const ok = await FlowManager.deleteFlow(flowId);
        if (ok) refresh();
    }

    async function duplicate(flowId) {
        const flow = await FlowManager.duplicateFlow(flowId);
        if (flow) refresh();
    }

    function openNewFlow() {
        window.location.href = '/flow-editor';
    }

    // ============================================================
    // Helpers
    // ============================================================
    function formatRelativeTime(isoStr) {
        try {
            const date = new Date(isoStr);
            const now = new Date();
            const diffMs = now - date;
            const diffMins = Math.floor(diffMs / 60000);

            if (diffMins < 1) return 'just now';
            if (diffMins < 60) return diffMins + 'm ago';
            const diffHours = Math.floor(diffMins / 60);
            if (diffHours < 24) return diffHours + 'h ago';
            const diffDays = Math.floor(diffHours / 24);
            if (diffDays < 7) return diffDays + 'd ago';
            return date.toLocaleDateString();
        } catch (e) {
            return isoStr;
        }
    }

    function escapeHtml(s) {
        const div = document.createElement('div');
        div.textContent = s || '';
        return div.innerHTML;
    }

    function show() {
        isVisible = true;
        if (listContainer) listContainer.style.display = '';
        refresh();
    }

    function hide() {
        isVisible = false;
        if (listContainer) listContainer.style.display = 'none';
    }

    // ============================================================
    // Export
    // ============================================================
    window.FlowList = {
        init,
        refresh,
        render,
        show,
        hide,
        showTemplatePicker,
        hideTemplatePicker,
        useTemplate,
        toggleFlow,
        deleteFlow,
        duplicate,
        openNewFlow
    };

})();
