/**
 * Scratchpad store switcher: a dropdown that lets every scratchpad dialog
 * show the entries from any of the three stores (Chat, Code, Squad),
 * plus a common Clear button that works on whichever store is visible.
 *
 * Usage:
 *   const sw = attachScratchpadSwitcher({
 *     toolbar,          // element to prepend the <select> into
 *     content,          // the content container that shows entries
 *     nativeControls,   // elements to hide when viewing a foreign store
 *     currentStore,     // 'chat' | 'code' | 'squad'
 *     onSwitch(store),  // called when leaving the native store
 *     onRestore(),      // called when returning to the native store
 *     onNativeClear(),  // called after native store is cleared
 *   });
 *   sw.destroy();       // remove the switcher
 */

import { Api } from './api.js';
import { appendMarkdown } from './markdown.js';

const STORES = [
  { id: 'chat', label: 'Chat scratchpad' },
  { id: 'code', label: 'Code scratchpad' },
  { id: 'squad', label: 'Squad scratchpad' },
];

/* ── Unified entry renderer ────────────────────────────────────── */

function renderEntries(container, entries) {
  container.replaceChildren();
  if (!entries.length) {
    const empty = document.createElement('p');
    empty.className = 'text-muted small';
    empty.textContent = 'This scratchpad is empty.';
    container.appendChild(empty);
    return;
  }
  for (const entry of entries) {
    const card = document.createElement('details');
    card.className = 'codebuilder-scratchpad-entry';
    card.dataset.kind = entry.kind || 'note';
    card.dataset.readerItem = '';
    card.open = true;

    const heading = document.createElement('summary');
    heading.textContent = [entry.author, entry.kind, entry.title].filter(Boolean).join(' · ');

    const meta = document.createElement('p');
    meta.className = 'text-muted small';
    const parts = [];
    if (entry.created_at) parts.push(new Date(entry.created_at).toLocaleString());
    if (entry.truncated) parts.push('content truncated');
    meta.textContent = parts.join(' · ');

    const body = document.createElement('div');
    body.className = 'prompt-rich-markdown';
    appendMarkdown(body, String(entry.content || ''));

    card.append(heading, meta, body);
    container.appendChild(card);
  }
}

/* ── Normalize agent (old) scratchpad → flat entries ───────────── */

function normalizeAgentScratchpad(memory) {
  const entries = [];
  const history = Array.isArray(memory.subagent_history) ? memory.subagent_history : [];
  const latest = memory.latest_subagent_result;
  if (latest && typeof latest === 'object' && Object.keys(latest).length
    && !history.some(e => e.updated_at === latest.updated_at)) {
    history.push(latest);
  }
  for (const entry of history) {
    const payload = entry.payload && typeof entry.payload === 'object' ? entry.payload : {};
    entries.push({
      author: entry.subagent_id || 'unknown',
      kind: 'result',
      title: entry.task_type || 'sub-agent result',
      content: payload.result || JSON.stringify(payload, null, 2),
      created_at: entry.updated_at || '',
    });
  }
  const reads = Array.isArray(memory.read_history)
    ? memory.read_history
    : (memory.latest_read ? [memory.latest_read] : []);
  for (const entry of reads) {
    entries.push({
      author: 'read_file',
      kind: 'read',
      title: entry.path || 'unknown file',
      content: entry.content || '',
      created_at: entry.updated_at || '',
    });
  }
  if (memory.latest_map?.tree) {
    entries.push({
      author: 'map_files',
      kind: 'map',
      title: memory.latest_map.requested_path || 'workspace root',
      content: memory.latest_map.tree?.name || '(workspace map)',
      created_at: '',
    });
  }
  return entries;
}

/* ── Foreign store controls ────────────────────────────────────── */

