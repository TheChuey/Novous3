/**
 * Agent library for the Overview: every agent across environments in one searchable,
 * filterable list, grouped by environment, with chat/open, edit and delete actions.
 */
import { Api } from '../api.js';
import { iconSvg } from '../icons.js';
import { esc, navigate, refreshAgents } from '../shell.js';

const ENVIRONMENTS = {
  workspace: { label: 'Workspace', open: 'Chat', openTab: 'chat', passId: true },
  'prompt-creation': { label: 'Prompt Creation', open: 'Open', openTab: 'prompt-creation', passId: false },
  codebuilder: { label: 'Code Builder', open: 'Open', openTab: 'codebuilder', passId: false },
};

// Filters survive re-renders of the Overview.
const filters = { text: '', environment: 'all', mode: 'all', sort: 'name' };
const collapsedGroups = new Set();

const SORTS = {
  name: (a, b) => a.name.localeCompare(b.name),
  tools: (a, b) => (b.tools?.length || 0) - (a.tools?.length || 0) || a.name.localeCompare(b.name),
  model: (a, b) => String(a.model || '').localeCompare(String(b.model || '')) || a.name.localeCompare(b.name),
};

async function loadAllAgents() {
  const [workspace, prompt, codebuilder] = await Promise.all([
    Api.getAgents('workspace'),
    Api.getAgents('prompt-creation'),
    Api.getCodeBuilderAgents(),
  ]);
  const tag = (list, environment) => (list.agents || []).map(agent => ({ ...agent, environment }));
  return [...tag(workspace, 'workspace'), ...tag(prompt, 'prompt-creation'), ...tag(codebuilder, 'codebuilder')];
}

function matches(agent) {
  if (filters.environment !== 'all' && agent.environment !== filters.environment) return false;
  if (filters.mode !== 'all' && agent.mode !== filters.mode) return false;
  const term = filters.text.trim().toLowerCase();
  if (!term) return true;
  return [agent.name, agent.id, agent.description, agent.model, ...(agent.tools || [])]
    .join(' ').toLowerCase().includes(term);
}

function agentRow(agent) {
  const env = ENVIRONMENTS[agent.environment];
  const tools = agent.tools || [];
  const initials = String(agent.name || agent.id).split(/[\s_-]+/).map(part => part[0]).join('').slice(0, 2).toUpperCase();
  return `
    <li class="ov-agent" data-agent="${esc(agent.id)}" data-env="${esc(agent.environment)}">
      <span class="ov-avatar ov-avatar-${esc(agent.environment)}" aria-hidden="true">${esc(initials)}</span>
      <div class="ov-agent-main">
        <div class="ov-agent-title">
          <strong>${esc(agent.name)}</strong>
          <code>${esc(agent.id)}</code>
        </div>
        <p>${esc(agent.description || 'No description.')}</p>
      </div>
      <div class="ov-agent-meta">
        <span class="badge ${agent.mode === 'agent' ? 'badge-accent' : ''}">${esc(agent.mode)}</span>
        <span class="ov-tools" title="${esc(tools.join(', ') || 'No tools')}">${tools.length} tool${tools.length === 1 ? '' : 's'}</span>
        <span class="ov-model" title="Model">${esc(agent.model || 'default model')}</span>
      </div>
      <div class="ov-agent-actions">
        <button type="button" class="cb-btn" data-act="open">${iconSvg(env.openTab === 'chat' ? 'send' : 'play')}<span>${env.open}</span></button>
        <button type="button" class="cb-btn cb-btn-icon cb-btn-ghost" data-act="edit" title="Edit prompt" aria-label="Edit ${esc(agent.name)}">${iconSvg('file-code')}</button>
        <button type="button" class="cb-btn cb-btn-icon cb-btn-ghost ov-delete" data-act="delete" title="Delete agent" aria-label="Delete ${esc(agent.name)}">${iconSvg('trash-2')}</button>
      </div>
    </li>`;
}

