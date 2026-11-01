import { Api } from './api.js';
import { attachCodeBuilderEditor } from './codebuilder/codebuilder_editor.js';
import { renderTree } from './tree.js';

const fileSelectionListeners = new WeakMap();

const panelWidthStorageKey = 'novous2.editor.panel-widths';
const RESIZER_WIDTH = 12;
const MIN_EDITOR_WIDTH = 240;
const COLLAPSE_THRESHOLD = 120;

const slugify = text => String(text).toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');

const state = {
  path: null,
  dirty: false,
  editor: null,
  managedPath: false
};

/**
 * File & Agent Editor Component (Monaco editor + prompt and sub-agent libraries).
 * @param {HTMLElement} container
 * @param {string|null} initialPath
 */
export function renderEditorView(container, initialPath = null) {
  const initialFile = typeof initialPath === 'string'
    ? { path: initialPath, managedFile: false }
    : initialPath;
  const oldSelectionListener = fileSelectionListeners.get(container);
  if (oldSelectionListener) window.removeEventListener('novous:file-selected', oldSelectionListener);
  state.editor?.dispose();
  state.path = null;
  state.dirty = false;
  state.managedPath = false;

  container.innerHTML = `
    <div class="editor-layout">
      <aside class="editor-sidebar">
        <h3 class="sidebar-title">Workspace</h3>
        <div id="editor-tree" class="tree-pane"></div>
      </aside>

      <div class="editor-resizer" data-panel="left" role="separator" tabindex="0"
           aria-orientation="vertical" aria-label="Resize workspace panel"
           title="Drag to resize · double-click to hide or show"></div>

      <section class="editor-main">
        <div class="editor-toolbar">
          <span id="editor-path" class="editor-path">No file open</span>
          <span id="editor-dirty" class="badge badge-warning hidden">Unsaved</span>
          <span class="spacer"></span>
          <button class="btn btn-sm" id="editor-reload">Reload</button>
          <button class="btn btn-sm btn-primary" id="editor-save">Save (Ctrl+S)</button>
        </div>
        <div id="editor-monaco" class="editor-monaco"></div>
        <div class="editor-status">
          <span id="editor-pos">Ln 1, Col 1</span>
          <span class="spacer"></span>
          <span id="editor-bytes">0 bytes</span>
        </div>
      </section>

      <div class="editor-resizer" data-panel="right" role="separator" tabindex="0"
           aria-orientation="vertical" aria-label="Resize library panel"
           title="Drag to resize · double-click to hide or show"></div>

      <aside class="editor-library">
        <section class="editor-library-section" aria-labelledby="editor-library-title">
          <div class="editor-library-head">
            <h3 id="editor-library-title" class="sidebar-title">Prompt Library</h3>
            <button type="button" class="btn btn-sm btn-primary" id="editor-new-prompt">+ New</button>
          </div>
          <form id="editor-new-prompt-form" class="prompt-library-create hidden">
            <label class="small" for="editor-new-prompt-name">Prompt name</label>
            <input class="form-input" id="editor-new-prompt-name" maxlength="100" required
                   placeholder="Prompt name">
            <label class="small" for="editor-new-prompt-id">File name</label>
            <input class="form-input" id="editor-new-prompt-id" maxlength="64" required
                   pattern="[A-Za-z0-9][A-Za-z0-9_-]{0,63}" placeholder="prompt-file-name">
            <div class="toolbar-row">
              <button type="submit" class="btn btn-sm btn-primary">Create prompt</button>
              <button type="button" class="btn btn-sm" id="editor-cancel-new-prompt">Cancel</button>
            </div>
          </form>
          <p id="editor-library-status" class="small text-muted" role="status" aria-live="polite">Loading prompts…</p>
          <div id="editor-library-list" class="prompt-library-list"></div>
        </section>
        <section class="editor-library-section" aria-labelledby="editor-subagent-title">
          <div class="editor-library-head">
            <h3 id="editor-subagent-title" class="sidebar-title">Sub-agent Library</h3>
            <button type="button" class="btn btn-sm btn-primary" id="editor-new-subagent">+ New</button>
          </div>
          <form id="editor-new-subagent-form" class="prompt-library-create hidden">
            <label class="small" for="editor-new-subagent-name">Sub-agent name</label>
            <input class="form-input" id="editor-new-subagent-name" maxlength="100" required
                   placeholder="Sub-agent name">
            <label class="small" for="editor-new-subagent-id">File name</label>
            <input class="form-input" id="editor-new-subagent-id" maxlength="64" required
                   pattern="[A-Za-z0-9][A-Za-z0-9_-]{0,63}" placeholder="sub-agent-file-name">
            <label class="small" for="editor-new-subagent-description">What it does (optional)</label>
            <textarea class="form-input" id="editor-new-subagent-description" rows="2" maxlength="500"
                      placeholder="Background worker that…"></textarea>
            <div class="toolbar-row">
              <button type="submit" class="btn btn-sm btn-primary">Create sub-agent</button>
              <button type="button" class="btn btn-sm" id="editor-cancel-new-subagent">Cancel</button>
            </div>
          </form>
          <p id="editor-subagent-status" class="small text-muted" role="status" aria-live="polite">Loading sub-agents…</p>
          <div id="editor-subagent-list" class="prompt-library-list"></div>
        </section>
      </aside>
    </div>`;

  container.insertAdjacentHTML('beforeend', `
    <dialog id="editor-save-dialog" class="workspace-save-dialog">
      <form id="editor-save-form">
        <h2>Save to Workspace</h2>
        <p class="text-muted">Choose a workspace folder and file name.</p>
        <label for="editor-save-directory">Folder</label>
        <select id="editor-save-directory" class="form-select"></select>
        <label for="editor-save-filename">File name</label>
        <input id="editor-save-filename" class="form-input" type="text" required>
        <p id="editor-save-error" class="text-danger hidden" role="alert"></p>
        <div class="workspace-save-actions">
          <button id="editor-save-cancel" class="btn" type="button">Cancel</button>
          <button id="editor-save-confirm" class="btn btn-primary" type="submit">Save</button>
        </div>
      </form>
    </dialog>
    <dialog id="editor-tools-dialog" class="workspace-save-dialog editor-tools-dialog">
      <form id="editor-tools-form">
        <h2 id="editor-tools-title">Tools</h2>
        <p id="editor-tools-path" class="text-muted small"></p>
        <input id="editor-tools-filter" class="form-input" type="search"
               placeholder="Filter tools…" aria-label="Filter tools">
        <div class="editor-tools-bulk">
          <span id="editor-tools-count" class="small text-muted"></span>
          <span class="spacer"></span>
          <button id="editor-tools-select-all" class="btn btn-sm" type="button"
                  title="Check every tool shown (respects the filter)">Select all</button>
          <button id="editor-tools-clear-all" class="btn btn-sm" type="button"
                  title="Uncheck every tool shown (respects the filter)">Clear all</button>
        </div>
        <div id="editor-tools-list" class="editor-tools-list" role="group" aria-label="Available tools"></div>
        <label id="editor-tools-mode" class="checkbox-row editor-tools-mode hidden">
          <input type="checkbox" id="editor-tools-agent-mode">
          <span>Switch to <strong>agent</strong> mode. This prompt is in chat mode, where tools never run.</span>
        </label>
        <p class="small text-muted editor-tools-note">
          Saving also rewrites the prompt's <code>## Available Tools</code> section: the name, provider,
          signature and description of each tool you select.
        </p>
        <p id="editor-tools-error" class="text-danger hidden" role="alert"></p>
        <div class="workspace-save-actions">
          <button id="editor-tools-cancel" class="btn" type="button">Cancel</button>
          <button id="editor-tools-confirm" class="btn btn-primary" type="submit">Save tools</button>
        </div>
      </form>
      <div class="editor-tools-grip" role="separator" aria-label="Resize tools dialog"
           title="Drag to resize"></div>
    </dialog>`);

  const editorMount = container.querySelector('#editor-monaco');
  const pathLabel = container.querySelector('#editor-path');
  const dirtyBadge = container.querySelector('#editor-dirty');
  const bytesLabel = container.querySelector('#editor-bytes');
  const posLabel = container.querySelector('#editor-pos');
  const libraryList = container.querySelector('#editor-library-list');
  const libraryStatus = container.querySelector('#editor-library-status');
  const subagentList = container.querySelector('#editor-subagent-list');
  const subagentStatus = container.querySelector('#editor-subagent-status');
  const createPromptForm = container.querySelector('#editor-new-prompt-form');
  const createPromptName = container.querySelector('#editor-new-prompt-name');
  const createPromptId = container.querySelector('#editor-new-prompt-id');
  const toolsDialog = container.querySelector('#editor-tools-dialog');
  const toolsList = container.querySelector('#editor-tools-list');
  const toolsFilter = container.querySelector('#editor-tools-filter');
  const toolsCount = container.querySelector('#editor-tools-count');
  const toolsError = container.querySelector('#editor-tools-error');
  const toolsMode = container.querySelector('#editor-tools-mode');
  const toolsAgentMode = container.querySelector('#editor-tools-agent-mode');
  const toolsConfirm = container.querySelector('#editor-tools-confirm');
  const saveDialog = container.querySelector('#editor-save-dialog');
  const saveDirectory = container.querySelector('#editor-save-directory');
  const saveFilename = container.querySelector('#editor-save-filename');
  const saveError = container.querySelector('#editor-save-error');
  const saveConfirm = container.querySelector('#editor-save-confirm');
  let saveDialogFiles = new Set();
  let suppressEditorChanges = false;
  let workspaceTree;
  const editorReady = attachCodeBuilderEditor(editorMount, '', 'plaintext').then(instance => {
    state.editor = instance;
    instance.onChange(() => {
      if (suppressEditorChanges) return;
      if (!state.dirty) { state.dirty = true; dirtyBadge.classList.remove('hidden'); }
      updateBytes();
    });
    if (state.path) {
      suppressEditorChanges = true;
      instance.setValue(state.content || '');
      instance.setLanguage(languageForPath(state.path));
      suppressEditorChanges = false;
    }
    instance.focus();
    updateBytes();
    return instance;
  });

  function setStatus(element, text, tone = 'muted') {
    element.textContent = text;
    element.className = `small text-${tone}`;
  }

  function libraryRow(name, path, actions) {
    const row = document.createElement('div');
    row.className = 'prompt-library-item';
    const file = document.createElement('div');
    file.className = 'prompt-library-file';
    const title = document.createElement('strong');
    title.textContent = name;
    title.title = name;
    const location = document.createElement('span');
    location.className = 'text-muted small';
    location.textContent = path;
    location.title = path;
    file.append(title, location);
    const buttons = document.createElement('div');
    buttons.className = 'prompt-library-actions';
    buttons.append(...actions);
    row.append(file, buttons);
    return row;
  }

  function libraryButton(label, ariaLabel, onClick, className = 'btn btn-sm') {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = className;
    button.textContent = label;
    button.setAttribute('aria-label', ariaLabel);
    button.addEventListener('click', () => onClick(button));
    return button;
  }

  async function loadPromptLibrary() {
    libraryList.replaceChildren();
    setStatus(libraryStatus, 'Loading prompts…');
    try {
      const environments = ['workspace', 'codebuilder', 'prompt-creation'];
      const results = await Promise.all(environments.map(environment => Api.getAgents(environment)));
      const prompts = results
        .flatMap((data, index) => (data.agents || []).map(agent => ({ ...agent, environment: environments[index] })))
        .sort((left, right) => String(left.name || left.id).localeCompare(String(right.name || right.id)));
      if (!prompts.length) {
        setStatus(libraryStatus, 'No prompts found in the app library.');
        return;
      }
      setStatus(libraryStatus, `${prompts.length} prompt${prompts.length === 1 ? '' : 's'} in library/`);
      for (const prompt of prompts) {
        const name = prompt.name || prompt.id;
        const path = prompt.path || `library/${prompt.id}.md`;
        const tools = libraryButton('Tools', `Choose tools for ${name}`,
          () => openToolsDialog('prompt', prompt.id, name));
        const edit = libraryButton('Edit', `Edit ${name}`, () => openFile(path, false));
        const remove = libraryButton('Delete', `Delete ${name}`, async button => {
          if (!confirm(`Delete prompt "${name}" from the app library?`)) return;
          button.disabled = true;
          try {
            await Api.deleteAgent(prompt.id, prompt.environment);
            if (state.path === path) clearEditor();
            await loadPromptLibrary();
          } catch (err) {
            setStatus(libraryStatus, `Could not delete prompt: ${err.message}`, 'danger');
            button.disabled = false;
          }
        }, 'btn btn-sm btn-danger');
        libraryList.appendChild(libraryRow(name, path, [tools, edit, remove]));
      }
    } catch (err) {
      setStatus(libraryStatus, `Could not load app library: ${err.message}`, 'danger');
    }
  }

  async function loadSubagentLibrary() {
    subagentList.replaceChildren();
    setStatus(subagentStatus, 'Loading sub-agents…');
    try {
      const catalog = await Api.getOrchestrationCatalog();
      const subagents = [...(catalog.subagents || [])]
        .sort((left, right) => String(left.name || left.id).localeCompare(String(right.name || right.id)));
      if (!subagents.length) {
        setStatus(subagentStatus, 'No sub-agent definitions were found.');
        return;
      }
      setStatus(subagentStatus, `${subagents.length} sub-agent${subagents.length === 1 ? '' : 's'} in subAgentLibrary/`);
      for (const agent of subagents) {
        const name = agent.name || agent.id;
        const path = `subAgentLibrary/${agent.id}.yaml`;
        const tools = libraryButton('Tools', `Choose tools for ${name}`,
          () => openToolsDialog('subagent', agent.id, name));
        const edit = libraryButton('Edit', `Edit ${name}`, () => openFile(path, false));
        const remove = libraryButton('Delete', `Delete ${name}`, async button => {
          if (!confirm(`Delete sub-agent "${name}" (${path})? This cannot be undone.`)) return;
          button.disabled = true;
          try {
            await Api.deleteSubagent(agent.id);
            if (state.path === path) clearEditor();
            await loadSubagentLibrary();
          } catch (err) {
            setStatus(subagentStatus, `Could not delete sub-agent: ${err.message}`, 'danger');
            button.disabled = false;
          }
        }, 'btn btn-sm btn-danger');
        subagentList.appendChild(libraryRow(name, path, [tools, edit, remove]));
      }
    } catch (err) {
      setStatus(subagentStatus, `Could not load sub-agents: ${err.message}`, 'danger');
    }
  }

  // --- Tools dialog ---------------------------------------------------------
  let toolCatalog = null;
  let toolsTarget = null;

  // The catalog sends each description as a list of lines; older entries may be a plain string.
  const toolSummary = tool => (Array.isArray(tool.description) ? tool.description.join(' ') : String(tool.description || '')).trim();

  const CATALOG_LABELS = {
    tools: 'Files',
    file_manager: 'File manager',
    editor: 'Editor',
    workspace: 'Workspace',
    codebuilder: 'CodeBuilder',
    core_engine: 'Core engine',
    orchestration: 'Orchestration',
    ragMemory: 'Knowledge (RAG)',
    scratchpadMemory: 'Scratchpad memory'
  };
  const catalogLabel = provider => CATALOG_LABELS[provider]
    || String(provider || 'Other').replace(/[_-]+/g, ' ').replace(/([a-z])([A-Z])/g, '$1 $2')
      .replace(/^./, c => c.toUpperCase());

  function renderToolOptions(catalog, selected) {
    const known = new Set(catalog.map(tool => tool.name));
    // Keep tools the file lists but the catalog lacks, so saving never drops them silently.
    const missing = selected.filter(name => !known.has(name)).map(name => ({ name, missing: true }));
    const groups = new Map();
    for (const tool of catalog) {
      const label = catalogLabel(tool.provider);
      if (!groups.has(label)) groups.set(label, []);
      groups.get(label).push(tool);
    }
    const sections = [...groups.entries()]
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([label, tools]) => [label, tools.sort((left, right) => left.name.localeCompare(right.name))]);
    if (missing.length) sections.unshift(['Not registered', missing]);
    toolsList.replaceChildren(...sections.map(([label, tools]) => renderToolGroup(label, tools, selected)));
    // Unfold the group holding call_subagent when it is already on, so its sub-agent picker is visible.
    const callSubagent = toolsList.querySelector(`input[name="tools"][value="${SUBAGENT_TOOL}"]`);
    if (callSubagent?.checked) callSubagent.closest('.editor-tool-group').open = true;
    updateToolsCount();
  }

  const SUBAGENT_TOOL = 'call_subagent';

  function renderSubagentPanel() {
    const panel = document.createElement('div');
    panel.className = 'editor-subagent-panel';
    const intro = document.createElement('p');
    intro.className = 'small text-muted';
    intro.textContent = 'Choose which sub-agents this prompt may call, and tell it when to call each one. '
      + 'Saved to the prompt\'s "## Sub-agents" section.';
    panel.appendChild(intro);
    const chosen = new Map((toolsTarget.subagents || []).map(entry => [entry.id, entry.usage]));
    const available = (toolsTarget.availableSubagents || [])
      .filter(agent => !(toolsTarget.kind === 'subagent' && agent.id === toolsTarget.agentId));
    // Entries the prompt names but the library no longer has: shown unchecked so saving drops them.
    const known = new Set(available.map(agent => agent.id));
    const gone = [...chosen.keys()].filter(id => !known.has(id)).map(id => ({ id, gone: true }));
    if (!available.length && !gone.length) {
      const empty = document.createElement('p');
      empty.className = 'small text-muted';
      empty.textContent = 'No sub-agents in subAgentLibrary/ yet. Create one with + New in the Sub-agent Library.';
      panel.appendChild(empty);
    }
    for (const agent of [...gone, ...available]) {
      const choice = document.createElement('div');
      choice.className = 'editor-subagent-choice';
      const label = document.createElement('label');
      label.className = 'editor-subagent-check';
      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.name = 'subagents';
      checkbox.value = agent.id;
      checkbox.checked = !agent.gone && chosen.has(agent.id);
      const copy = document.createElement('span');
      const title = document.createElement('strong');
      title.textContent = agent.name || agent.id;
      const detail = document.createElement('span');
      detail.className = agent.gone ? 'text-danger small' : 'text-muted small';
      detail.textContent = agent.gone
        ? `${agent.id}: no longer in subAgentLibrary/, so it will be removed when you save`
        : [agent.id, agent.description].filter(Boolean).join(' · ');
      copy.append(title, detail);
      label.append(checkbox, copy);
      const usage = document.createElement('textarea');
      usage.className = 'form-input editor-subagent-usage';
      usage.rows = 3;
      usage.maxLength = 2000;
      usage.dataset.subagent = agent.id;
      usage.value = chosen.get(agent.id) || '';
      usage.placeholder = `When should it call ${agent.name || agent.id}, and what should it pass? `
        + 'e.g. "Call it when the user asks about indexed notes; pass the question as task_prompt."';
      usage.setAttribute('aria-label', `When to use ${agent.name || agent.id}`);
      choice.append(label, usage);
      panel.appendChild(choice);
    }
    return panel;
  }

  function syncSubagentPanel() {
    const panel = toolsList.querySelector('.editor-subagent-panel');
    if (!panel) return;
    const option = panel.previousElementSibling;
    const enabled = option.querySelector('input[name="tools"]').checked;
    panel.classList.toggle('hidden', !enabled || option.classList.contains('hidden'));
    panel.querySelectorAll('.editor-subagent-choice').forEach(choice => {
      choice.querySelector('.editor-subagent-usage')
        .classList.toggle('hidden', !choice.querySelector('input[name="subagents"]').checked);
    });
  }

  function renderToolGroup(label, tools, selected) {
    const group = document.createElement('details');
    group.className = 'editor-tool-group';
    const summary = document.createElement('summary');
    const groupCheck = document.createElement('input');
    groupCheck.type = 'checkbox';
    groupCheck.className = 'editor-tool-group-check';
    groupCheck.setAttribute('aria-label', `Select all ${label} tools`);
    // Clicking the checkbox must not also fold the section.
    groupCheck.addEventListener('click', event => event.stopPropagation());
    groupCheck.addEventListener('change', () => {
      group.querySelectorAll('.editor-tool-option:not(.hidden) input[name="tools"]')
        .forEach(input => { input.checked = groupCheck.checked; });
      updateToolsCount();
    });
    const title = document.createElement('span');
    title.className = 'editor-tool-group-title';
    title.textContent = label;
    const count = document.createElement('span');
    count.className = 'editor-tool-group-count text-muted small';
    summary.append(groupCheck, title, count);
    group.append(summary, ...tools.flatMap(tool => tool.name === SUBAGENT_TOOL && !tool.missing
      ? [renderToolOption(tool, selected), renderSubagentPanel()]
      : [renderToolOption(tool, selected)]));
    return group;
  }

  function renderToolOption(tool, selected) {
    const label = document.createElement('label');
    label.className = 'editor-tool-option';
    const summary = toolSummary(tool);
    label.dataset.search = `${tool.name} ${summary}`.toLowerCase();
    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.name = 'tools';
    checkbox.value = tool.name;
    checkbox.checked = selected.includes(tool.name);
    const copy = document.createElement('span');
    const title = document.createElement('strong');
    title.textContent = tool.name;
    copy.appendChild(title);
    if (tool.missing) {
      const note = document.createElement('span');
      note.className = 'text-danger small';
      note.textContent = 'Not registered: this tool is missing from the tool catalog';
      copy.appendChild(note);
    } else if (summary) {
      const description = document.createElement('span');
      description.className = 'text-muted small';
      description.textContent = summary;
      copy.appendChild(description);
    }
    label.append(checkbox, copy);
    return label;
  }

  function updateToolsCount() {
    const checked = toolsList.querySelectorAll('input[name="tools"]:checked').length;
    const total = toolsList.querySelectorAll('input[name="tools"]').length;
    toolsCount.textContent = `${checked} of ${total} selected`;
    toolsList.querySelectorAll('.editor-tool-group').forEach(group => {
      const inputs = [...group.querySelectorAll('input[name="tools"]')];
      const on = inputs.filter(input => input.checked).length;
      const groupCheck = group.querySelector('.editor-tool-group-check');
      groupCheck.checked = on === inputs.length;
      groupCheck.indeterminate = on > 0 && on < inputs.length;
      group.querySelector('.editor-tool-group-count').textContent = `${on}/${inputs.length}`;
    });
    if (toolsTarget?.mode === 'chat' && checked && !toolsAgentMode.dataset.touched) toolsAgentMode.checked = true;
    syncSubagentPanel();
  }

  async function openToolsDialog(kind, agentId, name) {
    toolsTarget = null;
    container.querySelector('#editor-tools-title').textContent = `Tools for ${name}`;
    container.querySelector('#editor-tools-path').textContent = 'Loading…';
    toolsFilter.value = '';
    toolsList.replaceChildren();
    toolsError.classList.add('hidden');
    toolsMode.classList.add('hidden');
    toolsAgentMode.checked = false;
    delete toolsAgentMode.dataset.touched;
    toolsConfirm.disabled = true;
    toolsDialog.showModal();
    try {
      const [catalog, current, availableSubagents] = await Promise.all([
        toolCatalog ? Promise.resolve(toolCatalog) : Api.getTools().then(data => data.tools || []),
        Api.getAgentTools(kind, agentId),
        // A broken sub-agent file must not block editing tools; the picker just shows none.
        Api.getOrchestrationCatalog().then(data => data.subagents || []).catch(() => [])
      ]);
      toolCatalog = catalog;
      toolsTarget = { kind, agentId, ...current, availableSubagents };
      container.querySelector('#editor-tools-path').textContent = current.path;
      toolsMode.classList.toggle('hidden', current.mode !== 'chat');
      renderToolOptions(catalog, current.tools);
      toolsConfirm.disabled = false;
      toolsFilter.focus();
    } catch (err) {
      container.querySelector('#editor-tools-path').textContent = '';
      toolsError.textContent = `Could not load tools: ${err.message}`;
      toolsError.classList.remove('hidden');
    }
  }

  toolsFilter.addEventListener('input', () => {
    const query = toolsFilter.value.trim().toLowerCase();
    toolsList.querySelectorAll('.editor-tool-option').forEach(option => {
      option.classList.toggle('hidden', Boolean(query) && !option.dataset.search.includes(query));
    });
    toolsList.querySelectorAll('.editor-tool-group').forEach(group => {
      group.classList.toggle('hidden', !group.querySelector('.editor-tool-option:not(.hidden)'));
      // Groups start collapsed; a search unfolds the matches and clearing it folds them back.
      group.open = Boolean(query);
    });
    syncSubagentPanel();
  });

  // The dialog is centred, so it grows on both sides: scale the drag by two to track the pointer.
  const toolsGrip = container.querySelector('.editor-tools-grip');
  const toolsSizeKey = 'novous2.editor.tools-dialog-size';
  try {
    const size = JSON.parse(localStorage.getItem(toolsSizeKey) || 'null');
    if (size?.width && size?.height) {
      toolsDialog.style.width = `${size.width}px`;
      toolsDialog.style.height = `${size.height}px`;
    }
  } catch { /* storage unavailable */ }
  toolsGrip.addEventListener('pointerdown', event => {
    event.preventDefault();
    toolsGrip.setPointerCapture(event.pointerId);
    const startX = event.clientX;
    const startY = event.clientY;
    const startWidth = toolsDialog.offsetWidth;
    const startHeight = toolsDialog.offsetHeight;
    const onMove = moveEvent => {
      const width = Math.min(window.innerWidth - 32, Math.max(360, startWidth + (moveEvent.clientX - startX) * 2));
      const height = Math.min(window.innerHeight - 32, Math.max(360, startHeight + (moveEvent.clientY - startY) * 2));
      toolsDialog.style.width = `${width}px`;
      toolsDialog.style.height = `${height}px`;
    };
    const onUp = () => {
      toolsGrip.removeEventListener('pointermove', onMove);
      toolsGrip.removeEventListener('pointerup', onUp);
      toolsGrip.removeEventListener('pointercancel', onUp);
      try {
        localStorage.setItem(toolsSizeKey, JSON.stringify({
          width: toolsDialog.offsetWidth, height: toolsDialog.offsetHeight
        }));
      } catch { /* storage unavailable */ }
    };
    toolsGrip.addEventListener('pointermove', onMove);
    toolsGrip.addEventListener('pointerup', onUp);
    toolsGrip.addEventListener('pointercancel', onUp);
  });
  function setVisibleTools(checked) {
    toolsList.querySelectorAll('.editor-tool-option:not(.hidden) input[name="tools"]')
      .forEach(input => { input.checked = checked; });
    updateToolsCount();
  }
  container.querySelector('#editor-tools-select-all').addEventListener('click', () => setVisibleTools(true));
  container.querySelector('#editor-tools-clear-all').addEventListener('click', () => setVisibleTools(false));
  toolsList.addEventListener('change', event => {
    if (!event.target.classList.contains('editor-tool-group-check')) updateToolsCount();
  });
  toolsAgentMode.addEventListener('change', () => { toolsAgentMode.dataset.touched = 'true'; });
  container.querySelector('#editor-tools-cancel').addEventListener('click', () => toolsDialog.close());
  container.querySelector('#editor-tools-form').addEventListener('submit', async event => {
    event.preventDefault();
    if (!toolsTarget) return;
    const tools = [...toolsList.querySelectorAll('input[name="tools"]:checked')].map(input => input.value);
    const mode = toolsTarget.mode === 'chat' && toolsAgentMode.checked ? 'agent' : null;
    toolsConfirm.disabled = true;
    try {
      const subagents = tools.includes(SUBAGENT_TOOL)
        ? [...toolsList.querySelectorAll('input[name="subagents"]:checked')].map(input => ({
          id: input.value,
          usage: toolsList.querySelector(`.editor-subagent-usage[data-subagent="${CSS.escape(input.value)}"]`).value.trim()
        }))
        : [];
      const saved = await Api.setAgentTools(toolsTarget.kind, toolsTarget.agentId, tools, mode, subagents);
      toolsDialog.close();
      if (toolsTarget.kind === 'prompt') await loadPromptLibrary();
      else await loadSubagentLibrary();
      if (state.path === saved.path) {
        if (state.dirty) alert(`Tools were saved to ${saved.path}, but your unsaved edits in the editor are older than that change. Reload before saving to avoid overwriting the tools.`);
        else await openFile(saved.path, false);
      }
    } catch (err) {
      toolsError.textContent = `Could not save tools: ${err.message}`;
      toolsError.classList.remove('hidden');
    } finally {
      toolsConfirm.disabled = false;
    }
  });

  container.querySelector('#editor-new-prompt').addEventListener('click', () => {
    createPromptForm.classList.toggle('hidden');
    if (!createPromptForm.classList.contains('hidden')) createPromptName.focus();
  });
  container.querySelector('#editor-cancel-new-prompt').addEventListener('click', () => {
    createPromptForm.reset();
    delete createPromptId.dataset.edited;
    createPromptForm.classList.add('hidden');
  });
  createPromptName.addEventListener('input', () => {
    if (!createPromptId.dataset.edited) createPromptId.value = slugify(createPromptName.value);
  });
  createPromptId.addEventListener('input', () => {
    createPromptId.dataset.edited = 'true';
  });
  createPromptForm.addEventListener('submit', async event => {
    event.preventDefault();
    const name = createPromptName.value.trim();
    const id = createPromptId.value.trim().toLowerCase();
    if (!name || !/^[a-z0-9][a-z0-9_-]{0,63}$/.test(id)) {
      setStatus(libraryStatus, 'Enter a prompt name and a valid file name.', 'danger');
      return;
    }
    const submit = createPromptForm.querySelector('button[type="submit"]');
    submit.disabled = true;
    try {
      const created = await Api.createAgent(id, name, '', 'chat', '', 'prompt-creation');
      createPromptForm.reset();
      delete createPromptId.dataset.edited;
      createPromptForm.classList.add('hidden');
      await loadPromptLibrary();
      await openFile(created.path || `library/${id}.md`, false);
    } catch (err) {
      setStatus(libraryStatus, `Could not create prompt: ${err.message}`, 'danger');
    } finally {
      submit.disabled = false;
    }
  });

  const createSubagentForm = container.querySelector('#editor-new-subagent-form');
  const createSubagentName = container.querySelector('#editor-new-subagent-name');
  const createSubagentId = container.querySelector('#editor-new-subagent-id');
  container.querySelector('#editor-new-subagent').addEventListener('click', () => {
    createSubagentForm.classList.toggle('hidden');
    if (!createSubagentForm.classList.contains('hidden')) createSubagentName.focus();
  });
  container.querySelector('#editor-cancel-new-subagent').addEventListener('click', () => {
    createSubagentForm.reset();
    delete createSubagentId.dataset.edited;
    createSubagentForm.classList.add('hidden');
  });
  createSubagentName.addEventListener('input', () => {
    if (!createSubagentId.dataset.edited) createSubagentId.value = slugify(createSubagentName.value);
  });
  createSubagentId.addEventListener('input', () => {
    createSubagentId.dataset.edited = 'true';
  });
  createSubagentForm.addEventListener('submit', async event => {
    event.preventDefault();
    const name = createSubagentName.value.trim();
    const id = createSubagentId.value.trim().toLowerCase();
    if (!name || !/^[a-z0-9][a-z0-9_-]{0,63}$/.test(id)) {
      setStatus(subagentStatus, 'Enter a sub-agent name and a valid file name.', 'danger');
      return;
    }
    const description = container.querySelector('#editor-new-subagent-description').value.trim();
    const submit = createSubagentForm.querySelector('button[type="submit"]');
    submit.disabled = true;
    try {
      const created = await Api.createSubagent(id, name, description);
      createSubagentForm.reset();
      delete createSubagentId.dataset.edited;
      createSubagentForm.classList.add('hidden');
      await loadSubagentLibrary();
      await openFile(created.path, false);
    } catch (err) {
      setStatus(subagentStatus, `Could not create sub-agent: ${err.message}`, 'danger');
    } finally {
      submit.disabled = false;
    }
  });

  function clearEditor() {
    state.path = null;
    state.content = '';
    state.managedPath = false;
    state.editor?.setValue('');
    pathLabel.textContent = 'No file open';
    dirtyBadge.classList.add('hidden');
    state.dirty = false;
    updateBytes();
  }

  async function openFile(path, managedFile = false) {
    if (state.dirty && !confirm('Discard unsaved changes?')) return;
    try {
      const data = managedFile ? await Api.readManagedFile(path) : await Api.readFile(path);
      state.path = data.path;
      state.content = data.content;
      state.managedPath = managedFile;
      state.dirty = false;
      pathLabel.textContent = data.path;
      dirtyBadge.classList.add('hidden');
      await editorReady;
      suppressEditorChanges = true;
      state.editor.setLanguage(languageForPath(data.path));
      state.editor.setValue(data.content);
      state.editor.setHighlightedLines(initialFile?.previewLines || []);
      suppressEditorChanges = false;
      updateBytes();
      updatePos();
      state.editor.focus();
    } catch (err) {
      suppressEditorChanges = false;
      alert(err.message);
    }
  }

  function languageForPath(path) {
    const extension = path.split('.').pop().toLowerCase();
    return ({
      c: 'c', cc: 'cpp', cpp: 'cpp', cs: 'csharp', css: 'css', go: 'go',
      h: 'cpp', htm: 'html', html: 'html', java: 'java', js: 'javascript',
      json: 'json', jsx: 'javascript', md: 'markdown', mjs: 'javascript',
      py: 'python', rb: 'ruby', rs: 'rust', sh: 'shell', sql: 'sql',
      ts: 'typescript', tsx: 'typescript', txt: 'plaintext', xml: 'xml',
      yaml: 'yaml', yml: 'yaml'
    })[extension] || 'plaintext';
  }

  workspaceTree = renderTree(container.querySelector('#editor-tree'), {
    onBeforeDelete: path => {
      const deletesOpenFile = state.path === path || state.path?.startsWith(`${path}/`);
      return !deletesOpenFile || !state.dirty ||
        confirm('This item contains the open file with unsaved changes. Delete anyway?');
    },
    onDelete: path => {
      if (state.path === path || state.path?.startsWith(`${path}/`)) clearEditor();
    }
  });
  const onFileSelected = event => {
    const path = event.detail && event.detail.path;
    if (typeof path === 'string' && path) openFile(path, true);
  };
  fileSelectionListeners.set(container, onFileSelected);
  window.addEventListener('novous:file-selected', onFileSelected);
  editorReady.then(instance => instance.onPosition(updatePos));

  async function openSaveDialog() {
    try {
      const managedSave = state.managedPath || !state.path;
      const tree = managedSave
        ? await Api.getManagedFileTree()
        : await Api.getFileTree();
      const folders = [];
      const files = new Set();
      function collect(node) {
        if (node.type === 'directory') {
          if (node.path) folders.push(node.path);
          (node.children || []).forEach(collect);
        } else {
          files.add(node.path);
        }
      }
      collect(tree);
      // Library files live outside the workspace tree; keep their folder selectable.
      const openDirectory = (state.path || '').split('/').slice(0, -1).join('/');
      if (!managedSave && openDirectory && !folders.includes(openDirectory)) folders.push(openDirectory);
      folders.sort((a, b) => a.localeCompare(b));

      saveDirectory.replaceChildren();
      const rootOption = document.createElement('option');
      rootOption.value = '';
      rootOption.textContent = '/ (workspace root)';
      saveDirectory.appendChild(rootOption);
      folders.forEach(path => {
        const option = document.createElement('option');
        option.value = path;
        option.textContent = path;
        saveDirectory.appendChild(option);
      });

      const currentParts = (state.path || '').split('/');
      const currentFilename = currentParts.pop() || 'untitled.txt';
      const currentDirectory = currentParts.join('/');
      saveDirectory.value = folders.includes(currentDirectory) ? currentDirectory : '';
      saveFilename.value = currentFilename;
      saveError.textContent = '';
      saveError.classList.add('hidden');
      saveDialog.showModal();
      saveFilename.focus();
      saveFilename.select();
      saveDialogFiles = files;
    } catch (err) {
      alert(`Could not open the workspace save picker: ${err.message}`);
    }
  }

  async function saveFile() {
    if (!saveDialog.open) {
      await openSaveDialog();
      return;
    }

    const filename = saveFilename.value.trim();
    if (!filename || filename === '.' || filename === '..' || /[\\/]/.test(filename)) {
      saveError.textContent = 'Enter a file name without folder separators.';
      saveError.classList.remove('hidden');
      saveFilename.focus();
      return;
    }

    const targetPath = [saveDirectory.value, filename].filter(Boolean).join('/');
    if (saveDialogFiles.has(targetPath) && targetPath !== state.path &&
        !confirm(`"${targetPath}" already exists. Overwrite it?`)) {
      return;
    }

    saveConfirm.disabled = true;
    try {
      await editorReady;
      const content = state.editor.getValue();
      const managedSave = state.managedPath || !state.path;
      if (managedSave) await Api.writeManagedFile(targetPath, content);
      else await Api.writeFile(targetPath, content);
      state.path = targetPath;
      state.content = content;
      state.managedPath = managedSave;
      state.editor.setLanguage(languageForPath(targetPath));
      state.dirty = false;
      dirtyBadge.classList.add('hidden');
      pathLabel.textContent = targetPath;
      updateBytes();
      saveDialog.close();
      workspaceTree.reload();
      if (!managedSave && targetPath.startsWith('library/')) loadPromptLibrary();
      if (!managedSave && targetPath.startsWith('subAgentLibrary/')) loadSubagentLibrary();
    } catch (err) {
      saveError.textContent = `Save failed: ${err.message}`;
      saveError.classList.remove('hidden');
    } finally {
      saveConfirm.disabled = false;
    }
  }

  function updateBytes() {
    bytesLabel.textContent = `${new Blob([state.editor?.getValue() || '']).size} bytes`;
  }

  function updatePos() {
    const position = state.editor?.getPosition();
    if (position) posLabel.textContent = `Ln ${position.lineNumber}, Col ${position.column}`;
  }

  container.querySelector('#editor-save').addEventListener('click', openSaveDialog);
  container.querySelector('#editor-save-form').addEventListener('submit', (event) => {
    event.preventDefault();
    saveFile();
  });
  container.querySelector('#editor-save-cancel').addEventListener('click', () => saveDialog.close());
  container.querySelector('#editor-reload').addEventListener('click', () => {
    if (state.path) openFile(state.path, state.managedPath);
  });
  editorReady.then(instance => instance.setSaveHandler(saveFile));

  // --- Resizable side panels ------------------------------------------------
  const layout = container.querySelector('.editor-layout');
  const panels = {
    left: { element: container.querySelector('.editor-sidebar'), width: 250, restore: 250 },
    right: { element: container.querySelector('.editor-library'), width: 280, restore: 280 }
  };
  try {
    const saved = JSON.parse(localStorage.getItem(panelWidthStorageKey) || '{}');
    for (const side of Object.keys(panels)) {
      if (Number.isFinite(saved[side]?.width)) panels[side].width = saved[side].width;
      if (Number.isFinite(saved[side]?.restore)) panels[side].restore = saved[side].restore;
    }
  } catch { /* storage unavailable */ }

  function setPanelWidth(side, width) {
    const panel = panels[side];
    const other = panels[side === 'left' ? 'right' : 'left'];
    // A hidden layout has no width yet; skip clamping instead of collapsing everything.
    const maxWidth = layout.clientWidth
      ? Math.max(0, layout.clientWidth - other.width - (RESIZER_WIDTH * 2) - MIN_EDITOR_WIDTH)
      : Infinity;
    let nextWidth = Math.round(Math.min(maxWidth, Math.max(0, width)));
    if (nextWidth < COLLAPSE_THRESHOLD) nextWidth = 0;
    else panel.restore = nextWidth;
    panel.width = nextWidth;
    panel.element.classList.toggle('editor-panel-collapsed', nextWidth === 0);
    layout.style.setProperty(`--editor-${side}-width`, `${nextWidth}px`);
    const resizer = container.querySelector(`.editor-resizer[data-panel="${side}"]`);
    resizer.setAttribute('aria-valuenow', String(nextWidth));
    if (Number.isFinite(maxWidth)) resizer.setAttribute('aria-valuemax', String(maxWidth));
    resizer.classList.toggle('is-collapsed', nextWidth === 0);
    try {
      localStorage.setItem(panelWidthStorageKey, JSON.stringify({
        left: { width: panels.left.width, restore: panels.left.restore },
        right: { width: panels.right.width, restore: panels.right.restore }
      }));
    } catch { /* storage unavailable */ }
  }

  function togglePanel(side) {
    const panel = panels[side];
    setPanelWidth(side, panel.width ? 0 : (panel.restore || 250));
  }

  container.querySelectorAll('.editor-resizer').forEach(resizer => {
    const side = resizer.dataset.panel;
    resizer.setAttribute('aria-valuemin', '0');
    resizer.addEventListener('pointerdown', event => {
      if (window.matchMedia('(max-width: 820px)').matches) return;
      event.preventDefault();
      resizer.setPointerCapture(event.pointerId);
      layout.classList.add('is-resizing');
      const bounds = layout.getBoundingClientRect();
      const onMove = moveEvent => {
        setPanelWidth(side, side === 'left'
          ? moveEvent.clientX - bounds.left
          : bounds.right - moveEvent.clientX);
      };
      const onUp = () => {
        layout.classList.remove('is-resizing');
        resizer.removeEventListener('pointermove', onMove);
        resizer.removeEventListener('pointerup', onUp);
        resizer.removeEventListener('pointercancel', onUp);
      };
      resizer.addEventListener('pointermove', onMove);
      resizer.addEventListener('pointerup', onUp);
      resizer.addEventListener('pointercancel', onUp);
    });
    resizer.addEventListener('dblclick', () => togglePanel(side));
    resizer.addEventListener('keydown', event => {
      // Arrow keys move the handle; the panel grows toward the editor.
      const grow = side === 'left' ? 'ArrowRight' : 'ArrowLeft';
      const shrink = side === 'left' ? 'ArrowLeft' : 'ArrowRight';
      const width = panels[side].width;
      if (event.key === grow) setPanelWidth(side, Math.max(width + 20, COLLAPSE_THRESHOLD));
      else if (event.key === shrink) setPanelWidth(side, width - 20);
      else if (event.key === 'Home') setPanelWidth(side, 0);
      else if (event.key === 'End') setPanelWidth(side, layout.clientWidth);
      else if (event.key === 'Enter' || event.key === ' ') togglePanel(side);
      else return;
      event.preventDefault();
    });
  });
  setPanelWidth('left', panels.left.width);
  setPanelWidth('right', panels.right.width);

  loadPromptLibrary();
  loadSubagentLibrary();

  if (initialFile?.path) openFile(initialFile.path, initialFile.managedFile === true);
}
