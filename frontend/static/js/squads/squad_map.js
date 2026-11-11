/**
 * Squad Map — side-panel graph + editable prompts with connection controls.
 *
 * Layout: [ graph sidebar | prompt col | edge strip | prompt col | … ]
 *
 * Interactions:
 *   Click a graph NODE  → highlights the matching prompt column with the
 *                         agent's color and scrolls to it.
 *   Click a graph EDGE  → opens the connection-config popout for the two
 *                         agents that edge connects.
 */
import { Api } from '../api.js';
import { esc } from '../shell.js';
import { loadMonaco } from '../codebuilder/codebuilder_editor.js';

const HANDOFF_TOOLS = {
  call_subagent:          { label: 'Delegation',       color: '#f59e0b', desc: 'Lead can call this sub-agent' },
  read_squad_scratchpad:  { label: 'Read scratchpad',  color: '#22c55e', desc: 'Reads the squad scratchpad' },
  write_squad_scratchpad: { label: 'Write scratchpad', color: '#22c55e', desc: 'Writes results to scratchpad' },
};

const NODE_W = 140, NODE_H = 46, PAD_Y = 50;
const MIN_FONT = 11, MAX_FONT = 22, DEFAULT_FONT = 14, FONT_STEP = 1;

function clip(text, max) { const s = String(text || ''); return s.length > max ? s.slice(0, max - 1) + '…' : s; }

function dedup(lead, rawMembers) {
  const members = (rawMembers || []).filter(Boolean);
  return lead ? members.filter(m => m.id !== lead.id) : members;
}

/* ── Edge data ───────────────────────────────────────────────────── */

function buildEdges(lead, members, blueprint) {
  const allNodes = [lead, ...members].filter(Boolean);
  const edges = [];
  for (const node of allNodes) {
    const isLead = node.id === lead?.id;
    for (const tool of node.tools || []) {
      const info = HANDOFF_TOOLS[tool];
      if (!info) continue;
      if (tool === 'call_subagent') {
        for (const tid of node.allowed_subagents || [])
          edges.push({ from: node.id, to: tid, tool, label: info.label, color: info.color, kind: 'delegation' });
      } else if (tool === 'write_squad_scratchpad' && !isLead) {
        edges.push({ from: node.id, to: lead?.id, tool, label: info.label, color: info.color, kind: 'handoff' });
      } else if (tool === 'read_squad_scratchpad' && isLead) {
        for (const m of members)
          edges.push({ from: m.id, to: node.id, tool, label: info.label, color: info.color, kind: 'handoff' });
      }
    }
  }
  const ac = 'var(--primary)';
  if (members.length) {
    edges.push({ from: lead?.id, to: members[0]?.id, tool: null, label: 'start', color: ac, kind: 'flow' });
    for (let i = 0; i < members.length - 1; i++)
      edges.push({ from: members[i].id, to: members[i + 1].id, tool: null, label: `step ${i + 2}`, color: ac, kind: 'flow' });
    edges.push({ from: members.at(-1)?.id, to: lead?.id, tool: null, label: 'synthesis', color: ac, kind: 'flow' });
  }
  return edges;
}

/* ── SVG — vertical layout for the side panel ────────────────────── */

