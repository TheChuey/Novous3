import { Api } from './api.js';

const CATEGORY_LABELS = { squads: 'Squads', tool_usage: 'Tool usage', prompts: 'Prompts' };
let openSuiteDialog = null;
const DRAFT_KEY = 'novous.suiteDraft';
let suiteExecutionActive = false;

function parseSteps(content) {
  return String(content ?? '')
    .split(/\r?\n/)
    .map(line => line.trim())
    .filter(Boolean)
    .map((line, index) => {
      const checkbox = line.match(/^(?:[-*+]\s*)?\[([ xX])\]\s*(.*)$/);
      const plainListItem = line.match(/^(?:\d+[.)]\s+|[-*+]\s+)(.*)$/);
      return {
        id: index + 1,
        enabled: checkbox ? checkbox[1].toLowerCase() === 'x' : true,
        text: (checkbox ? checkbox[2] : plainListItem ? plainListItem[1] : line).trim(),
        expect: null
      };
    })
    .filter(step => step.text);
}

function formatRunTime(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value ?? '') : date.toLocaleString();
}

function evaluateChecks(expect, reply, toolEvents) {
  const checks = [];
  if (expect?.contains) {
    checks.push({
      type: 'contains',
      expected: expect.contains,
      passed: String(reply).toLowerCase().includes(expect.contains.toLowerCase())
    });
  }
  if (expect?.tool) {
    checks.push({
      type: 'tool',
      expected: expect.tool,
      passed: toolEvents.some(tool => tool.tool === expect.tool && tool.status === 'success')
    });
  }
  return checks;
}

function formatChecks(checks) {
  return checks.map(check =>
    `${check.passed ? '✓' : '✗'} ${check.type === 'tool' ? 'tool called' : 'reply contains'} "${check.expected}"`
  ).join('\n');
}

function compactToolEvent(tool) {
  return {
    tool: tool.tool || 'Tool',
    args: tool.args || {},
    status: tool.status || 'unknown',
    error: tool.error || null,
    result: String(tool.result || '').slice(0, 2000)
  };
}

function defaultSuiteName(firstStepText) {
  const text = String(firstStepText || '').replace(/\s+/g, ' ').trim();
  return text.length > 60 ? `${text.slice(0, 57)}…` : text || 'Untitled suite';
}

function ensureOption(select, value, label) {
  if (!value) return;
  if (![...select.options].some(option => option.value === value)) {
    const option = document.createElement('option');
    option.value = value;
    option.textContent = label || value;
    select.appendChild(option);
  }
  select.value = value;
}

