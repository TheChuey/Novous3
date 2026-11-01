/**
 * Agents section: the library of primary agents (library/*.md) and sub-agents
 * (subAgentLibrary/*.yaml), which squads use them, their tools, and creating new ones.
 */
import { Api } from '../api.js';
import { esc, navigate, openInEditor } from '../shell.js';

let kindFilter = 'all';
let search = '';

export function renderAgentsView(container) {
  container.innerHTML = `
    <div class="sq-page">
      <header class="sq-page-head">
        <div>
          <h2>Agents</h2>
          <p class="text-muted">Primary agents lead or join squads; sub-agents are background workers that hand results back.</p>
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
      <div class="sq-agent-grid" data-role="grid"><p class="text-muted small">Loading agents…</p></div>
    </div>`;

  const grid = container.querySelector('[data-role="grid"]');
  const errorBox = container.querySelector('[data-role="error"]');
  const createForm = container.querySelector('[data-role="create"]');
  let overview = { agents: [], squads: [], tools: [] };

  function squadsUsing(agentId) {
    return overview.squads.filter(squad =>
      squad.primary_agent === agentId || squad.subagents.includes(agentId));
  }

  function render() {
    const term = search.toLowerCase();
    const shown = overview.agents.filter(agent =>
      (kindFilter === 'all' || agent.kind === kindFilter) &&
      (!term || [agent.id, agent.name, agent.description, ...agent.tools].join(' ').toLowerCase().includes(term)));
    grid.innerHTML = shown.map(agent => {
      const squads = squadsUsing(agent.id);
      const available = overview.tools.filter(tool => !agent.tools.includes(tool.name));
      return `
        <article class="card sq-agent-card" data-agent="${esc(agent.id)}">
          <div class="sq-inspector-head">
            <strong>${esc(agent.name)}</strong>
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
          <p class="small text-muted">${squads.length
            ? `In squads: ${squads.map(s => `<button type="button" class="sq-link" data-open-squad="${esc(s.squad_id)}">${esc(s.name)}</button>`).join(', ')}`
            : 'Not in any squad yet.'}</p>
          <div class="sq-inspector-actions">
            <button type="button" class="btn btn-sm" data-act="edit">Edit prompt</button>
            ${agent.kind === 'agent' ? '<button type="button" class="btn btn-sm" data-act="chat">Chat</button>' : ''}
            ${agent.kind === 'subagent' ? '<button type="button" class="btn btn-sm btn-danger" data-act="delete">Delete</button>' : ''}
          </div>
        </article>`;
    }).join('') || '<p class="text-muted">No agents match.</p>';
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