function renderGraph(lead, members, edges, squadColor) {
  const allNodes = [lead, ...members].filter(Boolean);
  if (!allNodes.length) return '<p class="text-muted small">No agents.</p>';

  const svgW = 200;
  const svgH = allNodes.length * (NODE_H + PAD_Y) + 20;
  const cx = (svgW - NODE_W) / 2;

  const positions = new Map();
  allNodes.forEach((n, i) => positions.set(n.id, { x: cx + NODE_W / 2, y: 16 + i * (NODE_H + PAD_Y) + NODE_H / 2 }));

  const dash = { flow: '', delegation: '6 3', handoff: '3 3' };

  let svg = `<svg class="sq-map-canvas" viewBox="0 0 ${svgW} ${svgH}" xmlns="http://www.w3.org/2000/svg">
    <defs>
      <marker id="sqm-arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse">
        <path d="M0,0 L10,5 L0,10 z" fill="var(--text-muted)"/>
      </marker>
      ${Object.values(HANDOFF_TOOLS).map(info => `
        <marker id="sqm-arr-${info.color.replace('#','')}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse">
          <path d="M0,0 L10,5 L0,10 z" fill="${info.color}"/>
        </marker>`).join('')}
    </defs>`;

  // Edges — each gets a wide invisible hit-area + the visible path
  const drawn = new Set();
  for (const e of edges) {
    const from = positions.get(e.from), to = positions.get(e.to);
    if (!from || !to) continue;
    const key = `${e.from}-${e.to}-${e.kind}`;
    if (drawn.has(key)) continue;
    drawn.add(key);
    const off = e.kind === 'delegation' ? -18 : e.kind === 'handoff' ? 18 : 0;
    const mc = e.color.startsWith('#') ? e.color.replace('#', '') : '';
    const mk = mc ? `sqm-arr-${mc}` : 'sqm-arr';
    const fy = from.y + (from.y < to.y ? NODE_H / 2 : -NODE_H / 2);
    const ty = to.y   + (to.y < from.y ? NODE_H / 2 : -NODE_H / 2);
    const midY = (fy + ty) / 2;
    const d = `M${from.x + off},${fy} C${from.x + off},${midY} ${to.x + off},${midY} ${to.x + off},${ty}`;
    // Invisible wider hit area for clicking
    svg += `<path class="sq-map-edge-hit" d="${d}" data-edge-from="${esc(e.from)}" data-edge-to="${esc(e.to)}"
      fill="none" stroke="transparent" stroke-width="14" style="cursor:pointer"/>`;
    // Visible edge
    svg += `<path class="sq-map-edge-vis" d="${d}" data-edge-from="${esc(e.from)}" data-edge-to="${esc(e.to)}"
      fill="none" stroke="${e.color}" stroke-width="1.5" stroke-dasharray="${dash[e.kind] || ''}"
      marker-end="url(#${mk})" opacity="0.7" style="pointer-events:none"/>`;
  }

  // Nodes
  for (const [i, node] of allNodes.entries()) {
    const x = cx, y = 16 + i * (NODE_H + PAD_Y), isLead = node.id === lead?.id;
    svg += `
      <g class="sq-map-node" data-node="${esc(node.id)}" data-node-color="${isLead ? squadColor : 'var(--border-color)'}" tabindex="0" role="button" aria-label="${esc(node.name)}">
        <rect x="${x}" y="${y}" width="${NODE_W}" height="${NODE_H}" rx="8"
          fill="var(--bg-card)" stroke="${isLead ? squadColor : 'var(--border-color)'}" stroke-width="${isLead ? 2 : 1.5}"
          ${!isLead && node.kind === 'subagent' ? 'stroke-dasharray="5 3"' : ''}/>
        <text x="${x + 8}" y="${y + 19}" fill="var(--text-main)" font-size="11" font-weight="600">${esc(clip(node.name, 18))}</text>
        <text x="${x + 8}" y="${y + 34}" fill="var(--text-muted)" font-size="9">${isLead ? 'lead' : node.kind} · ${(node.tools || []).length} tools</text>
      </g>`;
  }

  svg += '</svg>';
  return svg + `<div class="sq-map-legend">
    <span style="color:var(--primary)">— flow</span>
    <span style="color:#f59e0b">- - delegation</span>
    <span style="color:#22c55e">··· scratchpad</span>
    <span class="text-muted" style="margin-left:auto">click arrow to configure</span>
  </div>`;
}

/* ── Edge popout (opens on edge click in graph) ──────────────────── */

