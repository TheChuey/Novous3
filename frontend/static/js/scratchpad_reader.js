/**
 * Reader controls shared by the Agent and Code scratchpads: adjustable text size,
 * find, expand/collapse, a pop-out window, and saving as Markdown or a standalone HTML page.
 *
 * Content opts in by marking each searchable block with `data-reader-item`.
 */
import { Api } from './api.js';
import { iconSvg } from './icons.js';

const SIZE_STEP = 0.1;
const SIZE_MIN = 0.7;
const SIZE_MAX = 1.6;
const SIZE_DEFAULT = 0.9;

function readStoredSize(key) {
  try {
    const value = Number(localStorage.getItem(key));
    return value >= SIZE_MIN && value <= SIZE_MAX ? value : SIZE_DEFAULT;
  } catch {
    return SIZE_DEFAULT;
  }
}

function storeSize(key, value) {
  try { localStorage.setItem(key, String(value)); } catch { /* storage unavailable */ }
}

function button(label, title, icon) {
  const element = document.createElement('button');
  element.type = 'button';
  element.className = 'cb-btn';
  element.title = title;
  element.setAttribute('aria-label', title);
  element.innerHTML = icon ? `${iconSvg(icon)}<span></span>` : '<span></span>';
  element.querySelector('span').textContent = label;
  return element;
}

/** A copy of the content without app-only controls, every entry visible. */
function staticCopy(content) {
  const copy = content.cloneNode(true);
  copy.removeAttribute('id');
  copy.querySelectorAll('.md-code-actions, iframe, .reader-empty-filter').forEach(node => node.remove());
  copy.querySelectorAll('[hidden]').forEach(node => node.removeAttribute('hidden'));
  // Labels (section shortcuts, map file names) stay as text; other controls are dropped.
  copy.querySelectorAll('button').forEach(node => {
    if (!node.matches('.reader-nav button, .scratchpad-file-link')) {
      node.remove();
      return;
    }
    const span = document.createElement('span');
    span.className = node.className;
    span.textContent = node.textContent;
    if (node.title) span.title = node.title;
    node.replaceWith(span);
  });
  copy.querySelectorAll('[id]').forEach(node => node.removeAttribute('id'));
  return copy;
}

