/**
 * Overview: the agent library (every agent, searchable and grouped), the workspace tree,
 * squad folders, workspace folders and knowledge sources.
 */
import { Api } from '../api.js';
import { renderTree } from '../tree.js';
import { ragWarningNodes } from '../rag_warnings.js';
import { AppState, esc, navigate, refreshProject } from '../shell.js';
import { renderAgentLibrary } from './agent_library.js';

export function renderWorkspaceView(container) {
  const project = AppState.project || {};
  const squads = project.squads || [];

  container.innerHTML = `
    <div class="ov-page">
      <header class="sq-page-head">
        <div>
          <h2>Overview</h2>
          <p class="text-muted">Agents, files and knowledge sources in this workspace.</p>
        </div>
      </header>

      <div class="ov-layout">
        <section class="card ov-agents" data-role="agent-library" aria-label="Agent library"></section>

        <aside class="ov-side">
          <section class="card dash-tree-card">
            <div class="card-head">
              <div>
                <h3>Workspace tree</h3>
                <p class="text-muted small">Pick a workspace folder; click a file to open it in the editor.</p>
              </div>
            </div>
            <div id="dash-tree" class="tree-pane tree-pane-sm"></div>
          </section>

          <section class="card dash-squad-card">
            <div class="card-head">
              <h3>Squad folders</h3>
              <button class="cb-btn" id="dash-new-squad">New folder</button>
            </div>
            <div class="form-row">
              <input id="sq-name" class="form-input hidden" placeholder="squad folder name">
              <button class="cb-btn cb-btn-primary hidden" id="dash-create-squad">Create</button>
            </div>
            <ul class="squad-list" id="dash-squad-list">
              ${squads.map(s => `<li class="squad-item"><strong>${esc(s.name)}</strong>
                <span class="text-muted">${esc(s.description || s.path)}</span>
                <button class="btn btn-sm btn-ghost" data-squad-delete="${esc(s.name)}" aria-label="Delete ${esc(s.name)}">✕</button></li>`).join('')
                || '<li class="text-muted small">No squad folders yet.</li>'}
            </ul>
            <p class="text-muted small mt-2">Agent squads, patterns and runs live in
              <button type="button" class="sq-link" data-goto="squads">Squads</button>.</p>
          </section>
        </aside>
      </div>

      <div class="ov-settings">
        <section class="card root-management">
          <h3>Workspace folders</h3>
          <p class="text-muted small">Folders offered in the Workspace picker. Paste one absolute path per line.</p>
          <ul class="root-path-list" id="dash-root-list" aria-label="Configured Workspace folders"></ul>
          <label class="small" for="dash-root-paths">Folder paths</label>
          <textarea id="dash-root-paths" class="form-input root-path-input" rows="3"
            placeholder="E:\\Novous_notes&#10;E:\\Projects"></textarea>
          <div class="root-management-actions">
            <button class="cb-btn cb-btn-primary" type="button" data-root-action="add">Add folders</button>
            <button class="cb-btn cb-btn-danger" type="button" data-root-action="remove">Remove folders</button>
          </div>
          <p class="small" id="dash-root-status" role="status" aria-live="polite"></p>
        </section>

        <section class="card root-management">
          <h3>Knowledge sources</h3>
          <p class="text-muted small">Each named source links one folder to its own index. Deleting a branch removes its saved index, never the folder or its files.</p>
          <ul class="root-path-list rag-base-list" id="dash-rag-bases" aria-label="Saved knowledge sources"></ul>
          <div class="root-management-actions">
            <button class="cb-btn" type="button" data-rag-action="read">Index selected source</button>
            <button class="cb-btn" type="button" data-rag-action="pause">Pause</button>
            <button class="cb-btn cb-btn-danger" type="button" data-rag-action="stop">Stop indexing</button>
            <button class="cb-btn" type="button" data-rag-action="resume">Resume</button>
            <button class="cb-btn cb-btn-danger" type="button" data-rag-action="clear">Clear selected index</button>
          </div>
          <p class="small" id="dash-rag-job" role="status" aria-live="polite">Loading indexing status…</p>
          <div class="root-management-actions" id="dash-rag-error-actions" hidden>
            <span class="small" id="dash-rag-error-question"></span>
            <button class="cb-btn" type="button" data-rag-action="error-retry">Retry this file</button>
            <button class="cb-btn" type="button" data-rag-action="error-skip">Skip this file</button>
            <button class="cb-btn cb-btn-danger" type="button" data-rag-action="error-skip-all">Skip all remaining errors</button>
          </div>
          <p class="small" id="dash-rag-status" role="status" aria-live="polite"></p>
          <details class="ov-add-source">
            <summary>Add a knowledge source</summary>
            <label class="small" for="dash-rag-name">Source name</label>
            <input id="dash-rag-name" class="form-input" type="text" placeholder="Business decisions">
            <label class="small" for="dash-rag-path">Folder to search</label>
            <textarea id="dash-rag-path" class="form-input root-path-input" rows="2"
              placeholder="E:\\BUSN410\\Critical Thinking Strategies\\week 1"></textarea>
            <div class="root-management-actions">
              <button class="cb-btn cb-btn-primary" type="button" data-rag-action="add">Save knowledge source</button>
            </div>
          </details>
        </section>
      </div>
    </div>
  `;

  renderAgentLibrary(container.querySelector('[data-role="agent-library"]'));

  container.querySelector('#dash-new-squad').onclick = () => {
    container.querySelector('#sq-name').classList.toggle('hidden');
    container.querySelector('#dash-create-squad').classList.toggle('hidden');
  };
  container.querySelector('#dash-create-squad').onclick = async () => {
    const input = container.querySelector('#sq-name');
    try {
      await Api.createSquad(input.value.trim());
      await refreshProject();
      renderWorkspaceView(container);
    } catch (err) { alert(err.message); }
  };
  container.querySelector('#dash-squad-list').addEventListener('click', async (e) => {
    const name = e.target.dataset.squadDelete;
    if (!name) return;
    if (!confirm(`Delete squad "${name}"?`)) return;
    try { await Api.deleteSquad(name); await refreshProject(); renderWorkspaceView(container); }
    catch (err) { alert(err.message); }
  });

  container.querySelector('[data-goto="squads"]').addEventListener('click', () => navigate('squads'));

  const dashboardTree = renderTree(container.querySelector('#dash-tree'), {
    manageRoots: true,
    onSelect: path => navigate('editor', { path, managedFile: true })
  });

  const rootPathInput = container.querySelector('#dash-root-paths');
  const rootStatus = container.querySelector('#dash-root-status');
  const rootList = container.querySelector('#dash-root-list');
  const rootActionButtons = [...container.querySelectorAll('[data-root-action]')];
  async function refreshConfiguredRoots() {
    const config = await Api.getFileManagerRoots();
    const rootItem = (root, missing) => {
      const item = document.createElement('li');
      const name = document.createElement('strong');
      const path = document.createElement('code');
      name.textContent = root.name;
      path.textContent = root.path;
      item.append(name, path);
      if (missing) {
        // A folder that no longer exists on disk: flag it and offer a one-click removal.
        item.classList.add('root-missing');
        const badge = document.createElement('span');
        badge.className = 'badge badge-danger';
        badge.textContent = 'missing';
        const remove = document.createElement('button');
        remove.type = 'button';
        remove.className = 'cb-btn cb-btn-danger';
        remove.textContent = 'Remove';
        remove.dataset.removeRoot = root.path;
        item.append(badge, remove);
      }
      return item;
    };
    rootList.replaceChildren(
      ...config.roots.map(root => rootItem(root, false)),
      ...(config.missing || []).map(root => rootItem(root, true)),
    );
  }
  rootList.addEventListener('click', async event => {
    const path = event.target.dataset.removeRoot;
    if (!path) return;
    event.target.disabled = true;
    try {
      await Api.manageFileManagerRoots('remove', [path]);
      rootStatus.textContent = `Removed missing folder ${path}.`;
      rootStatus.className = 'small text-success';
      await refreshConfiguredRoots();
      await dashboardTree.reload();
    } catch (err) {
      rootStatus.textContent = `Could not update Workspace folders: ${err.message}`;
      rootStatus.className = 'small text-danger';
      event.target.disabled = false;
    }
  });
  refreshConfiguredRoots().catch(err => {
    rootStatus.textContent = `Could not load Workspace folders: ${err.message}`;
    rootStatus.className = 'small text-danger';
  });
  rootActionButtons.forEach(button => {
    button.addEventListener('click', async () => {
      const paths = rootPathInput.value.split(/\r?\n/).map(path => path.trim()).filter(Boolean);
      if (!paths.length) {
        rootStatus.textContent = 'Paste at least one absolute folder path.';
        rootStatus.className = 'small text-danger';
        rootPathInput.focus();
        return;
      }
      rootActionButtons.forEach(actionButton => { actionButton.disabled = true; });
      rootStatus.textContent = 'Updating Workspace folders…';
      rootStatus.className = 'small text-muted';
      try {
        const result = await Api.manageFileManagerRoots(button.dataset.rootAction, paths);
        const changed = button.dataset.rootAction === 'add' ? result.added : result.removed;
        rootStatus.textContent = `${button.dataset.rootAction === 'add' ? 'Added' : 'Removed'} ${changed.length} folder(s).`;
        rootStatus.className = 'small text-success';
        await refreshConfiguredRoots();
        await dashboardTree.reload();
      } catch (err) {
        rootStatus.textContent = `Could not update Workspace folders: ${err.message}`;
        rootStatus.className = 'small text-danger';
      } finally {
        rootActionButtons.forEach(actionButton => { actionButton.disabled = false; });
      }
    });
  });

  const ragPathInput = container.querySelector('#dash-rag-path');
  const ragNameInput = container.querySelector('#dash-rag-name');
  const ragStatus = container.querySelector('#dash-rag-status');
  const ragJobStatus = container.querySelector('#dash-rag-job');
  const ragBaseList = container.querySelector('#dash-rag-bases');
  const ragButtons = [...container.querySelectorAll('[data-rag-action]')];
  let ragBases = [];
  let selectedRagBaseId = null;

  function showRagStatus(message, kind = 'muted') {
    ragStatus.textContent = message;
    ragStatus.className = `small text-${kind}`;
  }

  function describeRag(status) {
    return `${status.documents} document(s), ${status.chunks} chunk(s).`;
  }

  function renderRagBases() {
    ragBaseList.replaceChildren();
    ragBases.forEach(base => {
      const item = document.createElement('li');
      item.className = 'rag-base-item';
      item.dataset.baseId = base.id;
      const heading = document.createElement('strong');
      heading.textContent = `${base.name}${base.active ? '' : ' (archived)'}`;
      const path = document.createElement('button');
      path.type = 'button';
      path.className = 'rag-folder-link';
      path.setAttribute('aria-label', `Open folder in File Explorer: ${base.path}`);
      path.title = base.path;
      const pathCode = document.createElement('code');
      pathCode.textContent = base.path;
      path.appendChild(pathCode);
      path.disabled = !base.exists;
      path.addEventListener('click', async () => {
        try {
          await Api.openRagBaseFolder(base.id);
          showRagStatus(`Opened folder for "${base.name}" in File Explorer.`, 'success');
        } catch (error) {
          showRagStatus(`Could not open folder: ${error.message}`, 'danger');
        }
      });
      const detail = document.createElement('span');
      detail.className = 'small text-muted';
      detail.textContent = `${base.selected ? 'Selected · ' : ''}${base.exists ? 'Folder available' : 'Folder unavailable'}${base.cloud_provider ? ` · ${base.cloud_provider}` : ''} · ${base.documents} document(s) · ${base.chunks} chunks`;
      const actions = document.createElement('div');
      actions.className = 'root-management-actions';
      const select = document.createElement('button');
      select.type = 'button';
      select.className = 'btn btn-sm';
      select.textContent = base.selected ? 'Selected source' : 'Select source';
      select.disabled = base.selected || !base.active;
      select.addEventListener('click', async () => {
        try {
          await Api.selectRagBase(base.id);
          selectedRagBaseId = base.id;
          await loadRagBases();
          showRagStatus(`Selected "${base.name}".`, 'success');
        } catch (error) {
          showRagStatus(`Could not select source: ${error.message}`, 'danger');
        }
      });
      actions.appendChild(select);

      const rename = document.createElement('button');
      rename.type = 'button';
      rename.className = 'btn btn-sm';
      rename.textContent = 'Rename';
      rename.addEventListener('click', async () => {
        const name = prompt('New knowledge source name:', base.name);
        if (name === null || !name.trim()) return;
        try {
          await Api.renameRagBase(base.id, name);
          await loadRagBases();
          showRagStatus(`Renamed source to "${name.trim()}".`, 'success');
        } catch (error) {
          showRagStatus(`Could not rename source: ${error.message}`, 'danger');
        }
      });
      actions.appendChild(rename);

      const archive = document.createElement('button');
      archive.type = 'button';
      archive.className = base.active ? 'btn btn-sm btn-danger' : 'btn btn-sm';
      archive.textContent = base.active ? 'Archive source (keep saved)' : 'Restore source';
      archive.addEventListener('click', async () => {
        try {
          if (base.active) {
            if (!confirm(`Archive "${base.name}"? Its name, folder, and index will be kept.`)) return;
            await Api.archiveRagBase(base.id);
            showRagStatus(`Archived "${base.name}". Its folder and index are still saved.`, 'success');
          } else {
            await Api.restoreRagBase(base.id);
            selectedRagBaseId = base.id;
            showRagStatus(`Restored "${base.name}" with its saved folder and index.`, 'success');
          }
          await loadRagBases();
        } catch (error) {
          showRagStatus(`Could not update source: ${error.message}`, 'danger');
        }
      });
      actions.appendChild(archive);

      const remove = document.createElement('button');
      remove.type = 'button';
      remove.className = 'btn btn-sm btn-danger';
      remove.textContent = 'Delete branch';
      remove.addEventListener('click', async () => {
        if (!confirm(`Delete "${base.name}" and its indexed documents/vectors? The saved branch will be removed, but the folder and its files will NOT be deleted.`)) return;
        try {
          const result = await Api.deleteRagBase(base.id);
          await loadRagBases();
          showRagStatus(`Deleted "${result.name}" and ${result.documents_removed} indexed document(s). Folder files were left untouched.`, 'success');
        } catch (error) {
          showRagStatus(`Could not delete branch: ${error.message}`, 'danger');
        }
      });
      remove.dataset.ragDelete = 'true';
      remove.disabled = AppState.ragScanJob?.base_id === base.id &&
        ['queued', 'running', 'pausing', 'stopping', 'awaiting_decision'].includes(AppState.ragScanJob.status);
      actions.appendChild(remove);
      item.append(heading, path, detail, ...ragWarningNodes(base.warnings), actions);
      if (base.selected) item.classList.add('rag-base-selected');
      ragBaseList.appendChild(item);
    });
    if (!ragBases.length) {
      const empty = document.createElement('li');
      empty.className = 'text-muted small';
      empty.textContent = 'No saved knowledge sources yet.';
      ragBaseList.appendChild(empty);
    }
  }

  async function loadRagBases() {
    const response = await Api.getRagBases();
    ragBases = response.bases || [];
    const current = ragBases.find(base => base.selected && base.active);
    selectedRagBaseId = current?.id || ragBases.find(base => base.active)?.id || null;
    renderRagBases();
    const selected = ragBases.find(base => base.id === selectedRagBaseId);
    if (selected) {
      showRagStatus(`Selected source: ${selected.name} — ${selected.path}${selected.exists ? '' : ' (folder unavailable)'}` +
        (selected.cloud_provider ? ` · ${selected.cloud_provider} folder: online-only files are downloaded before indexing` : ''));
    } else {
      showRagStatus('No knowledge sources yet. Save a named source to create the first branch.');
    }
  }

  function renderRagJob(job) {
    AppState.ragScanJob = job;
    const base = ragBases.find(entry => entry.id === job.base_id);
    const baseName = job.base_name || base?.name || 'Knowledge source';
    const progress = `${job.completed || 0}/${job.discovered || 0} file(s) checked · ` +
      `${job.indexed || 0} indexed · ${job.unchanged || 0} unchanged · ` +
      `${job.skipped?.length || 0} skipped · ${job.errors?.length || 0} errors`;
    const current = job.current ? ` — ${job.current}${job.phase ? ` (${job.phase})` : ''}` : '';
    const firstError = job.errors?.[0];
    const errors = job.errors?.length
      ? `; ${job.errors.length} error(s), e.g. ${firstError.path || ''} — ${firstError.error || 'see server log'}`
      : '';
    const pending = job.pending_error
      ? `File needs your choice: ${job.pending_error.path} — ${job.pending_error.error}`
      : '';
    const schedule = job.resume_at
      ? `; scheduled resume ${new Date(job.resume_at).toLocaleString()}`
      : '';
    const warning = job.warnings?.length ? ` ⚠ ${job.warnings.map(item => item.message).join(' ')}` : '';
    const state = {
      queued: 'Starting',
      running: 'Indexing',
      pausing: 'Pausing after current file',
      stopping: 'Stopping after current file',
      stopped: 'Stopped',
      awaiting_decision: 'Waiting for your choice',
      paused: 'Paused',
      completed: 'Complete',
      completed_with_skips: 'Complete (files skipped by choice)',
      completed_with_errors: 'Complete with errors',
      unavailable: 'Folder unavailable'
    }[job.status] || job.status;
    ragJobStatus.textContent = job.status === 'idle'
      ? 'Index status: Waiting to start.'
      : `${baseName}: ${state} — ${progress}${pending ? ` — ${pending}` : errors}${schedule}${current}${warning}`;
    ragJobStatus.className = `small ${
      warning && ['completed', 'completed_with_skips'].includes(job.status) ? 'text-warning'
      : ['completed', 'completed_with_skips', 'running', 'queued'].includes(job.status) ? 'text-success'
        : ['paused', 'pausing', 'awaiting_decision'].includes(job.status) ? 'text-warning'
          : ['failed', 'completed_with_errors', 'unavailable'].includes(job.status) ? 'text-danger'
            : 'text-muted'
    }`;
    const active = ['queued', 'running', 'pausing', 'stopping'].includes(job.status);
    const blocksMutation = ['queued', 'running', 'pausing', 'paused', 'stopping', 'awaiting_decision'].includes(job.status);
    const selectedJob = !job.base_id || job.base_id === selectedRagBaseId;
    container.querySelector('[data-rag-action="read"]').disabled = active;
    container.querySelector('[data-rag-action="pause"]').disabled = !active || job.status === 'pausing';
    container.querySelector('[data-rag-action="stop"]').disabled =
      !['queued', 'running', 'pausing', 'paused', 'awaiting_decision'].includes(job.status);
    container.querySelector('[data-rag-action="resume"]').disabled =
      !selectedJob || !['paused', 'unavailable', 'failed'].includes(job.status);
    container.querySelector('[data-rag-action="clear"]').disabled =
      !selectedRagBaseId || blocksMutation;
    const errorActions = container.querySelector('#dash-rag-error-actions');
    errorActions.hidden = job.status !== 'awaiting_decision';
    container.querySelector('#dash-rag-error-question').textContent = job.pending_error
      ? `Retry this file, skip only it (ask again next time), or skip all remaining errors?`
      : '';
    errorActions.querySelectorAll('button').forEach(button => {
      button.disabled = job.status !== 'awaiting_decision';
    });
    ragBaseList.querySelectorAll('.rag-base-item').forEach(item => {
      const button = item.querySelector('[data-rag-delete]');
      if (button) {
        button.disabled = blocksMutation && job.status !== 'paused' &&
          job.base_id === item.dataset.baseId;
      }
    });
  }

  async function refreshRagJob() {
    try {
      renderRagJob(await Api.getRagScan());
    } catch (error) {
      ragJobStatus.textContent = `Could not load indexing status: ${error.message}`;
      ragJobStatus.className = 'small text-danger';
    }
  }

  loadRagBases().then(refreshRagJob).catch(error => {
    showRagStatus(`Could not load RAG settings: ${error.message}`, 'danger');
  });

  ragButtons.forEach(button => {
    button.addEventListener('click', async () => {
      const action = button.dataset.ragAction;
      // Saving a new source is the one action that needs no existing selection.
      if (action !== 'add' && !selectedRagBaseId) {
        showRagStatus('Save or select a knowledge source first.', 'warning');
        return;
      }
      const base = ragBases.find(item => item.id === selectedRagBaseId);
      if (action === 'clear' &&
          !confirm(`Clear only the saved index for "${base?.name}"? The saved name, folder, and files will remain.`)) return;
      if (['read', 'pause', 'stop', 'resume', 'clear', 'error-retry', 'error-skip', 'error-skip-all'].includes(action)) {
        button.disabled = true;
      }
      try {
        if (action === 'add') {
          const name = ragNameInput.value.trim();
          const path = ragPathInput.value.trim();
          if (!name || !path) {
            showRagStatus('Enter a source name and the complete folder path.', 'warning');
            return;
          }
          const added = await Api.addRagBase(name, path);
          ragNameInput.value = '';
          ragPathInput.value = '';
          selectedRagBaseId = added.id;
          await loadRagBases();
          showRagStatus(`Saved source "${added.name}" for ${added.path}.`, 'success');
        } else if (action === 'read') {
          const job = await Api.reindexRag(selectedRagBaseId);
          renderRagJob(job);
          if (job.status === 'paused') {
            showRagStatus(
              `The previous scan for "${base?.name}" is still paused. Resume it to retry unfinished files.`,
              'warning'
            );
          } else if (['queued', 'running'].includes(job.status)) {
            showRagStatus(`Indexing started for "${base?.name}".`, 'success');
          } else {
            showRagStatus(`The scan was not started (status: ${job.status}).`, 'warning');
          }
        } else if (action === 'pause') {
          renderRagJob(await Api.pauseRagScan());
          showRagStatus('Indexing paused after the current file finishes.', 'success');
        } else if (action === 'stop') {
          renderRagJob(await Api.stopRagScan());
          showRagStatus('Stopping indexing after the current file finishes. You can delete the branch when its status is Stopped.', 'success');
        } else if (action === 'resume') {
          renderRagJob(await Api.resumeRagScan());
          showRagStatus('Indexing resumed.', 'success');
        } else if (action.startsWith('error-')) {
          const decision = {
            'error-retry': 'retry',
            'error-skip': 'skip',
            'error-skip-all': 'skip_all'
          }[action];
          renderRagJob(await Api.decideRagScanError(decision));
          showRagStatus(
            decision === 'retry' ? 'Retrying the failed file.'
              : decision === 'skip' ? 'Skipped this file; Novous will ask again if another file fails.'
                : 'Skipping all remaining file errors automatically.',
            'success'
          );
        } else if (action === 'clear') {
          const result = await Api.clearRagIndex(selectedRagBaseId);
          await loadRagBases();
          showRagStatus(`Cleared ${result.removed} indexed document(s) from "${base?.name}".`, 'success');
        }
      } catch (err) {
        showRagStatus(err.message, 'danger');
      } finally {
        await refreshRagJob();
      }
    });
  });
  const ragRefreshInterval = setInterval(() => {
    if (!container.isConnected) {
      clearInterval(ragRefreshInterval);
      return;
    }
    refreshRagJob();
  }, 1500);
}
