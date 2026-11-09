import { Api } from './api.js';
import { appendMarkdown } from './markdown.js';
import { buildStandaloneHtml, setupScratchpadReader } from './scratchpad_reader.js';
import { attachScratchpadSwitcher } from './scratchpad_switcher.js';

async function request(path, options) {
  const response = await fetch(`/api/agent-memory/squad-scratchpads${path}`, options);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed (${response.status})`);
  }
  return response.json();
}

function renderEntries(target, entries) {
  target.replaceChildren();
  if (!entries.length) {
    target.textContent = 'No entries match this selection.';
    return;
  }
  for (const entry of entries) {
    const card = document.createElement('details');
    card.className = 'codebuilder-scratchpad-entry';
    card.dataset.kind = entry.kind;
    card.dataset.readerItem = '';
    card.open = true;
    const heading = document.createElement('summary');
    heading.textContent = `${entry.author} · ${entry.kind} · ${entry.title}`;
    const meta = document.createElement('p');
    meta.className = 'text-muted small';
    meta.textContent = `${new Date(entry.created_at).toLocaleString()}${entry.truncated ? ' · content truncated' : ''}`;
    const body = document.createElement('div');
    body.className = 'prompt-rich-markdown';
    appendMarkdown(body, entry.content);
    card.append(heading, meta, body);
    target.append(card);
  }
}

function markdown(run, entries) {
  const lines = [`# Squad scratchpad: ${run.squad_id}`, '', `- Run: ${run.run_id}`,
    `- Status: ${run.status}`, `- Request: ${run.query}`, ''];
  for (const entry of entries) {
    lines.push(`## ${entry.title}`, '', `- Agent: ${entry.author}`, `- Type: ${entry.kind}`,
      `- Time: ${entry.created_at}`, ...(entry.truncated ? ['- Content truncated'] : []),
      '', entry.content, '');
  }
  return lines.join('\n');
}

