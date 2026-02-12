/**
 * Flow Manager — Save, load, convert, and manage alert flows.
 * 
 * Handles conversion between Drawflow's internal JSON format and
 * the alert_engine.py flow format used by the backend.
 */

(function() {
    'use strict';

    const API_BASE = '/api/alerts';
    let editorInstance = null;
    let currentFlowId = null;
    let currentFlowMeta = null;
    let isDirty = false;

    // ============================================================
    // Initialise
    // ============================================================
    function init(editor) {
        editorInstance = editor;

        // Check URL for flow ID to load
        const pathParts = window.location.pathname.split('/');
        const flowIdIdx = pathParts.indexOf('flow-editor');
        if (flowIdIdx >= 0 && pathParts[flowIdIdx + 1]) {
            loadFlow(pathParts[flowIdIdx + 1]);
        }

        // Track changes
        editor.on('nodeCreated', () => setDirty(true));
        editor.on('nodeRemoved', () => setDirty(true));
        editor.on('connectionCreated', () => setDirty(true));
        editor.on('connectionRemoved', () => setDirty(true));
        editor.on('nodeMoved', () => setDirty(true));
        document.addEventListener('flow-property-changed', () => setDirty(true));

        // Warn on unsaved changes
        window.addEventListener('beforeunload', (e) => {
            if (isDirty) {
                e.preventDefault();
                e.returnValue = 'You have unsaved changes.';
            }
        });
    }

    // ============================================================
    // Dirty state tracking
    // ============================================================
    function setDirty(dirty) {
        isDirty = dirty;
        const indicator = document.getElementById('save-indicator');
        if (indicator) {
            indicator.textContent = dirty ? '● Unsaved' : '';
            indicator.className = dirty ? 'save-indicator unsaved' : 'save-indicator';
        }
    }

    // ============================================================
    // Convert: Drawflow JSON → Alert Engine format
    // ============================================================
    function drawflowToEngine(drawflowData, meta) {
        const nodes = [];
        const edges = [];

        // Drawflow stores modules > Home > data > {nodeId: nodeData}
        const moduleData = drawflowData.drawflow && drawflowData.drawflow.Home
            ? drawflowData.drawflow.Home.data
            : {};

        for (const [dfNodeId, dfNode] of Object.entries(moduleData)) {
            const nodeData = dfNode.data || {};
            const nodeType = nodeData.node_type || '';

            // Determine engine type and subtype
            const [category, subtype] = nodeType.split('.');
            let engineNode = {
                id: 'n' + dfNodeId,
                type: category,
                config: nodeData.config || {},
                position: { x: dfNode.pos_x, y: dfNode.pos_y }
            };

            // Add subtype field based on category
            if (category === 'trigger') {
                engineNode.trigger_type = nodeType.replace('trigger.', '');
                // Map event_filter to event for engine compat
                if (engineNode.config.event_filter) {
                    engineNode.config.event = engineNode.config.event_filter;
                }
            } else if (category === 'condition') {
                engineNode.condition_type = subtype;
            } else if (category === 'action') {
                engineNode.action_type = subtype;
                // Map template fields to engine format
                if (engineNode.config.title_template !== undefined) {
                    engineNode.config.title = engineNode.config.title_template;
                }
                if (engineNode.config.message_template !== undefined) {
                    engineNode.config.message = engineNode.config.message_template;
                }
                if (engineNode.config.payload_template !== undefined) {
                    engineNode.config.payload = engineNode.config.payload_template;
                }
            }

            nodes.push(engineNode);

            // Extract edges from outputs
            for (const [outputKey, connections] of Object.entries(dfNode.outputs || {})) {
                for (const conn of (connections.connections || [])) {
                    edges.push({
                        from: 'n' + dfNodeId,
                        to: 'n' + conn.node
                    });
                }
            }
        }

        return {
            id: currentFlowId || undefined,
            name: meta.name || 'Untitled Flow',
            description: meta.description || '',
            enabled: meta.enabled !== undefined ? meta.enabled : true,
            severity: meta.severity || 'warning',
            cooldown_seconds: meta.cooldown_seconds || 300,
            nodes,
            edges
        };
    }

    // ============================================================
    // Convert: Alert Engine format → Drawflow JSON
    // ============================================================
    function engineToDrawflow(engineFlow) {
        const drawflowData = { drawflow: { Home: { data: {} } } };
        const moduleData = drawflowData.drawflow.Home.data;

        // Map engine node IDs to Drawflow numeric IDs
        const idMap = {};
        let nextId = 1;

        for (const node of (engineFlow.nodes || [])) {
            const dfId = nextId++;
            idMap[node.id] = dfId;

            // Determine the flow-nodes.js type key
            let nodeType = '';
            if (node.type === 'trigger') {
                nodeType = node.trigger_type 
                    ? (node.trigger_type.includes('.') ? node.trigger_type : 'trigger.' + node.trigger_type)
                    : 'trigger.drone';
            } else if (node.type === 'condition') {
                nodeType = 'condition.' + (node.condition_type || 'geofence');
            } else if (node.type === 'action') {
                nodeType = 'action.' + (node.action_type || 'ui_alert');
            }

            const nodeDef = FlowNodes.NODE_TYPES[nodeType];
            if (!nodeDef) continue;

            // Build config with defaults filled in
            const config = { ...FlowNodes.getDefaultConfig(nodeType), ...(node.config || {}) };

            // Reverse-map engine field names to editor field names
            if (config.event && !config.event_filter) {
                config.event_filter = config.event;
            }
            if (config.title && !config.title_template) {
                config.title_template = config.title;
            }
            if (config.message && !config.message_template) {
                config.message_template = config.message;
            }
            if (config.payload && !config.payload_template) {
                config.payload_template = config.payload;
            }

            moduleData[dfId] = {
                id: dfId,
                name: nodeType,
                data: { node_type: nodeType, config },
                class: nodeType,
                html: FlowNodes.generateNodeHTML(nodeType, dfId),
                typenode: false,
                inputs: {},
                outputs: {},
                pos_x: (node.position && node.position.x) || 100,
                pos_y: (node.position && node.position.y) || 100
            };

            // Set up input/output ports
            for (let i = 1; i <= nodeDef.inputs; i++) {
                moduleData[dfId].inputs['input_' + i] = { connections: [] };
            }
            for (let i = 1; i <= nodeDef.outputs; i++) {
                moduleData[dfId].outputs['output_' + i] = { connections: [] };
            }
        }

        // Wire up edges
        for (const edge of (engineFlow.edges || [])) {
            const fromDfId = idMap[edge.from];
            const toDfId = idMap[edge.to];
            if (!fromDfId || !toDfId) continue;

            const fromNode = moduleData[fromDfId];
            const toNode = moduleData[toDfId];
            if (!fromNode || !toNode) continue;

            // Find first available output/input
            const outputKey = Object.keys(fromNode.outputs)[0];
            const inputKey = Object.keys(toNode.inputs)[0];
            if (!outputKey || !inputKey) continue;

            fromNode.outputs[outputKey].connections.push({
                node: String(toDfId),
                output: inputKey
            });
            toNode.inputs[inputKey].connections.push({
                node: String(fromDfId),
                input: outputKey
            });
        }

        return drawflowData;
    }

    // ============================================================
    // Save flow
    // ============================================================
    async function saveFlow() {
        if (!editorInstance) return;

        const nameInput = document.getElementById('flow-name');
        const meta = getFlowMeta();

        if (!meta.name || meta.name.trim() === '') {
            showNotification('Please enter a flow name', 'error');
            if (nameInput) nameInput.focus();
            return;
        }

        const drawflowData = editorInstance.export();
        const engineFlow = drawflowToEngine(drawflowData, meta);

        // Store the flow_json that the backend expects
        const payload = {
            name: engineFlow.name,
            description: engineFlow.description,
            enabled: engineFlow.enabled,
            severity: engineFlow.severity,
            cooldown_seconds: engineFlow.cooldown_seconds,
            flow_json: JSON.stringify({
                nodes: engineFlow.nodes,
                edges: engineFlow.edges
            }),
            nodes: engineFlow.nodes,
            edges: engineFlow.edges
        };

        try {
            let resp;
            if (currentFlowId) {
                resp = await fetch(`${API_BASE}/flows/${currentFlowId}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
            } else {
                resp = await fetch(`${API_BASE}/flows`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
            }

            if (!resp.ok) {
                const err = await resp.json();
                throw new Error(err.error || 'Save failed');
            }

            const saved = await resp.json();
            currentFlowId = saved.id;
            currentFlowMeta = saved;

            // Update URL without reload
            if (window.history.pushState) {
                window.history.pushState({}, '', `/flow-editor/${currentFlowId}`);
            }

            setDirty(false);
            showNotification('Flow saved successfully', 'success');
            return saved;

        } catch (err) {
            showNotification('Save failed: ' + err.message, 'error');
            console.error('[FlowManager] Save error:', err);
            return null;
        }
    }

    // ============================================================
    // Load flow
    // ============================================================
    async function loadFlow(flowId) {
        try {
            const resp = await fetch(`${API_BASE}/flows/${flowId}`);
            if (!resp.ok) throw new Error('Flow not found');

            const flow = await resp.json();
            currentFlowId = flow.id;
            currentFlowMeta = flow;

            // Parse flow_json if it's a string
            let flowDef = flow;
            if (flow.flow_json) {
                const parsed = typeof flow.flow_json === 'string'
                    ? JSON.parse(flow.flow_json)
                    : flow.flow_json;
                flowDef = { ...flow, nodes: parsed.nodes, edges: parsed.edges };
            }

            // Update UI
            setFlowMeta(flow);

            // Convert and import into Drawflow
            const drawflowData = engineToDrawflow(flowDef);
            editorInstance.clear();
            editorInstance.import(drawflowData);

            setDirty(false);
            showNotification('Flow loaded: ' + flow.name, 'success');

        } catch (err) {
            showNotification('Load failed: ' + err.message, 'error');
            console.error('[FlowManager] Load error:', err);
        }
    }

    // ============================================================
    // Test flow
    // ============================================================
    async function testFlow() {
        if (!editorInstance) return;

        const meta = getFlowMeta();
        const drawflowData = editorInstance.export();
        const engineFlow = drawflowToEngine(drawflowData, meta);

        // Validate
        const triggerNodes = engineFlow.nodes.filter(n => n.type === 'trigger');
        const actionNodes = engineFlow.nodes.filter(n => n.type === 'action');

        if (triggerNodes.length === 0) {
            showNotification('Flow must have at least one trigger node', 'error');
            return;
        }
        if (actionNodes.length === 0) {
            showNotification('Flow must have at least one action node', 'error');
            return;
        }
        if (engineFlow.edges.length === 0) {
            showNotification('Nodes must be connected', 'error');
            return;
        }

        try {
            const endpoint = currentFlowId
                ? `${API_BASE}/flows/${currentFlowId}/test`
                : `${API_BASE}/test`;

            const resp = await fetch(endpoint, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(engineFlow)
            });

            const result = await resp.json();
            if (resp.ok) {
                showNotification('Test passed ✓ ' + (result.message || ''), 'success');
            } else {
                showNotification('Test failed: ' + (result.error || 'Unknown'), 'error');
            }
        } catch (err) {
            showNotification('Test error: ' + err.message, 'error');
        }
    }

    // ============================================================
    // Delete flow
    // ============================================================
    async function deleteFlow(flowId) {
        if (!confirm('Delete this flow? This cannot be undone.')) return false;

        try {
            const resp = await fetch(`${API_BASE}/flows/${flowId}`, { method: 'DELETE' });
            if (!resp.ok) throw new Error('Delete failed');
            showNotification('Flow deleted', 'success');
            return true;
        } catch (err) {
            showNotification('Delete failed: ' + err.message, 'error');
            return false;
        }
    }

    // ============================================================
    // Toggle flow enabled/disabled
    // ============================================================
    async function toggleFlow(flowId, enabled) {
        try {
            const action = enabled ? 'enable' : 'disable';
            const resp = await fetch(`${API_BASE}/flows/${flowId}/${action}`, { method: 'POST' });
            if (!resp.ok) throw new Error(`${action} failed`);
            return true;
        } catch (err) {
            showNotification(err.message, 'error');
            return false;
        }
    }

    // ============================================================
    // List flows
    // ============================================================
    async function listFlows() {
        try {
            const resp = await fetch(`${API_BASE}/flows`);
            if (!resp.ok) throw new Error('Failed to load flows');
            const data = await resp.json();
            return data.flows || [];
        } catch (err) {
            console.error('[FlowManager] List error:', err);
            return [];
        }
    }

    // ============================================================
    // List templates
    // ============================================================
    async function listTemplates() {
        try {
            const resp = await fetch(`${API_BASE}/templates`);
            if (!resp.ok) throw new Error('Failed to load templates');
            const data = await resp.json();
            return data.templates || data || [];
        } catch (err) {
            console.error('[FlowManager] Templates error:', err);
            return [];
        }
    }

    // ============================================================
    // Create flow from template
    // ============================================================
    async function createFromTemplate(templateId, name, parameters) {
        try {
            const resp = await fetch(`${API_BASE}/flows`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    template_id: templateId,
                    name: name || undefined,
                    parameters: parameters || {}
                })
            });

            if (!resp.ok) {
                const err = await resp.json();
                throw new Error(err.error || 'Failed');
            }

            const flow = await resp.json();
            showNotification('Flow created from template', 'success');
            return flow;
        } catch (err) {
            showNotification('Template error: ' + err.message, 'error');
            return null;
        }
    }

    // ============================================================
    // Duplicate flow
    // ============================================================
    async function duplicateFlow(flowId) {
        try {
            const resp = await fetch(`${API_BASE}/flows/${flowId}`);
            if (!resp.ok) throw new Error('Flow not found');

            const flow = await resp.json();

            // Create copy with modified name
            const copy = {
                name: flow.name + ' (Copy)',
                description: flow.description,
                enabled: false,
                severity: flow.severity,
                cooldown_seconds: flow.cooldown_seconds,
                flow_json: flow.flow_json,
                nodes: flow.nodes,
                edges: flow.edges
            };

            const saveResp = await fetch(`${API_BASE}/flows`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(copy)
            });

            if (!saveResp.ok) throw new Error('Duplicate failed');
            const saved = await saveResp.json();
            showNotification('Flow duplicated', 'success');
            return saved;
        } catch (err) {
            showNotification('Duplicate failed: ' + err.message, 'error');
            return null;
        }
    }

    // ============================================================
    // UI Helpers
    // ============================================================
    function getFlowMeta() {
        return {
            name: (document.getElementById('flow-name') || {}).value || 'Untitled Flow',
            description: '',
            severity: (document.getElementById('flow-severity') || {}).value || 'warning',
            enabled: (document.getElementById('flow-enabled') || {}).checked !== false,
            cooldown_seconds: parseInt((document.getElementById('flow-cooldown') || {}).value) || 300
        };
    }

    function setFlowMeta(flow) {
        const nameEl = document.getElementById('flow-name');
        const sevEl = document.getElementById('flow-severity');
        const enabledEl = document.getElementById('flow-enabled');
        const cooldownEl = document.getElementById('flow-cooldown');

        if (nameEl) nameEl.value = flow.name || '';
        if (sevEl) sevEl.value = flow.severity || 'warning';
        if (enabledEl) enabledEl.checked = flow.enabled !== false;
        if (cooldownEl) cooldownEl.value = flow.cooldown_seconds || 300;
    }

    function newFlow() {
        currentFlowId = null;
        currentFlowMeta = null;
        if (editorInstance) editorInstance.clear();
        setFlowMeta({ name: '', severity: 'warning', enabled: true, cooldown_seconds: 300 });
        setDirty(false);
        if (window.history.pushState) {
            window.history.pushState({}, '', '/flow-editor');
        }
        FlowProperties.clearProperties();
        showNotification('New flow created', 'info');
    }

    function showNotification(message, type) {
        const container = document.getElementById('flow-notifications');
        if (!container) {
            console.log(`[FlowManager] ${type}: ${message}`);
            return;
        }

        const toast = document.createElement('div');
        toast.className = `flow-toast flow-toast--${type}`;
        toast.innerHTML = `
            <span class="flow-toast__icon">${type === 'success' ? '✓' : type === 'error' ? '✗' : 'ℹ'}</span>
            <span class="flow-toast__msg">${message}</span>
        `;
        container.appendChild(toast);

        setTimeout(() => toast.classList.add('show'), 10);
        setTimeout(() => {
            toast.classList.remove('show');
            setTimeout(() => toast.remove(), 300);
        }, 3000);
    }

    function getCurrentFlowId() { return currentFlowId; }
    function getIsDirty() { return isDirty; }

    // ============================================================
    // Export
    // ============================================================
    window.FlowManager = {
        init,
        saveFlow,
        loadFlow,
        testFlow,
        deleteFlow,
        toggleFlow,
        listFlows,
        listTemplates,
        createFromTemplate,
        duplicateFlow,
        newFlow,
        showNotification,
        getCurrentFlowId,
        getIsDirty,
        setDirty,
        drawflowToEngine,
        engineToDrawflow
    };

})();
