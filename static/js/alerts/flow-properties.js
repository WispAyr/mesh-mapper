/**
 * Flow Property Editor — Dynamic property panel for selected nodes.
 * 
 * Renders form fields based on node type, handles validation,
 * and syncs changes back to Drawflow node data.
 */

(function() {
    'use strict';

    let currentNodeId = null;
    let currentNodeType = null;
    let editorInstance = null;
    let zonesCache = null;

    // ============================================================
    // Initialise
    // ============================================================
    function init(editor) {
        editorInstance = editor;
        loadZones();
    }

    // ============================================================
    // Load zones for geofence dropdown
    // ============================================================
    async function loadZones() {
        try {
            // Try the alert zones API first, then fall back to main zones
            let resp = await fetch('/api/alerts/zones');
            if (resp.ok) {
                const data = await resp.json();
                zonesCache = data.zones || data || [];
                return;
            }
        } catch(e) {}
        
        try {
            let resp = await fetch('/api/zones');
            if (resp.ok) {
                const data = await resp.json();
                zonesCache = (data.zones || data || []).map(z => ({
                    id: z.id || z.zone_id,
                    name: z.name || z.label || z.id || 'Unnamed'
                }));
                return;
            }
        } catch(e) {}

        zonesCache = [];
    }

    // ============================================================
    // Show properties for a node
    // ============================================================
    function showProperties(nodeId) {
        const panel = document.getElementById('property-panel-body');
        if (!panel) return;

        // Get node data from Drawflow
        const nodeData = editorInstance.getNodeFromId(nodeId);
        if (!nodeData) {
            clearProperties();
            return;
        }

        currentNodeId = nodeId;
        // The node type is stored in the node's data
        currentNodeType = nodeData.data ? nodeData.data.node_type : null;

        if (!currentNodeType) {
            // Try to extract from the HTML
            const match = nodeData.html && nodeData.html.match(/data-node-type="([^"]+)"/);
            if (match) currentNodeType = match[1];
        }

        if (!currentNodeType) {
            panel.innerHTML = '<div class="prop-empty">Select a node to edit properties</div>';
            return;
        }

        const nodeDef = FlowNodes.NODE_TYPES[currentNodeType];
        if (!nodeDef) {
            panel.innerHTML = '<div class="prop-empty">Unknown node type</div>';
            return;
        }

        const config = (nodeData.data && nodeData.data.config) || {};
        const cat = FlowNodes.CATEGORIES[nodeDef.category];

        let html = `
            <div class="prop-header" style="border-left: 3px solid ${cat.color}">
                <span class="prop-header__icon">${nodeDef.icon}</span>
                <div class="prop-header__info">
                    <div class="prop-header__name">${nodeDef.name}</div>
                    <div class="prop-header__type">${currentNodeType}</div>
                </div>
            </div>
            <div class="prop-desc">${nodeDef.description}</div>
        `;

        const propEntries = Object.entries(nodeDef.properties);
        if (propEntries.length === 0) {
            html += '<div class="prop-empty-fields">No configurable properties</div>';
        } else {
            html += '<div class="prop-fields">';
            for (const [key, prop] of propEntries) {
                const value = config[key] !== undefined ? config[key] : prop.default;
                html += renderField(key, prop, value);
            }
            html += '</div>';
        }

        // Template variables hint
        if (hasTemplateFields(nodeDef)) {
            html += `
                <div class="prop-vars">
                    <div class="prop-vars__title">📝 Template Variables</div>
                    <div class="prop-vars__list">
                        ${FlowNodes.TEMPLATE_VARS.map(v => 
                            `<span class="prop-var" onclick="FlowProperties.insertVar(this, '${v}')" title="Click to copy">${v}</span>`
                        ).join('')}
                    </div>
                </div>
            `;
        }

        // Delete node button
        html += `
            <div class="prop-actions">
                <button class="prop-btn prop-btn--delete" onclick="FlowProperties.deleteNode()">
                    🗑️ Delete Node
                </button>
            </div>
        `;

        panel.innerHTML = html;

        // Attach change listeners
        panel.querySelectorAll('[data-prop-key]').forEach(el => {
            const eventType = (el.type === 'checkbox') ? 'change' : 'input';
            el.addEventListener(eventType, handlePropertyChange);
        });

        // For multiselect checkboxes
        panel.querySelectorAll('.prop-multiselect input[type="checkbox"]').forEach(el => {
            el.addEventListener('change', handleMultiselectChange);
        });
    }

    // ============================================================
    // Render a single field
    // ============================================================
    function renderField(key, prop, value) {
        let html = `<div class="prop-field" data-field-key="${key}">`;
        html += `<label class="prop-field__label">${prop.label || key}</label>`;

        switch (prop.type) {
            case 'text':
                html += `<input type="text" class="prop-input" data-prop-key="${key}" 
                    value="${escapeAttr(value || '')}" 
                    placeholder="${escapeAttr(prop.placeholder || '')}">`;
                break;

            case 'number':
                html += `<input type="number" class="prop-input" data-prop-key="${key}" 
                    value="${value !== undefined ? value : ''}" 
                    ${prop.min !== undefined ? `min="${prop.min}"` : ''}
                    ${prop.max !== undefined ? `max="${prop.max}"` : ''}
                    ${prop.step !== undefined ? `step="${prop.step}"` : ''}
                    placeholder="${escapeAttr(prop.placeholder || '')}">`;
                break;

            case 'select':
                html += `<select class="prop-input" data-prop-key="${key}">`;
                for (const opt of (prop.options || [])) {
                    const selected = String(value) === String(opt.value) ? ' selected' : '';
                    html += `<option value="${escapeAttr(opt.value)}"${selected}>${escapeHtml(opt.label)}</option>`;
                }
                html += '</select>';
                break;

            case 'zone_select':
                html += `<select class="prop-input" data-prop-key="${key}">`;
                html += '<option value="">— Select Zone —</option>';
                if (zonesCache && zonesCache.length > 0) {
                    for (const zone of zonesCache) {
                        const zid = zone.id || zone.zone_id;
                        const zname = zone.name || zone.label || zid;
                        const selected = String(value) === String(zid) ? ' selected' : '';
                        html += `<option value="${escapeAttr(zid)}"${selected}>${escapeHtml(zname)}</option>`;
                    }
                } else {
                    html += '<option value="" disabled>No zones available</option>';
                }
                html += '</select>';
                break;

            case 'checkbox':
                const checked = value ? ' checked' : '';
                html += `<label class="prop-checkbox">
                    <input type="checkbox" data-prop-key="${key}"${checked}>
                    <span class="prop-checkbox__text">${prop.label}</span>
                </label>`;
                break;

            case 'textarea':
                html += `<textarea class="prop-input prop-textarea" data-prop-key="${key}" 
                    placeholder="${escapeAttr(prop.placeholder || '')}" rows="3">${escapeHtml(value || '')}</textarea>`;
                break;

            case 'template':
                html += `<textarea class="prop-input prop-textarea prop-template" data-prop-key="${key}" 
                    placeholder="${escapeAttr(prop.placeholder || '')}" rows="3">${escapeHtml(value || '')}</textarea>`;
                break;

            case 'multiselect':
                html += '<div class="prop-multiselect" data-prop-key-group="' + key + '">';
                const selectedVals = Array.isArray(value) ? value : [];
                for (const opt of (prop.options || [])) {
                    const isChecked = selectedVals.includes(opt.value) ? ' checked' : '';
                    html += `<label class="prop-multi-option">
                        <input type="checkbox" value="${escapeAttr(opt.value)}" data-multi-key="${key}"${isChecked}>
                        <span>${escapeHtml(opt.label)}</span>
                    </label>`;
                }
                html += '</div>';
                break;

            default:
                html += `<input type="text" class="prop-input" data-prop-key="${key}" 
                    value="${escapeAttr(value || '')}">`;
        }

        html += '</div>';
        return html;
    }

    // ============================================================
    // Handle property changes
    // ============================================================
    function handlePropertyChange(e) {
        if (!currentNodeId || !editorInstance) return;

        const key = e.target.dataset.propKey;
        if (!key) return;

        let value;
        if (e.target.type === 'checkbox') {
            value = e.target.checked;
        } else if (e.target.type === 'number') {
            value = e.target.value !== '' ? parseFloat(e.target.value) : null;
        } else {
            value = e.target.value;
        }

        updateNodeConfig(key, value);
    }

    function handleMultiselectChange(e) {
        if (!currentNodeId || !editorInstance) return;
        const key = e.target.dataset.multiKey;
        if (!key) return;

        const container = e.target.closest('.prop-multiselect');
        const checked = container.querySelectorAll('input:checked');
        const values = Array.from(checked).map(cb => cb.value);
        updateNodeConfig(key, values);
    }

    function updateNodeConfig(key, value) {
        const nodeData = editorInstance.getNodeFromId(currentNodeId);
        if (!nodeData || !nodeData.data) return;

        if (!nodeData.data.config) nodeData.data.config = {};
        nodeData.data.config[key] = value;

        // Update the Drawflow internal data
        editorInstance.updateNodeDataFromId(currentNodeId, nodeData.data);

        // Dispatch custom event for flow-editor to handle
        document.dispatchEvent(new CustomEvent('flow-property-changed', {
            detail: { nodeId: currentNodeId, key, value }
        }));
    }

    // ============================================================
    // Clear properties
    // ============================================================
    function clearProperties() {
        currentNodeId = null;
        currentNodeType = null;
        const panel = document.getElementById('property-panel-body');
        if (panel) {
            panel.innerHTML = '<div class="prop-empty">Select a node to edit its properties</div>';
        }
    }

    // ============================================================
    // Delete current node
    // ============================================================
    function deleteNode() {
        if (!currentNodeId || !editorInstance) return;
        if (confirm('Delete this node?')) {
            editorInstance.removeNodeId('node-' + currentNodeId);
            clearProperties();
        }
    }

    // ============================================================
    // Insert template variable
    // ============================================================
    function insertVar(el, varName) {
        // Find the focused template textarea or the last one
        const panel = document.getElementById('property-panel-body');
        const textareas = panel.querySelectorAll('.prop-template');
        const focused = document.activeElement;
        
        let target = null;
        if (focused && focused.classList.contains('prop-template')) {
            target = focused;
        } else if (textareas.length > 0) {
            target = textareas[textareas.length - 1];
        }

        if (target) {
            const start = target.selectionStart;
            const end = target.selectionEnd;
            const text = target.value;
            target.value = text.substring(0, start) + varName + text.substring(end);
            target.selectionStart = target.selectionEnd = start + varName.length;
            target.focus();
            target.dispatchEvent(new Event('input'));
        }

        // Visual feedback
        el.style.background = '#00d4ff33';
        setTimeout(() => { el.style.background = ''; }, 300);
    }

    // ============================================================
    // Helpers: check if node has template fields
    // ============================================================
    function hasTemplateFields(nodeDef) {
        return Object.values(nodeDef.properties).some(p => p.type === 'template');
    }

    // ============================================================
    // Helpers: escape
    // ============================================================
    function escapeAttr(s) {
        return String(s).replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    function escapeHtml(s) {
        const div = document.createElement('div');
        div.textContent = s;
        return div.innerHTML;
    }

    // ============================================================
    // Export
    // ============================================================
    window.FlowProperties = {
        init,
        showProperties,
        clearProperties,
        deleteNode,
        insertVar,
        loadZones
    };

})();
