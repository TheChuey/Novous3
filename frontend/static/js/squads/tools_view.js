/**
 * Tools section: the tool catalog with who uses each tool, attaching tools to agents, and
 * the Tool Workbench (generate → test → promote) for building new ones.
 */
import { Api } from '../api.js';
import { esc } from '../shell.js';
import { renderTestingView } from '../testing.js';

export function renderToolsView(container, payload = null) {
  let attachTo = payload?.attachTo || '';
  container.innerHTML = `
    <div class="sq-page">
      <header class="sq-page-head">
        <div>
          <h2>Tools</h2>
          <p class="text-muted">Give agents the abilities they need. Build a new tool below, test it, promote it, then attach it.</p>
        </div>
      </header>
      <div class="sq-msg sq-msg-ok hidden" data-role="promoted" role="status"></div>
      <section class="card">
        <div class="card-head">
          <h3>Tool catalog</h3>
          <input class="form-input sq-search" type="search" placeholder="Filter tools" aria-label="Filter tools">
        </div>
        <p class="small text-danger hidden" data-role="error" role="alert"></p>
        <div class="sq-tool-catalog" data-role="catalog"><p class="text-muted small">Loading tools…</p></div>
      </section>
      <div data-role="workbench"></div>
    </div>`;

  const catalog = container.querySelector('[data-role="catalog"]');
  const errorBox = container.querySelector('[data-role="error"]');
  const promoted = container.querySelector('[data-role="promoted"]');
  let overview = { agents: [], tools: [] };
  let filter = '';

  const agentOptions = (exclude = []) => overview.agents
    .filter(agent => !exclude.includes(agent.id))
    .map(agent => `<option value="${esc(agent.id)}" ${agent.id === attachTo ? 'selected' : ''}>${esc(agent.name)} (${agent.kind === 'agent' ? 'primary' : 'sub-agent'})</option>`)
    .join('');

  function render() {
    const term = filter.toLowerCase();
    const tools = overview.tools.filter(tool =>
      !term || `${tool.name} ${tool.provider} ${tool.description}`.toLowerCase().includes(term));
    catalog.innerHTML = tools.map(tool => {
      const users = overview.agents.filter(agent => agent.tools.includes(tool.name));
      const description = Array.isArray(tool.description) ? tool.description.join(' ') : (tool.description || '');
      return `
        <div class="sq-tool-item" data-tool="${esc(tool.name)}">
          <div>
            <strong>${esc(tool.name)}</strong> <span class="badge">${esc(tool.provider)}</span>
            ${tool.custom ? '<span class="badge badge-accent">custom</span>' : ''}
            <p class="text-muted small">${esc(description.split('\n')[0] || 'No description.')}</p>
            <code class="small">${esc(tool.signature)}</code>
          </div>
          <div class="sq-tool-users">
            <span class="small text-muted">${users.length ? `Used by ${users.map(u => esc(u.name)).join(', ')}` : 'Not used by any agent'}</span>
            <span class="sq-tool-add">
              <select class="form-select" aria-label="Agent to attach ${esc(tool.name)} to">
                <option value="">Attach to…</option>${agentOptions(users.map(u => u.id))}
              </select>
              <button type="button" class="btn btn-sm" data-attach>Attach</button>
            </span>
          </div>
        </div>`;
    }).join('') || '<p class="text-muted small">No tools match.</p>';
  }

  async function load() {
    overview = await Api.getSquadsOverview();
    render();
  }

  async function attach(agentId, tool) {
    errorBox.classList.add('hidden');
    try {
      const updated = await Api.attachAgentTool(agentId, tool);
      attachTo = agentId;
      await load();
      return updated;
    } catch (err) {
      errorBox.textContent = err.message;
      errorBox.classList.remove('hidden');
      return null;
    }
  }

  container.querySelector('.sq-search').addEventListener('input', event => {
    filter = event.target.value;
    render();
  });
  catalog.addEventListener('click', async event => {
    const button = event.target.closest('[data-attach]');
    if (!button) return;
    const row = button.closest('[data-tool]');
    const agentId = row.querySelector('select').value;
    if (agentId) await attach(agentId, row.dataset.tool);
  });

  // A tool promoted in the workbench can be attached straight away.
  container.addEventListener('novous:tool-promoted', async event => {
    const name = event.detail.name;
    await load();
    promoted.classList.remove('hidden');
    promoted.innerHTML = `
      <span><strong>${esc(name)}</strong> is in the tool catalog. Attach it to an agent:</span>
      <select class="form-select" aria-label="Agent for ${esc(name)}"><option value="">Choose agent…</option>${agentOptions()}</select>
      <button type="button" class="btn btn-sm btn-primary">Attach</button>`;
    promoted.querySelector('button').addEventListener('click', async () => {
      const agentId = promoted.querySelector('select').value;
      if (!agentId) return;
      const updated = await attach(agentId, name);
      if (updated) promoted.innerHTML = `<span>Attached <strong>${esc(name)}</strong> to ${esc(updated.name)}.</span>`;
    });
  });

  renderTestingView(container.querySelector('[data-role="workbench"]'));
  load().catch(err => {
    catalog.innerHTML = `<p class="text-danger small">Could not load tools: ${esc(err.message)}</p>`;
  });
}