const STANDALONE_CSS = `
:root { color-scheme: dark; --bg: #0d0e11; --card: #1b1d23; --elev: #252931; --text: #e6eaf2;
  --muted: #aab3c5; --border: #3a3f4a; --primary: #7cb2ff; --accent: #a78bfa; --success: #4ade80;
  --warning: #fbbf24; --danger: #f87171; }
@media (prefers-color-scheme: light) {
  :root { color-scheme: light; --bg: #f4f6f9; --card: #ffffff; --elev: #eef1f6; --text: #1c2129;
    --muted: #4b5563; --border: #d0d6df; --primary: #1d4ed8; --accent: #6d28d9; --success: #166534;
    --warning: #92400e; --danger: #b91c1c; }
}
body { line-height: 1.6; }
h3 { padding-bottom: 0.35rem; border-bottom: 1px solid var(--border); }
.scratchpad-read .scratchpad-read { background: var(--elev); margin: 0.5rem 0 0; }
.scratchpad-read summary { line-height: 1.5; overflow-wrap: anywhere; }
.scratchpad-read[open] > summary { margin-bottom: 0.5rem; }
.scratchpad-result-query { padding: 0.4rem 0.6rem; background: var(--elev); border-radius: 6px; }
.codebuilder-scratchpad-entry { font-size: 0.85rem; border-left-width: 4px; }
.codebuilder-scratchpad-entry-meta { color: var(--muted); font-size: 0.8em; }
.codebuilder-scratchpad-entry-title { font-size: 0.95em; margin: 0.2rem 0 0.35rem; }
.codebuilder-scratchpad-kind { padding: 0.05rem 0.55rem; border: 0; font-size: 0.85em; font-weight: 700; color: #0b0c0f; background: var(--kind-color, var(--accent)); }
.codebuilder-scratchpad-entry[data-kind="code"] { --kind-color: #4ade80; }
.codebuilder-scratchpad-entry[data-kind="test"] { --kind-color: #fbbf24; }
.codebuilder-scratchpad-entry[data-kind="fix"] { --kind-color: #f87171; }
.codebuilder-scratchpad-entry[data-kind="note"] { --kind-color: #94a3b8; }
@media (prefers-color-scheme: light) {
  .codebuilder-scratchpad-entry[data-kind="code"] { --kind-color: #86efac; }
  .codebuilder-scratchpad-entry[data-kind="test"] { --kind-color: #fcd34d; }
  .codebuilder-scratchpad-entry[data-kind="fix"] { --kind-color: #fca5a5; }
  .codebuilder-scratchpad-entry[data-kind="note"] { --kind-color: #cbd5e1; }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text);
  font: 1rem/1.55 -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }
header { position: sticky; top: 0; z-index: 1; display: flex; flex-wrap: wrap; gap: 0.5rem; align-items: center;
  justify-content: space-between; padding: 0.75rem 1rem; background: var(--card); border-bottom: 1px solid var(--border); }
header h1 { margin: 0; font-size: 1.1rem; }
header p { margin: 0; color: var(--muted); font-size: 0.85rem; }
.tools { display: flex; flex-wrap: wrap; gap: 0.35rem; }
.tools button { padding: 0.3rem 0.65rem; color: var(--text); background: var(--elev); border: 1px solid var(--border);
  border-radius: 6px; font: inherit; font-size: 0.85rem; cursor: pointer; }
main { max-width: 1100px; margin: 0 auto; padding: 1rem; font-size: var(--reader-size, 0.95rem); }
main > div { display: grid; gap: 1rem; }
section { min-width: 0; }
h2, h3, h4 { margin: 0.4rem 0 0.5rem; line-height: 1.3; }
details > summary { cursor: pointer; font-weight: 600; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; padding: 0.7rem; margin: 0.5rem 0; background: var(--bg);
  border: 1px solid var(--border); border-radius: 6px; font: 0.9em/1.5 Consolas, 'Cascadia Mono', monospace; }
code { font-family: Consolas, 'Cascadia Mono', monospace; font-size: 0.92em; }
table { border-collapse: collapse; } th, td { padding: 0.3rem 0.5rem; border: 1px solid var(--border); }
.text-muted { color: var(--muted); } .text-danger { color: var(--danger); } .text-warning { color: var(--warning); }
.small { font-size: 0.85em; }
.badge { display: inline-block; padding: 0 0.45rem; border: 1px solid currentColor; border-radius: 999px; font-size: 0.8em; font-weight: 600; }
.badge-success { color: var(--success); } .badge-danger { color: var(--danger); } .badge-warning { color: var(--warning); }
.scratchpad-read, .codebuilder-scratchpad-entry { padding: 0.65rem 0.8rem; margin-bottom: 0.5rem; background: var(--card);
  border: 1px solid var(--border); border-radius: 6px; }
.scratchpad-result { background: var(--elev); }
.codebuilder-scratchpad-entry { border-left: 3px solid var(--accent); }
.codebuilder-scratchpad-entry[data-kind="code"] { border-left-color: var(--success); }
.codebuilder-scratchpad-entry[data-kind="test"] { border-left-color: var(--warning); }
.codebuilder-scratchpad-entry[data-kind="fix"] { border-left-color: var(--danger); }
.codebuilder-scratchpad-entry[data-kind="note"] { border-left-color: var(--muted); }
.codebuilder-scratchpad-entry-meta { display: flex; flex-wrap: wrap; gap: 0.5rem; align-items: center; font-size: 0.85em; }
.codebuilder-scratchpad-kind { padding: 0 0.4rem; border: 1px solid currentColor; border-radius: 999px; font-weight: 600; }
.reader-nav { display: flex; flex-wrap: wrap; gap: 0.4rem; }
.reader-nav a, .reader-nav span { padding: 0.15rem 0.6rem; color: var(--primary); border: 1px solid var(--border);
  border-radius: 999px; text-decoration: none; font-size: 0.85em; }
.scratchpad-tree-list { list-style: none; padding-left: 1.15rem; margin: 0.2rem 0; }
.scratchpad-file-link { color: var(--primary); }
.md-code-block { margin: 0.5rem 0; } .md-code-header { color: var(--muted); font-size: 0.8em; }
`;

const STANDALONE_SCRIPT = `
const main = document.querySelector('main');
let size = Number(getComputedStyle(main).getPropertyValue('--reader-size')) || 0.95;
const setSize = value => { size = Math.min(1.8, Math.max(0.7, value)); main.style.setProperty('--reader-size', size + 'rem'); };
document.querySelector('[data-smaller]').onclick = () => setSize(size - 0.1);
document.querySelector('[data-larger]').onclick = () => setSize(size + 0.1);
document.querySelector('[data-expand]').onclick = () => main.querySelectorAll('details').forEach(d => { d.open = true; });
document.querySelector('[data-collapse]').onclick = () => main.querySelectorAll('details').forEach(d => { d.open = false; });
`;

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[c]));
}

