/**
 * Squad Development — two-pane prompt editor with run and error feedback.
 *
 * Layout: squad tabs, run bar, error panel, two side-by-side Monaco editors
 * with dropdown agent selectors. Agents are ordered by execution sequence
 * (members first, lead last).
 */
import { Api } from './api.js';
import { esc, navigate } from './shell.js';
import { loadMonaco } from './codebuilder/codebuilder_editor.js';
import { formatPastedMarkdown } from './studio_paste_markdown.js';
import { createPromptStarter } from './prompt_starter.js';

const MIN_FONT = 11, MAX_FONT = 22, DEFAULT_FONT = 14, FONT_STEP = 1;
const STORAGE_KEY = 'sd-test-queries';

let activeStudio = null;

function loadSavedQueries(squadId) {
  try {
    const all = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}');
    return all[squadId] || [];
  } catch { return []; }
}

function saveSavedQueries(squadId, queries) {
  try {
    const all = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}');
    all[squadId] = queries;
    localStorage.setItem(STORAGE_KEY, JSON.stringify(all));
  } catch { /* storage unavailable */ }
}

export function leaveSquadDevelopment() {
  if (!activeStudio || activeStudio.canLeave()) {
    activeStudio?.close();
    activeStudio = null;
    return true;
  }
  return false;
}

