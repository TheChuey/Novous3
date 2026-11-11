import { Api } from './api.js';
import { renderEditorView } from './editor.js';
import { renderChatView } from './chat.js';
import { renderPromptCreationView } from './prompt_creation.js';
import { renderCodeBuilderView } from './codebuilder/codebuilder.js';
import { AppState, esc, refreshAgents, refreshSquads, updateHealthBadge } from './shell.js';
import { renderWorkspaceView } from './views/workspace_view.js';
import { renderFileManagerView } from './views/file_manager_view.js';
import { renderKnowledgeView } from './views/knowledge_view.js';
import { renderSquadsView } from './squads/squads_view.js';
import { renderAgentsView } from './squads/agents_view.js';
import { renderToolsView } from './squads/tools_view.js';
import { renderRunsView, stopRunStream } from './squads/runs_view.js';
import { renderModelsView } from './views/models_view.js';

export { AppState };

const views = {
  squads: renderSquadsView,
  agents: renderAgentsView,
  tools: renderToolsView,
  models: renderModelsView,
  runs: renderRunsView,
  dashboard: renderWorkspaceView,
  files: renderFileManagerView,
  knowledge: renderKnowledgeView,
  editor: renderEditorView,
  codebuilder: renderCodeBuilderView,
  chat: renderChatView,
  'prompt-creation': renderPromptCreationView
};

// Old links (?tab=testing, the former Prompt Testing page) land on their new section.
const TAB_ALIASES = { testing: 'tools' };

export function switchTab(tab, payload = null) {
  tab = TAB_ALIASES[tab] || tab;
  if (!Object.prototype.hasOwnProperty.call(views, tab)) tab = 'squads';
  if (tab !== 'runs') stopRunStream();
  AppState.activeTab = tab;
  document.querySelectorAll('.nav-btn').forEach(b =>
    b.classList.toggle('active', b.dataset.tab === tab));
  const container = document.getElementById('view-container');
  if (!container) return;
  container.replaceChildren();
  views[tab](container, payload);
}

window.addEventListener('novous:navigate', event => {
  switchTab(event.detail.tab, event.detail.payload);
});

window.addEventListener('novous:open-editor', event => {
  const file = event.detail;
  if (file && typeof file.path === 'string') {
    switchTab('editor', { path: file.path, managedFile: file.managedFile === true });
  }
});

async function boot() {
  document.querySelectorAll('.nav-btn').forEach(btn => {
    btn.addEventListener('click', () => switchTab(btn.dataset.tab));
  });

  try {
    await Promise.all([refreshAgents(), refreshSquads()]);
  } catch (err) {
    document.getElementById('view-container').innerHTML =
      `<div class="error-panel">Failed to load workspace: ${esc(err.message)}</div>`;
    return;
  }

  try {
    const h = await Api.getHealth();
    AppState.health = h;
    updateHealthBadge(h);
  } catch (_) { /* badge stays default */ }

  switchTab(new URLSearchParams(window.location.search).get('tab') || 'squads');
}

boot();