export function renderAgentLibrary(host) {
  host.innerHTML = `
    <div class="ov-agents-head">
      <div>
        <h3>Agent library <span class="text-muted small" data-role="total"></span></h3>
        <p class="text-muted small">Every agent in this workspace. Search by name, id, description, model or tool.</p>
      </div>
      <button type="button" class="cb-btn cb-btn-primary" data-act="toggle-create">${iconSvg('plus')}<span>New agent</span></button>
    </div>
    <form class="ov-create hidden" data-role="create">
      <div class="ov-create-grid">
        <label><span>Id</span><input class="form-input" name="id" placeholder="reviewer" required maxlength="64"></label>
        <label><span>Display name</span><input class="form-input" name="name" placeholder="Code Reviewer" maxlength="100"></label>
        <label class="ov-create-wide"><span>Description</span><input class="form-input" name="description" placeholder="What this agent is for" maxlength="500"></label>
        <label><span>Environment</span>
          <select class="cb-select" name="environment">
            ${Object.entries(ENVIRONMENTS).map(([key, env]) => `<option value="${key}">${env.label}</option>`).join('')}
          </select></label>
        <label><span>Mode</span>
          <select class="cb-select" name="mode"><option value="chat">chat</option><option value="agent">agent (uses tools)</option></select></label>
        <label class="ov-create-wide"><span>Custom environment name <em class="text-muted">(optional)</em></span>
          <input class="form-input" name="environment_name" maxlength="64"></label>
      </div>
      <div class="ov-create-actions">
        <p class="small text-danger" data-role="create-error" role="alert"></p>
        <button type="button" class="cb-btn cb-btn-ghost" data-act="toggle-create">Cancel</button>
        <button type="submit" class="cb-btn cb-btn-primary">Create agent</button>
      </div>
    </form>
    <div class="ov-toolbar">
      <label class="ov-search">
        ${iconSvg('search')}
        <input type="search" class="form-input" placeholder="Search agents…  ( / )" value="${esc(filters.text)}" aria-label="Search agents">
      </label>
      <div class="sq-segmented" role="radiogroup" aria-label="Environment" data-role="envs"></div>
      <select class="cb-select" data-role="mode" aria-label="Mode">
        <option value="all">Any mode</option><option value="agent">Agent mode</option><option value="chat">Chat mode</option>
      </select>
      <select class="cb-select" data-role="sort" aria-label="Sort">
        <option value="name">Sort: name</option><option value="tools">Sort: most tools</option><option value="model">Sort: model</option>
      </select>
    </div>
    <div class="ov-groups" data-role="groups"><p class="text-muted small">Loading agents…</p></div>`;

  const groupsBox = host.querySelector('[data-role="groups"]');
  const envBox = host.querySelector('[data-role="envs"]');
  const search = host.querySelector('input[type="search"]');
  const createForm = host.querySelector('[data-role="create"]');
  host.querySelector('[data-role="mode"]').value = filters.mode;
  host.querySelector('[data-role="sort"]').value = filters.sort;
  let agents = [];

  function render() {
    const counts = agents.reduce((acc, agent) => ({ ...acc, [agent.environment]: (acc[agent.environment] || 0) + 1 }), {});
    host.querySelector('[data-role="total"]').textContent = `· ${agents.length}`;
    envBox.innerHTML = [['all', 'All', agents.length], ...Object.entries(ENVIRONMENTS).map(([key, env]) => [key, env.label, counts[key] || 0])]
      .map(([key, label, count]) => `
        <label><input type="radio" name="ov-env" value="${key}" ${filters.environment === key ? 'checked' : ''}>
          ${esc(label)} <span class="ov-count">${count}</span></label>`).join('');

    const shown = agents.filter(matches).sort(SORTS[filters.sort]);
    if (!shown.length) {
      groupsBox.innerHTML = agents.length
        ? `<div class="ov-empty"><p>No agents match these filters.</p><button type="button" class="cb-btn" data-act="clear-filters">Clear filters</button></div>`
        : `<div class="ov-empty"><p>No agents yet.</p><button type="button" class="cb-btn cb-btn-primary" data-act="toggle-create">Create the first one</button></div>`;
      return;
    }
    groupsBox.innerHTML = Object.entries(ENVIRONMENTS).map(([key, env]) => {
      const items = shown.filter(agent => agent.environment === key);
      if (!items.length) return '';
      return `
        <details class="ov-group" data-group="${key}" ${collapsedGroups.has(key) ? '' : 'open'}>
          <summary><span>${esc(env.label)}</span><span class="ov-count">${items.length}</span></summary>
          <ul class="ov-agent-list">${items.map(agentRow).join('')}</ul>
        </details>`;
    }).join('');
  }

  async function load() {
    try {
      agents = await loadAllAgents();
      render();
    } catch (err) {
      groupsBox.innerHTML = `<p class="text-danger small">Could not load agents: ${esc(err.message)}</p>`;
    }
  }

  search.addEventListener('input', () => { filters.text = search.value; render(); });
  host.addEventListener('change', event => {
    const target = event.target;
    if (target.name === 'ov-env') filters.environment = target.value;
    else if (target.dataset.role === 'mode') filters.mode = target.value;
    else if (target.dataset.role === 'sort') filters.sort = target.value;
    else return;
    render();
  });
  host.addEventListener('toggle', event => {
    const group = event.target.closest?.('[data-group]');
    if (!group) return;
    if (group.open) collapsedGroups.delete(group.dataset.group);
    else collapsedGroups.add(group.dataset.group);
  }, true);

  // "/" jumps to search, like most list UIs.
  const onKey = event => {
    if (!host.isConnected) { document.removeEventListener('keydown', onKey); return; }
    if (event.key === '/' && !/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName || '')) {
      event.preventDefault();
      search.focus();
    }
  };
  document.addEventListener('keydown', onKey);

  host.addEventListener('click', async event => {
    const button = event.target.closest('button');
    if (!button) return;
    const act = button.dataset.act;
    if (act === 'toggle-create') {
      createForm.classList.toggle('hidden');
      if (!createForm.classList.contains('hidden')) createForm.querySelector('[name="id"]').focus();
      return;
    }
    if (act === 'clear-filters') {
      Object.assign(filters, { text: '', environment: 'all', mode: 'all' });
      search.value = '';
      host.querySelector('[data-role="mode"]').value = 'all';
      render();
      return;
    }
    const row = button.closest('[data-agent]');
    if (!row) return;
    const agent = agents.find(a => a.id === row.dataset.agent && a.environment === row.dataset.env);
    const env = ENVIRONMENTS[agent.environment];
    if (act === 'open') navigate(env.openTab, env.passId ? agent.id : null);
    else if (act === 'edit') navigate('editor', agent.path || `library/${agent.id}.md`);
    else if (act === 'delete') {
      if (!confirm(`Delete ${env.label} agent "${agent.name}"? Its definition file will be removed.`)) return;
      button.disabled = true;
      try {
        await Api.deleteAgent(agent.id, agent.environment);
        await refreshAgents();
        await load();
      } catch (err) {
        alert(err.message);
        button.disabled = false;
      }
    }
  });

  createForm.addEventListener('submit', async event => {
    event.preventDefault();
    const data = new FormData(createForm);
    const errorBox = createForm.querySelector('[data-role="create-error"]');
    errorBox.textContent = '';
    const id = String(data.get('id')).trim();
    try {
      await Api.createAgent(id, String(data.get('name')).trim() || id, String(data.get('description')).trim(),
        data.get('mode'), '', data.get('environment'), String(data.get('environment_name')).trim());
      createForm.reset();
      createForm.classList.add('hidden');
      filters.text = '';
      search.value = '';
      await refreshAgents();
      await load();
    } catch (err) {
      errorBox.textContent = err.message;
    }
  });

  load();
}
