/**
 * Agents section: agents grouped by squad with expandable prompt viewers.
 * Each squad gets a distinct accent color; unassigned agents sit at the bottom.
 */
import { Api } from '../api.js';
import { esc, navigate, openInEditor } from '../shell.js';
import { openSquadMap } from './squad_map.js';

let kindFilter = 'all';
let search = '';

const SQUAD_COLORS = [
  '#3b82f6', '#8b5cf6', '#06b6d4', '#f59e0b', '#10b981',
  '#ec4899', '#f97316', '#6366f1', '#14b8a6', '#e11d48',
];

export function renderAgentsView(container) {
  container.innerHTML = `
    <div class="sq-page">
      <header class="sq-page-head">
        <div>
          <h2>Agents</h2>
          <p class="text-muted">Agents grouped by squad. Expand any agent to see its prompt.</p>
        </div>
        <button type="button" class="btn btn-primary" data-act="toggle-create">+ New agent</button>
      </header>
      <form class="card sq-create-card hidden" data-role="create">
        <div class="sq-segmented" role="radiogroup" aria-label="Agent kind">
          <label><input type="radio" name="kind" value="agent" checked> Primary agent <span class="text-muted small">library/*.md</span></label>
          <label><input type="radio" name="kind" value="subagent"> Sub-agent <span class="text-muted small">subAgentLibrary/*.yaml</span></label>
        </div>
        <div class="form-row mt-2">
          <input class="form-input" name="agent_id" placeholder="id (e.g. reviewer)" maxlength="64" required>
          <input class="form-input" name="name" placeholder="Display name" maxlength="100">
        </div>
        <div class="form-row">
          <input class="form-input" name="description" placeholder="What it does" maxlength="500">
          <button type="submit" class="btn btn-primary">Create</button>
        </div>
        <p class="small text-danger hidden" data-role="create-error" role="alert"></p>
      </form>
      <div class="sq-toolbar">
        <div class="sq-segmented" role="radiogroup" aria-label="Filter by kind">
          ${['all', 'agent', 'subagent'].map(kind => `
            <label><input type="radio" name="filter" value="${kind}" ${kindFilter === kind ? 'checked' : ''}>
              ${kind === 'all' ? 'All' : kind === 'agent' ? 'Primary' : 'Sub-agents'}</label>`).join('')}
        </div>
        <input class="form-input sq-search" type="search" placeholder="Search agents or tools" value="${esc(search)}" aria-label="Search agents">
      </div>
      <p class="small text-danger hidden" data-role="error" role="alert"></p>
      <div class="sq-agent-groups" data-role="grid"><p class="text-muted small">Loading agents…</p></div>
    </div>`;

  const grid = container.querySelector('[data-role="grid"]');
  const errorBox = container.querySelector('[data-role="error"]');
  const createForm = container.querySelector('[data-role="create"]');
  let overview = { agents: [], squads: [], tools: [] };
  const promptCache = {};

  function squadsUsing(agentId) {
    return overview.squads.filter(squad =>
      squad.primary_agent === agentId || squad.subagents.includes(agentId));
  }

  function buildGroups() {
    const term = search.toLowerCase();
    const shown = overview.agents.filter(agent =>
      (kindFilter === 'all' || agent.kind === kindFilter) &&
      (!term || [agent.id, agent.name, agent.description, ...agent.tools].join(' ').toLowerCase().includes(term)));

    const squadMap = new Map();
    const unassigned = [];
    for (const agent of shown) {
      const agentSquads = squadsUsing(agent.id);
      if (agentSquads.length) {
        for (const squad of agentSquads) {
          if (!squadMap.has(squad.squad_id)) squadMap.set(squad.squad_id, { squad, agents: [] });
          squadMap.get(squad.squad_id).agents.push(agent);
        }
      } else {
        unassigned.push(agent);
      }
    }
    return { squadGroups: [...squadMap.values()], unassigned };
  }

  function agentCardHtml(agent, color) {
    const available = overview.tools.filter(tool => !agent.tools.includes(tool.name));
    const isLead = overview.squads.some(s => s.primary_agent === agent.id);
    return `
      <article class="card sq-agent-card" data-agent="${esc(agent.id)}" style="border-left: 3px solid ${color}">
        <div class="sq-inspector-head">
          <strong>${esc(agent.name)}</strong>
          ${isLead ? '<span class="badge badge-accent">lead</span>' : ''}
          <span class="badge ${agent.kind === 'agent' ? 'badge-accent' : ''}">${agent.kind === 'agent' ? 'primary' : 'sub-agent'}</span>
          <span class="badge">${esc(agent.mode)}</span>
        </div>
        <p class="text-muted small">${esc(agent.description || 'No description.')}</p>
        <div class="sq-chip-row">
          ${agent.tools.map(tool => `<span class="sq-chip">${esc(tool)}
            <button type="button" class="sq-chip-x" data-detach="${esc(tool)}" aria-label="Remove ${esc(tool)}">×</button></span>`).join('')
            || '<span class="text-muted small">No tools</span>'}
        </div>
        <select class="form-select" data-attach aria-label="Attach a tool to ${esc(agent.name)}">
          <option value="">+ Attach tool…</option>
          ${available.map(tool => `<option value="${esc(tool.name)}">${esc(tool.name)} · ${esc(tool.provider)}</option>`).join('')}
        </select>
        <details class="sq-prompt-viewer" data-prompt-for="${esc(agent.id)}">
          <summary class="sq-prompt-toggle">View prompt</summary>
          <div class="sq-prompt-content"><p class="text-muted small">Loading…</p></div>
        </details>
        <div class="sq-inspector-actions">
          <button type="button" class="btn btn-sm" data-act="edit">Edit prompt</button>
          ${agent.kind === 'agent' ? '<button type="button" class="btn btn-sm" data-act="chat">Chat</button>' : ''}
          ${agent.kind === 'subagent' ? '<button type="button" class="btn btn-sm btn-danger" data-act="delete">Delete</button>' : ''}
        </div>
      </article>`;
  }

  function render() {
    const { squadGroups, unassigned } = buildGroups();
    const colorMap = new Map();
    overview.squads.forEach((s, i) => colorMap.set(s.squad_id, SQUAD_COLORS[i % SQUAD_COLORS.length]));

    let html = '';
    for (const { squad, agents } of squadGroups) {
      const color = colorMap.get(squad.squad_id);
      html += `
        <details class="sq-squad-group" open>
          <summary class="sq-squad-group-header" style="border-left: 4px solid ${color}">
            <span class="sq-squad-color-dot" style="background: ${color}"></span>
            <strong>${esc(squad.name)}</strong>
            <span class="badge">${agents.length} agent${agents.length !== 1 ? 's' : ''}</span>
            <span class="badge">${esc(squad.default_pattern)}</span>
            <button type="button" class="btn btn-sm sq-squad-link" data-open-squad="${esc(squad.squad_id)}">Open squad</button>
            <button type="button" class="btn btn-sm" data-map-squad="${esc(squad.squad_id)}" data-map-color="${color}">Map prompts</button>
          </summary>
          <div class="sq-agent-grid">
            ${agents.map(a => agentCardHtml(a, color)).join('')}
          </div>
        </details>`;
    }
    if (unassigned.length) {
      html += `
        <details class="sq-squad-group" open>
          <summary class="sq-squad-group-header sq-squad-group-unassigned">
            <strong>Unassigned</strong>
            <span class="badge">${unassigned.length} agent${unassigned.length !== 1 ? 's' : ''}</span>
          </summary>
          <div class="sq-agent-grid">
            ${unassigned.map(a => agentCardHtml(a, 'var(--border-color)')).join('')}
          </div>
        </details>`;
    }
    grid.innerHTML = html || '<p class="text-muted">No agents match.</p>';
  }

  async function load() {
    overview = await Api.getSquadsOverview();
    render();
  }

  function showError(message) {
    errorBox.textContent = message;
    errorBox.classList.toggle('hidden', !message);
  }

  async function updateAgent(request) {
    showError('');
    try {
      const updated = await request;
      const index = overview.agents.findIndex(agent => agent.id === updated.id);
      if (index >= 0) overview.agents[index] = updated;
      render();
    } catch (err) {
      showError(err.message);
    }
  }

  async function loadPrompt(agentId, contentEl) {
    if (promptCache[agentId]) {
      contentEl.innerHTML = `<pre class="sq-pre">${esc(promptCache[agentId])}</pre>`;
      return;
    }
    try {
      const { prompt } = await Api.getAgentPrompt(agentId);
      promptCache[agentId] = prompt;
      contentEl.innerHTML = `<pre class="sq-pre">${esc(prompt)}</pre>`;
    } catch (err) {
      contentEl.innerHTML = `<p class="text-muted small">Could not load prompt: ${esc(err.message)}</p>`;
    }
  }

  container.addEventListener('toggle', event => {
    const viewer = event.target.closest('[data-prompt-for]');
    if (!viewer || !viewer.open) return;
    const contentEl = viewer.querySelector('.sq-prompt-content');
    if (contentEl.querySelector('.sq-pre')) return;
    loadPrompt(viewer.dataset.promptFor, contentEl);
  }, true);

  container.addEventListener('change', event => {
    if (event.target.name === 'filter') { kindFilter = event.target.value; render(); }
    const card = event.target.closest('[data-agent]');
    if (card && event.target.hasAttribute('data-attach') && event.target.value) {
      updateAgent(Api.attachAgentTool(card.dataset.agent, event.target.value));
    }
  });
  container.querySelector('.sq-search').addEventListener('input', event => {
    search = event.target.value;
    render();
  });

  container.addEventListener('click', async event => {
    const button = event.target.closest('button');
    if (!button) return;
    if (button.dataset.act === 'toggle-create') { createForm.classList.toggle('hidden'); return; }
    if (button.dataset.openSquad) { navigate('squads', { squadId: button.dataset.openSquad }); return; }
    if (button.dataset.mapSquad) {
      openSquadMap(button.dataset.mapSquad, button.dataset.mapColor, overview);
      return;
    }
    const card = button.closest('[data-agent]');
    if (!card) return;
    const agent = overview.agents.find(a => a.id === card.dataset.agent);
    if (button.dataset.detach) updateAgent(Api.detachAgentTool(agent.id, button.dataset.detach));
    else if (button.dataset.act === 'edit') openInEditor(agent.path);
    else if (button.dataset.act === 'chat') navigate('chat', agent.id);
    else if (button.dataset.act === 'delete') {
      if (!confirm(`Delete sub-agent "${agent.id}"? Its YAML file will be removed.`)) return;
      try {
        await Api.deleteSubagent(agent.id);
        await load();
      } catch (err) {
        showError(err.message);
      }
    }
  });

  createForm.addEventListener('submit', async event => {
    event.preventDefault();
    const data = new FormData(createForm);
    const createError = createForm.querySelector('[data-role="create-error"]');
    createError.classList.add('hidden');
    try {
      const id = String(data.get('agent_id')).trim();
      await Api.createSquadMember(data.get('kind'), id, String(data.get('name')).trim() || id, String(data.get('description')).trim());
      createForm.reset();
      createForm.classList.add('hidden');
      await load();
    } catch (err) {
      createError.textContent = err.message;
      createError.classList.remove('hidden');
    }
  });

  load().catch(err => {
    grid.innerHTML = `<p class="text-danger">Could not load agents: ${esc(err.message)}</p>`;
  });
}