function openEdgePopout(dialog, leftNode, rightNode, edges, liveNodes, blueprint, squadColor, refreshGraph, showStatus) {
  dialog.querySelector('.sq-map-edge-popout')?.remove();

  const relevant = edges.filter(e =>
    (e.from === leftNode.id && e.to === rightNode.id) ||
    (e.from === rightNode.id && e.to === leftNode.id));

  const tools = Object.entries(HANDOFF_TOOLS);

  const popout = document.createElement('div');
  popout.className = 'sq-map-edge-popout';
  popout.innerHTML = `
    <div class="sq-map-edge-popout-body">
      <header class="sq-map-edge-popout-head">
        <strong>Connections</strong>
        <span class="text-muted">${esc(leftNode.name)} ↔ ${esc(rightNode.name)}</span>
        <button type="button" class="cb-btn cb-btn-sm" data-act="close-popout">✕</button>
      </header>
      <div class="sq-map-edge-popout-current">
        <h4>Active connections</h4>
        ${relevant.length ? relevant.map(e => `
          <div class="sq-map-epop-row" style="border-left: 3px solid ${e.color}; padding-left: 0.6rem; margin-bottom: 0.4rem;">
            <span style="font-size: 1.1rem">${e.from === leftNode.id ? '→' : '←'}</span>
            <strong>${esc(e.label)}</strong>
            <span class="text-muted"> · ${e.kind}</span>
          </div>`).join('') : '<p class="text-muted">No direct connections between these agents.</p>'}
      </div>
      <hr style="border-color: var(--border-color); margin: 0.75rem 0;">
      <h4>Toggle tools</h4>
      <p class="text-muted small">Check a box to give an agent that tool; uncheck to remove it.</p>
      <div class="sq-map-epop-toggles">
        ${tools.map(([tool, info]) => {
          const lHas = (leftNode.tools || []).includes(tool);
          const rHas = (rightNode.tools || []).includes(tool);
          return `
            <div class="sq-map-epop-tool">
              <div class="sq-map-epop-tool-head">
                <span class="sq-map-epop-dot" style="background:${info.color}"></span>
                <strong>${esc(info.label)}</strong>
                <span class="text-muted small">${esc(tool)}</span>
              </div>
              <p class="text-muted small" style="margin:0.15rem 0 0.3rem">${esc(info.desc)}</p>
              <label class="sq-map-epop-cb">
                <input type="checkbox" ${lHas ? 'checked' : ''}
                  data-toggle-tool="${esc(tool)}" data-toggle-agent="${esc(leftNode.id)}">
                <span>${esc(leftNode.name)}</span>
              </label>
              <label class="sq-map-epop-cb">
                <input type="checkbox" ${rHas ? 'checked' : ''}
                  data-toggle-tool="${esc(tool)}" data-toggle-agent="${esc(rightNode.id)}">
                <span>${esc(rightNode.name)}</span>
              </label>
            </div>`;
        }).join('')}
      </div>
    </div>`;

  popout.querySelector('[data-act="close-popout"]').addEventListener('click', () => popout.remove());
  popout.addEventListener('click', e => { if (e.target === popout) popout.remove(); });

  popout.addEventListener('change', async event => {
    const cb = event.target;
    if (!cb.dataset.toggleTool || !cb.dataset.toggleAgent) return;
    const tool = cb.dataset.toggleTool;
    const agentId = cb.dataset.toggleAgent;
    const attach = cb.checked;
    cb.disabled = true;
    try {
      const updated = attach
        ? await Api.attachAgentTool(agentId, tool)
        : await Api.detachAgentTool(agentId, tool);
      const node = liveNodes.find(n => n.id === updated.id);
      if (node) Object.assign(node, updated);
      refreshGraph();
      showStatus(`${attach ? 'Attached' : 'Detached'} ${tool} ${attach ? 'to' : 'from'} ${agentId}`);
    } catch (err) {
      cb.checked = !attach;
      showStatus(`Could not ${attach ? 'attach' : 'detach'} tool: ${err.message}`, true);
    } finally {
      cb.disabled = false;
    }
  });

  dialog.appendChild(popout);
}

/* ── Prompt column selection highlight ───────────────────────────── */

function selectPromptCol(promptsHost, agentId, color) {
  // Clear previous selection
  promptsHost.querySelectorAll('.sq-map-prompt-col').forEach(col => {
    col.classList.remove('sq-map-col-selected');
    col.style.removeProperty('--sel-color');
  });
  // Also clear node selection in graph
  const graphHost = promptsHost.closest('.sq-map-dialog')?.querySelector('[data-role="graph"]');
  if (graphHost) graphHost.querySelectorAll('.sq-map-node').forEach(n => n.classList.remove('is-selected'));

  if (!agentId) return;

  const col = promptsHost.querySelector(`[data-col="${CSS.escape(agentId)}"]`);
  if (col) {
    col.classList.add('sq-map-col-selected');
    col.style.setProperty('--sel-color', color);
    col.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'center' });
  }
  // Highlight matching node
  if (graphHost) {
    const nodeEl = graphHost.querySelector(`[data-node="${CSS.escape(agentId)}"]`);
    if (nodeEl) nodeEl.classList.add('is-selected');
  }
}