export function openTestSuiteRunner({
  filePath,
  agentId,
  model,
  agents,
  models,
  onRunningChange = () => {},
  onChatMessage = () => {},
  onToolEvent = () => {},
  popout = false
}) {
  if (openSuiteDialog) {
    openSuiteDialog.focus();
    return;
  }

  // In the pop-out window, messages also go to the main window's chat over a BroadcastChannel.
  const suiteChannel = popout && 'BroadcastChannel' in window ? new BroadcastChannel('novous-suite-runner') : null;
  const emitChat = (role, text) => {
    onChatMessage(role, text);
    suiteChannel?.postMessage({ type: 'chat', role, text });
  };
  const emitTool = tool => {
    onToolEvent(tool);
    suiteChannel?.postMessage({ type: 'tool', event: tool });
  };

  const overlay = document.createElement('div');
  // A floating window: it does not block the page, so the chat stays visible and usable behind it.
  overlay.className = `suite-dialog-overlay suite-dialog-floating${popout ? ' suite-dialog-popout' : ''}`;
  overlay.innerHTML = `
    <section class="card suite-dialog" role="dialog" aria-labelledby="suite-dialog-title" tabindex="-1">
      <header class="suite-dialog-header" data-suite-drag title="Drag to move">
        <div>
          <h2 id="suite-dialog-title">Test Suite Runner</h2>
          <p class="text-muted small" data-suite-source></p>
        </div>
        <div class="suite-window-controls">
          <button class="btn btn-sm btn-ghost" type="button" data-suite-popout
            aria-label="Pop out to a separate window" title="Pop out to a separate window">⧉</button>
          <button class="btn btn-sm btn-ghost" type="button" data-suite-hide
            aria-label="Hide window" title="Hide window">–</button>
          <button class="btn btn-sm btn-ghost" type="button" data-suite-expand
            aria-label="Expand window" title="Expand window">□</button>
          <button class="btn btn-sm btn-ghost" type="button" data-suite-close
            aria-label="Close Test Suite Runner" title="Close">×</button>
        </div>
      </header>
      <div class="suite-window-body">
      <div class="suite-size-controls" aria-label="Window size">
        <label>Width
          <input type="range" data-suite-width min="320" max="1200" step="10">
          <output data-suite-width-value></output>
        </label>
        <label>Height
          <input type="range" data-suite-height min="360" max="1000" step="10">
          <output data-suite-height-value></output>
        </label>
        <button class="btn btn-sm btn-ghost" type="button" data-suite-size-reset>Reset size</button>
      </div>
      <div class="suite-library">
        <label>Saved suites
          <select class="form-select" data-suite-saved></select>
        </label>
        <button class="btn btn-sm" type="button" data-suite-load>Load</button>
        <button class="btn btn-sm btn-ghost" type="button" data-suite-delete>Delete</button>
      </div>
      <div class="suite-dialog-config">
        <label>Suite name
          <input class="form-input" type="text" maxlength="120" data-suite-name
            placeholder="e.g. RAG table of contents">
        </label>
        <label>Category
          <select class="form-select" data-suite-category>
            <option value="squads">Squads</option>
            <option value="tool_usage">Tool usage</option>
            <option value="prompts">Prompts</option>
          </select>
        </label>
      </div>
      <div class="suite-dialog-config">
        <label>Agent
          <select class="form-select" data-suite-agent></select>
        </label>
        <label>Model
          <select class="form-select" data-suite-model></select>
        </label>
      </div>
      <div class="suite-dialog-steps-heading">
        <h3>Test steps</h3>
        <button class="btn btn-sm" type="button" data-suite-add>+ Add Step</button>
      </div>
      <ol class="suite-step-list" data-suite-steps aria-label="Test steps"></ol>
      <section class="suite-output-section" aria-label="Test execution output">
        <div class="suite-output-heading">
          <h3>Execution output</h3>
          <span class="text-muted small" data-suite-progress>Ready</span>
        </div>
        <p class="text-muted small suite-evaluation-note">
          Steps with an expected text or tool are checked. Other steps only show the agent's response as evidence.
        </p>
        <div class="suite-output" data-suite-output role="log" aria-live="polite"></div>
      </section>
      <section class="suite-history" aria-label="Run history">
        <div class="suite-output-heading">
          <h3>Run history</h3>
          <span class="text-muted small" data-suite-history-note></span>
        </div>
        <ul data-suite-history></ul>
      </section>
      <footer class="suite-dialog-footer">
        <button class="btn btn-primary" type="button" data-suite-run>Run Test Suite</button>
        <div class="suite-dialog-footer-secondary">
          <button class="btn btn-sm" type="button" data-suite-stop disabled>Stop</button>
          <button class="btn btn-sm" type="button" data-suite-save>Save Suite</button>
          <button class="btn btn-sm" type="button" data-suite-close>Close</button>
        </div>
      </footer>
      </div>
    </section>`;

  const dialog = overlay.querySelector('.suite-dialog');
  if (popout) dialog.classList.add('is-popout');
  const sourceLabel = overlay.querySelector('[data-suite-source]');
  const savedSelect = overlay.querySelector('[data-suite-saved]');
  const loadButton = overlay.querySelector('[data-suite-load]');
  const deleteButton = overlay.querySelector('[data-suite-delete]');
  const nameInput = overlay.querySelector('[data-suite-name]');
  const categorySelect = overlay.querySelector('[data-suite-category]');
  const agentSelect = overlay.querySelector('[data-suite-agent]');
  const modelSelect = overlay.querySelector('[data-suite-model]');
  const stepList = overlay.querySelector('[data-suite-steps]');
  const output = overlay.querySelector('[data-suite-output]');
  const progress = overlay.querySelector('[data-suite-progress]');
  const runButton = overlay.querySelector('[data-suite-run]');
  const stopButton = overlay.querySelector('[data-suite-stop]');
  const saveButton = overlay.querySelector('[data-suite-save]');
  const addButton = overlay.querySelector('[data-suite-add]');
  const historyList = overlay.querySelector('[data-suite-history]');
  const historyNote = overlay.querySelector('[data-suite-history-note]');
  let steps = [];
  let nextId = 1;
  let running = false;
  let loadingSource = Boolean(filePath);
  let stopRequested = false;
  let dirty = false;
  let closed = false;
  let closeAfterRun = false;
  let currentSuite = null; // { category, slug } once the suite is saved
  let savedSuites = [];

  sourceLabel.textContent = filePath
    ? `Imported from To-Do file: ${filePath}`
    : 'Saved suites are stored in test_environment/data/saved_suites.';
  agentSelect.replaceChildren(...agents.map(agent => {
    const option = document.createElement('option');
    option.value = agent.id;
    option.dataset.environment = agent.environment || 'workspace';
    option.textContent = `${agent.name} (${agent.mode}) · ${agent.environment || 'workspace'}`;
    option.selected = agent.id === agentId;
    return option;
  }));
  modelSelect.replaceChildren(...models.map(name => {
    const option = document.createElement('option');
    option.value = name;
    option.textContent = name;
    option.selected = name === model;
    return option;
  }));
  if (model) ensureOption(modelSelect, model, model);

  function setStatus(message, type = '') {
    progress.textContent = message;
    progress.className = `small${type ? ` text-${type}` : ' text-muted'}`;
  }

  function updateControls() {
    const canEdit = !running && !loadingSource;
    stepList.querySelectorAll('input, button').forEach(control => {
      control.disabled = !canEdit || control.dataset.unavailable === 'true';
    });
    addButton.disabled = !canEdit;
    agentSelect.disabled = !canEdit;
    modelSelect.disabled = !canEdit || !modelSelect.options.length;
    nameInput.disabled = !canEdit;
    categorySelect.disabled = !canEdit;
    savedSelect.disabled = !canEdit;
    loadButton.disabled = !canEdit || !savedSelect.value;
    deleteButton.disabled = !canEdit || !savedSelect.value;
    runButton.disabled = running || suiteExecutionActive || loadingSource ||
      !agentSelect.value || !modelSelect.value ||
      !steps.some(step => step.enabled && step.text.trim());
    stopButton.disabled = !running || stopRequested;
    // A name is optional: saving without one uses the first step's text.
    saveButton.disabled = running || loadingSource || !steps.some(step => step.text.trim());
  }

  function expectInput(step, index, key, placeholder) {
    const input = document.createElement('input');
    input.type = 'text';
    input.className = 'form-input';
    input.value = step.expect?.[key] || '';
    input.placeholder = placeholder;
    input.setAttribute('aria-label', `Step ${index + 1} ${placeholder.toLowerCase()}`);
    input.addEventListener('input', () => {
      step.expect = { ...(step.expect || {}), [key]: input.value };
      dirty = true;
      updateControls();
    });
    return input;
  }

  function renderSteps(focusId = null) {
    stepList.replaceChildren();
    steps.forEach((step, index) => {
      const item = document.createElement('li');
      item.className = 'suite-step';
      item.dataset.stepId = String(step.id);

      const enabled = document.createElement('input');
      enabled.type = 'checkbox';
      enabled.checked = step.enabled;
      enabled.setAttribute('aria-label', `Enable step ${index + 1}`);
      enabled.addEventListener('change', () => {
        step.enabled = enabled.checked;
        dirty = true;
        updateControls();
      });

      const instruction = document.createElement('input');
      instruction.type = 'text';
      instruction.className = 'form-input';
      instruction.value = step.text;
      instruction.placeholder = `Step ${index + 1} instruction`;
      instruction.setAttribute('aria-label', `Step ${index + 1} instruction`);
      instruction.addEventListener('input', () => {
        step.text = instruction.value;
        dirty = true;
        updateControls();
      });

      const actions = document.createElement('div');
      actions.className = 'suite-step-actions';
      [
        { action: 'up', label: 'Move up', icon: '↑', disabled: index === 0 },
        { action: 'down', label: 'Move down', icon: '↓', disabled: index === steps.length - 1 },
        { action: 'remove', label: 'Remove step', icon: '×', disabled: false }
      ].forEach(control => {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'btn btn-sm btn-ghost suite-step-action';
        button.textContent = control.icon;
        button.title = control.label;
        button.setAttribute('aria-label', control.label);
        button.disabled = control.disabled;
        button.dataset.unavailable = String(control.disabled);
        button.addEventListener('click', () => {
          if (control.action === 'remove') {
            steps = steps.filter(itemStep => itemStep.id !== step.id);
          } else {
            const nextIndex = control.action === 'up' ? index - 1 : index + 1;
            [steps[index], steps[nextIndex]] = [steps[nextIndex], steps[index]];
          }
          dirty = true;
          renderSteps();
        });
        actions.appendChild(button);
      });

      const expectRow = document.createElement('div');
      expectRow.className = 'suite-step-expect';
      expectRow.append(
        expectInput(step, index, 'contains', 'Expected text (optional)'),
        expectInput(step, index, 'tool', 'Expected tool (optional)')
      );

      item.append(enabled, instruction, actions, expectRow);
      stepList.appendChild(item);
    });
    updateControls();
    if (focusId != null) {
      stepList.querySelector(`[data-step-id="${focusId}"] input[type="text"]`)?.focus();
    }
  }

  function appendOutput(title, body, status = 'info') {
    const entry = document.createElement('article');
    entry.className = `suite-result suite-result-${status}`;
    const heading = document.createElement('div');
    heading.className = 'suite-result-heading';
    const label = document.createElement('strong');
    label.textContent = title;
    const badge = document.createElement('span');
    badge.className = `badge ${status === 'failed' ? 'badge-warning' : status === 'completed' ? 'badge-success' : 'badge-accent'}`;
    badge.textContent = status === 'info' ? 'RUNNING' : status.toUpperCase();
    heading.append(label, badge);
    const text = document.createElement('pre');
    text.textContent = body;
    entry.append(heading, text);
    output.appendChild(entry);
    output.scrollTop = output.scrollHeight;
    return { entry, text };
  }

  async function refreshSavedList(selectedValue = '') {
    try {
      const { suites } = await Api.listTestSuites();
      if (closed) return;
      savedSuites = suites;
      const groups = Object.entries(CATEGORY_LABELS).map(([category, label]) => {
        const members = suites.filter(suite => suite.category === category);
        if (!members.length) return null;
        const group = document.createElement('optgroup');
        group.label = label;
        members.forEach(suite => {
          const option = document.createElement('option');
          option.value = `${suite.category}/${suite.id}`;
          option.textContent = `${suite.name} (${suite.steps?.length || 0} steps)`;
          group.appendChild(option);
        });
        return group;
      }).filter(Boolean);
      savedSelect.replaceChildren(new Option('New suite', ''), ...groups);
      const exists = [...savedSelect.options].some(option => option.value === selectedValue);
      savedSelect.value = exists ? selectedValue : '';
    } catch (error) {
      if (!closed) setStatus(`Could not load saved suites: ${error.message}`, 'danger');
    } finally {
      if (!closed) updateControls();
    }
  }

  async function refreshHistory() {
    historyList.replaceChildren();
    if (!currentSuite) {
      historyNote.textContent = 'Save this suite to keep its run history.';
      return;
    }
    historyNote.textContent = 'Select a run to show its output.';
    try {
      const { runs } = await Api.getTestSuiteResults(currentSuite.category, currentSuite.slug);
      if (closed) return;
      if (!runs.length) {
        historyNote.textContent = 'No runs yet.';
        return;
      }
      runs.forEach(run => {
        const item = document.createElement('li');
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'btn btn-sm btn-ghost suite-history-run';
        button.textContent = `${formatRunTime(run.run_at)} · ${run.passed} passed · ${run.failed} failed · ${run.model}`;
        button.addEventListener('click', () => showRun(run));
        item.appendChild(button);
        historyList.appendChild(item);
      });
    } catch (error) {
      historyNote.textContent = `Could not load history: ${error.message}`;
    }
  }

  function showRun(run) {
    output.replaceChildren();
    appendOutput(
      `Run · ${formatRunTime(run.run_at)}`,
      `${run.passed} passed · ${run.failed} failed · Agent: ${run.agent_id} · Model: ${run.model} · ${run.duration_ms} ms`,
      run.failed ? 'failed' : 'completed'
    );
    run.steps.forEach((step, index) => {
      const checks = formatChecks(step.checks || []);
      const body = [step.reply || (step.status === 'request_failed' ? 'Request failed.' : ''), checks]
        .filter(Boolean).join('\n\n');
      appendOutput(
        `Step ${index + 1}: ${step.text}`,
        body || 'No response was recorded.',
        step.status === 'failed' || step.status === 'request_failed' ? 'failed' : 'completed'
      );
    });
  }

  function applySuite(suite) {
    steps = (suite.steps || []).map((step, index) => ({
      id: index + 1,
      enabled: step.enabled !== false,
      text: step.text || '',
      expect: step.expect || null
    }));
    nextId = steps.length + 1;
    nameInput.value = suite.name || '';
    categorySelect.value = suite.category;
    const agent = agents.find(item => item.id === suite.agent_id);
    ensureOption(agentSelect, suite.agent_id, agent ? `${agent.name} (${agent.mode})` : suite.agent_id);
    ensureOption(modelSelect, suite.model, suite.model);
    currentSuite = { category: suite.category, slug: suite.id };
    dirty = false;
    renderSteps();
    refreshHistory();
    setStatus(`Loaded "${suite.name}" · ${steps.length} step(s).`, 'success');
  }

  async function loadSavedSuite() {
    const suite = savedSuites.find(item => `${item.category}/${item.id}` === savedSelect.value);
    if (!suite) return;
    if (dirty && !confirm('Discard unsaved changes and load this suite?')) return;
    applySuite(suite);
    savedSelect.value = `${suite.category}/${suite.id}`;
  }

  async function deleteSavedSuite() {
    const suite = savedSuites.find(item => `${item.category}/${item.id}` === savedSelect.value);
    if (!suite) return;
    if (!confirm(`Delete saved suite "${suite.name}" and its run history? This cannot be undone.`)) return;
    try {
      await Api.deleteTestSuite(suite.category, suite.id);
      if (currentSuite?.category === suite.category && currentSuite?.slug === suite.id) {
        currentSuite = null;
        dirty = true;
        refreshHistory();
      }
      await refreshSavedList();
      setStatus(`Deleted "${suite.name}".`, 'success');
    } catch (error) {
      setStatus(`Could not delete suite: ${error.message}`, 'danger');
    }
  }

  async function saveSuite() {
    if (running) return;
    const cleanSteps = steps.filter(step => step.text.trim()).map(step => {
      const contains = step.expect?.contains?.trim() || '';
      const tool = step.expect?.tool?.trim() || '';
      return {
        text: step.text.trim(),
        enabled: step.enabled,
        expect: contains || tool ? { contains: contains || null, tool: tool || null } : null
      };
    });
    if (!cleanSteps.length) {
      setStatus('Add at least one step with text before saving.', 'danger');
      return;
    }
    const name = nameInput.value.trim() || defaultSuiteName(cleanSteps[0].text);
    nameInput.value = name;
    saveButton.disabled = true;
    setStatus('Saving suite…');
    try {
      const suite = await Api.saveTestSuite({
        name,
        category: categorySelect.value,
        description: '',
        agent_id: agentSelect.value,
        model: modelSelect.value,
        steps: cleanSteps
      });
      currentSuite = { category: suite.category, slug: suite.id };
      dirty = false;
      await refreshSavedList(`${suite.category}/${suite.id}`);
      await refreshHistory();
      setStatus(`Saved "${suite.name}" to ${suite.category}.`, 'success');
    } catch (error) {
      setStatus(`Could not save suite: ${error.message}`, 'danger');
    } finally {
      updateControls();
    }
  }

  async function runSuite() {
    if (running || suiteExecutionActive) return;
    const enabledSteps = steps.filter(step => step.enabled && step.text.trim());
    if (!enabledSteps.length) {
      setStatus('Enable or add at least one step.', 'danger');
      return;
    }
    if (!agentSelect.value || !modelSelect.value) {
      setStatus('Select an agent and installed model.', 'danger');
      return;
    }

    running = true;
    suiteExecutionActive = true;
    stopRequested = false;
    output.replaceChildren();
    onRunningChange(true);
    updateControls();
    const selectedAgent = agentSelect.value;
    const selectedEnvironment = agentSelect.selectedOptions[0]?.dataset.environment || 'workspace';
    const selectedModel = modelSelect.value;
    // One session per run: steps in this run share context, earlier runs do not leak into it.
    const sessionId = `suite-${Date.now()}`;
    const startedAt = Date.now();
    const runSteps = [];
    appendOutput(
      'Suite started',
      `${enabledSteps.length} enabled step(s) · Agent: ${selectedAgent} · Model: ${selectedModel}`,
      'info'
    );
    emitChat('user', `Starting execution of ${enabledSteps.length} test step(s).`);

    let completed = 0;
    let passed = 0;
    let failed = 0;
    let toolCallsSucceeded = 0;
    let toolCallsFailed = 0;
    try {
      for (let index = 0; index < enabledSteps.length; index += 1) {
        if (stopRequested) break;
        const step = enabledSteps[index];
        const stepStarted = Date.now();
        setStatus(`Step ${index + 1} of ${enabledSteps.length}`);
        const stepOutput = appendOutput(
          `Step ${index + 1}: ${step.text}`,
          'Waiting for agent response…',
          'info'
        );
        emitChat('user', `Task ${index + 1}: ${step.text}`);
        const stepToolEvents = [];
        let reply = '';
        const record = { text: step.text, status: 'request_failed', reply: '', duration_ms: 0, tool_events: [], checks: [] };
        try {
          const result = await Api.streamMessage(
            step.text,
            selectedAgent,
            selectedModel,
            sessionId,
            selectedEnvironment,
            async event => {
              if (event.type === 'token') {
                reply += event.text;
                stepOutput.text.textContent = reply;
                output.scrollTop = output.scrollHeight;
              } else if (event.type === 'tool_event') {
                const tool = event.event || {};
                stepToolEvents.push(tool);
                const succeeded = tool.status === 'success';
                const resultText = tool.error || tool.result || 'No result was returned.';
                const details = [
                  `Status: ${tool.status || 'unknown'}`,
                  tool.args ? `Arguments: ${JSON.stringify(tool.args)}` : '',
                  `Result: ${resultText}`,
                  tool.result_truncated ? '(Result preview truncated.)' : ''
                ].filter(Boolean).join('\n');
                appendOutput(
                  `Step ${index + 1} · ${tool.tool || 'Tool'}`,
                  details,
                  succeeded ? 'completed' : 'failed'
                );
                if (succeeded) toolCallsSucceeded += 1;
                else toolCallsFailed += 1;
                emitTool(tool);
              }
            }
          );
          reply = result.reply || reply;
          if (!reply.trim()) throw new Error('The agent returned an empty response.');
          emitChat('assistant', reply);

          const checks = evaluateChecks(step.expect, reply, stepToolEvents);
          const checksFailed = checks.some(check => !check.passed);
          const badge = stepOutput.entry.querySelector('.suite-result-heading .badge');
          if (checks.length) {
            badge.textContent = checksFailed ? 'CHECK FAILED' : 'CHECK PASSED';
            badge.className = `badge ${checksFailed ? 'badge-warning' : 'badge-success'}`;
            record.status = checksFailed ? 'failed' : 'passed';
          } else {
            badge.textContent = stepToolEvents.length
              ? stepToolEvents.some(tool => tool.status !== 'success') ? 'RESPONSE · TOOL ERROR' : 'RESPONSE · TOOLS OK'
              : 'RESPONSE · NO TOOL CALLS';
            badge.className = `badge ${stepToolEvents.some(tool => tool.status !== 'success') ? 'badge-warning' : 'badge-accent'}`;
            record.status = 'completed';
          }
          stepOutput.text.textContent = [reply, formatChecks(checks)].filter(Boolean).join('\n\n');
          record.checks = checks;
          if (checksFailed) failed += 1;
          else if (checks.length) passed += 1;
          else completed += 1;
        } catch (error) {
          failed += 1;
          const badge = stepOutput.entry.querySelector('.suite-result-heading .badge');
          badge.textContent = 'FAILED';
          badge.className = 'badge badge-warning';
          stepOutput.text.textContent = reply
            ? `${reply}\n\nRequest failed: ${error.message}`
            : error.message;
          emitChat('system', `Test step ${index + 1} failed: ${error.message}`);
        }
        record.reply = reply.slice(0, 20000);
        record.tool_events = stepToolEvents.slice(0, 50).map(compactToolEvent);
        record.duration_ms = Date.now() - stepStarted;
        runSteps.push(record);
        if (stopRequested) break;
      }
      const stopped = stopRequested;
      setStatus(
        stopped
          ? `Stopped · ${passed} passed · ${failed} failed · ${completed} unchecked responses`
          : `Finished · ${passed} passed · ${failed} failed · ${completed} unchecked responses`,
        failed || toolCallsFailed ? 'danger' : 'success'
      );
      appendOutput(
        stopped ? 'Suite stopped' : 'Suite finished',
        `${passed} check(s) passed · ${failed} failed · ${completed} unchecked response(s) · `
          + `${toolCallsSucceeded} successful tool call(s) · ${toolCallsFailed} tool error(s). `
          + (stopped ? 'Remaining steps were not run.' : ''),
        failed || toolCallsFailed ? 'failed' : stopped ? 'stopped' : 'completed'
      );

      const run = {
        run_at: new Date().toISOString(),
        agent_id: selectedAgent,
        model: selectedModel,
        duration_ms: Date.now() - startedAt,
        passed,
        failed,
        steps: runSteps
      };
      if (currentSuite) {
        try {
          await Api.saveTestSuiteRun(currentSuite.category, currentSuite.slug, run);
          refreshHistory();
        } catch (error) {
          appendOutput('Could not save run history', error.message, 'failed');
        }
      } else {
        appendOutput('Run not kept', 'Save this suite to keep its run history.', 'info');
      }
    } finally {
      running = false;
      suiteExecutionActive = false;
      stopRequested = false;
      onRunningChange(false);
      updateControls();
      if (closeAfterRun) {
        closed = true;
        openSuiteDialog = null;
        overlay.remove();
        if (popout) window.close();
      }
    }
  }

  // Pop out: keep the unsaved steps in sessionStorage (copied to the new window), then open it.
  function popOut() {
    if (running || suiteExecutionActive) {
      setStatus('Stop the running suite before popping it out.', 'danger');
      return;
    }
    try {
      sessionStorage.setItem(DRAFT_KEY, JSON.stringify({
        name: nameInput.value,
        category: categorySelect.value,
        agent: agentSelect.value,
        model: modelSelect.value,
        steps
      }));
    } catch (_) { /* the window still opens, without the draft */ }
    const popup = window.open(`${window.location.origin}/chat?suite_window=1`, 'novous-suite-runner',
      'popup,width=760,height=900');
    if (!popup) {
      try { sessionStorage.removeItem(DRAFT_KEY); } catch (_) { /* ignore */ }
      setStatus('The browser blocked the pop-out window. Allow pop-ups for this site and try again.', 'danger');
      return;
    }
    closeDialog({ force: true });
  }

  // In the pop-out window, restore the steps that were unsaved in the main window.
  function applyPopoutDraft() {
    let draft = null;
    try {
      draft = JSON.parse(sessionStorage.getItem(DRAFT_KEY) || 'null');
      sessionStorage.removeItem(DRAFT_KEY);
    } catch (_) { draft = null; }
    if (!draft) return;
    steps = (draft.steps || []).map((step, index) => ({
      id: index + 1,
      enabled: step.enabled !== false,
      text: step.text || '',
      expect: step.expect || null
    }));
    nextId = steps.length + 1;
    nameInput.value = draft.name || '';
    if (draft.category) categorySelect.value = draft.category;
    ensureOption(agentSelect, draft.agent, draft.agent);
    ensureOption(modelSelect, draft.model, draft.model);
    dirty = true;
    renderSteps();
    setStatus('Restored your unsaved steps from the main window.', 'success');
  }

  function closeDialog({ force = false } = {}) {
    if (running) {
      if (closeAfterRun) return;
      if (!confirm('A step is still running. Stop after its response finishes and close the runner?')) return;
      stopRequested = true;
      closeAfterRun = true;
      setStatus('Stop requested; waiting for the current response before stopping.');
      updateControls();
      return;
    }
    if (dirty && !force && !confirm('Close without saving your test suite changes?')) {
      return;
    }
    closed = true;
    openSuiteDialog = null;
    overlay.remove();
    if (popout) window.close();
  }

  addButton.addEventListener('click', () => {
    const step = { id: nextId++, enabled: true, text: '', expect: null };
    steps.push(step);
    dirty = true;
    renderSteps(step.id);
  });
  nameInput.addEventListener('input', () => {
    dirty = true;
    updateControls();
  });
  categorySelect.addEventListener('change', () => {
    dirty = true;
  });
  savedSelect.addEventListener('change', updateControls);
  loadButton.addEventListener('click', loadSavedSuite);
  deleteButton.addEventListener('click', deleteSavedSuite);
  runButton.addEventListener('click', runSuite);
  stopButton.addEventListener('click', () => {
    if (!running || stopRequested) return;
    stopRequested = true;
    stopButton.disabled = true;
    setStatus('Stop requested; the current response will finish before stopping.');
  });
  saveButton.addEventListener('click', saveSuite);
  overlay.querySelector('[data-suite-popout]').addEventListener('click', popOut);
  overlay.querySelectorAll('[data-suite-close]').forEach(button => {
    button.addEventListener('click', closeDialog);
  });
  const onKeyDown = event => {
    if (event.key === 'Escape' && !closed) closeDialog();
  };
  overlay.addEventListener('keydown', onKeyDown);

  // --- Window: move by the header, resize from any edge or corner, hide, expand, close.
  const WINDOW_KEY = 'novous.suiteWindow';
  const MIN_WIDTH = 320;
  const MIN_HEIGHT = 220;
  const DEFAULT_WIDTH = 680;
  const DEFAULT_HEIGHT = 702;
  const dragHandle = overlay.querySelector('[data-suite-drag]');
  const hideButton = overlay.querySelector('[data-suite-hide]');
  const expandButton = overlay.querySelector('[data-suite-expand]');
  const widthInput = overlay.querySelector('[data-suite-width]');
  const heightInput = overlay.querySelector('[data-suite-height]');
  const widthValue = overlay.querySelector('[data-suite-width-value]');
  const heightValue = overlay.querySelector('[data-suite-height-value]');
  let geometry = null; // { x, y, width, height } of the normal (not expanded) window
  let minimized = false;
  let expanded = popout; // the pop-out window always fills its own window

  function defaultGeometry() {
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const width = Math.min(DEFAULT_WIDTH, vw - 16);
    const height = Math.min(DEFAULT_HEIGHT, vh - 16);
    return { x: Math.max(8, vw - width - 20), y: 72, width, height };
  }

  // Keeps the whole window on screen, so every edge and button stays reachable.
  function clampGeometry(g) {
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const width = Math.min(Math.max(g.width, MIN_WIDTH), vw);
    const height = Math.min(Math.max(g.height, MIN_HEIGHT), vh);
    return {
      width,
      height,
      x: Math.min(Math.max(g.x, 0), vw - width),
      y: Math.min(Math.max(g.y, 0), vh - height)
    };
  }

  function readSavedGeometry() {
    try {
      const saved = JSON.parse(localStorage.getItem(WINDOW_KEY) || 'null');
      if (saved && [saved.x, saved.y, saved.width, saved.height].every(Number.isFinite)) {
        return { x: saved.x, y: saved.y, width: saved.width, height: saved.height };
      }
    } catch (_) { /* storage can be unavailable; use the default window */ }
    return null;
  }

  function saveGeometry() {
    try {
      localStorage.setItem(WINDOW_KEY, JSON.stringify(geometry));
    } catch (_) { /* the window still works without saved geometry */ }
  }

  function render() {
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const shown = expanded ? { x: 8, y: 8, width: vw - 16, height: vh - 16 } : geometry;
    dialog.style.left = `${shown.x}px`;
    dialog.style.top = `${shown.y}px`;
    dialog.style.right = 'auto';
    dialog.style.width = `${shown.width}px`;
    dialog.style.height = minimized && !expanded ? 'auto' : `${shown.height}px`;
    dialog.classList.toggle('is-minimized', minimized && !expanded);
    dialog.classList.toggle('is-expanded', expanded);
    hideButton.textContent = minimized ? '▢' : '–';
    hideButton.title = minimized ? 'Restore window' : 'Hide window';
    hideButton.setAttribute('aria-label', hideButton.title);
    expandButton.textContent = expanded ? '❐' : '□';
    expandButton.title = expanded ? 'Restore size' : 'Expand window';
    expandButton.setAttribute('aria-label', expandButton.title);
    widthInput.max = String(vw);
    heightInput.max = String(vh);
    widthInput.value = String(Math.round(shown.width));
    heightInput.value = String(Math.round(shown.height));
    widthValue.textContent = `${Math.round(shown.width)}px`;
    heightValue.textContent = `${Math.round(shown.height)}px`;
  }

  function setGeometry(next) {
    geometry = clampGeometry(next);
    saveGeometry();
    render();
  }

  hideButton.addEventListener('click', () => {
    minimized = !minimized;
    if (minimized) expanded = false;
    render();
  });
  expandButton.addEventListener('click', () => {
    expanded = !expanded;
    if (expanded) minimized = false;
    render();
  });

  // Drag by the header (buttons excluded).
  dragHandle.addEventListener('pointerdown', event => {
    if (event.button !== 0 || event.target.closest('button') || expanded) return;
    event.preventDefault();
    const offsetX = event.clientX - geometry.x;
    const offsetY = event.clientY - geometry.y;
    dragHandle.setPointerCapture(event.pointerId);
    const move = moveEvent => {
      geometry = clampGeometry({ ...geometry, x: moveEvent.clientX - offsetX, y: moveEvent.clientY - offsetY });
      render();
    };
    const stop = () => {
      dragHandle.removeEventListener('pointermove', move);
      dragHandle.removeEventListener('pointerup', stop);
      dragHandle.removeEventListener('pointercancel', stop);
      saveGeometry();
    };
    dragHandle.addEventListener('pointermove', move);
    dragHandle.addEventListener('pointerup', stop);
    dragHandle.addEventListener('pointercancel', stop);
  });

  // Resize handles on all four sides and four corners.
  ['n', 's', 'e', 'w', 'ne', 'nw', 'se', 'sw'].forEach(edge => {
    const handle = document.createElement('div');
    handle.className = `suite-resize suite-resize-${edge}`;
    handle.setAttribute('aria-hidden', 'true');
    dialog.appendChild(handle);
    handle.addEventListener('pointerdown', event => {
      if (event.button !== 0 || minimized || expanded) return;
      event.preventDefault();
      const start = { ...geometry, px: event.clientX, py: event.clientY };
      handle.setPointerCapture(event.pointerId);
      const move = moveEvent => {
        const dx = moveEvent.clientX - start.px;
        const dy = moveEvent.clientY - start.py;
        let { x, y, width, height } = start;
        if (edge.includes('e')) width = start.width + dx;
        if (edge.includes('w')) { width = start.width - dx; x = start.x + dx; }
        if (edge.includes('s')) height = start.height + dy;
        if (edge.includes('n')) { height = start.height - dy; y = start.y + dy; }
        if (width < MIN_WIDTH) {
          if (edge.includes('w')) x = start.x + start.width - MIN_WIDTH;
          width = MIN_WIDTH;
        }
        if (height < MIN_HEIGHT) {
          if (edge.includes('n')) y = start.y + start.height - MIN_HEIGHT;
          height = MIN_HEIGHT;
        }
        geometry = clampGeometry({ x, y, width, height });
        render();
      };
      const stop = () => {
        handle.removeEventListener('pointermove', move);
        handle.removeEventListener('pointerup', stop);
        handle.removeEventListener('pointercancel', stop);
        saveGeometry();
      };
      handle.addEventListener('pointermove', move);
      handle.addEventListener('pointerup', stop);
      handle.addEventListener('pointercancel', stop);
    });
  });

  // Sliders set the normal size and leave the expanded view.
  widthInput.addEventListener('input', () => {
    expanded = false;
    setGeometry({ ...geometry, width: Number(widthInput.value) });
  });
  heightInput.addEventListener('input', () => {
    expanded = false;
    setGeometry({ ...geometry, height: Number(heightInput.value) });
  });
  overlay.querySelector('[data-suite-size-reset]').addEventListener('click', () => {
    expanded = false;
    setGeometry(defaultGeometry());
  });

  const onViewportResize = () => {
    if (closed) {
      window.removeEventListener('resize', onViewportResize);
      return;
    }
    geometry = clampGeometry(geometry);
    render();
  };
  window.addEventListener('resize', onViewportResize);

  geometry = clampGeometry(readSavedGeometry() || defaultGeometry());
  document.body.appendChild(overlay);
  render();
  openSuiteDialog = dialog;
  dialog.querySelector('[data-suite-close]').focus({ preventScroll: true });
  renderSteps();
  refreshSavedList();
  refreshHistory();
  if (popout) applyPopoutDraft();
  if (filePath) {
    setStatus('Loading selected To-Do list…');
    Api.readFile(filePath).then(file => {
      if (closed) return;
      steps = parseSteps(file.content);
      nextId = steps.reduce((maximum, step) => Math.max(maximum, step.id), 0) + 1;
      if (!nameInput.value.trim()) {
        nameInput.value = filePath.split(/[\\/]/).pop().replace(/\.[^.]+$/, '');
      }
      loadingSource = false;
      renderSteps();
      setStatus(`${steps.length} step(s) imported. Save to keep it in Saved suites.`, 'success');
    }).catch(error => {
      if (closed) return;
      loadingSource = false;
      setStatus(`Could not load ${filePath}: ${error.message}`, 'danger');
      appendOutput('Could not load To-Do file', error.message, 'failed');
      updateControls();
    });
  } else {
    setStatus('Pick a saved suite, or add steps and save a new one.');
  }
}