function createSquadControls(container) {
  const wrapper = document.createElement('div');
  wrapper.className = 'scratchpad-switcher-sub';
  wrapper.dataset.swForeign = '';
  const label = document.createElement('label');
  label.className = 'small';
  label.textContent = 'Run ';
  const runSelect = document.createElement('select');
  runSelect.className = 'form-select';
  runSelect.setAttribute('aria-label', 'Select squad run');
  label.appendChild(runSelect);
  wrapper.appendChild(label);

  const load = async () => {
    container.replaceChildren();
    container.textContent = 'Loading…';
    const { runs } = await Api.getSquadScratchpadRuns();
    runSelect.replaceChildren();
    if (!runs.length) {
      container.replaceChildren();
      container.textContent = 'No squad scratchpads are available.';
      return;
    }
    for (const run of runs) {
      runSelect.add(new Option(`${run.squad_id} · ${run.status} · ${run.run_id}`, run.run_id));
    }
    await loadRun();
  };
  const loadRun = async () => {
    container.replaceChildren();
    container.textContent = 'Loading run…';
    const run = await Api.getSquadScratchpadRun(runSelect.value);
    renderEntries(container, run.entries || []);
  };
  runSelect.addEventListener('change', () => loadRun().catch(err => {
    container.replaceChildren();
    container.textContent = `Error: ${err.message}`;
  }));

  return { element: wrapper, runSelect, load, loadRun, destroy: () => wrapper.remove() };
}

function createChatSessionControls(container) {
  const wrapper = document.createElement('div');
  wrapper.className = 'scratchpad-switcher-sub';
  wrapper.dataset.swForeign = '';
  const label = document.createElement('label');
  label.className = 'small';
  label.textContent = 'Session ';
  const sessionSelect = document.createElement('select');
  sessionSelect.className = 'form-select';
  sessionSelect.setAttribute('aria-label', 'Select chat session');
  label.appendChild(sessionSelect);
  wrapper.appendChild(label);

  const load = async () => {
    container.replaceChildren();
    container.textContent = 'Loading…';
    const { sessions } = await Api.listChatScratchpadSessions();
    sessionSelect.replaceChildren();
    if (!sessions.length) {
      container.replaceChildren();
      container.textContent = 'No chat sessions have scratchpad entries.';
      return;
    }
    for (const s of sessions) {
      sessionSelect.add(new Option(`${s.session_id} (${s.count} entries)`, s.session_id));
    }
    await loadSession();
  };
  const loadSession = async () => {
    container.replaceChildren();
    container.textContent = 'Loading session…';
    const { entries } = await Api.getChatScratchpad(sessionSelect.value);
    renderEntries(container, entries);
  };
  sessionSelect.addEventListener('change', () => loadSession().catch(err => {
    container.replaceChildren();
    container.textContent = `Error: ${err.message}`;
  }));

  return { element: wrapper, sessionSelect, load, destroy: () => wrapper.remove() };
}

/* ── Clear helpers per store ──────────────────────────────────── */

async function clearStore(storeId, foreignCtrl) {
  if (storeId === 'chat') {
    await Api.clearAgentScratchpad();
  } else if (storeId === 'code') {
    await Api.clearCodeScratchpad();
  } else if (storeId === 'squad') {
    if (!foreignCtrl?.runSelect?.value) throw new Error('No squad run selected.');
    const runId = foreignCtrl.runSelect.value;
    const res = await fetch(`/api/agent-memory/squad-scratchpads/${encodeURIComponent(runId)}`, { method: 'DELETE' });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || `Clear failed (${res.status})`);
    }
  }
}

/* ── Main export ───────────────────────────────────────────────── */

/**
 * Add a store-switcher dropdown and a clear button to an existing scratchpad dialog.
 *
 * @param {Object} options
 * @param {HTMLElement}  options.toolbar        - Element to prepend the dropdown into
 * @param {HTMLElement}  options.content        - Content area for entries
 * @param {HTMLElement[]} options.nativeControls - Elements to hide when viewing a foreign store
 * @param {string}       options.currentStore   - 'chat' | 'code' | 'squad'
 * @param {Function}     [options.onSwitch]     - Called when leaving the native store
 * @param {Function}     [options.onRestore]    - Called when returning to the native store
 * @param {Function}     [options.onNativeClear] - Called after the native store is cleared
 */