/* ── Dialog ──────────────────────────────────────────────────────── */

export async function openSquadMap(squadId, squadColor, overview) {
  const dialog = document.createElement('dialog');
  dialog.className = 'sq-map-dialog';
  dialog.innerHTML = `
    <header class="sq-map-head">
      <h2>Squad map</h2>
      <p class="text-muted small" data-role="subtitle">Loading…</p>
      <div class="sq-map-font-controls">
        <button type="button" class="cb-btn cb-btn-sm" data-act="font-down" title="Smaller text">A−</button>
        <span class="sq-map-font-label" data-role="font-label">${DEFAULT_FONT}px</span>
        <button type="button" class="cb-btn cb-btn-sm" data-act="font-up" title="Larger text">A+</button>
      </div>
      <div class="sq-map-layout-controls">
        <button type="button" class="cb-btn cb-btn-sm cb-btn-active" data-act="toggle-graph" title="Toggle graph sidebar">Graph</button>
        <button type="button" class="cb-btn cb-btn-sm" data-act="expand" title="Toggle fullscreen">Expand</button>
      </div>
      <button type="button" class="cb-btn" data-act="close">Close</button>
    </header>
    <div class="sq-map-body" data-role="body">
      <aside class="sq-map-graph" data-role="graph"><p class="text-muted small">Loading…</p></aside>
      <div class="sq-map-resize-handle" data-role="resize" title="Drag to resize"></div>
      <div class="sq-map-prompts" data-role="prompts"><p class="text-muted small">Loading prompts…</p></div>
    </div>
    <p data-role="status" class="small sq-map-status" role="status" aria-live="polite"></p>`;
  document.body.append(dialog);

  const body = dialog.querySelector('[data-role="body"]');
  const graphHost = dialog.querySelector('[data-role="graph"]');
  const promptsHost = dialog.querySelector('[data-role="prompts"]');
  const subtitle = dialog.querySelector('[data-role="subtitle"]');
  const statusEl = dialog.querySelector('[data-role="status"]');
  const fontLabel = dialog.querySelector('[data-role="font-label"]');
  const resizeHandle = dialog.querySelector('[data-role="resize"]');

  let fontSize = DEFAULT_FONT;
  let graphVisible = true;
  let expanded = false;
  const dirty = new Map();
  const editors = new Map(); // agentId → monaco editor instance
  let liveNodes = [];
  let blueprint = null;

  function applyFontSize() {
    promptsHost.style.setProperty('--map-font-size', `${fontSize}px`);
    fontLabel.textContent = `${fontSize}px`;
    for (const ed of editors.values()) {
      if (ed.updateOptions) ed.updateOptions({ fontSize });
    }
  }
  applyFontSize();

  function showStatus(msg, isError = false) {
    statusEl.textContent = msg;
    statusEl.classList.toggle('text-danger', isError);
  }

  function refreshGraph() {
    const lead = liveNodes[0], members = liveNodes.slice(1);
    const edges = buildEdges(lead, members, blueprint);
    graphHost.innerHTML = renderGraph(lead, members, edges, squadColor);

    const colsHost = promptsHost.querySelector('[data-role="cols"]');
    if (!colsHost) return;
    for (const node of liveNodes) {
      const row = colsHost.querySelector(`[data-tools-for="${CSS.escape(node.id)}"]`);
      if (!row) continue;
      row.innerHTML = (node.tools || []).map(t => {
        const hi = HANDOFF_TOOLS[t];
        return `<span class="sq-chip" ${hi ? `style="border-color:${hi.color};color:${hi.color}"` : ''}>${esc(t)}</span>`;
      }).join('') || '<span class="text-muted small">no tools</span>';
    }
  }

  dialog.addEventListener('close', () => {
    for (const ed of editors.values()) ed.dispose();
    editors.clear();
    dialog.remove();
  });
  dialog.addEventListener('cancel', event => {
    if (dirty.size && !confirm(`You have unsaved changes in ${dirty.size} prompt(s). Close anyway?`)) {
      event.preventDefault();
      return;
    }
    dialog.close();
  });

  // Graph interactions: node click → highlight column, edge click → config popout
  graphHost.addEventListener('click', event => {
    // Edge click?
    const edgeHit = event.target.closest('.sq-map-edge-hit');
    if (edgeHit) {
      const fromId = edgeHit.dataset.edgeFrom, toId = edgeHit.dataset.edgeTo;
      const fromNode = liveNodes.find(n => n.id === fromId);
      const toNode = liveNodes.find(n => n.id === toId);
      if (fromNode && toNode) {
        const lead = liveNodes[0], members = liveNodes.slice(1);
        const edges = buildEdges(lead, members, blueprint);
        openEdgePopout(dialog, fromNode, toNode, edges, liveNodes, blueprint, squadColor, refreshGraph, showStatus);
      }
      return;
    }
    // Node click?
    const nodeEl = event.target.closest('[data-node]');
    if (nodeEl) {
      selectPromptCol(promptsHost, nodeEl.dataset.node, squadColor);
    }
  });

  // Click on a prompt column header → also highlight it and select graph node
  promptsHost.addEventListener('click', event => {
    const header = event.target.closest('.sq-map-prompt-header');
    if (!header) return;
    const col = header.closest('.sq-map-prompt-col');
    if (!col) return;
    const agentId = col.dataset.col;
    selectPromptCol(promptsHost, agentId, squadColor);
  });

  // Button actions
  dialog.addEventListener('click', event => {
    const btn = event.target.closest('button');
    if (!btn) return;
    const act = btn.dataset.act;

    if (act === 'close') {
      if (dirty.size && !confirm(`You have unsaved changes in ${dirty.size} prompt(s). Close anyway?`)) return;
      dialog.close();
    }
    if (act === 'font-down') { fontSize = Math.max(MIN_FONT, fontSize - FONT_STEP); applyFontSize(); }
    if (act === 'font-up')   { fontSize = Math.min(MAX_FONT, fontSize + FONT_STEP); applyFontSize(); }
    if (act === 'toggle-graph') {
      graphVisible = !graphVisible;
      graphHost.classList.toggle('sq-map-graph-hidden', !graphVisible);
      resizeHandle.classList.toggle('sq-map-resize-hidden', !graphVisible);
      btn.classList.toggle('cb-btn-active', graphVisible);
    }
    if (act === 'expand') {
      expanded = !expanded;
      dialog.classList.toggle('sq-map-expanded', expanded);
      btn.textContent = expanded ? 'Shrink' : 'Expand';
    }
    if (act === 'save') {
      const agentId = btn.dataset.agent;
      const ed = editors.get(agentId);
      if (!ed) return;
      btn.disabled = true; btn.textContent = 'Saving…';
      Api.setAgentPrompt(agentId, ed.getValue()).then(() => {
        dirty.delete(agentId);
        btn.textContent = 'Saved'; btn.classList.remove('cb-btn-danger'); btn.classList.add('cb-btn-success');
        const dot = promptsHost.querySelector(`[data-dirty="${CSS.escape(agentId)}"]`);
        if (dot) dot.hidden = true;
        setTimeout(() => { btn.textContent = 'Save'; btn.classList.remove('cb-btn-success'); }, 1500);
      }).catch(err => { btn.textContent = 'Save'; showStatus(`Save failed: ${err.message}`, true); })
        .finally(() => { btn.disabled = false; });
    }
    if (act === 'save-all') {
      if (!dirty.size) return;
      btn.disabled = true; btn.textContent = 'Saving…';
      const errs = [];
      (async () => {
        for (const [agentId] of dirty) {
          const ed = editors.get(agentId);
          if (!ed) continue;
          try {
            await Api.setAgentPrompt(agentId, ed.getValue());
            dirty.delete(agentId);
            const dot = promptsHost.querySelector(`[data-dirty="${CSS.escape(agentId)}"]`);
            if (dot) dot.hidden = true;
            const sb = promptsHost.querySelector(`button[data-agent="${CSS.escape(agentId)}"][data-act="save"]`);
            if (sb) sb.classList.remove('cb-btn-danger');
          } catch (err) { errs.push(`${agentId}: ${err.message}`); }
        }
        btn.disabled = false; btn.textContent = `Save all (${dirty.size})`;
        showStatus(errs.length ? errs.join('; ') : 'All prompts saved.', !!errs.length);
      })();
    }
  });

  // Resize handles — graph sidebar + prompt column dividers
  let resizeTarget = null; // the element being resized
  function startResize(e, target) {
    e.preventDefault(); e.stopPropagation();
    resizeTarget = target;
    dialog.style.cursor = 'col-resize';
    dialog.classList.add('sq-map-resizing');
  }
  resizeHandle.addEventListener('mousedown', e => startResize(e, graphHost));
  // Column dividers (delegated)
  promptsHost.addEventListener('mousedown', e => {
    const div = e.target.closest('.sq-map-col-divider');
    if (!div) return;
    const col = div.previousElementSibling;
    if (col?.classList.contains('sq-map-prompt-col')) startResize(e, col);
  });
  dialog.addEventListener('mousemove', e => {
    if (!resizeTarget) return;
    if (resizeTarget === graphHost) {
      const bodyRect = body.getBoundingClientRect();
      const x = e.clientX - bodyRect.left;
      const clamped = Math.max(100, Math.min(bodyRect.width * 0.5, x));
      graphHost.style.width = `${clamped}px`;
      graphHost.style.flexBasis = `${clamped}px`;
      graphHost.style.flexShrink = '0';
    } else {
      const rect = resizeTarget.getBoundingClientRect();
      const w = e.clientX - rect.left;
      const clamped = Math.max(180, Math.min(900, w));
      resizeTarget.style.flex = `0 0 ${clamped}px`;
      resizeTarget.style.minWidth = `${clamped}px`;
    }
  });
  dialog.addEventListener('mouseup', () => {
    if (!resizeTarget) return;
    resizeTarget = null;
    dialog.style.cursor = '';
    dialog.classList.remove('sq-map-resizing');
  });

  dialog.showModal();

  try {
    blueprint = await Api.getSquadBlueprint(squadId);
    const lead = blueprint.lead;
    const members = dedup(lead, blueprint.members);
    liveNodes = [lead, ...members].filter(Boolean);

    subtitle.textContent = `${blueprint.name} · ${blueprint.pattern} · ${liveNodes.length} agent${liveNodes.length !== 1 ? 's' : ''}`;

    const edges = buildEdges(lead, members, blueprint);
    graphHost.innerHTML = renderGraph(lead, members, edges, squadColor);

    let colsHtml = '';
    for (let i = 0; i < liveNodes.length; i++) {
      const node = liveNodes[i];
      colsHtml += `
        <section class="sq-map-prompt-col" data-col="${esc(node.id)}">
          <header class="sq-map-prompt-header" style="border-top: 3px solid ${node.id === lead?.id ? squadColor : 'var(--border-color)'}">
            <strong>${esc(node.name)}</strong>
            <span class="badge">${node.id === lead?.id ? 'lead' : node.kind}</span>
            <span class="badge" style="opacity:0.6">${node.path?.endsWith('.yaml') || node.path?.endsWith('.yml') ? 'yaml' : 'md'}</span>
            <span class="sq-map-dirty-dot" data-dirty="${esc(node.id)}" hidden title="Unsaved changes"></span>
            <button type="button" class="cb-btn cb-btn-sm" data-act="save" data-agent="${esc(node.id)}">Save</button>
          </header>
          <div class="sq-map-prompt-tools sq-chip-row" data-tools-for="${esc(node.id)}">
            ${(node.tools || []).map(t => {
              const hi = HANDOFF_TOOLS[t];
              return `<span class="sq-chip" ${hi ? `style="border-color:${hi.color};color:${hi.color}"` : ''}>${esc(t)}</span>`;
            }).join('') || '<span class="text-muted small">no tools</span>'}
          </div>
          <div class="sq-map-prompt-body" data-prompt-body="${esc(node.id)}">
            <p class="text-muted small">Loading prompt…</p>
          </div>
        </section>`;
      if (i < liveNodes.length - 1) {
        colsHtml += `<div class="sq-map-col-divider" title="Drag to resize column"></div>`;
      }
    }

    promptsHost.innerHTML = `
      <div class="sq-map-prompts-toolbar">
        <button type="button" class="cb-btn cb-btn-sm" data-act="save-all">Save all (0)</button>
      </div>
      <div class="sq-map-prompts-cols" data-role="cols">${colsHtml}</div>`;

    const saveAllBtn = promptsHost.querySelector('[data-act="save-all"]');
    function updateSaveAllLabel() { saveAllBtn.textContent = `Save all (${dirty.size})`; }

    const results = await Promise.allSettled(
      liveNodes.map(async node => {
        try { const { prompt } = await Api.getAgentPrompt(node.id); return { id: node.id, prompt }; }
        catch (err) { return { id: node.id, prompt: null, error: err.message }; }
      })
    );

    // Create Monaco editors for each prompt
    let monaco;
    try { monaco = await loadMonaco(); } catch { monaco = null; }

    for (const result of results) {
      const val = result.status === 'fulfilled' ? result.value : null;
      if (!val) continue;
      const el = promptsHost.querySelector(`[data-prompt-body="${CSS.escape(val.id)}"]`);
      if (!el) continue;
      if (val.prompt != null) {
        const node = liveNodes.find(n => n.id === val.id);
        const lang = node?.path?.endsWith('.yaml') || node?.path?.endsWith('.yml') ? 'yaml' : 'markdown';

        if (monaco) {
          el.innerHTML = '';
          el.classList.add('sq-map-editor-host');
          const ed = monaco.editor.create(el, {
            value: val.prompt,
            language: lang,
            theme: 'vs-dark',
            automaticLayout: true,
            fontSize: fontSize,
            fontFamily: "'Cascadia Code', 'Fira Code', Consolas, monospace",
            lineNumbers: 'on',
            minimap: { enabled: false },
            scrollBeyondLastLine: false,
            wordWrap: 'on',
            wrappingIndent: 'indent',
            padding: { top: 8, bottom: 8 },
            renderLineHighlight: 'line',
            folding: true,
            glyphMargin: false,
            lineDecorationsWidth: 4,
            lineNumbersMinChars: 3,
            readOnly: false,
            ariaLabel: `Prompt for ${val.id} (${lang})`,
          });
          editors.set(val.id, ed);
          ed.onDidChangeModelContent(() => {
            dirty.set(val.id, true);
            const dot = promptsHost.querySelector(`[data-dirty="${CSS.escape(val.id)}"]`);
            if (dot) dot.hidden = false;
            const sb = promptsHost.querySelector(`button[data-agent="${CSS.escape(val.id)}"][data-act="save"]`);
            if (sb) sb.classList.add('cb-btn-danger');
            updateSaveAllLabel();
          });
        } else {
          // Fallback: plain textarea if Monaco unavailable
          const textarea = document.createElement('textarea');
          textarea.className = 'sq-map-editor';
          textarea.dataset.editor = val.id;
          textarea.value = val.prompt;
          textarea.spellcheck = false;
          textarea.setAttribute('aria-label', `Prompt for ${val.id}`);
          el.replaceChildren(textarea);
          const fallbackEd = {
            getValue: () => textarea.value,
            dispose: () => {},
            updateOptions: () => {},
          };
          editors.set(val.id, fallbackEd);
          textarea.addEventListener('input', () => {
            dirty.set(val.id, true);
            const dot = promptsHost.querySelector(`[data-dirty="${CSS.escape(val.id)}"]`);
            if (dot) dot.hidden = false;
            const sb = promptsHost.querySelector(`button[data-agent="${CSS.escape(val.id)}"][data-act="save"]`);
            if (sb) sb.classList.add('cb-btn-danger');
            updateSaveAllLabel();
          });
        }
      } else {
        el.innerHTML = `<p class="text-muted small">Could not load prompt: ${esc(val.error || 'unknown error')}</p>`;
      }
    }

  } catch (err) {
    showStatus(`Error: ${err.message}`, true);
  }
}
