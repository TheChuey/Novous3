/**
 * Squads home: every registered squad, the Squad Architect canvas for the selected one,
 * and an inspector for the selected agent (tools, delegation, editor and chat shortcuts).
 */
import { Api } from '../api.js';
import { esc, navigate, openInEditor } from '../shell.js';
import { renderArchitectCanvas } from './architect_canvas.js';
import { renderBuilder } from './builder_steps.js';
import { PATTERN_INFO } from './run_client.js';

let selectedSquadId = null;
let selectedNodeId = null;

function healthBadge(status = 'healthy') {
  const cls = status === 'healthy' ? 'badge-success' : status === 'degraded' ? 'badge-warning' : 'badge-danger';
  return `<span class="badge ${cls}">${esc(status)}</span>`;
}

function metricsLine(metrics = {}) {
  return `${metrics.total_runs || 0} runs · ${metrics.successful_runs || 0} ok · ${metrics.failed_runs || 0} failed · avg ${Math.round(metrics.average_duration_ms || 0)} ms`;
}

export function renderSquadsView(container, payload = null) {
  if (payload?.squadId) selectedSquadId = payload.squadId;
  container.innerHTML = `
    <div class="sq-page">
      <header class="sq-page-head">
        <div>
          <h2>Squads</h2>
          <p class="text-muted">Build an ordered workflow with one lead and sub-agent steps that pass results forward.</p>
        </div>
        <button type="button" class="btn btn-primary" data-act="new">+ New squad</button>
      </header>
      <div class="sq-home">
        <aside class="sq-list" data-role="list" aria-label="Squads"><p class="text-muted small">Loading squads…</p></aside>
        <section class="sq-detail" data-role="detail"></section>
      </div>
    </div>`;

  const list = container.querySelector('[data-role="list"]');
  const detail = container.querySelector('[data-role="detail"]');
  let overview = { squads: [], agents: [], tools: [] };

  async function load() {
    overview = await Api.getSquadsOverview();
    if (!overview.squads.some(squad => squad.squad_id === selectedSquadId)) {
      selectedSquadId = overview.squads[0]?.squad_id || null;
    }
    renderList();
    if (payload?.builder === 'new') openBuilder(null);
    else await renderDetail();
  }

  function renderList() {
    list.innerHTML = overview.squads.map(squad => `
      <button type="button" class="sq-card ${squad.squad_id === selectedSquadId ? 'is-selected' : ''}" data-squad="${esc(squad.squad_id)}">
        <span class="sq-card-head"><strong>${esc(squad.name)}</strong>${healthBadge(squad.metrics?.last_status)}</span>
        <span class="sq-card-flow">
          <span class="badge badge-accent">${esc(PATTERN_INFO[squad.default_pattern]?.label || squad.default_pattern)}</span>
          ${esc(squad.primary_agent)} → ${squad.subagents.length} member${squad.subagents.length === 1 ? '' : 's'}
        </span>
        <span class="text-muted small">${esc(metricsLine(squad.metrics))}</span>
      </button>`).join('') || `
      <div class="sq-empty-state">
        <p>No squads yet.</p>
        <button type="button" class="btn btn-primary" data-act="new">Build your first squad</button>
      </div>`;
  }

  async function renderDetail() {
    if (!selectedSquadId) {
      detail.innerHTML = `<div class="card sq-empty-state"><p class="text-muted">Select or create a squad to see its structure.</p></div>`;
      return;
    }
    detail.innerHTML = '<p class="text-muted small">Loading blueprint…</p>';
    let blueprint;
    try {
      blueprint = await Api.getSquadBlueprint(selectedSquadId);
    } catch (err) {
      detail.innerHTML = `<div class="error-panel">Could not load squad: ${esc(err.message)}</div>`;
      return;
    }
    const nodes = [blueprint.lead, ...blueprint.members].filter(Boolean);
    if (!nodes.some(node => node.id === selectedNodeId)) selectedNodeId = blueprint.lead?.id || null;
    const missing = blueprint.members.filter(member => !member).length;

    detail.innerHTML = `
      <div class="card sq-architect">
        <div class="sq-architect-head">
          <div>
            <h3>${esc(blueprint.name)}</h3>
            <p class="text-muted small">${esc(blueprint.description || 'No description.')}</p>
          </div>
          <div class="sq-architect-actions">
            <span class="badge badge-accent">Ordered workflow</span>
            <button type="button" class="btn btn-sm" data-act="edit">Edit in builder</button>
            <button type="button" class="btn btn-sm btn-primary" data-act="run">▶ Run</button>
            <button type="button" class="btn btn-sm btn-danger" data-act="delete">Delete</button>
          </div>
        </div>
        <p class="text-muted small sq-pattern-note">${esc(PATTERN_INFO.sequential.summary)}
          ${blueprint.dedicated_pipeline ? ' This squad uses its dedicated Plan → Build → Test → Fix pipeline.' : ''}
          ${missing ? ` <span class="text-danger">${missing} member definition(s) are missing.</span>` : ''}</p>
        <div class="sq-architect-body">
          <div class="sq-canvas-host" data-role="canvas"></div>
          <aside class="sq-inspector" data-role="inspector"></aside>
        </div>
        <p class="text-muted small">${esc(metricsLine(blueprint.metrics))}</p>
      </div>`;

    const canvas = detail.querySelector('[data-role="canvas"]');
    const draw = () => renderArchitectCanvas(canvas, blueprint, {
      selectedId: selectedNodeId,
      onSelect: id => { selectedNodeId = id; draw(); renderInspector(); }
    });
    const inspector = detail.querySelector('[data-role="inspector"]');

    function renderInspector() {
      const node = nodes.find(n => n.id === selectedNodeId);
      if (!node) { inspector.innerHTML = '<p class="text-muted small">Select an agent.</p>'; return; }
      const isLead = node.id === blueprint.lead?.id;
      const available = overview.tools.filter(tool => !node.tools.includes(tool.name));
      inspector.innerHTML = `
        <div class="sq-inspector-head">
          <strong>${esc(node.name)}</strong>
          <span class="badge ${isLead ? 'badge-accent' : ''}">${isLead ? 'lead' : node.kind === 'subagent' ? 'sub-agent' : 'agent'}</span>
          <span class="badge">${esc(node.mode)}</span>
        </div>
        <p class="text-muted small">${esc(node.description || 'No description.')}</p>
        <code class="small">${esc(node.path)}</code>
        <h4>Tools</h4>
        <div class="sq-chip-row">
          ${node.tools.map(tool => `<span class="sq-chip">${esc(tool)}
            <button type="button" class="sq-chip-x" data-detach="${esc(tool)}" aria-label="Remove ${esc(tool)}">×</button></span>`).join('')
            || '<span class="text-muted small">No tools</span>'}
        </div>
        <select class="form-select mt-2" data-role="attach" aria-label="Attach a tool">
          <option value="">+ Attach tool…</option>
          ${available.map(tool => `<option value="${esc(tool.name)}">${esc(tool.name)} · ${esc(tool.provider)}</option>`).join('')}
        </select>
        ${node.allowed_subagents?.length ? `
          <h4>May call</h4>
          <div class="sq-chip-row">${node.allowed_subagents.map(id => `<span class="sq-chip">${esc(id)}</span>`).join('')}</div>` : ''}
        <p class="small text-danger hidden" data-role="inspector-error"></p>
        <div class="sq-inspector-actions">
          <button type="button" class="btn btn-sm" data-act="open-editor">Edit prompt</button>
          ${node.kind === 'agent' ? '<button type="button" class="btn btn-sm" data-act="chat">Chat</button>' : ''}
          <button type="button" class="btn btn-sm" data-act="tools">Build a tool</button>
        </div>`;

    }

    async function applyToSelected(request) {
      const node = nodes.find(n => n.id === selectedNodeId);
      try {
        Object.assign(node, await request);
        draw();
        renderInspector();
      } catch (err) {
        const error = inspector.querySelector('[data-role="inspector-error"]');
        error.textContent = err.message;
        error.classList.remove('hidden');
      }
    }
    inspector.addEventListener('change', event => {
      if (event.target.dataset.role === 'attach' && event.target.value) {
        applyToSelected(Api.attachAgentTool(selectedNodeId, event.target.value));
      }
    });
    inspector.addEventListener('click', event => {
      const button = event.target.closest('button');
      const node = nodes.find(n => n.id === selectedNodeId);
      if (!button || !node) return;
      if (button.dataset.detach) applyToSelected(Api.detachAgentTool(node.id, button.dataset.detach));
      else if (button.dataset.act === 'open-editor') openInEditor(node.path);
      else if (button.dataset.act === 'chat') navigate('chat', node.id);
      else if (button.dataset.act === 'tools') navigate('tools', { attachTo: node.id });
    });

    detail.querySelector('.sq-architect-actions').addEventListener('click', async event => {
      const act = event.target.closest('button')?.dataset.act;
      if (act === 'edit') openBuilder(blueprint);
      else if (act === 'run') navigate('runs', { squadId: blueprint.squad_id });
      else if (act === 'delete') {
        if (!confirm(`Delete squad "${blueprint.name}"? Only the registration and its metrics are removed; agent files stay.`)) return;
        try {
          await Api.deleteOrchestrationSquad(blueprint.squad_id);
          selectedSquadId = null;
          await load();
        } catch (err) {
          alert(`Could not delete squad: ${err.message}`);
        }
      }
    });

    draw();
    renderInspector();
  }

  function openBuilder(initial) {
    payload = null;
    const host = document.createElement('div');
    detail.replaceChildren(host);
    renderBuilder(host, {
      overview,
      initial,
      onCancel: () => renderDetail(),
      onSaved: async (blueprint, warnings) => {
        selectedSquadId = blueprint.squad_id;
        selectedNodeId = blueprint.lead?.id || null;
        await load();
        if (warnings.length) {
          detail.insertAdjacentHTML('afterbegin',
            `<div class="sq-msg sq-msg-warn">Saved with notes: ${warnings.map(esc).join(' · ')}</div>`);
        }
      }
    });
  }

  container.addEventListener('click', event => {
    const squadButton = event.target.closest('[data-squad]');
    if (squadButton) {
      selectedSquadId = squadButton.dataset.squad;
      selectedNodeId = null;
      renderList();
      renderDetail();
      return;
    }
    if (event.target.closest('[data-act="new"]')) openBuilder(null);
  });

  load().catch(err => {
    list.innerHTML = `<p class="text-danger small">Could not load squads: ${esc(err.message)}</p>`;
  });
}
