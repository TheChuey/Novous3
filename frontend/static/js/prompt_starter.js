import { Api } from './api.js';
import { esc } from './shell.js';
import { groupTools } from './tool_categories.js';

const VARIABLE = /\[([A-Z][A-Z0-9 _-]{0,63})\]/g;

export function starterVariables(text) {
  return [...new Set([...text.matchAll(VARIABLE)].map(match => match[1]))];
}

export function renderStarter(text, values) {
  return text.replace(VARIABLE, (match, name) => {
    const value = values[name]?.trim();
    if (value) return value;
    return /^TOOL \d+$/.test(name) ? '' : match;
  });
}

export function replaceToolVariables(text, indexes) {
  const lines = text.replace(/\r\n/g, '\n').split('\n');
  if (lines[0]?.trim() !== '---') throw new Error('Add tools only to an agent definition with YAML frontmatter.');
  let end = lines.findIndex((line, index) => index > 0 && line.trim() === '---');
  if (end < 0) throw new Error('The agent definition is missing its YAML frontmatter closing delimiter.');
  let toolsIndex = lines.findIndex((line, index) => index > 0 && index < end && /^tools\s*:/.test(line));
  if (toolsIndex < 0) {
    lines.splice(end, 0, indexes.length ? 'tools:' : 'tools: []');
    if (!indexes.length) return lines.join('\n');
    toolsIndex = end;
    end += 1;
  }
  const toolsLine = lines[toolsIndex].trim();
  if (/^tools\s*:\s*\[\s*\]$/.test(toolsLine)) {
    if (!indexes.length) return lines.join('\n');
    lines[toolsIndex] = 'tools:';
  } else if (!/^tools\s*:\s*$/.test(toolsLine)) {
    throw new Error('The tools field must be an empty list or a block list before adding a tool.');
  }
  let blockEnd = toolsIndex + 1;
  while (blockEnd < end && (!lines[blockEnd].trim() || /^\s+-\s/.test(lines[blockEnd]))) blockEnd += 1;
  if (blockEnd < end && /^\s/.test(lines[blockEnd])) {
    throw new Error('The tools field contains an unsupported YAML entry.');
  }
  lines.splice(toolsIndex + 1, blockEnd - toolsIndex - 1, ...indexes.map(index => `  - "[TOOL ${index}]"`));
  if (!indexes.length) lines[toolsIndex] = 'tools: []';
  return lines.join('\n');
}

export function addToolParameterVariables(instructions, toolIndex, descriptions = {}) {
  let inParameters = false;
  return instructions.split(/\r?\n/).map(line => {
    if (line.trim() === 'Parameters:') {
      inParameters = true;
      return line;
    }
    if (inParameters && line.trim().startsWith('Return format:')) inParameters = false;
    const match = inParameters && line.match(/^(-\s+)(.+?):\s*(.*)$/);
    if (!match) return line;
    const parameterName = match[2].replace(/\s*\(required\)$/i, '');
    const tokenName = parameterName.toUpperCase()
      .replace(/[^A-Z0-9 _-]/g, ' ')
      .replace(/\s+/g, ' ')
      .trim() || 'VALUE';
    const variable = `TOOL ${toolIndex} ${tokenName}`.slice(0, 63).trimEnd();
    if (match[3]?.trim()) descriptions[variable] = match[3].trim();
    return `${match[1]}${match[2]}: [${variable}]${match[3] ? ` - ${match[3]}` : ''}`;
  }).join('\n');
}

export function starterEditorText(node) {
  if (node.nodeType === 3) return node.nodeValue;
  if (node.nodeType !== 1 && node.nodeType !== 11) return '';
  if (node.nodeType === 1) {
    if (node.hasAttribute('data-placeholder')) return `[${node.dataset.placeholder}]`;
    if (node.tagName === 'BR') return '\n';
  }
  let text = '';
  for (const child of node.childNodes) {
    const block = child.nodeType === 1 && (child.tagName === 'DIV' || child.tagName === 'P');
    if (block && text && !text.endsWith('\n')) text += '\n';
    text += starterEditorText(child);
  }
  return text;
}