export function renderSquadDevelopment(container) {
  const editors = new Map();
  const dirty = new Map();
  const baselines = new Map();
  const prompts = new Map();
  let agents = [];
  let leadId = null;
  let fontSize = DEFAULT_FONT;
  let currentSquadId = null;
  let eventSource = null;
  let failedAgents = new Set();
  let wordWrap = true;
  let sidebarOpen = true;
  let paneState = { left: null, right: null };
  let orderChanged = false;

  container.innerHTML = `
    <div class="sd-page">
      <header class="sd-head">
        <div>
          <h2>Squad Development</h2>
          <p class="text-muted small">Edit agent prompts side by side. Run the squad to test changes.</p>
        </div>
        <div class="sd-head-actions">
          <button class="btn btn-sm" data-action="prompt-starter">Prompt Starter</button>
          <button class="btn btn-sm" data-action="toggle-sidebar" title="Toggle sidebar">Panel</button>
          <button class="btn btn-sm" data-action="chat">Chat Agent</button>
        </div>
      </header>
      <div class="sd-toolbar">
        <div class="sd-squad-tabs" data-role="squad-tabs">
          <span class="text-muted small">Loading squads…</span>
        </div>
        <div class="sd-font-controls">
          <button class="btn btn-sm" data-action="font-down" title="Decrease font size">A-</button>
          <span class="sd-font-label" data-role="font-label">${fontSize}px</span>
          <button class="btn btn-sm" data-action="font-up" title="Increase font size">A+</button>
        </div>
      </div>
      <div class="sd-body">
        <nav class="sd-nav" data-role="nav-panel">
          <h4 class="sd-nav-heading">Agent Flow</h4>
          <div class="sd-flow" data-role="flow-list">
            <p class="text-muted small" style="padding:0.5rem">Select a squad.</p>
          </div>
          <div class="sd-flow-footer sd-hidden" data-role="flow-footer">
            <button class="btn btn-sm btn-block" data-action="save-order">Save Order</button>
          </div>
        </nav>
        <div class="sd-nav-divider" data-role="nav-divider" title="Drag to resize"></div>
        <div class="sd-left">
          <div class="sd-workspace" data-role="workspace">
            <p class="text-muted" style="padding:2rem;text-align:center">Select a squad to begin editing prompts.</p>
          </div>
        </div>
        <div class="sd-sidebar-divider" data-role="sidebar-divider" title="Drag to resize panel"></div>
        <aside class="sd-sidebar" data-role="sidebar">
          <section class="sd-sb-section">
            <h4 class="sd-sb-heading">Input</h4>
            <textarea class="form-input sd-sb-query" data-role="run-query" rows="3" placeholder="Describe the task to run…"></textarea>
            <div class="sd-sb-btn-row">
              <button class="btn btn-sm btn-primary" data-action="run-squad">Run</button>
              <button class="btn btn-sm" data-action="add-query" title="Save current query">Save</button>
              <span class="sd-run-status text-muted small" data-role="run-status"></span>
            </div>
            <ul class="sd-sb-query-list" data-role="sb-query-list"></ul>
            <p class="sd-sb-empty text-muted small" data-role="sb-queries-empty">No saved queries yet.</p>
          </section>
          <section class="sd-sb-section">
            <h4 class="sd-sb-heading">Tools</h4>
            <div class="sd-sb-actions">
              <button class="btn btn-sm btn-block" data-action="save-all-sb">Save All</button>
              <button class="btn btn-sm btn-block" data-action="toggle-wrap">Word Wrap: On</button>
              <button class="btn btn-sm btn-block" data-action="reset-changes">Reset Changes</button>
              <button class="btn btn-sm btn-block" data-action="validate-prompts">Validate</button>
            </div>
          </section>
          <section class="sd-sb-section sd-sb-output">
            <h4 class="sd-sb-heading">Output</h4>
            <p class="small text-danger sd-error-line" data-role="error" role="alert"></p>
            <p class="sd-sb-output-status" data-role="output-status"></p>
            <div class="sd-errors sd-hidden" data-role="run-errors">
              <div class="sd-errors-banner" data-role="errors-banner"></div>
              <details>
                <summary class="sd-errors-summary">Show details</summary>
                <div data-role="run-errors-body"></div>
              </details>
            </div>
            <p class="sd-sb-empty text-muted small" data-role="output-empty">Run a squad to see results.</p>
          </section>
        </aside>
      </div>
    </div>`;

  const errorEl = container.querySelector('[data-role="error"]');
  const squadTabs = container.querySelector('[data-role="squad-tabs"]');
  const workspace = container.querySelector('[data-role="workspace"]');
  const fontLabel = container.querySelector('[data-role="font-label"]');
  const runQuery = container.querySelector('[data-role="run-query"]');
  const runStatus = container.querySelector('[data-role="run-status"]');
  const runErrorsHost = container.querySelector('[data-role="run-errors"]');
  const errorsBanner = container.querySelector('[data-role="errors-banner"]');
  const runErrorsBody = container.querySelector('[data-role="run-errors-body"]');
  const sidebar = container.querySelector('[data-role="sidebar"]');
  const sidebarDivider = container.querySelector('[data-role="sidebar-divider"]');
  const sbQueryList = container.querySelector('[data-role="sb-query-list"]');
  const sbQueriesEmpty = container.querySelector('[data-role="sb-queries-empty"]');
  const wrapBtn = container.querySelector('[data-action="toggle-wrap"]');
  const outputStatus = container.querySelector('[data-role="output-status"]');
  const outputEmpty = container.querySelector('[data-role="output-empty"]');
  const flowList = container.querySelector('[data-role="flow-list"]');
  const flowFooter = container.querySelector('[data-role="flow-footer"]');
  const navPanel = container.querySelector('[data-role="nav-panel"]');
  const navDivider = container.querySelector('[data-role="nav-divider"]');

  function showError(err) { errorEl.textContent = err?.message || String(err); }
  function clearError() { errorEl.textContent = ''; }
  const starter = createPromptStarter(container);
  function canDiscard() { return !dirty.size || confirm('Discard unsaved prompt changes?'); }

  activeStudio = {
    canLeave: () => (!dirty.size && !starter.hasChanges()) || confirm('Discard unsaved prompt and Prompt Starter changes?'),
    close() {
      for (const ed of editors.values()) ed.dispose();
      editors.clear(); dirty.clear(); baselines.clear(); prompts.clear();
      eventSource?.close();
      starter.dispose();
    },
  };

  function updateSaveAll() {
    const btn = container.querySelector('[data-action="save-all-sb"]');
    if (!btn) return;
    const n = dirty.size;
    btn.textContent = n ? `Save All (${n})` : 'Save All';
  }

  function setFontSize(size) {
    fontSize = Math.max(MIN_FONT, Math.min(MAX_FONT, size));
    fontLabel.textContent = `${fontSize}px`;
    for (const ed of editors.values()) ed.updateOptions({ fontSize });
  }

  function agentLabel(a) {
    const idx = agents.indexOf(a);
    const step = a.id === leadId ? 'synthesis' : `step ${idx + 1}`;
    return `${a.name || a.id} (${step})`;
  }

  // --- Squad tabs ---
  async function loadSquads() {
    try {
      const overview = await Api.getSquadsOverview();
      const squads = overview.squads || [];
      if (!squads.length) {
        squadTabs.innerHTML = '<span class="text-muted small">No registered squads found.</span>';
        return;
      }
      squadTabs.innerHTML = squads.map(s =>
        `<button class="btn btn-sm${s.squad_id === currentSquadId ? ' cb-btn-active' : ''}" data-squad="${esc(s.squad_id)}">${esc(s.name || s.squad_id)}</button>`
      ).join('');
    } catch (err) { showError(err); }
  }

  squadTabs.addEventListener('click', e => {
    const btn = e.target.closest('[data-squad]');
    if (!btn) return;
    if (btn.dataset.squad === currentSquadId) return;
    if (!canDiscard()) return;
    clearError();
    selectSquad(btn.dataset.squad);
  });

  // --- Select squad ---
  async function selectSquad(squadId) {
    for (const ed of editors.values()) ed.dispose();
    editors.clear(); dirty.clear(); baselines.clear(); prompts.clear();
    failedAgents.clear();
    eventSource?.close(); eventSource = null;
    currentSquadId = squadId;
    orderChanged = false;
    updateSaveAll();

    squadTabs.querySelectorAll('.btn').forEach(b =>
      b.classList.toggle('cb-btn-active', b.dataset.squad === squadId));

    runStatus.textContent = '';
    runErrorsHost.classList.add('sd-hidden');
    outputStatus.textContent = '';
    outputEmpty.hidden = false;
    workspace.innerHTML = '<p class="text-muted" style="padding:2rem;text-align:center">Loading…</p>';

    try {
      const blueprint = await Api.getSquadBlueprint(squadId);
      const lead = blueprint.lead;
      const members = (blueprint.members || []).filter(m => m && m.id !== lead?.id);
      agents = [...members, lead].filter(Boolean);
      leadId = lead?.id || null;

      if (!agents.length) {
        workspace.innerHTML = '<p class="text-muted" style="padding:2rem;text-align:center">This squad has no agents.</p>';
        return;
      }

      const results = await Promise.allSettled(
        agents.map(async a => {
          const { prompt } = await Api.getAgentPrompt(a.id);
          return { id: a.id, prompt };
        })
      );
      for (const r of results) {
        if (r.status === 'fulfilled' && r.value.prompt != null) {
          prompts.set(r.value.id, r.value.prompt);
          baselines.set(r.value.id, r.value.prompt);
        }
      }

      loadRunErrors(squadId);
      renderQueryList();
      renderPanes();
    } catch (err) {
      workspace.innerHTML = `<p class="text-danger" style="padding:2rem;text-align:center">${esc(err.message)}</p>`;
      showError(err);
    }
  }

  // --- Mount editor in a pane ---
  async function mountEditor(side) {
    const pane = workspace.querySelector(`[data-pane="${side}"]`);
    if (!pane) return;
    const agentId = paneState[side];
    const agent = agents.find(a => a.id === agentId);
    if (!agent) return;

    const infoEl = pane.querySelector('[data-role="pane-info"]');
    const editorEl = pane.querySelector('[data-role="pane-editor"]');
    const isLead = agent.id === leadId;
    const lang = agent.path?.endsWith('.yaml') || agent.path?.endsWith('.yml') ? 'yaml' : 'markdown';
    const langLabel = lang === 'yaml' ? 'yaml' : 'md';
    const failed = failedAgents.has(agentId);

    infoEl.innerHTML = `
      <div class="sd-pane-chips">
        <span class="badge${isLead ? ' badge-accent' : ''}">${isLead ? 'lead · synthesis' : `step ${agents.indexOf(agent) + 1}`}</span>
        <span class="badge" style="opacity:0.6">${esc(langLabel)}</span>
        ${failed ? '<span class="badge badge-danger">error in last run</span>' : ''}
        <span class="text-muted small">${(agent.tools || []).length} tools</span>
      </div>`;

    const oldEd = editors.get(side);
    if (oldEd) { oldEd.dispose(); editors.delete(side); }

    const text = prompts.get(agentId);
    if (text == null) {
      editorEl.innerHTML = '<p class="text-danger small" style="padding:0.5rem">Could not load prompt.</p>';
      return;
    }

    let monaco;
    try { monaco = await loadMonaco(); } catch { monaco = null; }

    if (monaco) {
      editorEl.innerHTML = '';
      editorEl.classList.add('sd-editor-host');
      const ed = monaco.editor.create(editorEl, {
        value: text, language: lang, theme: 'vs-dark', automaticLayout: true, fontSize,
        fontFamily: "'Cascadia Code', 'Fira Code', Consolas, monospace",
        lineNumbers: 'on', minimap: { enabled: false }, scrollBeyondLastLine: false,
        wordWrap: wordWrap ? 'on' : 'off', wrappingIndent: 'indent',
        padding: { top: 8, bottom: 8 }, renderLineHighlight: 'line',
        folding: true, glyphMargin: false, lineDecorationsWidth: 4, lineNumbersMinChars: 3,
        ariaLabel: `Prompt for ${agentId} (${lang})`,
      });
      editors.set(side, ed);

      if (lang === 'markdown') {
        ed.onDidPaste(() => {
          const model = ed.getModel();
          if (!model) return;
          const full = model.getValue();
          const converted = formatPastedMarkdown(full);
          if (converted !== full) model.setValue(converted);
        });
      }

      ed.onDidChangeModelContent(() => {
        const current = ed.getValue();
        prompts.set(agentId, current);
        if (current !== baselines.get(agentId)) dirty.set(agentId, true);
        else dirty.delete(agentId);
        const dot = pane.querySelector(`[data-dirty="${side}"]`);
        if (dot) dot.hidden = !dirty.has(agentId);
        updateSaveAll();
      });

      const dot = pane.querySelector(`[data-dirty="${side}"]`);
      if (dot) dot.hidden = !dirty.has(agentId);
    } else {
      editorEl.innerHTML = `<textarea class="sd-editor-textarea" aria-label="Prompt for ${esc(agentId)}">${esc(text)}</textarea>`;
      const ta = editorEl.querySelector('textarea');
      ta.addEventListener('paste', e => {
        if (lang !== 'markdown') return;
        const clip = e.clipboardData?.getData('text/plain');
        if (!clip) return;
        const converted = formatPastedMarkdown(clip);
        if (converted !== clip) {
          e.preventDefault();
          const start = ta.selectionStart, end = ta.selectionEnd;
          ta.value = ta.value.slice(0, start) + converted + ta.value.slice(end);
          ta.selectionStart = ta.selectionEnd = start + converted.length;
          ta.dispatchEvent(new Event('input'));
        }
      });
      ta.addEventListener('input', () => {
        prompts.set(agentId, ta.value);
        if (ta.value !== baselines.get(agentId)) dirty.set(agentId, true);
        else dirty.delete(agentId);
        const dot = pane.querySelector(`[data-dirty="${side}"]`);
        if (dot) dot.hidden = !dirty.has(agentId);
        updateSaveAll();
      });
    }
  }

  function updatePaneLabel(side) {
    const label = workspace.querySelector(`[data-pane="${side}"] [data-role="pane-label"]`);
    if (!label) return;
    const agent = agents.find(a => a.id === paneState[side]);
    label.textContent = agent ? agentLabel(agent) : '';
  }

  // --- Two-pane editor layout ---
  function renderPanes() {
    const leftDefault = agents[0]?.id || '';
    const rightDefault = agents.length > 1 ? agents[1].id : agents[0]?.id || '';

    workspace.innerHTML = `
      <div class="sd-panes">
        <section class="sd-pane" data-pane="left">
          <header class="sd-pane-header">
            <button class="btn btn-sm sd-pane-nav" data-action="pane-prev" data-pane="left" title="Previous agent">◀</button>
            <span class="sd-pane-label" data-role="pane-label"></span>
            <button class="btn btn-sm sd-pane-nav" data-action="pane-next" data-pane="left" title="Next agent">▶</button>
            <span class="sd-dirty-dot" data-dirty="left" hidden title="Unsaved changes"></span>
            <button class="btn btn-sm" data-action="save-pane" data-pane="left">Save</button>
          </header>
          <div class="sd-pane-info" data-role="pane-info"></div>
          <div class="sd-pane-body" data-role="pane-editor"></div>
        </section>
        <div class="sd-pane-divider" title="Drag to resize"></div>
        <section class="sd-pane" data-pane="right">
          <header class="sd-pane-header">
            <button class="btn btn-sm sd-pane-nav" data-action="pane-prev" data-pane="right" title="Previous agent">◀</button>
            <span class="sd-pane-label" data-role="pane-label"></span>
            <button class="btn btn-sm sd-pane-nav" data-action="pane-next" data-pane="right" title="Next agent">▶</button>
            <span class="sd-dirty-dot" data-dirty="right" hidden title="Unsaved changes"></span>
            <button class="btn btn-sm" data-action="save-pane" data-pane="right">Save</button>
          </header>
          <div class="sd-pane-info" data-role="pane-info"></div>
          <div class="sd-pane-body" data-role="pane-editor"></div>
        </section>
      </div>`;

    setupColumnResize(workspace);
    paneState.left = leftDefault;
    paneState.right = rightDefault;
    updatePaneLabel('left');
    updatePaneLabel('right');
    mountEditor('left');
    mountEditor('right');
    renderFlowPanel();

    workspace.addEventListener('click', e => {
      const btn = e.target.closest('[data-action="save-pane"]');
      if (!btn) return;
      const side = btn.dataset.pane;
      const agentId = paneState[side];
      if (agentId) saveAgent(agentId, side);
    });
  }

  // --- Run errors ---
  function showRunResult(run) {
    failedAgents.clear();
    outputEmpty.hidden = true;
    if (!run || !run.execution_trace) {
      runErrorsHost.classList.add('sd-hidden');
      outputStatus.textContent = '';
      outputEmpty.hidden = false;
      return;
    }
    const errors = (run.execution_trace || []).filter(s => s.status === 'failure' || s.status === 'error');
    for (const step of errors) {
      if (step.agent_id) failedAgents.add(step.agent_id);
    }

    if (!errors.length && run.status !== 'error') {
      runErrorsHost.classList.add('sd-hidden');
      errorsBanner.innerHTML = '';
      runErrorsBody.innerHTML = '';
      outputStatus.textContent = 'Last run completed successfully.';
      outputStatus.className = 'sd-sb-output-status text-success small';
      return;
    }
    outputStatus.textContent = '';
    runErrorsHost.classList.remove('sd-hidden');
    const firstError = errors[0];
    const summary = run.status === 'error'
      ? run.final_answer || 'Run failed'
      : `${firstError?.agent_id || 'unknown'}: ${(firstError?.result || '').slice(0, 200)}`;
    errorsBanner.innerHTML = `<span class="sd-errors-icon">Run failed</span> <span class="sd-errors-text">${esc(summary)}</span>`;

    let html = '';
    if (run.status === 'error' && run.final_answer) {
      html += `<div class="sd-error-item"><strong>Final answer</strong><pre class="sq-pre">${esc(run.final_answer)}</pre></div>`;
    }
    for (const step of run.execution_trace || []) {
      const isFail = step.status === 'failure' || step.status === 'error';
      html += `<div class="sd-error-item${isFail ? ' sd-error-fail' : ''}">
        <strong>${esc(step.agent_id || 'unknown')}</strong>
        <span class="badge${isFail ? ' badge-danger' : ''}">${esc(step.status)}</span>
        <span class="text-muted small">step ${step.step_number || '?'}</span>
        <pre class="sq-pre">${esc(step.result || '')}</pre>
      </div>`;
    }
    runErrorsBody.innerHTML = html;
    refreshPaneSelectors();
  }

  async function loadRunErrors(squadId) {
    try {
      const data = await Api.getLatestOrchestrationRun(squadId);
      showRunResult(data?.result);
    } catch {
      runErrorsHost.classList.add('sd-hidden');
    }
  }

  function refreshPaneSelectors() {
    updatePaneLabel('left');
    updatePaneLabel('right');
    renderFlowPanel();
    workspace.querySelectorAll('[data-role="pane-info"]').forEach(info => {
      const pane = info.closest('[data-pane]');
      const select = pane?.querySelector('[data-role="agent-select"]');
      const agentId = select?.value;
      if (!agentId) return;
      const badge = info.querySelector('.badge-danger');
      if (failedAgents.has(agentId) && !badge) {
        const chips = info.querySelector('.sd-pane-chips');
        if (chips) chips.insertAdjacentHTML('beforeend', '<span class="badge badge-danger">error in last run</span>');
      } else if (!failedAgents.has(agentId) && badge) {
        badge.remove();
      }
    });
  }

  // --- Run squad ---
  async function runSquad() {
    if (!currentSquadId) return;
    const query = runQuery.value.trim();
    if (!query) { showError(new Error('Enter a task description to run.')); return; }
    clearError();
    runStatus.textContent = 'Starting…';
    const runBtn = container.querySelector('[data-action="run-squad"]');
    runBtn.disabled = true;

    try {
      const started = await Api.startOrchestrationRun(currentSquadId, query);
      runStatus.textContent = `Running (${started.trace_id.slice(0, 12)}…)`;
      eventSource?.close();
      eventSource = new EventSource(`/api/orchestration/stream/${encodeURIComponent(started.trace_id)}`);

      eventSource.addEventListener('telemetry', async msg => {
        if (!container.isConnected) { eventSource.close(); return; }
        const event = JSON.parse(msg.data);
        if (event.event_type !== 'RUN_COMPLETE') {
          runStatus.textContent = `${event.sender || ''}: ${event.event_type || ''}`.trim();
          return;
        }
        eventSource.close(); eventSource = null;
        try {
          const result = await Api.getOrchestrationRunResult(started.trace_id);
          runStatus.textContent = `Run ${result.status}`;
          if (result.status === 'error') runStatus.classList.add('text-danger');
          else runStatus.classList.remove('text-danger');
          showRunResult(result);
        } catch (err) { showError(err); }
        runBtn.disabled = false;
      });

      eventSource.onerror = () => {
        if (eventSource?.readyState === EventSource.CLOSED) {
          runStatus.textContent = 'Stream disconnected; run may still be active.';
          runBtn.disabled = false;
        }
      };
    } catch (err) {
      showError(err);
      runStatus.textContent = '';
      runBtn.disabled = false;
    }
  }

  // --- Save ---
  async function saveAgent(agentId, side) {
    const prompt = prompts.get(agentId);
    if (prompt == null) return;
    const pane = side ? workspace.querySelector(`[data-pane="${side}"]`) : null;
    const btn = pane?.querySelector('[data-action="save-pane"]');
    if (btn) btn.disabled = true;
    try {
      await Api.setAgentPrompt(agentId, prompt);
      baselines.set(agentId, prompt);
      dirty.delete(agentId);
      if (pane) {
        const dot = pane.querySelector(`[data-dirty="${side}"]`);
        if (dot) dot.hidden = true;
      }
      updateSaveAll();
      clearError();
    } catch (err) { showError(err); }
    finally { if (btn) btn.disabled = false; }
  }

  async function saveAll() {
    const ids = [...dirty.keys()];
    if (!ids.length) return;
    for (const id of ids) await saveAgent(id);
    updateSaveAll();
  }

  // --- Column resize ---
  function setupColumnResize(host) {
    let target = null, startX = 0, startW = 0;
    host.addEventListener('mousedown', e => {
      if (!e.target.classList.contains('sd-pane-divider')) return;
      target = e.target.previousElementSibling;
      if (!target) return;
      startX = e.clientX;
      startW = target.getBoundingClientRect().width;
      host.classList.add('sd-resizing');
      e.preventDefault();
    });
    document.addEventListener('mousemove', e => {
      if (!target) return;
      target.style.flex = 'none';
      target.style.width = `${Math.max(280, startW + (e.clientX - startX))}px`;
    });
    document.addEventListener('mouseup', () => {
      if (!target) return;
      target = null;
      host.classList.remove('sd-resizing');
    });
  }

  // --- Agent flow panel ---
  function renderFlowPanel() {
    if (!agents.length) {
      flowList.innerHTML = '<p class="text-muted small" style="padding:0.5rem">No agents loaded.</p>';
      flowFooter.classList.add('sd-hidden');
      return;
    }
    flowFooter.classList.toggle('sd-hidden', !orderChanged);
    const lastMemberIdx = leadId ? agents.length - 2 : agents.length - 1;
    let html = '';
    agents.forEach((a, i) => {
      const isLead = a.id === leadId;
      const isFailed = failedAgents.has(a.id);
      const isLeft = paneState.left === a.id;
      const isRight = paneState.right === a.id;
      const cls = ['sd-flow-node',
        isLead ? 'sd-flow-lead' : '',
        isFailed ? 'sd-flow-error' : '',
        isLeft ? 'sd-flow-active-l' : '',
        isRight ? 'sd-flow-active-r' : '',
      ].filter(Boolean).join(' ');
      html += `<div class="${cls}" data-agent="${esc(a.id)}" data-action="flow-select">
        <div class="sd-flow-step">${isLead ? '★' : i + 1}</div>
        <div class="sd-flow-info">
          <span class="sd-flow-name">${esc(a.name || a.id)}</span>
          <span class="sd-flow-role">${isLead ? 'lead · synthesis' : `member · step ${i + 1}`}</span>
        </div>
        ${!isLead ? `<div class="sd-flow-actions">
          <button data-action="flow-up" data-agent="${esc(a.id)}" title="Move up" ${i === 0 ? 'disabled' : ''}>↑</button>
          <button data-action="flow-down" data-agent="${esc(a.id)}" title="Move down" ${i >= lastMemberIdx ? 'disabled' : ''}>↓</button>
        </div>` : ''}
      </div>`;
      if (i < agents.length - 1) html += '<div class="sd-flow-arrow">↓</div>';
    });
    flowList.innerHTML = html;
  }

  function selectAgent(agentId, side) {
    if (paneState[side] === agentId) return;
    paneState[side] = agentId;
    updatePaneLabel(side);
    mountEditor(side);
    renderFlowPanel();
  }

  function navigatePane(side, direction) {
    const currentId = paneState[side];
    const idx = agents.findIndex(a => a.id === currentId);
    const newIdx = idx + direction;
    if (newIdx < 0 || newIdx >= agents.length) return;
    selectAgent(agents[newIdx].id, side);
  }

  function moveAgent(agentId, direction) {
    const idx = agents.findIndex(a => a.id === agentId);
    if (idx < 0 || agents[idx].id === leadId) return;
    const newIdx = idx + direction;
    const lastMemberIdx = leadId ? agents.length - 2 : agents.length - 1;
    if (newIdx < 0 || newIdx > lastMemberIdx) return;
    [agents[idx], agents[newIdx]] = [agents[newIdx], agents[idx]];
    orderChanged = true;
    renderFlowPanel();
    updatePaneLabel('left');
    updatePaneLabel('right');
  }

  async function saveOrder() {
    if (!currentSquadId || !orderChanged) return;
    try {
      const memberIds = agents.filter(a => a.id !== leadId).map(a => a.id);
      await Api.saveSquadBlueprint({ squad_id: currentSquadId, primary_agent: leadId, subagents: memberIds });
      orderChanged = false;
      renderFlowPanel();
      clearError();
      showError({ message: 'Agent order saved.' });
    } catch (err) { showError(err); }
  }

  function setupNavResize() {
    let dragging = false, startX = 0, startW = 0;
    navDivider.addEventListener('mousedown', e => {
      dragging = true;
      startX = e.clientX;
      startW = navPanel.getBoundingClientRect().width;
      document.body.classList.add('sd-resizing');
      e.preventDefault();
    });
    document.addEventListener('mousemove', e => {
      if (!dragging) return;
      const w = Math.max(140, Math.min(400, startW + (e.clientX - startX)));
      navPanel.style.width = `${w}px`;
      navPanel.style.minWidth = `${w}px`;
      navPanel.style.maxWidth = `${w}px`;
    });
    document.addEventListener('mouseup', () => {
      if (!dragging) return;
      dragging = false;
      document.body.classList.remove('sd-resizing');
    });
  }
  setupNavResize();

  // --- Sidebar: test queries ---
  function renderQueryList() {
    if (!currentSquadId) { sbQueryList.innerHTML = ''; sbQueriesEmpty.hidden = false; return; }
    const queries = loadSavedQueries(currentSquadId);
    sbQueriesEmpty.hidden = queries.length > 0;
    sbQueryList.innerHTML = queries.map((q, i) =>
      `<li class="sd-sb-query-item">
        <button class="sd-sb-query-text" data-action="use-query" data-idx="${i}" title="Click to load">${esc(q)}</button>
        <button class="btn btn-sm sd-sb-query-del" data-action="del-query" data-idx="${i}" title="Delete">x</button>
      </li>`
    ).join('');
  }

  function addQuery() {
    if (!currentSquadId) return;
    const text = runQuery.value.trim();
    if (!text) return;
    const queries = loadSavedQueries(currentSquadId);
    if (queries.includes(text)) return;
    queries.unshift(text);
    saveSavedQueries(currentSquadId, queries);
    renderQueryList();
  }

  function useQuery(idx) {
    if (!currentSquadId) return;
    const queries = loadSavedQueries(currentSquadId);
    if (queries[idx] != null) runQuery.value = queries[idx];
  }

  function deleteQuery(idx) {
    if (!currentSquadId) return;
    const queries = loadSavedQueries(currentSquadId);
    queries.splice(idx, 1);
    saveSavedQueries(currentSquadId, queries);
    renderQueryList();
  }

  // --- Sidebar: features ---
  function toggleWordWrap() {
    wordWrap = !wordWrap;
    wrapBtn.textContent = `Word Wrap: ${wordWrap ? 'On' : 'Off'}`;
    for (const ed of editors.values()) ed.updateOptions({ wordWrap: wordWrap ? 'on' : 'off' });
  }

  function resetChanges() {
    if (!dirty.size) return;
    if (!confirm('Revert all unsaved changes to last saved state?')) return;
    for (const [agentId] of dirty) {
      const baseline = baselines.get(agentId);
      if (baseline != null) prompts.set(agentId, baseline);
    }
    dirty.clear();
    updateSaveAll();
    if (currentSquadId) renderPanes();
  }

  async function validatePrompts() {
    if (!currentSquadId) return;
    clearError();
    try {
      const res = await fetch(`/api/squads-architect/squads/${encodeURIComponent(currentSquadId)}/validate`, { method: 'POST' });
      const data = await res.json();
      if (!res.ok) throw new Error(data?.detail || 'Validation failed');
      showError({ message: data?.valid ? 'All prompts valid.' : (data?.errors?.join('; ') || 'Validation issues found.') });
    } catch (err) { showError(err); }
  }

  function toggleSidebar() {
    sidebarOpen = !sidebarOpen;
    sidebar.classList.toggle('sd-hidden', !sidebarOpen);
    sidebarDivider.classList.toggle('sd-hidden', !sidebarOpen);
  }

  // --- Sidebar resize ---
  function setupSidebarResize() {
    let dragging = false, startX = 0, startW = 0;
    sidebarDivider.addEventListener('mousedown', e => {
      dragging = true;
      startX = e.clientX;
      startW = sidebar.getBoundingClientRect().width;
      document.body.classList.add('sd-resizing');
      e.preventDefault();
    });
    document.addEventListener('mousemove', e => {
      if (!dragging) return;
      const w = Math.max(180, Math.min(600, startW - (e.clientX - startX)));
      sidebar.style.flex = `0 0 ${w}px`;
    });
    document.addEventListener('mouseup', () => {
      if (!dragging) return;
      dragging = false;
      document.body.classList.remove('sd-resizing');
    });
  }
  setupSidebarResize();

  // --- Event handlers (delegated to survive hidden/dynamic elements) ---
  container.addEventListener('click', e => {
    const el = e.target.closest('[data-action]');
    if (!el) return;
    const action = el.dataset.action;
    if (action === 'chat') navigate('prompt-creation');
    else if (action === 'prompt-starter') starter.open();
    else if (action === 'font-down') setFontSize(fontSize - FONT_STEP);
    else if (action === 'font-up') setFontSize(fontSize + FONT_STEP);
    else if (action === 'run-squad') runSquad();
    else if (action === 'save-all-sb') saveAll();
    else if (action === 'flow-select') { const aid = el.dataset.agent; if (aid) selectAgent(aid, (e.shiftKey || e.ctrlKey) ? 'right' : 'left'); }
    else if (action === 'flow-up') { const aid = el.dataset.agent; if (aid) moveAgent(aid, -1); }
    else if (action === 'flow-down') { const aid = el.dataset.agent; if (aid) moveAgent(aid, 1); }
    else if (action === 'save-order') saveOrder();
    else if (action === 'pane-prev') navigatePane(el.dataset.pane, -1);
    else if (action === 'pane-next') navigatePane(el.dataset.pane, 1);
    else if (action === 'toggle-wrap') toggleWordWrap();
    else if (action === 'reset-changes') resetChanges();
    else if (action === 'validate-prompts') validatePrompts();
    else if (action === 'toggle-sidebar') toggleSidebar();
    else if (action === 'add-query') addQuery();
    else if (action === 'use-query') useQuery(+el.dataset.idx);
    else if (action === 'del-query') deleteQuery(+el.dataset.idx);
  });
  runQuery.addEventListener('keydown', e => { if (e.key === 'Enter' && e.ctrlKey) { e.preventDefault(); runSquad(); } });

  loadSquads();
}