export function attachScratchpadSwitcher({ toolbar, content, nativeControls = [], currentStore, onSwitch, onRestore, onNativeClear }) {
  const wrapper = document.createElement('div');
  wrapper.className = 'scratchpad-switcher-bar';

  const select = document.createElement('select');
  select.className = 'form-select scratchpad-store-select';
  select.setAttribute('aria-label', 'Switch scratchpad store');
  for (const store of STORES) {
    const option = new Option(store.label, store.id);
    if (store.id === currentStore) option.selected = true;
    select.add(option);
  }

  const clearBtn = document.createElement('button');
  clearBtn.type = 'button';
  clearBtn.className = 'cb-btn cb-btn-danger scratchpad-clear-btn';
  clearBtn.textContent = 'Clear';
  clearBtn.title = 'Clear the visible scratchpad';

  wrapper.append(select, clearBtn);

  let nativeContent = null;
  let foreignCtrl = null;
  let activeStore = currentStore;

  function hideNative() {
    if (!nativeContent) {
      nativeContent = [...content.childNodes];
      nativeContent.forEach(n => {
        if (n.nodeType === Node.ELEMENT_NODE) n.dataset._swHidden = n.hidden ? '1' : '';
      });
    }
    nativeContent.forEach(n => {
      if (n.nodeType === Node.ELEMENT_NODE) n.hidden = true;
      else if (n.parentNode === content) content.removeChild(n);
    });
    nativeControls.forEach(el => { el.dataset._swDisplay = el.style.display; el.style.display = 'none'; });
  }

  function restoreNative() {
    content.querySelectorAll('[data-sw-foreign]').forEach(n => n.remove());
    if (nativeContent) {
      nativeContent.forEach(n => {
        if (!n.isConnected) content.appendChild(n);
        if (n.nodeType === Node.ELEMENT_NODE) {
          n.hidden = n.dataset._swHidden === '1';
          delete n.dataset._swHidden;
        }
      });
      nativeContent = null;
    }
    nativeControls.forEach(el => { el.style.display = el.dataset._swDisplay || ''; delete el.dataset._swDisplay; });
    if (foreignCtrl) { foreignCtrl.destroy(); foreignCtrl = null; }
  }

  function foreignDiv() {
    let el = content.querySelector('[data-sw-foreign="entries"]');
    if (!el) {
      el = document.createElement('div');
      el.dataset.swForeign = 'entries';
      content.appendChild(el);
    }
    return el;
  }

  async function switchTo(storeId) {
    activeStore = storeId;
    if (storeId === currentStore) {
      restoreNative();
      onRestore?.();
      return;
    }
    hideNative();
    if (foreignCtrl) { foreignCtrl.destroy(); foreignCtrl = null; }
    const target = foreignDiv();
    target.replaceChildren();
    target.textContent = 'Loading…';
    onSwitch?.(storeId);

    try {
      if (storeId === 'chat') {
        const memory = await Api.getAgentScratchpad();
        const entries = normalizeAgentScratchpad(memory);
        if (entries.length) {
          renderEntries(target, entries);
        } else {
          foreignCtrl = createChatSessionControls(target);
          content.insertBefore(foreignCtrl.element, target);
          await foreignCtrl.load();
        }
      } else if (storeId === 'code') {
        const data = await Api.getCodeScratchpad({ limit: 100 });
        renderEntries(target, data.entries || []);
      } else if (storeId === 'squad') {
        foreignCtrl = createSquadControls(target);
        content.insertBefore(foreignCtrl.element, target);
        await foreignCtrl.load();
      }
    } catch (err) {
      target.replaceChildren();
      target.textContent = `Could not load scratchpad: ${err.message}`;
    }
  }

  async function handleClear() {
    const storeName = STORES.find(s => s.id === activeStore)?.label || activeStore;
    if (activeStore === currentStore) {
      if (onNativeClear) {
        await onNativeClear();
      } else {
        if (!confirm(`Clear the ${storeName}? This cannot be undone.`)) return;
        clearBtn.disabled = true;
        try { await clearStore(activeStore, null); } finally { clearBtn.disabled = false; }
      }
      return;
    }
    if (!confirm(`Clear the ${storeName}? This cannot be undone.`)) return;
    clearBtn.disabled = true;
    try {
      await clearStore(activeStore, foreignCtrl);
      await switchTo(activeStore);
    } catch (err) {
      const target = content.querySelector('[data-sw-foreign="entries"]');
      if (target) target.textContent = `Could not clear: ${err.message}`;
    } finally {
      clearBtn.disabled = false;
    }
  }

  select.addEventListener('change', () => switchTo(select.value));
  clearBtn.addEventListener('click', handleClear);
  toolbar.prepend(wrapper);

  return {
    element: wrapper,
    destroy() {
      restoreNative();
      wrapper.remove();
    },
  };
}
