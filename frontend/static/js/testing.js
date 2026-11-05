import { Api } from './api.js';
import { offerInstallForRun, offerInstallBeforeRun, confirmReset } from './sandbox_ui.js';

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[c]));
}

export function renderTestingView(container, payload = null) {
  container.replaceChildren();
  renderToolWorkbench(container, payload?.type === 'tool' ? payload : null);
}

function renderToolWorkbench(container, toolPayload = null) {
  container.insertAdjacentHTML('beforeend', `
    <section class="card tool-workbench mt-3" aria-labelledby="tool-workbench-title">
      <div class="card-head">
        <div>
          <h3 id="tool-workbench-title">Tool Function Testing Workbench</h3>
          <p class="text-muted small">Tests run on this computer using the local Python interpreter. Only run code you trust: tested code can access files, network, and resources available to your user account. Each run has a 5-second timeout.</p>
        </div>
      </div>
      <h4 class="tool-workbench-stage">Stage 1: Generate Python Function</h4>
      <div class="tool-workbench-grid">
        <label class="tool-workbench-field">
          <span>LLM Model</span>
          <span class="tool-workbench-model">
            <select id="tw-model" class="form-select" aria-label="LLM model">
              <option value="">Loading models…</option>
            </select>
            <button type="button" class="btn btn-sm" id="tw-refresh-models">Refresh</button>
          </span>
        </label>
        <label class="tool-workbench-field">
          <span>Tool Description &amp; Rules</span>
          <textarea id="tw-description" class="form-input" rows="5"></textarea>
        </label>
        <div class="tool-workbench-actions tool-workbench-wide">
          <button type="button" class="btn btn-primary" id="tw-generate-fn">Generate Python Function</button>
        </div>
        <div class="tool-workbench-field tool-workbench-wide">
          <span>Python Function Code</span>
          <div class="code-editor">
            <div class="code-editor-bar">
              <span class="code-editor-file">tool_function.py</span>
              <span class="code-editor-actions">
                <button type="button" class="btn btn-sm" id="tw-send-builder" title="Open this code in the Code Builder editor">Send to Code Builder</button>
                <button type="button" class="btn btn-sm" id="tw-edit-code">Edit</button>
                <button type="button" class="btn btn-sm" id="tw-copy-code">Copy</button>
              </span>
            </div>
            <div class="code-view" id="tw-code-view" tabindex="0" aria-label="Python function code (read-only view)"></div>
            <textarea id="tw-code" class="code-input" spellcheck="false" wrap="off" hidden
                      aria-label="Python function code" placeholder="# Generate a function in Stage 1, or paste your own code here"></textarea>
          </div>
        </div>
      </div>
      <h4 class="tool-workbench-stage mt-3">Stage 2: Ask LLM to Use Function</h4>
      <div class="tool-workbench-grid">
        <div class="tool-workbench-field tool-workbench-wide">
          <span>Models to test</span>
          <div id="tw-llm-models" class="tw-model-checks text-muted small">Loading models…</div>
          <label class="small"><input type="checkbox" id="tw-llm-dry"> Check the call only (don't run the tool)</label>
        </div>
        <label class="tool-workbench-field">
          <span>Natural Language Request</span>
          <textarea id="tw-prompt" class="form-input" rows="5"></textarea>
        </label>
        <label class="tool-workbench-field">
          <span>Extracted JSON Arguments</span>
          <textarea id="tw-input" class="tool-workbench-editor" rows="5" spellcheck="false"></textarea>
          <span class="text-muted small">Filled with the arguments the model sends. Edit it to run a Direct Python Test. Cleared if the model makes no tool call.</span>
        </label>
      </div>
      <div class="tool-workbench-actions">
        <button type="button" class="btn btn-primary" id="tw-run-llm">Ask LLM to Use Tool</button>
      </div>
      <div id="tw-llm-matrix" class="tw-llm-matrix" hidden></div>
      <h4 class="tool-workbench-stage mt-3">Stage 3 &amp; 4: Execution &amp; Test Results</h4>
      <div class="tool-workbench-actions">
        <button type="button" class="btn" id="tw-run">Direct Python Test</button>
        <label class="text-muted small" for="tw-timeout">Time limit</label>
        <select id="tw-timeout" class="form-select" style="width:auto" title="Raise this for GUI windows or slow code">
          <option value="5">5 seconds</option>
          <option value="30">30 seconds</option>
          <option value="60">1 minute</option>
          <option value="300">5 minutes</option>
          <option value="600">10 minutes</option>
        </select>
        <button type="button" class="btn" id="tw-save" disabled>Save Test Report (.md)</button>
        <button type="button" class="btn" id="tw-reset">Clear Fields</button>
        <button type="button" class="btn" id="tw-reset-env" title="Remove all packages installed for testing">Reset Environment</button>
        <button type="button" class="btn" id="tw-promote" disabled
                title="Run a successful test before adding a tool">Add Passed Tool to Library</button>
        <span id="tw-status" class="text-muted small" role="status">Ready</span>
      </div>
      <label id="tw-llm-response-section" class="tool-workbench-field" hidden>
        <span>Raw LLM Response</span>
        <pre id="tw-llm-response" class="tool-workbench-output" aria-live="polite"></pre>
      </label>
      <label class="tool-workbench-field">
        <span>Execution Output &amp; Result</span>
        <pre id="tw-output" class="tool-workbench-output" aria-live="polite">Run a test to see its result.</pre>
      </label>
      <div class="tool-workbench-library">
        <h4>Registered Tool Library</h4>
        <p class="text-muted small">Tools listed here are registered from the five file-tool modules and <code>promoted_tools.py</code> in <code>tools/</code>.</p>
        <div id="tw-library" class="text-muted small">Loading tools…</div>
      </div>
      <p class="text-muted small">Promoted code is trusted application code and will run in the Novous backend when an agent invokes it.</p>
      <dialog id="tw-tool-dialog" class="scratchpad-dialog tool-workbench-dialog" aria-labelledby="tw-tool-dialog-title">
        <div class="scratchpad-dialog-head">
          <div>
            <h2 id="tw-tool-dialog-title">Tool</h2>
            <p id="tw-tool-dialog-meta" class="text-muted small"></p>
          </div>
          <div class="root-management-actions">
            <button type="button" class="btn btn-sm" id="tw-tool-dialog-copy">Copy</button>
            <button type="button" class="btn btn-sm btn-primary" id="tw-tool-dialog-use">Add to Python Function Code</button>
            <button type="button" class="btn btn-sm" id="tw-tool-dialog-close" aria-label="Close">Close</button>
          </div>
        </div>
        <div class="code-editor">
          <div class="code-editor-bar">
            <span class="code-editor-file" id="tw-tool-dialog-file"></span>
            <button type="button" class="btn btn-sm" id="tw-tool-dialog-edit">Edit code</button>
            <button type="button" class="btn btn-sm btn-primary hidden" id="tw-tool-dialog-save-code">Save code</button>
            <button type="button" class="btn btn-sm hidden" id="tw-tool-dialog-cancel-code">Cancel</button>
          </div>
          <div class="code-view" id="tw-tool-dialog-code" tabindex="0" aria-label="Tool function source"></div>
          <textarea id="tw-tool-dialog-editor" class="tool-workbench-editor tool-dialog-editor hidden" spellcheck="false" aria-label="Edit tool function source"></textarea>
        </div>
        <p class="text-muted small">Built-in tools that rely on module-level imports or objects may fail on Direct Python Test, but Stage 2 (asking the LLM to use the tool) works for every tool. Built-in code is read-only; only the prompt description below can be changed for them.</p>
        <section class="tool-prompt-pane" aria-labelledby="tw-tool-prompt-title">
          <h3 id="tw-tool-prompt-title">Prompt description</h3>
          <p class="text-muted small">This wording is added to agent prompts and to the function schema the LLM sees when the tool is attached. Keep the docstring in the code describing how the tool works; use this to say when and how an agent should call it. Leave it empty to use the docstring.</p>
          <textarea id="tw-tool-prompt" class="form-input tool-prompt-input" rows="3" maxlength="2000" aria-label="Prompt description"></textarea>
          <div class="text-muted small">Text injected into prompts</div>
          <pre id="tw-tool-prompt-preview" class="tool-workbench-output"></pre>
          <div class="tool-prompt-actions">
            <button type="button" class="btn btn-sm" id="tw-tool-prompt-reset">Reset to docstring</button>
            <button type="button" class="btn btn-sm btn-primary" id="tw-tool-prompt-save">Save prompt description</button>
            <span id="tw-tool-dialog-msg" class="small" aria-live="polite"></span>
          </div>
        </section>
      </dialog>
    </section>`);

  const codeField = container.querySelector('#tw-code');
  const descriptionField = container.querySelector('#tw-description');
  const generateButton = container.querySelector('#tw-generate-fn');
  const inputField = container.querySelector('#tw-input');
  const promptField = container.querySelector('#tw-prompt');
  const modelSelect = container.querySelector('#tw-model');
  const runButton = container.querySelector('#tw-run');
  const llmButton = container.querySelector('#tw-run-llm');
  const saveButton = container.querySelector('#tw-save');
  const promoteButton = container.querySelector('#tw-promote');
  const output = container.querySelector('#tw-output');
  const llmResponseSection = container.querySelector('#tw-llm-response-section');
  const llmResponse = container.querySelector('#tw-llm-response');
  const llmModelList = container.querySelector('#tw-llm-models');
  const dryRunBox = container.querySelector('#tw-llm-dry');
  const llmMatrix = container.querySelector('#tw-llm-matrix');
  let llmRows = [];
  let llmContext = null;
  let selectedRow = -1;
  const status = container.querySelector('#tw-status');
  const timeoutSelect = container.querySelector('#tw-timeout');
  const timeoutSeconds = () => Number(timeoutSelect.value) || 5;
  const library = container.querySelector('#tw-library');

  let lastPassed = null;
  let lastTestReport = null;
  let libraryTools = [];
  let busy = false;
  const codeView = container.querySelector('#tw-code-view');
  const editButton = container.querySelector('#tw-edit-code');

  const PY_KEYWORDS = new Set(['and', 'as', 'assert', 'async', 'await', 'break', 'class', 'continue', 'def', 'del', 'elif', 'else', 'except', 'finally', 'for', 'from', 'global', 'if', 'import', 'in', 'is', 'lambda', 'nonlocal', 'not', 'or', 'pass', 'raise', 'return', 'try', 'while', 'with', 'yield', 'None', 'True', 'False']);
  const PY_BUILTINS = new Set(['str', 'int', 'float', 'bool', 'list', 'dict', 'set', 'tuple', 'bytes', 'len', 'range', 'print', 'round', 'sum', 'min', 'max', 'abs', 'sorted', 'enumerate', 'zip', 'map', 'filter', 'isinstance', 'any', 'all', 'self', 'Any', 'Optional', 'List', 'Dict']);
  const PY_TOKEN = /("""[\s\S]*?(?:"""|$)|'''[\s\S]*?(?:'''|$))|(#[^\n]*)|((?:[rbfRBF]{1,2})?(?:"(?:\\.|[^"\\\n])*"?|'(?:\\.|[^'\\\n])*'?))|(@[A-Za-z_][\w.]*)|(\b\d[\d_]*\.?\d*(?:[eE][+-]?\d+)?\b)|([A-Za-z_]\w*)/g;

  function escapeHtml(text) {
    return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }
  function highlightPython(source) {
    let html = '';
    let last = 0;
    let previousWord = '';
    for (const match of source.matchAll(PY_TOKEN)) {
      html += escapeHtml(source.slice(last, match.index));
      const [text, triple, comment, string, decorator, number, word] = match;
      // Close and reopen the span at each newline so every line is self-contained.
      const wrap = (cls) => text.split('\n')
        .map(part => part ? `<span class="${cls}">${escapeHtml(part)}</span>` : '')
        .join('\n');
      if (triple || string) html += wrap('tok-str');
      else if (comment) html += wrap('tok-com');
      else if (decorator) html += wrap('tok-dec');
      else if (number) html += wrap('tok-num');
      else if (word && PY_KEYWORDS.has(word)) html += wrap('tok-kw');
      else if (word && (previousWord === 'def' || previousWord === 'class')) html += wrap('tok-fn');
      else if (word && PY_BUILTINS.has(word)) html += wrap('tok-bi');
      else html += escapeHtml(text);
      if (word) previousWord = word;
      else if (!/^\s+$/.test(text)) previousWord = '';
      last = match.index + text.length;
    }
    return html + escapeHtml(source.slice(last));
  }

  const HARNESS_START = '# --- Novous test call';
  function renderLines(source) {
    return highlightPython(source).split('\n').map((line, index) =>
      `<div class="code-line"><span class="code-ln">${index + 1}</span><span class="code-text">${line || ' '}</span></div>`
    ).join('');
  }
  function renderCodeView() {
    const source = codeField.value.replace(/\r\n?/g, '\n');
    codeView.innerHTML = source
      ? renderLines(source)
      : '<div class="code-empty">No code yet. Generate a function in Stage 1, or click Edit to paste your own.</div>';
  }
  async function setEditing(editing) {
    if (editing && codeField.value.trim() && !codeField.value.includes(HARNESS_START)) {
      try {
        const { harness } = await Api.getToolTestHarness(codeField.value);
        codeField.value = `${codeField.value.replace(/\s+$/, '')}\n\n\n${harness}`;
      } catch (_) { /* invalid code: edit it without a test call */ }
    }
    codeField.hidden = !editing;
    codeView.hidden = editing;
    editButton.textContent = editing ? 'Done' : 'Edit';
    if (editing) {
      codeField.focus();
    } else {
      renderCodeView();
    }
  }
  function setCode(value) {
    codeField.value = value;
    setEditing(false);
  }
  editButton.addEventListener('click', () => setEditing(codeField.hidden));
  codeView.addEventListener('dblclick', () => setEditing(true));
  setEditing(false);

  container.querySelector('#tw-send-builder').addEventListener('click', () => {
    if (!codeField.value.trim()) {
      status.textContent = 'There is no code to send yet.';
      status.className = 'text-danger small';
      return;
    }
    try {
      localStorage.setItem('novous2.codebuilder.incoming', codeField.value);
    } catch (err) {
      status.textContent = `Could not hand off code: ${err.message}`;
      status.className = 'text-danger small';
      return;
    }
    window.open('/codebuilder', '_blank');
  });

  async function copyText(text, button) {
    try {
      await navigator.clipboard.writeText(text);
      button.textContent = 'Copied!';
    } catch (_) {
      const scratch = document.createElement('textarea');
      scratch.value = text;
      scratch.style.position = 'fixed';
      scratch.style.opacity = '0';
      (button.closest('dialog') || document.body).appendChild(scratch);
      scratch.select();
      const ok = document.execCommand('copy');
      scratch.remove();
      button.textContent = ok ? 'Copied!' : 'Copy failed';
    }
    setTimeout(() => { button.textContent = 'Copy'; }, 1500);
  }
  container.querySelector('#tw-copy-code').addEventListener('click', event =>
    copyText(codeField.value, event.currentTarget));

  const toolDialog = container.querySelector('#tw-tool-dialog');
  let viewedTool = null;
  let viewedDetails = null;
  const dialogEditButton = container.querySelector('#tw-tool-dialog-edit');
  const dialogSaveCodeButton = container.querySelector('#tw-tool-dialog-save-code');
  const dialogCancelCodeButton = container.querySelector('#tw-tool-dialog-cancel-code');
  const dialogCodeView = container.querySelector('#tw-tool-dialog-code');
  const dialogEditor = container.querySelector('#tw-tool-dialog-editor');
  const toolPromptField = container.querySelector('#tw-tool-prompt');
  const promptPreview = container.querySelector('#tw-tool-prompt-preview');
  const dialogMessage = container.querySelector('#tw-tool-dialog-msg');
  enableTabIndent(dialogEditor);

  function setDialogMessage(text, cls = 'text-muted small') {
    dialogMessage.textContent = text;
    dialogMessage.className = cls;
  }
  function previewPrompt(text) {
    const details = viewedDetails;
    if (!details) return;
    const wording = text.trim().replace(/\s+/g, ' ') || details.docstring;
    promptPreview.textContent = [
      `### ${details.name}`,
      `- Provider: ${details.provider}`,
      `- Signature: ${details.signature}`,
      wording,
    ].filter(Boolean).join('\n');
  }
  function setCodeEditing(editing) {
    dialogEditor.classList.toggle('hidden', !editing);
    dialogCodeView.classList.toggle('hidden', editing);
    dialogEditButton.classList.toggle('hidden', editing);
    dialogSaveCodeButton.classList.toggle('hidden', !editing);
    dialogCancelCodeButton.classList.toggle('hidden', !editing);
  }
  async function openToolDialog(name) {
    try {
      const [tool, details] = await Promise.all([
        Api.getWorkbenchTool(name),
        Api.getToolDetails(name),
      ]);
      viewedTool = tool;
      viewedDetails = details;
      container.querySelector('#tw-tool-dialog-title').textContent = details.name;
      container.querySelector('#tw-tool-dialog-meta').textContent =
        `${details.path} · ${details.name}${details.signature}${details.custom ? '' : ' · built in'}`;
      container.querySelector('#tw-tool-dialog-file').textContent = details.path;
      dialogCodeView.innerHTML = renderLines(details.source.replace(/\r\n?/g, '\n'));
      dialogEditor.value = details.source;
      setCodeEditing(false);
      dialogEditButton.disabled = !details.custom;
      dialogEditButton.title = details.custom ? '' : 'Built-in tools are read-only';
      toolPromptField.value = details.prompt_description;
      previewPrompt(details.prompt_description);
      setDialogMessage('');
      toolDialog.showModal();
    } catch (err) {
      status.textContent = `Could not open tool: ${err.message}`;
      status.className = 'text-danger small';
    }
  }
  async function saveToolCode() {
    const name = viewedDetails.name;
    dialogSaveCodeButton.disabled = true;
    try {
      await Api.updateTool(name, dialogEditor.value);
      [viewedTool, viewedDetails] = await Promise.all([
        Api.getWorkbenchTool(name),
        Api.getToolDetails(name),
      ]);
      dialogCodeView.innerHTML = renderLines(viewedDetails.source.replace(/\r\n?/g, '\n'));
      dialogEditor.value = viewedDetails.source;
      container.querySelector('#tw-tool-dialog-meta').textContent =
        `${viewedDetails.path} · ${viewedDetails.name}${viewedDetails.signature}`;
      setCodeEditing(false);
      setDialogMessage(`Saved ${name} to ${viewedDetails.path}.`, 'text-success small');
      await loadToolLibrary();
      container.dispatchEvent(new CustomEvent('novous:tool-promoted', {
        bubbles: true, detail: { name }
      }));
    } catch (err) {
      setDialogMessage(`Could not save code: ${err.message}`, 'text-danger small');
    } finally {
      dialogSaveCodeButton.disabled = false;
    }
  }
  async function savePromptDescription(text) {
    const name = viewedDetails.name;
    try {
      const saved = await Api.saveToolPrompt(name, text);
      viewedDetails.prompt_description = saved.prompt_description;
      toolPromptField.value = saved.prompt_description;
      previewPrompt(saved.prompt_description);
      const count = saved.refreshed_agents.length;
      setDialogMessage(
        `Saved. Updated ${count} agent prompt${count === 1 ? '' : 's'} that use ${name}.`,
        'text-success small'
      );
    } catch (err) {
      setDialogMessage(`Could not save prompt description: ${err.message}`, 'text-danger small');
    }
  }
  dialogEditButton.addEventListener('click', () => {
    dialogEditor.value = viewedDetails.source;
    setCodeEditing(true);
    dialogEditor.focus();
  });
  dialogCancelCodeButton.addEventListener('click', () => {
    dialogEditor.value = viewedDetails.source;
    setCodeEditing(false);
    setDialogMessage('');
  });
  dialogSaveCodeButton.addEventListener('click', saveToolCode);
  toolPromptField.addEventListener('input', () => previewPrompt(toolPromptField.value));
  container.querySelector('#tw-tool-prompt-save').addEventListener('click', () => savePromptDescription(toolPromptField.value));
  container.querySelector('#tw-tool-prompt-reset').addEventListener('click', () => {
    toolPromptField.value = '';
    savePromptDescription('');
  });
  function useTool(tool) {
    if (codeField.value.trim() && !confirm(`Replace the current Python Function Code with ${tool.name}?`)) {
      return false;
    }
    setCode(tool.test_source ?? tool.source);
    lastPassed = null;
    updatePromotion();
    status.textContent = `Loaded ${tool.name} from ${tool.path}. Next: write a request in Stage 2.`;
    status.className = 'text-muted small';
    return true;
  }
  container.querySelector('#tw-tool-dialog-copy').addEventListener('click', event => {
    if (viewedTool) copyText(viewedTool.source, event.currentTarget);
  });
  container.querySelector('#tw-tool-dialog-use').addEventListener('click', () => {
    if (!viewedTool || !useTool(viewedTool)) return;
    toolDialog.close();
    promptField.scrollIntoView({ block: 'center' });
    promptField.focus();
  });
  container.querySelector('#tw-tool-dialog-close').addEventListener('click', () => toolDialog.close());
  function enableTabIndent(field) {
    field.addEventListener('keydown', event => {
      if (event.key !== 'Tab') return;
      event.preventDefault();
      const start = field.selectionStart;
      const end = field.selectionEnd;
      field.value = field.value.slice(0, start) + '    ' + field.value.slice(end);
      field.selectionStart = field.selectionEnd = start + 4;
      field.dispatchEvent(new Event('input'));
    });
  }

  function createTestReport(result, context = {}) {
    return {
      tested_at: new Date().toISOString(),
      execution_mode: context.execution_mode || 'local-python',
      model_used: context.model_used || null,
      function_code: context.function_code ?? codeField.value,
      function_input: Object.prototype.hasOwnProperty.call(context, 'function_input')
        ? context.function_input
        : inputField.value,
      prompt: context.prompt ?? '',
      test_output: result
    };
  }

  function markdownCodeBlock(value, language = '') {
    const text = typeof value === 'string' ? value : JSON.stringify(value, null, 2);
    const longestFence = Math.max(2, ...((text.match(/`+/g) || []).map(run => run.length)));
    const fence = '`'.repeat(longestFence + 1);
    return `${fence}${language}\n${text}\n${fence}`;
  }

  function renderTestReport(report) {
    const testOutput = report.test_output || {};
    const execution = testOutput.tool_execution_result || testOutput;
    const passed = testOutput.status === 'SUCCESS';
    const fields = [
      `- **Result:** ${passed ? 'PASS' : 'FAIL'}`,
      `- **Tested at (UTC):** ${report.tested_at}`,
      `- **Execution mode:** ${report.execution_mode}`,
      `- **LLM used:** ${report.model_used || 'No — direct local Python test'}`,
      `- **Function:** ${execution.function_name || 'Not identified'}`,
      `- **Error code:** ${testOutput.error_code || execution.error_code || 'None'}`
    ];
    const sections = [
      '# Tool Function Test Report',
      '',
      ...fields,
      '',
      '## LLM Prompt',
      '',
      report.prompt ? markdownCodeBlock(report.prompt, 'text') : '_No LLM prompt was used._',
      '',
      '## Function Input',
      '',
      markdownCodeBlock(report.function_input, 'json'),
      '',
      '## Function Code',
      '',
      markdownCodeBlock(report.function_code, 'python'),
      '',
      '## Result',
      '',
      `**Status:** ${testOutput.status || 'ERROR'}`,
      ''
    ];

    if (execution.output !== undefined) {
      sections.push('### Function output', '', markdownCodeBlock(execution.output, 'json'), '');
    }
    if (testOutput.llm_text_response) {
      sections.push('### LLM response', '', markdownCodeBlock(testOutput.llm_text_response, 'text'), '');
    }
    if (testOutput.tool_calls_detected?.length) {
      sections.push(
        '### LLM tool calls',
        '',
        markdownCodeBlock(testOutput.tool_calls_detected, 'json'),
        ''
      );
    }
    if (testOutput.message) {
      sections.push('### Failure details', '', markdownCodeBlock(testOutput.message, 'text'), '');
    }
    for (const [label, value] of [
      ['Standard error', execution.stderr],
      ['Standard output', execution.stdout],
      ['Exit code', execution.exit_code]
    ]) {
      if (value !== undefined && value !== null && value !== '') {
        sections.push(`### ${label}`, '', markdownCodeBlock(value, 'text'), '');
      }
    }
    sections.push(
      '## Machine-readable JSON',
      '',
      'The complete report data is included below for reuse or automated processing.',
      '',
      markdownCodeBlock(report, 'json'),
      ''
    );
    return sections.join('\n');
  }

  function parseFunctionInput() {
    const parsed = JSON.parse(inputField.value || '{}');
    if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') {
      throw new Error('Function input must be a JSON object.');
    }
    return parsed;
  }

  function updatePromotion() {
    const unchangedSincePass = lastPassed &&
      lastPassed.code === codeField.value &&
      lastPassed.input === inputField.value;
    let hasValidInput = true;
    try {
      parseFunctionInput();
    } catch (_) {
      hasValidInput = false;
    }
    promoteButton.disabled = busy || !unchangedSincePass || !hasValidInput;
  }

  function renderOutput(result) {
    const execution = result.tool_execution_result || result;
    const lines = [];
    const add = (text, cls = '') => {
      const span = document.createElement('span');
      if (cls) span.className = cls;
      span.textContent = text.endsWith('\n') ? text : `${text}\n`;
      lines.push(span);
    };
    if (execution.stdout) add(execution.stdout);
    if (execution.stderr) add(execution.stderr, 'tw-out-error');
    if (result.message && !execution.stderr) add(`Error: ${result.message}`, 'tw-out-error');
    if (result.status === 'SUCCESS' && execution.output !== undefined) {
      add(`Result: ${JSON.stringify(execution.output, null, 2)}`, 'tw-out-result');
    }
    if (result.status !== 'SUCCESS') {
      const code = result.error_code || execution.error_code;
      if (code) add(`[${code}]`, 'tw-out-error');
    }
    if (execution.exit_code !== undefined && execution.exit_code !== null) {
      add(`Process exited with code ${execution.exit_code}.`, 'tw-out-meta');
    }
    if (!lines.length) add('Execution completed with no output.', 'tw-out-meta');
    output.replaceChildren(...lines);
  }

  function showResult(result, reportContext) {
    const notRun = {
      CALL_VALID: 'Dry run: the model made a valid call. The tool was not run.',
      UNSUPPORTED: 'This model cannot use tools, so it was not tested.',
    }[result.status];
    if (notRun) {
      output.textContent = result.message || notRun;
    } else {
      renderOutput(result);
    }
    lastTestReport = createTestReport(result, reportContext);
    saveButton.disabled = false;
    const passed = result.status === 'SUCCESS';
    if (notRun) {
      status.textContent = notRun;
      status.className = 'text-muted small';
    } else {
      status.textContent = passed ? 'Test passed' : 'Test failed';
      status.className = passed ? 'text-success small' : 'text-danger small';
    }
    lastPassed = passed
      ? { code: codeField.value, input: inputField.value }
      : null;
    updatePromotion();
  }

  function showLlmResponse(result) {
    const sections = [];
    if (typeof result.llm_text_response === 'string' && result.llm_text_response) {
      sections.push(result.llm_text_response);
    }
    if (result.tool_calls_detected?.length) {
      sections.push(`Tool calls:\n${JSON.stringify(result.tool_calls_detected, null, 2)}`);
    }
    if (!sections.length) {
      sections.push(result.message || 'No response content was returned by the model.');
    }
    llmResponse.textContent = sections.join('\n\n');
    llmResponseSection.hidden = false;
  }

  function selectedLlmModels() {
    return Array.from(llmModelList.querySelectorAll('input[type="checkbox"]:checked')).map(box => box.value);
  }

  // Installed chat models with tool support; models without it are shown but cannot be ticked.
  async function loadLlmModels(preferred) {
    llmModelList.textContent = 'Loading models…';
    try {
      const data = await Api.getModelProfiles();
      const models = (data.models || []).map(item => ({
        name: item.model,
        supportsTools: !item.capabilities?.length || item.capabilities.includes('tools'),
      }));
      if (!models.length) {
        llmModelList.textContent = data.error || 'No chat models installed.';
        return;
      }
      const defaultModel = models.find(item => item.supportsTools && item.name === preferred)
        || models.find(item => item.supportsTools);
      llmModelList.innerHTML = models.map(item => `
        <label class="tw-model-check${item.supportsTools ? '' : ' text-muted'}">
          <input type="checkbox" value="${esc(item.name)}"
                 ${item.supportsTools && item.name === defaultModel?.name ? 'checked' : ''}
                 ${item.supportsTools ? '' : 'disabled'}>
          <span>${esc(item.name)}</span>${item.supportsTools ? '' : ' <em>(no tool support)</em>'}
        </label>`).join('');
    } catch (err) {
      llmModelList.textContent = `Could not load models: ${err.message}`;
    }
  }

  // Ask each selected model in turn (Ollama loads one model at a time); one failing model
  // does not stop the others.
  async function collectLlmRows(models) {
    const rows = [];
    for (const [index, model] of models.entries()) {
      status.textContent = `Asking ${model} (${index + 1} of ${models.length})…`;
      status.className = 'text-muted small';
      try {
        const result = await Api.testToolWithLlm(model, codeField.value, promptField.value, timeoutSeconds(), {
          execute: !dryRunBox.checked,
        });
        rows.push({ model, result });
      } catch (err) {
        rows.push({
          model,
          result: {
            status: 'ERROR', error_code: 'ERR_TOOL_TEST_FAILED', message: err.message,
            function_input: null, tool_calls_detected: [], llm_text_response: '',
          },
        });
      }
    }
    return rows;
  }

  function llmArguments(result) {
    if (result.function_input != null) return result.function_input;
    const rawArgs = result.tool_calls_detected?.[0]?.function?.arguments;
    return rawArgs && typeof rawArgs === 'object' ? rawArgs : null;
  }

  // Show one model's answer in the panels, the same way a single LLM test always did.
  function applyLlmRow(index) {
    const row = llmRows[index];
    selectedRow = index;
    showLlmResponse(row.result);
    const extracted = llmArguments(row.result);
    inputField.value = extracted != null ? JSON.stringify(extracted, null, 2) : '';
    showResult(row.result, { ...llmContext, model_used: row.model, function_input: extracted });
    renderLlmMatrix();
    return row.result;
  }

  function renderLlmMatrix() {
    llmMatrix.hidden = !llmRows.length;
    if (!llmRows.length) return;
    const outcome = result => ({
      SUCCESS: '✓ ran',
      CALL_VALID: '✓ valid call',
      UNSUPPORTED: 'no tool support',
    }[result.status] || `✗ ${result.error_code || result.status}`);
    llmMatrix.innerHTML = `
      <table class="tw-llm-table">
        <thead><tr><th>Model</th><th>Result</th><th>Call</th><th>Arguments</th></tr></thead>
        <tbody>${llmRows.map((row, index) => {
          const args = llmArguments(row.result);
          return `<tr data-row="${index}" tabindex="0" class="${index === selectedRow ? 'is-selected' : ''}">
            <td>${esc(row.model)}</td>
            <td>${esc(outcome(row.result))}</td>
            <td>${esc(row.result.origin || '—')}</td>
            <td><code>${esc(args == null ? '' : JSON.stringify(args).slice(0, 120))}</code></td>
          </tr>`;
        }).join('')}</tbody>
      </table>
      <p class="text-muted small">Click a row to see that model's answer and arguments.</p>`;
  }

  llmMatrix.addEventListener('click', event => {
    const row = event.target.closest('tr[data-row]');
    if (row && !busy) applyLlmRow(Number(row.dataset.row));
  });

  async function loadModels() {
    modelSelect.replaceChildren(new Option('Loading models…', ''));
    try {
      const data = await Api.getModels();
      const models = data.models || [];
      modelSelect.replaceChildren();
      if (!models.length) {
        modelSelect.appendChild(new Option(data.error || 'No models found', ''));
        return;
      }
      models.forEach(model => modelSelect.appendChild(new Option(model, model)));
      modelSelect.value = models.includes(data.default) ? data.default : models[0];
      await loadLlmModels(modelSelect.value);
    } catch (err) {
      modelSelect.replaceChildren(new Option(`Could not load models: ${err.message}`, ''));
    }
  }

  async function loadToolLibrary() {
    try {
      const data = await Api.getToolLibrary();
      const tools = libraryTools = data.tools || [];
      library.innerHTML = tools.length
        ? tools.map(tool => `
          <div class="tool-workbench-library-item">
            <code>${esc(tool.name)}</code>
            <span>${esc(tool.signature)}</span>
            <button type="button" class="btn btn-sm" data-tool-view="${esc(tool.name)}">${tool.custom ? 'Edit' : 'View'}</button>
            ${tool.custom ? `
              <button type="button" class="btn btn-sm" data-tool-load="${esc(tool.name)}">Load</button>
              <button type="button" class="btn btn-sm" data-tool-delete="${esc(tool.name)}">Remove</button>
            ` : ''}
          </div>`).join('')
        : 'No tools registered.';
    } catch (err) {
      library.textContent = `Could not load tools: ${err.message}`;
    }
  }

  async function runTest(useLlm) {
    let functionInput;
    if (!useLlm) {
      try {
        functionInput = parseFunctionInput();
      } catch (err) {
        lastPassed = null;
        renderOutput({
          status: 'ERROR',
          error_code: 'ERR_INVALID_JSON_INPUT',
          message: err.message
        });
        lastTestReport = createTestReport({
          status: 'ERROR',
          error_code: 'ERR_INVALID_JSON_INPUT',
          message: err.message
        });
        saveButton.disabled = false;
        status.textContent = 'Test failed';
        status.className = 'text-danger small';
        updatePromotion();
        return;
      }
    }
    if (useLlm && !codeField.value.trim()) {
      status.textContent = 'Complete Stage 1 first: generate or enter a Python function.';
      status.className = 'text-danger small';
      return;
    }
    const llmModels = useLlm ? selectedLlmModels() : [];
    if (useLlm && !llmModels.length) {
      status.textContent = 'Select at least one installed model to test.';
      status.className = 'text-danger small';
      return;
    }

    await offerInstallBeforeRun(codeField.value);

    let reportInput;
    try {
      reportInput = parseFunctionInput();
    } catch (_) {
      reportInput = inputField.value;
    }
    const reportContext = {
      execution_mode: useLlm ? 'local-llm-tool-call' : 'local-python',
      model_used: useLlm ? llmModels.join(', ') : null,
      function_code: codeField.value,
      function_input: useLlm ? null : reportInput,
      prompt: useLlm ? promptField.value : ''
    };
    busy = true;
    lastPassed = null;
    updatePromotion();
    runButton.disabled = true;
    llmButton.disabled = true;
    status.textContent = useLlm ? 'Requesting local model tool call…' : 'Running with local Python…';
    status.className = 'text-muted small';
    output.textContent = 'Test in progress…';
    llmResponseSection.hidden = true;
    llmResponse.textContent = '';
    let needsInstallCheck = '';
    try {
      let result;
      if (useLlm) {
        llmContext = reportContext;
        selectedRow = -1;
        llmRows = await collectLlmRows(llmModels);
        const firstPassing = llmRows.findIndex(row => row.result.status === 'SUCCESS' || row.result.status === 'CALL_VALID');
        result = applyLlmRow(firstPassing === -1 ? 0 : firstPassing);
      } else {
        result = await Api.executeToolTest(codeField.value, functionInput, timeoutSeconds());
        showResult(result, reportContext);
      }
      const execution = result.tool_execution_result || result;
      const ran = !['SUCCESS', 'CALL_VALID', 'UNSUPPORTED'].includes(result.status);
      needsInstallCheck = ran
        ? `${execution.stderr || ''}\n${execution.stdout || ''}\n${result.message || ''}`
        : '';
    } catch (err) {
      const result = {
        status: 'ERROR',
        error_code: 'ERR_TOOL_TEST_FAILED',
        message: err.message
      };
      if (useLlm) showLlmResponse(result);
      renderOutput(result);
      lastTestReport = createTestReport(result, reportContext);
      saveButton.disabled = false;
      status.textContent = 'Test failed';
      status.className = 'text-danger small';
    } finally {
      busy = false;
      runButton.disabled = false;
      llmButton.disabled = false;
      updatePromotion();
    }
    if (needsInstallCheck && await offerInstallForRun(codeField.value, needsInstallCheck)) {
      await runTest(useLlm);
    }
  }

  runButton.addEventListener('click', () => runTest(false));
  llmButton.addEventListener('click', () => runTest(true));
  generateButton.addEventListener('click', async () => {
    if (!descriptionField.value.trim()) {
      status.textContent = 'Enter a tool description first.';
      status.className = 'text-danger small';
      return;
    }
    if (!modelSelect.value) {
      status.textContent = 'Select an installed model first.';
      status.className = 'text-danger small';
      return;
    }
    busy = true;
    generateButton.disabled = runButton.disabled = llmButton.disabled = true;
    status.textContent = 'Generating Python function…';
    status.className = 'text-muted small';
    try {
      const result = await Api.generateToolFunction(modelSelect.value, descriptionField.value);
      if (result.status === 'SUCCESS') {
        setCode(result.code);
        lastPassed = null;
        status.textContent = `Generated ${result.function_name}. Next: ask the LLM to use it.`;
        status.className = 'text-success small';
      } else {
        status.textContent = `${result.error_code}: ${result.message}`;
        status.className = 'text-danger small';
      }
    } catch (err) {
      status.textContent = `Could not generate function: ${err.message}`;
      status.className = 'text-danger small';
    } finally {
      busy = false;
      generateButton.disabled = runButton.disabled = llmButton.disabled = false;
      updatePromotion();
    }
  });
  container.querySelector('#tw-refresh-models').addEventListener('click', loadModels);
  library.addEventListener('click', async event => {
    const viewButton = event.target.closest('[data-tool-view]');
    const loadButton = event.target.closest('[data-tool-load]');
    const deleteButton = event.target.closest('[data-tool-delete]');
    if (viewButton) await openToolDialog(viewButton.dataset.toolView);
    if (loadButton) {
      try {
        useTool(await Api.getWorkbenchTool(loadButton.dataset.toolLoad));
      } catch (err) {
        status.textContent = `Could not load tool: ${err.message}`;
        status.className = 'text-danger small';
      }
    }
    if (deleteButton && confirm(
      `Remove ${deleteButton.dataset.toolDelete} from the tool library? Existing agent sessions may keep the loaded function until reset.`
    )) {
      try {
        const deleted = await Api.deleteWorkbenchTool(deleteButton.dataset.toolDelete);
        await loadToolLibrary();
        status.textContent = `Removed ${deleted.name} from ${deleted.path}`;
        status.className = 'text-success small';
      } catch (err) {
        status.textContent = `Could not remove tool: ${err.message}`;
        status.className = 'text-danger small';
      }
    }
  });
  enableTabIndent(codeField);
  enableTabIndent(inputField);
  codeField.addEventListener('input', () => {
    lastPassed = null;
    updatePromotion();
  });
  inputField.addEventListener('input', () => {
    lastPassed = null;
    updatePromotion();
  });
  saveButton.addEventListener('click', async () => {
    if (!lastTestReport) return;
    const stamp = new Date().toISOString().replace(/[:.]/g, '-');
    try {
      const saved = await Api.saveMarkdown(
        renderTestReport(lastTestReport),
        `novous-tool-test-${stamp}.md`
      );
      if (saved.locationChosen) {
        alert(`Test results saved as ${saved.filename}.`);
      } else {
        alert(`Test results download started: ${saved.filename}`);
      }
    } catch (err) {
      if (err.name !== 'AbortError') {
        alert(`Could not save test results: ${err.message}`);
      }
    }
  });
  promoteButton.addEventListener('click', async () => {
    if (!lastPassed || !confirm(
      'Add this tested function to the tool library? Promoted code runs as trusted code in the Novous backend.'
    )) return;

    try {
      busy = true;
      runButton.disabled = true;
      llmButton.disabled = true;
      updatePromotion();
      status.textContent = 'Verifying test and adding tool…';
      const result = await Api.promoteTestedTool(codeField.value, parseFunctionInput(), timeoutSeconds());
      output.textContent = JSON.stringify(result, null, 2);
      status.textContent = `Added ${result.promoted.name} to the tool library`;
      if (result.requirements_added?.length) {
        status.textContent += `. Added to requirements.txt: ${result.requirements_added.join(', ')} (run pip install -r requirements.txt in the app environment)`;
      }
      status.className = 'text-success small';
      lastPassed = null;
      await loadToolLibrary();
      container.dispatchEvent(new CustomEvent('novous:tool-promoted', {
        bubbles: true, detail: { name: result.promoted.name }
      }));
    } catch (err) {
      output.textContent = JSON.stringify({
        status: 'ERROR',
        error_code: 'ERR_TOOL_PROMOTION_FAILED',
        message: err.message
      }, null, 2);
      status.textContent = 'Could not add tool';
      status.className = 'text-danger small';
    } finally {
      busy = false;
      runButton.disabled = false;
      llmButton.disabled = false;
      updatePromotion();
    }
  });

  container.querySelector('#tw-reset-env').addEventListener('click', async () => {
    try {
      const result = await confirmReset();
      if (!result) return;
      status.textContent = result.output;
      status.className = result.ok ? 'text-success small' : 'text-danger small';
    } catch (err) {
      status.textContent = `Reset failed: ${err.message}`;
      status.className = 'text-danger small';
    }
  });

  container.querySelector('#tw-reset').addEventListener('click', () => {
    setCode('');
    descriptionField.value = '';
    inputField.value = '';
    promptField.value = '';
    output.textContent = 'Run a test to see its result.';
    llmResponse.textContent = '';
    llmResponseSection.hidden = true;
    status.textContent = 'Ready';
    status.className = 'text-muted small';
    lastPassed = null;
    lastTestReport = null;
    saveButton.disabled = true;
    updatePromotion();
  });

  loadModels();
  loadToolLibrary();

  if (toolPayload) {
    setCode(toolPayload.source);
    inputField.value = toolPayload.input;
    promptField.value = toolPayload.prompt;
    status.textContent = `Loaded ${toolPayload.name} from ${toolPayload.path} for testing`;
    container.querySelector('.tool-workbench').scrollIntoView({ block: 'start' });
  }
}