/** Open a recoverable, run-scoped squad scratchpad. */
export async function openSquadScratchpad(preferredRun = '') {
  const dialog = document.createElement('dialog');
  dialog.className = 'squad-scratchpad-dialog reader-dialog';
  dialog.innerHTML = `
    <header class="squad-scratchpad-header"><h2>Squad scratchpad</h2>
      <button type="button" class="cb-btn" data-action="close">Close</button></header>
    <p class="text-muted small">Each run keeps its own scratchpad. Closing preserves it in the Run dropdown for later.</p>
    <div class="squad-scratchpad-controls">
      <label>Run <select class="form-select" data-role="run"></select></label>
      <label>Agent <select class="form-select" data-role="author"><option value="">All agents</option></select></label>
      <label>Type <select class="form-select" data-role="kind"><option value="">All types</option>
        <option>plan</option><option>code</option><option>test</option><option>fix</option>
        <option>summary</option><option>note</option></select></label>
      <button type="button" class="cb-btn" data-action="refresh">Refresh</button>
      <button type="button" class="cb-btn" data-action="print">Print / Save PDF</button>
      <button type="button" class="cb-btn cb-btn-danger" data-action="clear">Clear notepad</button>
    </div>
    <div data-role="reader"></div>
    <p data-role="status" role="status" aria-live="polite"></p>
    <div class="squad-scratchpad-content" data-role="content"></div>
    <details class="squad-scratchpad-note"><summary>Add a note</summary>
      <label>Title <input class="form-input" data-role="title" maxlength="200"></label>
      <label>Note <textarea class="form-input" data-role="note" maxlength="20000" rows="3"></textarea></label>
      <button type="button" class="cb-btn" data-action="note">Add note</button>
    </details>`;
  document.body.append(dialog);
  const select = dialog.querySelector('[data-role="run"]');
  const author = dialog.querySelector('[data-role="author"]');
  const kind = dialog.querySelector('[data-role="kind"]');
  const content = dialog.querySelector('[data-role="content"]');
  const status = dialog.querySelector('[data-role="status"]');
  let run = null;
  let displayed = [];
  const reader = setupScratchpadReader({
    content, toolbar: dialog.querySelector('[data-role="reader"]'),
    prefsKey: 'novous.squad.scratchpad.', title: 'Squad scratchpad',
    getMarkdown: () => markdown(run, displayed), filenameBase: 'novous-squad-scratchpad',
    onError: error => { status.textContent = `Could not save: ${error.message}`; },
  });
  const squadControls = dialog.querySelector('.squad-scratchpad-controls');
  attachScratchpadSwitcher({
    toolbar: dialog.querySelector('.squad-scratchpad-header'),
    content,
    nativeControls: squadControls ? [squadControls] : [],
    currentStore: 'squad',
    onRestore: () => loadRuns().catch(err => { status.textContent = err.message; }),
  });

  dialog.addEventListener('close', () => { dialog.remove(); });

  async function loadRuns() {
    const data = await request('');
    select.replaceChildren();
    for (const item of data.runs) {
      select.add(new Option(`${item.squad_id} · ${item.status} · ${item.run_id}`, item.run_id));
    }
    if ([...select.options].some(option => option.value === preferredRun)) select.value = preferredRun;
    if (!select.value) {
      run = null;
      content.textContent = 'No squad scratchpads are available.';
      status.textContent = '';
      return;
    }
    await loadRun();
  }
  async function loadRun() {
    run = await request(`/${encodeURIComponent(select.value)}`);
    const previous = author.value;
    author.replaceChildren(new Option('All agents', ''));
    for (const name of [...new Set(run.entries.map(entry => entry.author))].sort()) {
      author.add(new Option(name, name));
    }
    author.value = [...author.options].some(option => option.value === previous) ? previous : '';
    render();
  }
  function render() {
    displayed = run.entries.filter(entry =>
      (!author.value || entry.author === author.value) && (!kind.value || entry.kind === kind.value));
    renderEntries(content, displayed);
    reader.refresh();
    status.textContent = `${run.total} entries · ${run.status} · ${run.query}`;
  }
  function fullHtml() {
    const full = document.createElement('div');
    renderEntries(full, run.entries);
    return buildStandaloneHtml(`Squad scratchpad: ${run.squad_id} · ${run.run_id}`, full, 0.95);
  }
  async function clearNotepad() {
    if (!run) throw new Error('Select a run first.');
    if (!confirm('Clear this notepad? The entries will be permanently removed.')) return;
    await request(`/${encodeURIComponent(run.run_id)}?expected_version=${run.version || 0}`, { method: 'DELETE' });
    status.textContent = 'Notepad cleared.';
    await loadRuns();
  }
  dialog.addEventListener('cancel', () => { dialog.close(); });
  dialog.addEventListener('click', async event => {
    const action = event.target.closest('button')?.dataset.action;
    if (!action) return;
    try {
      if (action === 'close') { dialog.close(); return; }
      if (action === 'refresh') { preferredRun = select.value; await loadRuns(); }
      if (action === 'clear') await clearNotepad();
      if (!run && !['close', 'refresh', 'clear'].includes(action)) throw new Error('Select a run first.');
      if (action === 'print') {
        const popup = window.open('', '_blank');
        if (!popup) throw new Error('The browser blocked the print window. Allow pop-ups and try again.');
        popup.document.open();
        popup.document.write(fullHtml());
        popup.document.close();
        popup.focus();
        popup.print();
      }
      if (action === 'note') {
        const title = dialog.querySelector('[data-role="title"]');
        const note = dialog.querySelector('[data-role="note"]');
        if (!title.value.trim() || !note.value.trim()) throw new Error('Enter a title and note.');
        await request(`/${encodeURIComponent(run.run_id)}/notes`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ title: title.value.trim(), content: note.value.trim() }),
        });
        title.value = ''; note.value = '';
        await loadRun();
      }
    } catch (error) {
      if (error.name !== 'AbortError') status.textContent = `Scratchpad: ${error.message}`;
    }
  });
  select.addEventListener('change', () => {
    loadRun().catch(error => { status.textContent = `Could not load scratchpad: ${error.message}`; });
  });
  author.addEventListener('change', render);
  kind.addEventListener('change', render);
  dialog.showModal();
  try { await loadRuns(); } catch (error) { status.textContent = `Could not load scratchpads: ${error.message}`; }
}
