/**
 * Shared app-shell state and navigation. Views import from here instead of app.js so the
 * router and its views never import each other.
 */
import { Api } from './api.js';

export const AppState = {
  activeTab: 'squads',
  agents: [],
  squads: [],
  health: null,
  currentPath: '',
  ragScanJob: null
};

export function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[c]));
}

/** Ask the shell to open another section; app.js owns the actual routing. */
export function navigate(tab, payload = null) {
  window.dispatchEvent(new CustomEvent('novous:navigate', { detail: { tab, payload } }));
}

export function openInEditor(path) {
  navigate('editor', { path, managedFile: false });
}

export async function refreshAgents() {
  const data = await Api.getAgents();
  AppState.agents = data.agents || [];
  return AppState.agents;
}

export async function refreshSquads() {
  const data = await Api.getSquads();
  AppState.squads = data.squads || [];
  return AppState.squads;
}

export function updateHealthBadge(health) {
  const badge = document.getElementById('health-badge');
  if (!badge || !health) return;
  const ok = health.ollama?.reachable;
  badge.className = 'badge ' + (ok ? 'badge-success' : 'badge-warning');
  badge.textContent = ok ? 'Engine Ready' : 'Ollama Offline';
}
