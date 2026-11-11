/**
 * Workspace view: manage squad folders and link to squad workflows.
 */
import { Api } from '../api.js';
import { AppState, esc, navigate, refreshSquads } from '../shell.js';

export function renderWorkspaceView(container) {
  const squads = AppState.squads || [];

  container.innerHTML = `
    <div class="sq-page">
      <header class="sq-page-head">
        <div>
          <h2>Squad folders</h2>
          <p class="text-muted">Create and organize the folders used by the squad workspace.</p>
        </div>
        <button class="btn btn-primary" type="button" data-action="show-create">New folder</button>
      </header>
      <form class="card hidden" data-role="create-form">
        <label class="small" for="workspace-squad-name">Folder name</label>
        <input id="workspace-squad-name" class="form-input" name="name" maxlength="64"
          placeholder="e.g. Research" required>
        <label class="small" for="workspace-squad-description">Description</label>
        <input id="workspace-squad-description" class="form-input" name="description" maxlength="500">
        <p class="small text-danger" data-role="error" role="alert"></p>
        <div class="toolbar-row">
          <button class="btn btn-primary" type="submit">Create folder</button>
          <button class="btn" type="button" data-action="cancel-create">Cancel</button>
        </div>
      </form>
      <section class="card">
        <ul class="squad-list" data-role="squad-list">
          ${squads.map(squad => `
            <li class="squad-item">
              <div>
                <strong>${esc(squad.name)}</strong>
                <span class="text-muted">${esc(squad.description || squad.path)}</span>
              </div>
              <div class="toolbar-row">
                <button class="btn btn-sm" type="button" data-open-squads>Open squads</button>
                <button class="btn btn-sm btn-danger" type="button" data-delete-squad="${esc(squad.name)}">Delete</button>
              </div>
            </li>`).join('')
            || '<li class="text-muted small">No squad folders yet.</li>'}
        </ul>
        <p class="small" data-role="status" role="status" aria-live="polite"></p>
      </section>
    </div>`;

  const form = container.querySelector('[data-role="create-form"]');
  const status = container.querySelector('[data-role="status"]');
  container.querySelector('[data-action="show-create"]').addEventListener('click', () => {
    form.classList.remove('hidden');
    form.querySelector('[name="name"]').focus();
  });
  container.querySelector('[data-action="cancel-create"]').addEventListener('click', () => {
    form.classList.add('hidden');
    form.reset();
  });
  form.addEventListener('submit', async event => {
    event.preventDefault();
    const data = new FormData(form);
    const submit = form.querySelector('[type="submit"]');
    submit.disabled = true;
    try {
      await Api.createSquad(String(data.get('name')).trim(), String(data.get('description')).trim());
      await refreshSquads();
      renderWorkspaceView(container);
    } catch (error) {
      form.querySelector('[data-role="error"]').textContent = error.message;
    } finally {
      submit.disabled = false;
    }
  });

  container.querySelector('[data-role="squad-list"]').addEventListener('click', async event => {
    const openButton = event.target.closest('[data-open-squads]');
    if (openButton) {
      navigate('squads');
      return;
    }
    const deleteButton = event.target.closest('[data-delete-squad]');
    if (!deleteButton) return;
    const name = deleteButton.dataset.deleteSquad;
    if (!confirm(`Delete squad folder "${name}"? This cannot be undone.`)) return;
    deleteButton.disabled = true;
    try {
      await Api.deleteSquad(name);
      await refreshSquads();
      renderWorkspaceView(container);
    } catch (error) {
      status.textContent = `Could not delete squad folder: ${error.message}`;
      status.className = 'small text-danger';
      deleteButton.disabled = false;
    }
  });
}