async function choiceRequest(method, body) {
  const response = await fetch('/api/squad-development/prompt-starter/choices', {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Could not load Prompt Starter choices.');
  return data.choices;
}

export function extractSubagentConfig(values) {
  const id = (values['SUB-AGENT ID'] || values['SUBAGENT ID'] || '').trim();
  if (!id) return null;
  const usage = [values['CONDITION'], values['TASK'], values['HOW TO USE THE RESULT']]
    .filter(v => v?.trim()).map(v => v.trim()).join('\n') || '';
  return { id, usage };
}

function toAgentId(name) {
  return name.trim().toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 64) || 'new-agent';
}

export function createPromptStarter(container) {
  const host = document.createElement('div');
  host.innerHTML = `<dialog class="sd-starter-dialog" aria-labelledby="sd-starter-title">
    <header class="sd-starter-head">
      <div><h2 id="sd-starter-title">Prompt Starter</h2>
      <p class="small text-muted">Edit the Markdown directly. Select a tool or sub-agent in the text, and fill the other variables on the right.</p></div>
      <button type="button" class="btn btn-sm" data-starter="close">Close</button>
    </header>
    <div class="sd-starter-template-bar">
      <label class="small" for="sd-starter-tpl-select">Template:</label>
      <select id="sd-starter-tpl-select" class="form-select form-select-sm" data-starter="tpl-select">
        <option value="agent">Default (Novous Agent)</option>
        <option value="subagent">Default (Novous Sub-agent YAML)</option>
      </select>
      <button type="button" class="btn btn-sm" data-starter="tpl-save" title="Save current editor as a reusable template">Save as Template</button>
      <button type="button" class="btn btn-sm" data-starter="tpl-delete" title="Delete the selected template" disabled>Delete</button>
      <button type="button" class="btn btn-sm" data-starter="tpl-load-generic" title="Load the generic prompt from the server">Load Generic</button>
    </div>
    <p class="small text-muted">Add only the tools you need. Their descriptions and parameter variables are added directly to the prompt. The sub-agent default edits its generated prompt body.</p>
    <p class="small text-danger" role="alert" data-starter="error"></p>
    <div class="sd-starter-layout">
      <section class="sd-starter-main">
        <div class="sd-starter-editor-toolbar">
          <h3 data-starter="editor-heading">Agent definition</h3>
          <button type="button" class="btn btn-sm" data-starter="add-tool">+ Add tools</button>
        </div>
        <p class="small text-muted">Add a placeholder like [NEW VARIABLE] to create its own field. Click a tool or sub-agent variable to choose it, or another variable to jump to its field.</p>
        <div class="sd-starter-editor" data-starter="editor" contenteditable="true" role="textbox" aria-multiline="true" aria-label="Prompt Starter Markdown editor" spellcheck="false"></div>
      </section>
      <div class="sd-starter-divider" data-starter="layout-divider" title="Drag to resize"></div>
      <aside class="sd-starter-fields" aria-label="Prompt variables">
        <h3>Variables</h3>
        <p class="small text-muted">Values appear in the Markdown. Save a choice to reuse it later.</p>
        <div data-starter="fields"></div>
      </aside>
    </div>
    <dialog class="sd-starter-tool-picker" data-starter="tool-picker" aria-labelledby="sd-starter-tool-picker-title">
      <section class="sd-starter-tool-picker-panel">
        <div class="sd-starter-tool-picker-head">
          <h3 id="sd-starter-tool-picker-title">Choose tools</h3>
          <button type="button" class="btn btn-sm" data-starter="tool-picker-close">Close</button>
        </div>
        <p class="small text-muted">Select one or more tools. Their descriptions and parameter variables will be added to the prompt.</p>
        <input class="form-input" type="search" data-starter="tool-filter" placeholder="Filter tools…" aria-label="Filter tools">
        <div class="editor-tools-bulk">
          <span class="small text-muted" data-starter="tool-count"></span>
          <span class="spacer"></span>
          <button type="button" class="btn btn-sm" data-starter="tool-select-all">Select all</button>
          <button type="button" class="btn btn-sm" data-starter="tool-clear-all">Clear all</button>
        </div>
        <div class="editor-tools-list" data-starter="tool-options" role="group" aria-label="Available tools"></div>
        <p class="small text-danger" data-starter="tool-picker-error" role="alert"></p>
        <div class="workspace-save-actions">
          <button type="button" class="btn" data-starter="tool-picker-cancel">Cancel</button>
          <button type="button" class="btn btn-primary" data-starter="tool-picker-apply">Add selected tools</button>
        </div>
      </section>
    </dialog>
    <div class="sd-starter-picker" data-starter="picker" hidden>
      <label for="sd-starter-picker-select" data-starter="picker-label"></label>
      <select id="sd-starter-picker-select" class="form-select" data-starter="picker-select"></select>
      <input class="form-input" data-starter="picker-value" aria-label="Custom selection" placeholder="Or enter a value">
      <div class="sd-starter-picker-actions">
        <button type="button" class="btn btn-sm" data-starter="picker-save">Save choice</button>
        <button type="button" class="btn btn-sm" data-starter="picker-create-subagent" hidden>Create new sub-agent</button>
        <button type="button" class="btn btn-sm" data-starter="picker-clear">Clear</button>
        <button type="button" class="btn btn-sm" data-starter="picker-close">Done</button>
      </div>
    </div>
    <div class="sd-starter-create-form" data-starter="create-form" hidden>
      <h4 data-starter="create-form-title">Create Agent</h4>
      <div class="sd-starter-create-row" data-starter="create-squad-row" hidden>
        <label class="small">Squad Name <input class="form-input form-input-sm" data-starter="create-squad-name" placeholder="e.g. Analysis Squad"></label>
      </div>
      <div class="sd-starter-create-row">
        <label class="small" data-starter="create-name-label"><span>Agent Name</span> <input class="form-input form-input-sm" data-starter="create-name" placeholder="e.g. Report Builder"></label>
        <label class="small">Agent ID <input class="form-input form-input-sm" data-starter="create-id" placeholder="auto-generated"></label>
        <label class="small" data-starter="create-kind-label">Kind
          <select class="form-select form-select-sm" data-starter="create-kind">
            <option value="agent">Agent (Markdown)</option>
            <option value="subagent">Sub-agent (YAML)</option>
          </select>
        </label>
      </div>
      <div class="sd-starter-create-row">
        <button type="button" class="btn btn-sm btn-primary" data-starter="create-confirm">Create</button>
        <button type="button" class="btn btn-sm" data-starter="create-cancel">Cancel</button>
        <span class="small text-muted" data-starter="create-status"></span>
      </div>
    </div>
    <footer class="sd-starter-actions">
      <button type="button" class="btn btn-sm" data-starter="reset" disabled>Reset</button>
      <button type="button" class="btn btn-sm" data-starter="copy" disabled>Copy prompt</button>
      <span class="sd-starter-actions-sep"></span>
      <button type="button" class="btn btn-sm btn-primary" data-starter="create-agent" disabled>Create Agent</button>
      <button type="button" class="btn btn-sm btn-primary" data-starter="create-squad" disabled>Create New Squad</button>
      <span class="small" role="status" data-starter="status"></span>
    </footer>
  </dialog>`;
  const dialog = host.firstElementChild;
  container.append(dialog);
  const fields = dialog.querySelector('[data-starter="fields"]');
  const editor = dialog.querySelector('[data-starter="editor"]');
  const picker = dialog.querySelector('[data-starter="picker"]');
  const pickerLabel = dialog.querySelector('[data-starter="picker-label"]');
  const pickerSelect = dialog.querySelector('[data-starter="picker-select"]');
  const pickerValue = dialog.querySelector('[data-starter="picker-value"]');
  const pickerClearButton = dialog.querySelector('[data-starter="picker-clear"]');
  const pickerCreateSubagent = dialog.querySelector('[data-starter="picker-create-subagent"]');
  const editorHeading = dialog.querySelector('[data-starter="editor-heading"]');
  const addToolButton = dialog.querySelector('[data-starter="add-tool"]');
  const toolPicker = dialog.querySelector('[data-starter="tool-picker"]');
  const toolFilter = dialog.querySelector('[data-starter="tool-filter"]');
  const toolCount = dialog.querySelector('[data-starter="tool-count"]');
  const toolOptions = dialog.querySelector('[data-starter="tool-options"]');
  const toolPickerError = dialog.querySelector('[data-starter="tool-picker-error"]');
  const error = dialog.querySelector('[data-starter="error"]');
  const status = dialog.querySelector('[data-starter="status"]');
  const tplSelect = dialog.querySelector('[data-starter="tpl-select"]');
  const tplDeleteBtn = dialog.querySelector('[data-starter="tpl-delete"]');
  const createForm = dialog.querySelector('[data-starter="create-form"]');
  const createFormTitle = dialog.querySelector('[data-starter="create-form-title"]');
  const createSquadRow = dialog.querySelector('[data-starter="create-squad-row"]');
  const createSquadNameInput = dialog.querySelector('[data-starter="create-squad-name"]');
  const createNameInput = dialog.querySelector('[data-starter="create-name"]');
  const createNameLabel = dialog.querySelector('[data-starter="create-name-label"] span');
  const createIdInput = dialog.querySelector('[data-starter="create-id"]');
  const createKindSelect = dialog.querySelector('[data-starter="create-kind"]');
  const createKindLabel = dialog.querySelector('[data-starter="create-kind-label"]');
  const createStatus = dialog.querySelector('[data-starter="create-status"]');
  const createAgentButton = dialog.querySelector('[data-starter="create-agent"]');
  const values = {};
  let choices = {};
  let tools = [];
  let subagents = [];
  let templates = [];
  let starterDefaults = null;
  let disposed = false;
  let catalogReady = false;
  let variablesKey = '';
  let pickerName = '';
  let toolInstructionsRequest = 0;
  let toolInstructionEntries = [];
  let toolInstructionsState = 'ready';
  let createMode = '';
  let pendingSubagentCreation = null;
  let toolPickerSelection = new Set();
  let toolPickerReturnFocus = null;

  function showError(err) { error.textContent = err.message || String(err); }
  function isToolParameterVariable(name) { return /^TOOL \d+ [A-Z0-9 _-]+$/.test(name); }
  function isToolPickerVariable(name) { return /^TOOL \d+$/.test(name); }
  function isCatalogVariable(name) {
    return isToolPickerVariable(name) || name === 'SUB-AGENT ID' || name === 'SUBAGENT ID';
  }
  function isSubagentVariable(name) { return name === 'SUB-AGENT ID' || name === 'SUBAGENT ID'; }
  function updateCreateKindLabels() {
    const kindLabel = createKindSelect.value === 'subagent' ? 'Sub-agent' : 'Agent';
    createAgentButton.textContent = `Create ${kindLabel}`;
    createNameLabel.textContent = `${kindLabel} Name`;
    if (!createForm.hidden && createMode !== 'subagent') {
      createFormTitle.textContent = createMode === 'squad'
        ? `Create New Squad + First ${kindLabel}` : `Create New ${kindLabel}`;
    }
    addToolButton.hidden = tplSelect.value !== 'agent' || createKindSelect.value !== 'agent';
  }
  function editorText() { return starterEditorText(editor); }
  function promptBodyText() {
    const text = editorText();
    const marker = /\n{2,}## Available Tools[ \t]*\r?\n/.exec(text);
    return marker ? text.slice(0, marker.index).replace(/\s+$/, '') : text;
  }
  function finishedPrompt() { return renderStarter(editorText(), values); }
  function withToolInstructions(body, instructions) {
    return `${body.replace(/\s+$/, '')}\n\n## Available Tools\n\n${instructions}\n`;
  }
  function renderEditor(text) {
    const fragment = document.createDocumentFragment();
    let last = 0;
    for (const match of text.matchAll(VARIABLE)) {
      fragment.append(document.createTextNode(text.slice(last, match.index)));
      const token = document.createElement('button');
      token.type = 'button';
      token.contentEditable = 'false';
      token.className = 'sd-starter-token';
      token.dataset.placeholder = match[1];
      token.setAttribute('aria-label', `${match[1]}: ${values[match[1]]?.trim() || 'unfilled'}`);
      if (isCatalogVariable(match[1])) token.setAttribute('aria-haspopup', 'true');
      token.textContent = values[match[1]]?.trim() || match[0];
      fragment.append(token);
      last = match.index + match[0].length;
    }
    fragment.append(document.createTextNode(text.slice(last)));
    editor.replaceChildren(fragment);
  }
  function updateTokens(name) {
    editor.querySelectorAll('[data-placeholder]').forEach(token => {
      if (token.dataset.placeholder !== name) return;
      token.textContent = values[name]?.trim() || `[${name}]`;
      token.setAttribute('aria-label', `${name}: ${values[name]?.trim() || 'unfilled'}`);
    });
  }
  function selectedToolNames() {
    return Object.entries(values)
      .filter(([name, value]) => /^TOOL \d+$/.test(name) && value?.trim())
      .map(([variable, value]) => ({ index: Number(variable.slice(5)), name: value.trim() }))
      .sort((left, right) => left.index - right.index);
  }
  async function refreshToolInstructions() {
    const request = ++toolInstructionsRequest;
    const selected = selectedToolNames().filter(entry => tools.some(tool => tool.name === entry.name));
    const body = promptBodyText();
    toolInstructionEntries = [];
    renderEditor(body);
    renderFields();
    if (!selected.length || tplSelect.value !== 'agent') {
      toolInstructionsState = 'ready';
      return;
    }
    toolInstructionsState = 'loading';
    try {
      const entries = await Promise.all(selected.map(async ({ index, name }) => {
        const result = await Api.getStarterToolInstructions(name);
        const lines = result.content.split(/\r?\n/);
        const parameterDescriptions = {};
        const description = addToolParameterVariables(lines.slice(1).join('\n').trim(), index, parameterDescriptions);
        return { name, description, parameterDescriptions };
      }));
      if (request !== toolInstructionsRequest || disposed) return;
      toolInstructionEntries = entries;
      const currentBody = promptBodyText();
      const instructions = entries.map(entry => `### ${entry.name}\n${entry.description}`).join('\n\n');
      renderEditor(withToolInstructions(currentBody, instructions));
      toolInstructionsState = 'ready';
      renderFields();
    } catch (err) {
      if (request !== toolInstructionsRequest || disposed) return;
      toolInstructionsState = 'error';
      showError(err);
    }
  }
  function openPickerForVariable(name, token) {
    if (isToolPickerVariable(name)) {
      openToolPicker(token);
      return;
    }
    if (!catalogReady) {
      showError(new Error('Tool and sub-agent choices are not available yet. Close and reopen Prompt Starter to retry.'));
      return;
    }
    pickerName = name;
    pickerLabel.textContent = `Choose ${name}`;
    pickerSelect.setAttribute('aria-label', `Choose ${name}`);
    pickerSelect.innerHTML = `<option value="">Choose a value</option>${optionsFor(name)}`;
    pickerSelect.value = values[name] || '';
    pickerValue.value = values[name] || '';
    pickerCreateSubagent.hidden = !isSubagentVariable(name);
    pickerClearButton.textContent = 'Clear';
    picker.hidden = false;
    const rect = token.getBoundingClientRect();
    const bounds = dialog.getBoundingClientRect();
    picker.style.left = `${Math.max(8, Math.min(rect.left - bounds.left, bounds.width - picker.offsetWidth - 8))}px`;
    picker.style.top = `${Math.max(8, Math.min(rect.bottom - bounds.top + 4, bounds.height - picker.offsetHeight - 8))}px`;
    pickerSelect.focus();
  }
  function updateToolPickerState() {
    toolOptions.querySelectorAll('.editor-tool-group').forEach(group => {
      const inputs = [...group.querySelectorAll('input[name="starter-tools"]')];
      const selectedCount = inputs.filter(input => toolPickerSelection.has(input.value)).length;
      const groupCheck = group.querySelector('.editor-tool-group-check');
      groupCheck.checked = inputs.length > 0 && selectedCount === inputs.length;
      groupCheck.indeterminate = selectedCount > 0 && selectedCount < inputs.length;
      group.querySelector('.editor-tool-group-count').textContent = `${selectedCount}/${inputs.length}`;
      inputs.forEach(input => { input.checked = toolPickerSelection.has(input.value); });
    });
    const selectableCount = tools.filter(tool => tool.name !== 'call_subagent').length;
    toolCount.textContent = `${toolPickerSelection.size} of ${selectableCount} selected`;
  }
  function renderToolPickerOptions() {
    const query = toolFilter.value.trim().toLowerCase();
    const grouped = groupTools(tools.filter(tool => tool.name !== 'call_subagent'))
      .map(([category, entries]) => [category, entries.filter(tool => [
        category, tool.name, tool.provider, tool.description,
      ].flat().join(' ').toLowerCase().includes(query))])
      .filter(([, entries]) => entries.length);
    toolOptions.replaceChildren(...grouped.map(([category, entries]) => {
      const group = document.createElement('details');
      group.className = 'editor-tool-group';
      group.open = Boolean(query);
      const summary = document.createElement('summary');
      const groupCheck = document.createElement('input');
      groupCheck.type = 'checkbox';
      groupCheck.className = 'editor-tool-group-check';
      groupCheck.setAttribute('aria-label', `Select all ${category} tools`);
      groupCheck.addEventListener('click', event => event.stopPropagation());
      groupCheck.addEventListener('change', () => {
        entries.forEach(tool => groupCheck.checked
          ? toolPickerSelection.add(tool.name)
          : toolPickerSelection.delete(tool.name));
        updateToolPickerState();
      });
      const title = document.createElement('span');
      title.className = 'editor-tool-group-title';
      title.textContent = category;
      const count = document.createElement('span');
      count.className = 'editor-tool-group-count text-muted small';
      summary.append(groupCheck, title, count);
      group.appendChild(summary);
      for (const tool of entries) {
        const label = document.createElement('label');
        label.className = 'editor-tool-option';
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.name = 'starter-tools';
        checkbox.value = tool.name;
        checkbox.addEventListener('change', () => {
          checkbox.checked ? toolPickerSelection.add(tool.name) : toolPickerSelection.delete(tool.name);
          updateToolPickerState();
        });
        const copy = document.createElement('span');
        const name = document.createElement('strong');
        name.textContent = tool.name;
        copy.appendChild(name);
        const description = Array.isArray(tool.description)
          ? tool.description.join(' ').trim()
          : String(tool.description || '').trim();
        if (description) {
          const detail = document.createElement('span');
          detail.className = 'text-muted small';
          detail.textContent = description;
          copy.appendChild(detail);
        }
        label.append(checkbox, copy);
        group.appendChild(label);
      }
      return group;
    }));
    if (!grouped.length) {
      const empty = document.createElement('p');
      empty.className = 'small text-muted';
      empty.textContent = 'No tools match this filter.';
      toolOptions.appendChild(empty);
    }
    updateToolPickerState();
  }
  function openToolPicker(trigger = addToolButton) {
    if (!catalogReady) {
      showError(new Error('The tool catalog is not available yet. Close and reopen Prompt Starter to retry.'));
      return;
    }
    toolPickerSelection = new Set(selectedToolNames().map(tool => tool.name));
    toolPickerReturnFocus = trigger;
    toolPickerError.textContent = '';
    toolFilter.value = '';
    renderToolPickerOptions();
    toolPicker.showModal();
    toolFilter.focus();
  }
  function closeToolPicker() {
    if (toolPicker.open) toolPicker.close();
    if (toolPickerReturnFocus?.isConnected) toolPickerReturnFocus.focus();
    else addToolButton.focus();
    toolPickerReturnFocus = null;
  }
  function applyToolSelection() {
    const currentTools = selectedToolNames();
    const indexByName = new Map(currentTools.map(tool => [tool.name, tool.index]));
    const frontmatterNumbers = [...editorText().matchAll(/\[TOOL (\d+)\]/g)].map(match => Number(match[1]));
    let nextIndex = Math.max(0, ...frontmatterNumbers);
    const assignments = [...toolPickerSelection].map(name => ({
      name,
      index: indexByName.get(name) || ++nextIndex,
    }));
    try {
      const updatedText = replaceToolVariables(editorText(), assignments.map(tool => tool.index));
      const retained = new Set(assignments.map(tool => `TOOL ${tool.index}`));
      Object.keys(values).forEach(name => {
        if (/^TOOL \d+$/.test(name) && !retained.has(name)) delete values[name];
      });
      assignments.forEach(tool => { values[`TOOL ${tool.index}`] = tool.name; });
      variablesKey = '';
      renderEditor(updatedText);
      renderFields();
      closeToolPicker();
      refreshToolInstructions();
    } catch (err) {
      toolPickerError.textContent = err.message || String(err);
    }
  }
  function addToolVariable() {
    openToolPicker();
  }
  function optionsFor(name) {
    let catalog = '';
    if (isSubagentVariable(name)) {
      catalog = `<optgroup label="Sub-agents">${subagents.map(agent =>
        `<option value="${esc(agent.id)}" title="${esc(agent.description || '')}">${esc(agent.name || agent.id)} (${esc(agent.id)})</option>`).join('')}</optgroup>`;
    }
    const saved = choices[name] || [];
    return `${catalog}${saved.length ? `<optgroup label="Saved choices">${saved.map(value => `<option value="${esc(value)}">${esc(value)}</option>`).join('')}</optgroup>` : ''}`;
  }
  function renderFields() {
    const variables = starterVariables(editorText())
      .filter(name => !isCatalogVariable(name));
    const parameterDescriptions = Object.assign({}, ...toolInstructionEntries.map(entry => entry.parameterDescriptions));
    const key = variables.map(name => `${name}:${parameterDescriptions[name] || ''}`).join('\n');
    if (variablesKey === key) return;
    variablesKey = key;
    fields.innerHTML = variables.map((name, index) => {
      const description = parameterDescriptions[name];
      return `<div class="sd-starter-field" data-variable="${esc(name)}">
        <label for="sd-starter-value-${index}"><strong>${esc(name)}</strong></label>
        ${description ? `<p class="sd-starter-field-description">${esc(description)}</p>` : ''}
        <select class="form-select" aria-label="Choose ${esc(name)}" data-starter-choice>
          <option value="">Choose or type below</option>${optionsFor(name)}
        </select>
        <textarea id="sd-starter-value-${index}" class="form-input" rows="2" data-starter-value>${esc(values[name] || '')}</textarea>
        <button type="button" class="btn btn-sm" data-starter="save-choice">Save choice</button>
      </div>`;
    }).join('') || '<p class="small text-muted">Add an uppercase placeholder in the prompt to create a field.</p>';
  }

  // --- Template selector ---
  function renderTemplateSelect() {
    const current = tplSelect.value;
    tplSelect.innerHTML = `<option value="agent">Default (Novous Agent)</option>`
      + `<option value="subagent">Default (Novous Sub-agent YAML)</option>`
      + templates.map(t => `<option value="template:${esc(t.name)}">${esc(t.name)}</option>`).join('');
    if (current === 'agent' || current === 'subagent'
        || (current.startsWith('template:') && templates.some(t => t.name === current.slice(9)))) {
      tplSelect.value = current;
    } else {
      tplSelect.value = 'agent';
    }
    tplDeleteBtn.disabled = !tplSelect.value.startsWith('template:');
  }

  async function loadTemplates() {
    try {
      const data = await Api.getStarterTemplates();
      if (disposed) return;
      templates = data.templates || [];
      renderTemplateSelect();
    } catch { /* non-critical */ }
  }

  function applyTemplate(name) {
    const isCustom = name.startsWith('template:');
    const templateName = isCustom ? name.slice(9) : '';
    const tpl = isCustom ? templates.find(t => t.name === templateName) : null;
    const kind = name === 'subagent' ? 'subagent' : 'agent';
    if (isCustom && !tpl) return;
    if (!isCustom) {
      if (!starterDefaults) {
        showError(new Error('Novous prompt defaults are not available yet.'));
        return;
      }
      createKindSelect.value = kind;
      updateCreateKindLabels();
    }
    const body = tpl ? tpl.body : starterDefaults[kind].prompt;
    Object.keys(values).forEach(key => delete values[key]);
    variablesKey = '';
    picker.hidden = true;
    addToolButton.hidden = isCustom || kind !== 'agent';
    toolInstructionsRequest += 1;
    renderEditor(body);
    editorHeading.textContent = isCustom ? 'Prompt' : kind === 'subagent' ? 'Sub-agent prompt' : 'Agent definition';
    renderFields();
    refreshToolInstructions();
  }

  // --- Create form helpers ---
  function showCreateForm(mode) {
    createMode = mode;
    const isSquad = mode === 'squad';
    const isSubagent = mode === 'subagent';
    createFormTitle.textContent = isSquad ? 'Create New Squad + First Agent'
      : isSubagent ? 'Create New Sub-agent' : 'Create New Agent';
    createSquadRow.hidden = !isSquad;
    createSquadNameInput.value = '';
    createNameInput.value = values['AGENT NAME'] || values['SUB-AGENT NAME'] || '';
    createIdInput.value = values['AGENT ID'] || toAgentId(createNameInput.value);
    createKindLabel.hidden = isSubagent;
    createKindSelect.disabled = isSubagent;
    if (isSubagent) createKindSelect.value = 'subagent';
    createStatus.textContent = '';
    createForm.hidden = false;
    updateCreateKindLabels();
    if (isSquad) createSquadNameInput.focus();
    else createNameInput.focus();
  }

  function hideCreateForm() {
    createForm.hidden = true;
    createMode = '';
    createStatus.textContent = '';
  }

  function beginSubagentCreation() {
    pendingSubagentCreation = {
      text: promptBodyText(),
      values: { ...values },
      template: tplSelect.value,
      kind: createKindSelect.value,
      targetName: pickerName,
    };
    picker.hidden = true;
    applyTemplate('subagent');
    showCreateForm('subagent');
  }

  function restorePendingSubagent(selectedId = '') {
    if (!pendingSubagentCreation) return;
    const pending = pendingSubagentCreation;
    pendingSubagentCreation = null;
    Object.keys(values).forEach(key => delete values[key]);
    Object.assign(values, pending.values);
    variablesKey = '';
    tplSelect.value = pending.template;
    createKindSelect.value = pending.kind;
    createKindSelect.disabled = false;
    createKindLabel.hidden = false;
    updateCreateKindLabels();
    renderEditor(pending.text);
    renderFields();
    refreshToolInstructions();
    if (selectedId) {
      values[pending.targetName] = selectedId;
      pickerSelect.value = selectedId;
      pickerValue.value = selectedId;
      updateTokens(pending.targetName);
    }
    picker.hidden = true;
  }

  const layoutDivider = dialog.querySelector('[data-starter="layout-divider"]');
  const layout = dialog.querySelector('.sd-starter-layout');
  const mainSection = dialog.querySelector('.sd-starter-main');
  {
    let dragging = false, startX = 0, startW = 0;
    layoutDivider.addEventListener('mousedown', e => {
      dragging = true;
      startX = e.clientX;
      startW = mainSection.getBoundingClientRect().width;
      document.body.classList.add('sd-resizing');
      e.preventDefault();
    });
    document.addEventListener('mousemove', e => {
      if (!dragging) return;
      const layoutW = layout.getBoundingClientRect().width;
      const w = Math.max(280, Math.min(layoutW - 220, startW + (e.clientX - startX)));
      mainSection.style.flex = 'none';
      mainSection.style.width = `${w}px`;
    });
    document.addEventListener('mouseup', () => {
      if (!dragging) return;
      dragging = false;
      document.body.classList.remove('sd-resizing');
    });
  }

  // --- Dialog window resize from any edge/corner ---
  {
    const EDGES = ['n', 's', 'e', 'w', 'ne', 'nw', 'se', 'sw'];
    for (const edge of EDGES) {
      const handle = document.createElement('div');
      handle.className = `sd-starter-resize sd-resize-${edge}`;
      handle.dataset.resize = edge;
      dialog.append(handle);
    }
    let dir = null, startX = 0, startY = 0, startRect = null;
    const MIN_W = 480, MIN_H = 360;

    dialog.addEventListener('mousedown', e => {
      const handle = e.target.closest('[data-resize]');
      if (!handle) return;
      dir = handle.dataset.resize;
      startX = e.clientX;
      startY = e.clientY;
      startRect = dialog.getBoundingClientRect();
      dialog.style.margin = '0';
      dialog.style.inset = 'unset';
      dialog.style.top = `${startRect.top}px`;
      dialog.style.left = `${startRect.left}px`;
      dialog.style.width = `${startRect.width}px`;
      dialog.style.height = `${startRect.height}px`;
      dialog.style.maxWidth = 'none';
      dialog.style.maxHeight = 'none';
      document.body.classList.add('sd-resizing');
      e.preventDefault();
    });
    document.addEventListener('mousemove', e => {
      if (!dir) return;
      const dx = e.clientX - startX;
      const dy = e.clientY - startY;
      let top = startRect.top, left = startRect.left;
      let w = startRect.width, h = startRect.height;

      if (dir.includes('e')) w = Math.max(MIN_W, startRect.width + dx);
      if (dir.includes('w')) { w = Math.max(MIN_W, startRect.width - dx); left = startRect.right - w; }
      if (dir.includes('s')) h = Math.max(MIN_H, startRect.height + dy);
      if (dir.includes('n')) { h = Math.max(MIN_H, startRect.height - dy); top = startRect.bottom - h; }

      dialog.style.top = `${top}px`;
      dialog.style.left = `${left}px`;
      dialog.style.width = `${w}px`;
      dialog.style.height = `${h}px`;
    });
    document.addEventListener('mouseup', () => {
      if (!dir) return;
      dir = null;
      document.body.classList.remove('sd-resizing');
    });
  }

  renderEditor('');
  renderFields();
  editor.addEventListener('input', renderFields);
  function insertPlainText(text) {
    if (document.execCommand('insertText', false, text)) return;
    const selection = window.getSelection();
    if (!selection?.rangeCount || !editor.contains(selection.anchorNode)) {
      showError(new Error('Could not insert text into the Markdown editor.'));
      return;
    }
    const range = selection.getRangeAt(0);
    range.deleteContents();
    const node = document.createTextNode(text);
    range.insertNode(node);
    range.setStartAfter(node);
    range.collapse(true);
    selection.removeAllRanges();
    selection.addRange(range);
    renderFields();
  }
  editor.addEventListener('beforeinput', event => {
    if (event.inputType === 'insertParagraph' || event.inputType === 'insertLineBreak') {
      event.preventDefault();
      insertPlainText('\n');
    }
  });
  editor.addEventListener('paste', event => {
    event.preventDefault();
    if (!event.clipboardData) { showError(new Error('Could not read the pasted text.')); return; }
    insertPlainText(event.clipboardData.getData('text/plain').replace(/\r\n?/g, '\n'));
  });
  editor.addEventListener('focusout', event => {
    if (editor.contains(event.relatedTarget)) return;
    setTimeout(() => {
      if (disposed || editor.contains(document.activeElement)) return;
      const walker = document.createTreeWalker(editor, NodeFilter.SHOW_TEXT, {
        acceptNode: node => node.parentElement.closest('[data-placeholder]') ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT,
      });
      let node;
      while ((node = walker.nextNode())) {
        if (starterVariables(node.nodeValue).length) {
          const scrollTop = editor.scrollTop;
          renderEditor(editorText());
          editor.scrollTop = scrollTop;
          break;
        }
      }
    }, 0);
  });
  editor.addEventListener('click', event => {
    const token = event.target.closest('[data-placeholder]');
    if (!token) return;
    const name = token.dataset.placeholder;
    if (/^TOOL \d+$/.test(name)) {
      openToolPicker(token);
      return;
    }
    if (!isCatalogVariable(name)) {
      [...fields.querySelectorAll('[data-variable]')].find(field => field.dataset.variable === name)?.querySelector('[data-starter-value]')?.focus();
      return;
    }
    openPickerForVariable(name, token);
  });
  pickerSelect.addEventListener('change', () => {
    if (!pickerSelect.value) return;
    pickerValue.value = pickerSelect.value;
    values[pickerName] = pickerSelect.value;
    updateTokens(pickerName);
  });
  pickerValue.addEventListener('input', () => {
    values[pickerName] = pickerValue.value;
    updateTokens(pickerName);
  });
  picker.addEventListener('keydown', event => {
    if (event.key === 'Escape') {
      event.preventDefault();
      event.stopPropagation();
      picker.hidden = true;
      editor.focus();
    }
  });
  toolFilter.addEventListener('input', renderToolPickerOptions);
  toolPicker.addEventListener('cancel', event => {
    event.preventDefault();
    closeToolPicker();
  });
  dialog.addEventListener('cancel', event => {
    if (!picker.hidden) { event.preventDefault(); picker.hidden = true; editor.focus(); }
    else if (pendingSubagentCreation) {
      event.preventDefault();
      restorePendingSubagent();
      hideCreateForm();
    }
  });
  fields.addEventListener('input', event => {
    const input = event.target.closest('[data-starter-value]');
    if (!input) return;
    const name = input.closest('[data-variable]').dataset.variable;
    values[name] = input.value;
    updateTokens(name);
  });
  fields.addEventListener('change', event => {
    const select = event.target.closest('[data-starter-choice]');
    if (!select || !select.value) return;
    const field = select.closest('[data-variable]');
    field.querySelector('[data-starter-value]').value = select.value;
    values[field.dataset.variable] = select.value;
    updateTokens(field.dataset.variable);
  });

  // --- Template bar events ---
  tplSelect.addEventListener('change', () => {
    tplDeleteBtn.disabled = !tplSelect.value.startsWith('template:');
    applyTemplate(tplSelect.value);
    status.textContent = tplSelect.value.startsWith('template:')
      ? `Loaded template: ${tplSelect.value.slice(9)}`
      : `Loaded ${tplSelect.value === 'subagent' ? 'Novous sub-agent YAML' : 'Novous Agent'} default.`;
  });

  // --- Auto-fill ID from name ---
  createNameInput.addEventListener('input', () => {
    createIdInput.value = toAgentId(createNameInput.value);
  });
  createKindSelect.addEventListener('change', updateCreateKindLabels);

  dialog.addEventListener('click', async event => {
    const button = event.target.closest('[data-starter]');
    if (!button || button.tagName !== 'BUTTON') return;
    const action = button.dataset.starter;
    error.textContent = '';
    status.textContent = '';
    if (action === 'close') {
      if (pendingSubagentCreation) restorePendingSubagent();
      if (toolPicker.open) closeToolPicker();
      picker.hidden = true;
      hideCreateForm();
      dialog.close();
      return;
    }
    if (action === 'tool-picker-close' || action === 'tool-picker-cancel') {
      closeToolPicker();
      return;
    }
    if (action === 'tool-select-all' || action === 'tool-clear-all') {
      toolOptions.querySelectorAll('input[name="starter-tools"]').forEach(input => {
        if (action === 'tool-select-all') toolPickerSelection.add(input.value);
        else toolPickerSelection.delete(input.value);
      });
      updateToolPickerState();
      return;
    }
    if (action === 'tool-picker-apply') {
      applyToolSelection();
      return;
    }
    if (action === 'picker-close') { picker.hidden = true; return; }
    if (action === 'picker-clear') {
      values[pickerName] = '';
      pickerValue.value = '';
      pickerSelect.value = '';
      updateTokens(pickerName);
      refreshToolInstructions();
      return;
    }
    if (action === 'add-tool') {
      addToolVariable();
      return;
    }
    if (action === 'reset') {
      if (!confirm('Reset the starter template and its current values? Saved choices will remain.')) return;
      pendingSubagentCreation = null;
      picker.hidden = true;
      hideCreateForm();
      createKindSelect.disabled = false;
      createKindLabel.hidden = false;
      tplSelect.value = 'agent';
      tplDeleteBtn.disabled = true;
      applyTemplate('agent');
      status.textContent = 'Starter reset.';
      return;
    }
    if (action === 'picker-create-subagent') {
      beginSubagentCreation();
      return;
    }

    // --- Template actions ---
    if (action === 'tpl-save') {
      const body = promptBodyText().trim();
      if (!body) { showError(new Error('The editor is empty.')); return; }
      const name = prompt('Name for this template:');
      if (!name?.trim()) return;
      button.disabled = true;
      try {
        const data = await Api.saveStarterTemplate(name.trim(), body);
        if (disposed) return;
        templates = data.templates || [];
        renderTemplateSelect();
        tplSelect.value = `template:${name.trim()}`;
        tplDeleteBtn.disabled = false;
        status.textContent = `Template "${name.trim()}" saved.`;
      } catch (err) { if (!disposed) showError(err); }
      finally { button.disabled = false; }
      return;
    }
    if (action === 'tpl-delete') {
      if (!tplSelect.value.startsWith('template:')) return;
      const name = tplSelect.value.slice(9);
      if (!confirm(`Delete template "${name}"?`)) return;
      button.disabled = true;
      try {
        const data = await Api.deleteStarterTemplate(name);
        if (disposed) return;
        templates = data.templates || [];
        renderTemplateSelect();
        tplSelect.value = 'agent';
        applyTemplate('agent');
        status.textContent = `Template "${name}" deleted.`;
      } catch (err) { if (!disposed) showError(err); }
      finally { button.disabled = false; }
      return;
    }
    if (action === 'tpl-load-generic') {
      button.disabled = true;
      try {
        const data = await Api.getGenericPrompt();
        if (disposed) return;
        Object.keys(values).forEach(key => delete values[key]);
        variablesKey = '';
        picker.hidden = true;
        tplSelect.value = 'agent';
        tplDeleteBtn.disabled = true;
        renderEditor(data.prompt || '');
        renderFields();
        status.textContent = 'Loaded generic prompt from server.';
      } catch (err) { if (!disposed) showError(err); }
      finally { button.disabled = false; }
      return;
    }

    // --- Choice saving ---
    if (action === 'save-choice' || action === 'picker-save') {
      const field = button.closest('[data-variable]');
      const name = field ? field.dataset.variable : pickerName;
      const value = (field ? field.querySelector('[data-starter-value]').value : pickerValue.value).trim();
      if (!value) { showError(new Error('Enter a value before saving a choice.')); return; }
      button.disabled = true;
      try {
        const saved = await choiceRequest('POST', { variable: name, value });
        if (disposed) return;
        choices = saved;
        variablesKey = '';
        renderFields();
        if (!picker.hidden && pickerName === name) {
          pickerSelect.innerHTML = `<option value="">Choose a value</option>${optionsFor(name)}`;
          pickerSelect.value = values[name] || '';
        }
        status.textContent = `Choice saved for ${name}.`;
      } catch (err) { if (!disposed) showError(err); }
      finally { button.disabled = false; }
      return;
    }
    if (action === 'copy') {
      try { await navigator.clipboard.writeText(finishedPrompt()); status.textContent = 'Prompt copied.'; }
      catch (err) { showError(new Error(`Could not copy the prompt: ${err.message}.`)); }
      return;
    }

    // --- Create Agent / Squad ---
    if (action === 'create-agent') {
      showCreateForm('agent');
      return;
    }
    if (action === 'create-squad') {
      showCreateForm('squad');
      return;
    }
    if (action === 'create-cancel') {
      hideCreateForm();
      restorePendingSubagent();
      return;
    }
    if (action === 'create-confirm') {
      const mode = createMode;
      const agentName = createNameInput.value.trim();
      const agentId = createIdInput.value.trim();
      const kind = mode === 'subagent' ? 'subagent' : createKindSelect.value;
      const squadName = createSquadNameInput.value.trim();
      const nameVariable = kind === 'subagent' ? 'SUB-AGENT NAME' : 'AGENT NAME';
      if (!agentName) { showError(new Error('Enter a name for the agent.')); return; }
      if (!agentId) { showError(new Error('Enter an ID for the agent.')); return; }
      values[nameVariable] = agentName;
      updateTokens(nameVariable);
      if (kind === 'agent') {
        values['AGENT ID'] = agentId;
        updateTokens('AGENT ID');
      }
      if (kind === 'subagent') {
        values['SUB-AGENT ID'] = agentId;
        updateTokens('SUB-AGENT ID');
      }
      if (kind === 'agent' && toolInstructionsState !== 'ready') {
        showError(new Error(toolInstructionsState === 'loading'
          ? 'Wait for tool descriptions to finish loading before creating the agent.'
          : 'Tool descriptions could not be loaded; retry the tool selection before creating the agent.'));
        return;
      }
      const promptText = finishedPrompt();
      if (!promptText.trim()) { showError(new Error('The finished prompt must not be empty.')); return; }
      if (promptText.startsWith('---') && kind !== 'agent') {
        showError(new Error('The Novous Agent YAML definition must be created as an agent.'));
        return;
      }
      if (mode === 'squad' && !squadName) {
        showError(new Error('Enter a name for the squad.')); return;
      }
      const unresolved = starterVariables(promptText);
      if (unresolved.length && !confirm(`These variables are still unfilled: ${unresolved.join(', ')}. Continue?`)) return;

      button.disabled = true;
      createStatus.textContent = 'Creating…';
      try {
        const fromSubagentPicker = mode === 'subagent' && Boolean(pendingSubagentCreation);
        let promptToSave = promptText;
        let description = '';
        let agentTools = null;
        let agentToolMode = null;
        if (kind === 'agent' && promptText.startsWith('---')) {
          createStatus.textContent = 'Validating agent definition…';
          const definition = await Api.parseStarterAgentDefinition(promptText);
          if (definition.id !== agentId || definition.name !== agentName) {
            throw new Error('Agent ID and name in the YAML frontmatter must match the creation form.');
          }
          promptToSave = definition.prompt;
          description = definition.description || '';
          agentTools = definition.tools;
          agentToolMode = definition.mode;
        }
        if (mode === 'squad') {
          createStatus.textContent = 'Creating squad…';
          await Api.createSquad(squadName);
        }
        createStatus.textContent = `Creating ${kind === 'subagent' ? 'sub-agent' : 'agent'}…`;
        const created = await Api.createSquadMember(kind, agentId, agentName, description);
        if (agentTools !== null) {
          createStatus.textContent = 'Saving selected tools…';
          await Api.setAgentTools('prompt', agentId, agentTools, agentToolMode);
        }
        createStatus.textContent = 'Setting prompt…';
        await Api.setAgentPrompt(agentId, promptToSave);
        if (fromSubagentPicker) {
          subagents = [...subagents.filter(agent => agent.id !== agentId), {
            id: created.id || agentId,
            name: created.name || agentName,
            description: created.description || '',
          }];
        }
        createStatus.textContent = '';
        hideCreateForm();
        if (fromSubagentPicker) {
          restorePendingSubagent(agentId);
          status.textContent = `Sub-agent "${agentName}" created and selected.`;
        } else if (mode === 'squad') {
          status.textContent = `Squad "${squadName}" and agent "${agentName}" created.`;
        } else {
          status.textContent = `${kind === 'subagent' ? 'Sub-agent' : 'Agent'} "${agentName}" created with prompt.`;
        }
      } catch (err) {
        createStatus.textContent = '';
        if (!disposed) showError(err);
      }
      finally { button.disabled = false; }
      return;
    }
  });

  return {
    async open() {
      error.textContent = '';
      status.textContent = '';
      hideCreateForm();
      if (!starterDefaults) {
        try {
          starterDefaults = await Api.getStarterDefaults();
          if (disposed) return;
          tplSelect.value = 'agent';
          applyTemplate('agent');
        } catch (err) {
          if (!disposed) {
            showError(err);
            dialog.showModal();
          }
          return;
        }
      }
      dialog.showModal();
      try {
        const [catalog, agents, saved] = await Promise.all([
          Api.getTools(), Api.getOrchestrationCatalog(), choiceRequest('GET'),
        ]);
        if (disposed || !dialog.open) return;
        tools = catalog.tools || [];
        subagents = agents.subagents || [];
        choices = saved;
        catalogReady = true;
        variablesKey = '';
        renderFields();
        dialog.querySelectorAll('.sd-starter-actions button').forEach(button => { button.disabled = false; });
      } catch (err) { if (!disposed) showError(err); }
      loadTemplates();
    },
    hasChanges() {
      const kind = tplSelect.value === 'subagent' ? 'subagent' : 'agent';
      const defaultPrompt = starterDefaults?.[kind]?.prompt || '';
      return editorText().replace(/\r\n/g, '\n') !== defaultPrompt
        || Object.values(values).some(value => value.trim());
    },
    dispose() { disposed = true; dialog.remove(); },
  };
}