/** A self-contained page (styles and its own size/expand controls inline) of the current content. */
export function buildStandaloneHtml(title, content, sizeRem) {
  const exported = new Date().toLocaleString();
  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${escapeHtml(title)}</title>
<style>${STANDALONE_CSS}</style>
</head>
<body>
<header>
  <div><h1>${escapeHtml(title)}</h1><p>Exported ${escapeHtml(exported)}</p></div>
  <div class="tools">
    <button type="button" data-smaller title="Smaller text">A−</button>
    <button type="button" data-larger title="Larger text">A+</button>
    <button type="button" data-expand>Expand all</button>
    <button type="button" data-collapse>Collapse all</button>
  </div>
</header>
<main style="--reader-size: ${sizeRem}rem">${staticCopy(content).outerHTML}</main>
<script>${STANDALONE_SCRIPT}</script>
</body>
</html>`;
}

/**
 * Attach reader controls to ``toolbar`` for the scratchpad rendered into ``content``.
 * ``getMarkdown`` returns (or resolves to) the Markdown export; ``filenameBase`` names saved files.
 * Call ``refresh()`` on the returned controller after re-rendering the content.
 */
export function setupScratchpadReader({ content, toolbar, prefsKey, title, getMarkdown, filenameBase, onError }) {
  const sizeKey = `${prefsKey}readerSize`;
  let size = readStoredSize(sizeKey);
  content.classList.add('scratchpad-reader');
  const applySize = () => content.style.setProperty('--reader-size', `${size.toFixed(2)}rem`);
  applySize();

  const sizeGroup = document.createElement('div');
  sizeGroup.className = 'cb-group';
  sizeGroup.setAttribute('role', 'group');
  sizeGroup.setAttribute('aria-label', 'Scratchpad text size');
  const smaller = button('A−', 'Smaller text');
  const larger = button('A+', 'Larger text');
  sizeGroup.append(smaller, larger);
  const resize = delta => {
    size = Math.min(SIZE_MAX, Math.max(SIZE_MIN, Math.round((size + delta) * 100) / 100));
    applySize();
    storeSize(sizeKey, size);
  };
  smaller.addEventListener('click', () => resize(-SIZE_STEP));
  larger.addEventListener('click', () => resize(SIZE_STEP));

  const find = document.createElement('input');
  find.type = 'search';
  find.className = 'form-input reader-find';
  find.placeholder = 'Find…';
  find.setAttribute('aria-label', 'Find in scratchpad');
  const findCount = document.createElement('span');
  findCount.className = 'small text-muted reader-find-count';
  findCount.setAttribute('aria-live', 'polite');

  const expand = button('Expand all', 'Expand every entry');
  const collapse = button('Collapse all', 'Collapse every entry');
  const setOpen = open => content.querySelectorAll('details').forEach(details => { details.open = open; });
  expand.addEventListener('click', () => setOpen(true));
  collapse.addEventListener('click', () => setOpen(false));

  const popOut = button('Pop out', 'Open in a separate, resizable window', 'file-code');
  const saveMd = button('Save .md', 'Save as a Markdown file', 'save');
  const saveHtml = button('Save .html', 'Save as a web page you can open in any browser', 'save-as');

  const stamp = () => new Date().toISOString().slice(0, 10);
  const report = error => {
    if (error?.name === 'AbortError') return;
    onError?.(error);
  };
  popOut.addEventListener('click', () => {
    const html = buildStandaloneHtml(title, content, size);
    const url = URL.createObjectURL(new Blob([html], { type: 'text/html;charset=utf-8' }));
    const opened = window.open(url, '_blank', 'popup,width=1000,height=800');
    if (!opened) report(new Error('The browser blocked the pop-out window. Allow pop-ups for this site and try again.'));
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
  });
  saveMd.addEventListener('click', async () => {
    saveMd.disabled = true;
    try {
      await Api.saveTextFile(await getMarkdown(), `${filenameBase}-${stamp()}.md`, 'md');
    } catch (error) {
      report(error);
    } finally {
      saveMd.disabled = false;
    }
  });
  saveHtml.addEventListener('click', async () => {
    saveHtml.disabled = true;
    try {
      await Api.saveTextFile(buildStandaloneHtml(title, content, size), `${filenameBase}-${stamp()}.html`, 'html');
    } catch (error) {
      report(error);
    } finally {
      saveHtml.disabled = false;
    }
  });

  const applyFilter = () => {
    const query = find.value.trim().toLowerCase();
    const items = [...content.querySelectorAll('[data-reader-item]')];
    let shown = 0;
    items.forEach(item => {
      const match = !query || item.textContent.toLowerCase().includes(query);
      item.hidden = !match;
      if (match) {
        shown += 1;
        if (query) {
          if (item.tagName === 'DETAILS') item.open = true;
          item.querySelectorAll('details').forEach(details => { details.open = true; });
        }
      }
    });
    content.querySelector('.reader-empty-filter')?.remove();
    findCount.textContent = query ? `${shown} of ${items.length}` : '';
    if (query && !shown) {
      const empty = document.createElement('p');
      empty.className = 'text-muted reader-empty-filter';
      empty.textContent = `Nothing matches "${find.value.trim()}".`;
      content.prepend(empty);
    }
  };
  find.addEventListener('input', applyFilter);

  toolbar.classList.add('reader-toolbar');
  toolbar.append(sizeGroup, find, findCount, expand, collapse, popOut, saveMd, saveHtml);
  return { refresh: applyFilter };
}
