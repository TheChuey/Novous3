/**
 * Step-by-step squad builder: Basics → Lead → Members → Tools → Review & save.
 * Works on a local draft and only writes through the Squad Architect API on save.
 */
import { Api } from '../api.js';
import { esc, navigate } from '../shell.js';
import { renderArchitectCanvas } from './architect_canvas.js';

const STEPS = ['Basics', 'Lead agent', 'Members', 'Tools', 'Review'];
const LEAD_HANDOFF = 'read_squad_scratchpad';
const MEMBER_HANDOFF = 'write_squad_scratchpad';

function sameList(a = [], b = []) {
  return a.length === b.length && a.every((value, i) => value === b[i]);
}

/**
 * @param {HTMLElement} host
 * @param {{overview: object, initial?: object|null, onSaved: Function, onCancel: Function}} options
 */
export function renderBuilder(host, { overview, initial = null, onSaved, onCancel }) {
  let agents = overview.agents || [];
  const toolCatalog = overview.tools || [];
  const byId = () => Object.fromEntries(agents.map(agent => [agent.id, agent]));

  const draft = {
    squad_id: initial?.squad_id || null,
    name: initial?.name || '',
    description: initial?.description || '',
    pattern: 'sequential',
    lead: initial?.lead?.id || '',
    members: (initial?.members || []).filter(member => member?.kind === 'subagent').map(member => member.id),
    tools: {}
  };
  let step = 0;
  let validation = null;

  const toolsFor = id => draft.tools[id] ?? [...(byId()[id]?.tools || [])];
  const nodeFor = id => ({ ...byId()[id], tools: toolsFor(id) });

  function payload() {
    const spec = id => {
      const changed = draft.tools[id] && !sameList(draft.tools[id], byId()[id]?.tools || []);
      return changed ? { agent_id: id, tools: draft.tools[id] } : { agent_id: id };
    };
    return {
      squad_id: draft.squad_id,
      name: draft.name.trim(),
      description: draft.description.trim(),
      pattern: draft.pattern,
      lead: spec(draft.lead),
      members: draft.members.map(spec)
    };
  }

  function canAdvance() {
    if (step === 0) return draft.name.trim().length > 0;
    if (step === 1) return Boolean(draft.lead);
    return true;
  }

  function render() {
    host.innerHTML = `
      <div class="sq-builder card">
        <div class="card-head">
          <div>
            <h3>${draft.squad_id ? `Edit squad · ${esc(draft.name)}` : 'New squad'}</h3>
            <p class="text-muted small">Choose one lead and an ordered set of sub-agents. Each step runs once at a time and passes its result forward.</p>
          </div>
          <button type="button" class="btn btn-sm" data-act="cancel">Close</button>
        </div>
        <ol class="sq-stepper">
          ${STEPS.map((label, i) => `
            <li class="${i === step ? 'is-current' : i < step ? 'is-done' : ''}">
              <button type="button" data-step="${i}" ${i > step && !canAdvance() ? 'disabled' : ''}>
                <span class="sq-step-num">${i + 1}</span>${esc(label)}
              </button>
            </li>`).join('')}
        </ol>
        <div class="sq-step-body">${stepBody()}</div>
        <p class="small text-danger hidden" data-role="error" role="alert"></p>
        <div class="sq-builder-nav">
          <button type="button" class="btn" data-act="back" ${step === 0 ? 'disabled' : ''}>Back</button>
          <span class="spacer"></span>
          ${step < STEPS.length - 1
            ? `<button type="button" class="btn btn-primary" data-act="next" ${canAdvance() ? '' : 'disabled'}>Next: ${esc(STEPS[step + 1])}</button>`
            : `<button type="button" class="btn btn-primary" data-act="save" ${validation?.ok ? '' : 'disabled'}>Save squad</button>`}
        </div>
      </div>`;
    if (step === 4) afterReviewRender();
  }

  function stepBody() {
    if (step === 0) {
      return `
        <label class="sq-field"><span>Squad name</span>
          <input class="form-input" data-field="name" maxlength="80" value="${esc(draft.name)}" placeholder="e.g. Research Squad"></label>
        <label class="sq-field"><span>What does this squad do?</span>
          <input class="form-input" data-field="description" maxlength="500" value="${esc(draft.description)}"></label>
        <p class="sq-field text-muted small">The workflow is always sequential to keep local model use predictable. You control the exact run order in the Members step.</p>`;
    }
    if (step === 1) {
      const primaries = agents.filter(agent => agent.kind === 'agent');
      return `
        <p class="text-muted small">The lead receives the request and produces the final answer after sub-agents complete their ordered steps.</p>
        <div class="sq-choice-grid">
          ${primaries.map(agent => `
            <label class="sq-choice ${draft.lead === agent.id ? 'is-selected' : ''}">
              <input type="radio" name="lead" value="${esc(agent.id)}" ${draft.lead === agent.id ? 'checked' : ''}>
              <strong>${esc(agent.name)}</strong>
              <span class="text-muted small">${esc(agent.description || agent.id)}</span>
              <span class="sq-chip-row">${(agent.tools || []).slice(0, 4).map(t => `<span class="sq-chip">${esc(t)}</span>`).join('')}</span>
            </label>`).join('') || '<p class="text-muted">No primary agents yet.</p>'}
        </div>
        ${createMemberForm('agent', true)}`;
    }
    if (step === 2) {
      const candidates = agents.filter(agent => agent.id !== draft.lead && agent.kind === 'subagent');
      const group = `
        <h4 class="sq-group-title">Sub-agents <span class="text-muted small">run one at a time and hand their result to the next step</span></h4>
        <div class="sq-choice-grid">
          ${candidates.map(agent => `
            <label class="sq-choice ${draft.members.includes(agent.id) ? 'is-selected' : ''}">
              <input type="checkbox" name="member" value="${esc(agent.id)}" ${draft.members.includes(agent.id) ? 'checked' : ''}>
              <strong>${esc(agent.name)}</strong>
              <span class="text-muted small">${esc(agent.description || agent.id)}</span>
            </label>`).join('') || '<p class="text-muted small">None available.</p>'}
        </div>`;
      return `
        <div class="sq-two-col">
          <div>
            ${group}
            ${createMemberForm('subagent')}
          </div>
          <aside class="sq-order card">
            <h4>Run order</h4>
            ${draft.members.length ? `<ol class="sq-order-list">
              ${draft.members.map((id, i) => `
                <li><span>${esc(byId()[id]?.name || id)}</span>
                  <span class="sq-order-actions">
                    <button type="button" class="btn btn-sm btn-ghost" data-move="${i}:-1" ${i === 0 ? 'disabled' : ''} aria-label="Move up">↑</button>
                    <button type="button" class="btn btn-sm btn-ghost" data-move="${i}:1" ${i === draft.members.length - 1 ? 'disabled' : ''} aria-label="Move down">↓</button>
                  </span></li>`).join('')}
            </ol>` : '<p class="text-muted small">Pick members on the left.</p>'}
            <p class="text-muted small">Each step receives the task and the previous step's result—not the entire conversation.</p>
          </aside>
        </div>`;
    }
    if (step === 3) {
      const ids = [draft.lead, ...draft.members];
      return `
        <p class="text-muted small">Tools decide what each agent can do. Assigning tools switches a chat-mode agent to agent mode.
          Squad runs control the order and withhold direct sub-agent delegation.
          Need something new? <button type="button" class="btn btn-sm" data-act="build-tool">Build a tool in the workbench</button></p>
        <div class="sq-tool-table">
          ${ids.map((id, index) => toolRow(id, index === 0)).join('')}
        </div>`;
    }
    return `
      <div class="sq-review">
        <div class="sq-canvas-host" data-role="preview"></div>
        <div class="sq-validation" data-role="validation"><p class="text-muted small">Checking blueprint…</p></div>
      </div>`;
  }

  function toolRow(id, isLead) {
    const tools = toolsFor(id);
    const agent = byId()[id] || { id, name: id, kind: 'agent' };
    const handoff = isLead ? LEAD_HANDOFF : agent.kind === 'subagent' ? MEMBER_HANDOFF : null;
    const missingHandoff = handoff && !tools.includes(handoff);
    const available = toolCatalog.filter(tool => !tools.includes(tool.name));
    return `
      <div class="sq-tool-row">
        <div class="sq-tool-owner">
          <strong>${esc(agent.name)}</strong>
          <span class="badge ${isLead ? 'badge-accent' : ''}">${isLead ? 'lead' : agent.kind === 'subagent' ? 'sub-agent' : 'agent'}</span>
        </div>
        <div class="sq-chip-row">
          ${tools.map(tool => `<span class="sq-chip">${esc(tool)}
            <button type="button" class="sq-chip-x" data-remove-tool="${esc(id)}|${esc(tool)}" aria-label="Remove ${esc(tool)}">×</button></span>`).join('')
            || '<span class="text-muted small">No tools</span>'}
        </div>
        <div class="sq-tool-add">
          <select class="form-select" data-add-tool="${esc(id)}" aria-label="Add a tool to ${esc(agent.name)}">
            <option value="">+ Add tool…</option>
            ${available.map(tool => `<option value="${esc(tool.name)}" title="${esc(tool.description || '')}">${esc(tool.name)} · ${esc(tool.provider)}</option>`).join('')}
          </select>
          ${missingHandoff ? `<button type="button" class="btn btn-sm" data-quick-tool="${esc(id)}|${handoff}">Add ${handoff}</button>` : ''}
        </div>
      </div>`;
  }

  function createMemberForm(defaultKind, allowPrimary = false) {
    return `
      <details class="sq-create">
        <summary>Create a new ${allowPrimary ? 'lead agent or sub-agent' : 'sub-agent'}</summary>
        <div class="form-row mt-2">
          <select class="form-select" data-new="kind" aria-label="Kind">
            <option value="subagent" ${defaultKind === 'subagent' ? 'selected' : ''}>Sub-agent (subAgentLibrary/*.yaml)</option>
            ${allowPrimary ? `<option value="agent" ${defaultKind === 'agent' ? 'selected' : ''}>Primary agent (library/*.md)</option>` : ''}
          </select>
          <input class="form-input" data-new="id" placeholder="id (e.g. summarizer)" maxlength="64">
          <input class="form-input" data-new="name" placeholder="Display name" maxlength="100">
        </div>
        <div class="form-row">
          <input class="form-input" data-new="description" placeholder="What it does" maxlength="500">
          <button type="button" class="btn btn-primary" data-act="create-member">Create</button>
        </div>
      </details>`;
  }

  async function afterReviewRender() {
    const preview = host.querySelector('[data-role="preview"]');
    renderArchitectCanvas(preview, {
      pattern: draft.pattern,
      lead: nodeFor(draft.lead),
      members: draft.members.map(nodeFor)
    });
    const box = host.querySelector('[data-role="validation"]');
    try {
      validation = await Api.validateSquadBlueprint(payload());
    } catch (err) {
      validation = { ok: false, errors: [err.message], warnings: [] };
    }
    box.innerHTML = `
      <h4>${validation.ok ? '✓ Ready to save' : 'Fix before saving'}</h4>
      ${validation.errors.map(e => `<p class="sq-msg sq-msg-error">${esc(e)}</p>`).join('')}
      ${validation.warnings.map(w => `<p class="sq-msg sq-msg-warn">${esc(w)}</p>`).join('')}
      ${!validation.errors.length && !validation.warnings.length ? '<p class="text-muted small">No issues found.</p>' : ''}
      <p class="text-muted small">Saving registers the squad and writes tool changes into the agents' definition files.</p>`;
    const save = host.querySelector('[data-act="save"]');
    if (save) save.disabled = !validation.ok;
  }

  function showError(message) {
    const box = host.querySelector('[data-role="error"]');
    box.textContent = message;
    box.classList.toggle('hidden', !message);
  }

  function setTools(id, tools) {
    draft.tools[id] = tools;
    validation = null;
    render();
  }

  host.addEventListener('input', event => {
    const field = event.target.dataset.field;
    if (!field) return;
    draft[field] = event.target.value;
    const next = host.querySelector('[data-act="next"]');
    if (next) next.disabled = !canAdvance();
  });

  host.addEventListener('change', event => {
    const target = event.target;
    if (target.name === 'lead') {
      draft.lead = target.value;
      draft.members = draft.members.filter(id => id !== draft.lead);
      render();
    } else if (target.name === 'member') {
      draft.members = target.checked
        ? [...draft.members, target.value]
        : draft.members.filter(id => id !== target.value);
      render();
    } else if (target.dataset.addTool && target.value) {
      setTools(target.dataset.addTool, [...toolsFor(target.dataset.addTool), target.value]);
    }
  });

  host.addEventListener('click', async event => {
    const button = event.target.closest('button');
    if (!button) return;
    showError('');
    if (button.dataset.step !== undefined) { step = Number(button.dataset.step); render(); return; }
    if (button.dataset.move) {
      const [index, delta] = button.dataset.move.split(':').map(Number);
      const list = [...draft.members];
      [list[index], list[index + delta]] = [list[index + delta], list[index]];
      draft.members = list;
      render();
      return;
    }
    if (button.dataset.removeTool) {
      const [id, tool] = button.dataset.removeTool.split('|');
      setTools(id, toolsFor(id).filter(name => name !== tool));
      return;
    }
    if (button.dataset.quickTool) {
      const [id, tool] = button.dataset.quickTool.split('|');
      setTools(id, [...toolsFor(id), tool]);
      return;
    }
    const act = button.dataset.act;
    if (act === 'cancel') onCancel();
    else if (act === 'back') { step -= 1; render(); }
    else if (act === 'next' && canAdvance()) { step += 1; render(); }
    else if (act === 'build-tool') navigate('tools');
    else if (act === 'create-member') {
      const form = button.closest('.sq-create');
      const value = key => form.querySelector(`[data-new="${key}"]`).value.trim();
      button.disabled = true;
      try {
        const created = await Api.createSquadMember(value('kind'), value('id'), value('name') || value('id'), value('description'));
        agents = (await Api.getSquadsOverview()).agents || agents;
        if (step === 1 && created.kind === 'agent') draft.lead = created.id;
        else if (created.kind === 'subagent' && created.id !== draft.lead && !draft.members.includes(created.id)) {
          draft.members.push(created.id);
        }
        render();
      } catch (err) {
        showError(err.message);
        button.disabled = false;
      }
    } else if (act === 'save') {
      button.disabled = true;
      try {
        const saved = await Api.saveSquadBlueprint(payload());
        onSaved(saved.blueprint, saved.warnings || []);
      } catch (err) {
        showError(err.message);
        button.disabled = false;
      }
    }
  });

  render();
}
