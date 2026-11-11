/** Manage knowledge sources and their background indexing jobs. */
import { Api } from '../api.js';
import { AppState, esc } from '../shell.js';
import { ragWarningNodes } from '../rag_warnings.js';

export function renderKnowledgeView(container) {
  container.innerHTML = `
    <div class="sq-page">
      <header class="sq-page-head">
        <div>
          <h2>Knowledge sources</h2>
          <p class="text-muted">Manage indexed folders used by knowledge retrieval.</p>
        </div>
      </header>
      <section class="card root-management">
        <h3>Sources</h3>
        <ul class="root-path-list rag-base-list" data-role="sources" aria-label="Knowledge sources">
          <li class="text-muted small">Loading sources…</li>
        </ul>
        <p class="small" data-role="status" role="status" aria-live="polite"></p>
        <details class="ov-add-source" open>
          <summary>Add a knowledge source</summary>
          <form data-role="add-source">
            <label class="small" for="knowledge-name">Source name</label>
            <input id="knowledge-name" class="form-input" name="name" type="text" required
              placeholder="Business decisions">
            <label class="small" for="knowledge-path">Folder to index</label>
            <textarea id="knowledge-path" class="form-input root-path-input" name="path" rows="2" required
              placeholder="E:\\Documents\\Knowledge"></textarea>
            <button class="btn btn-primary" type="submit">Save knowledge source</button>
          </form>
        </details>
      </section>
      <section class="card root-management">
        <h3>Indexing</h3>
        <div class="root-management-actions">
          <button class="btn" type="button" data-action="index">Index selected source</button>
          <button class="btn" type="button" data-action="pause">Pause</button>
          <button class="btn btn-danger" type="button" data-action="stop">Stop indexing</button>
          <button class="btn" type="button" data-action="resume">Resume</button>
          <button class="btn btn-danger" type="button" data-action="clear">Clear selected index</button>
        </div>
        <p class="small" data-role="job" role="status" aria-live="polite">Loading indexing status…</p>
        <div class="root-management-actions" data-role="error-actions" hidden>
          <span class="small" data-role="error-question"></span>
          <button class="btn" type="button" data-action="error-retry">Retry this file</button>
          <button class="btn" type="button" data-action="error-skip">Skip this file</button>
          <button class="btn btn-danger" type="button" data-action="error-skip-all">Skip all remaining errors</button>
        </div>
      </section>
    </div>`;

  const sourceList = container.querySelector('[data-role="sources"]');
  const status = container.querySelector('[data-role="status"]');
  const jobStatus = container.querySelector('[data-role="job"]');
  const selectedActionButtons = [...container.querySelectorAll('[data-action]')];
  let sources = [];
  let selectedSourceId = null;

  function showStatus(message, kind = 'muted') {
    status.textContent = message;
    status.className = `small text-${kind}`;
  }

  function renderSources() {
    sourceList.replaceChildren();
    sources.forEach(source => {
      const item = document.createElement('li');
      item.className = `rag-base-item${source.selected ? ' rag-base-selected' : ''}`;
      item.dataset.sourceId = source.id;
      const name = document.createElement('strong');
      name.textContent = `${source.name}${source.active ? '' : ' (archived)'}`;
      const path = document.createElement('code');
      path.textContent = source.path;
      const detail = document.createElement('span');
      detail.className = 'small text-muted';
      detail.textContent = `${source.selected ? 'Selected · ' : ''}${source.exists ? 'Folder available' : 'Folder unavailable'}${source.cloud_provider ? ` · ${source.cloud_provider}` : ''} · ${source.documents} document(s) · ${source.chunks} chunks`;
      const actions = document.createElement('div');
      actions.className = 'root-management-actions';
      const addAction = (label, action, disabled = false) => {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'btn btn-sm';
        button.textContent = label;
        button.dataset.sourceAction = action;
        button.disabled = disabled;
        actions.appendChild(button);
      };
      addAction(source.selected ? 'Selected' : 'Select', 'select', source.selected || !source.active);
      addAction('Open folder', 'open', !source.exists);
      addAction('Rename', 'rename');
      addAction(source.active ? 'Archive' : 'Restore', source.active ? 'archive' : 'restore');
      addAction('Delete branch', 'delete');
      item.append(name, path, detail, ...ragWarningNodes(source.warnings), actions);
      sourceList.appendChild(item);
    });
    if (!sources.length) {
      const empty = document.createElement('li');
      empty.className = 'text-muted small';
      empty.textContent = 'No knowledge sources yet.';
      sourceList.appendChild(empty);
    }
  }

  async function loadSources() {
    const data = await Api.getRagBases();
    sources = data.bases || [];
    selectedSourceId = sources.find(source => source.selected && source.active)?.id
      || (sources.some(source => source.id === selectedSourceId && source.active)
        ? selectedSourceId
        : sources.find(source => source.active)?.id)
      || null;
    if (!sources.some(source => source.id === selectedSourceId)) selectedSourceId = null;
    renderSources();
    const selected = sources.find(source => source.id === selectedSourceId);
    showStatus(selected
      ? `Selected source: ${selected.name} — ${selected.path}${selected.exists ? '' : ' (folder unavailable)'}` +
        (selected.cloud_provider ? ` · ${selected.cloud_provider} folder: online-only files are downloaded before indexing` : '')
      : 'No knowledge sources yet. Save a named source to create the first branch.');
  }

  function renderJob(job) {
    AppState.ragScanJob = job;
    const base = sources.find(source => source.id === job.base_id);
    const progress = `${job.completed || 0}/${job.discovered || 0} file(s) checked · ` +
      `${job.indexed || 0} indexed · ${job.unchanged || 0} unchanged · ` +
      `${job.skipped?.length || 0} skipped · ${job.errors?.length || 0} errors`;
    const current = job.current ? ` — ${job.current}${job.phase ? ` (${job.phase})` : ''}` : '';
    const firstError = job.errors?.[0];
    const errors = job.errors?.length
      ? `; ${job.errors.length} error(s), e.g. ${firstError.path || ''} — ${firstError.error || 'see server log'}`
      : '';
    const pending = job.pending_error
      ? ` · Needs a choice: ${job.pending_error.path} — ${job.pending_error.error}`
      : '';
    const resumeAt = job.resume_at ? ` · Scheduled resume ${new Date(job.resume_at).toLocaleString()}` : '';
    const warnings = job.warnings?.length
      ? ` ⚠ ${job.warnings.map(warning => warning.message).join(' ')}`
      : '';
    const labels = {
      queued: 'Starting',
      running: 'Indexing',
      pausing: 'Pausing after current file',
      stopping: 'Stopping after current file',
      stopped: 'Stopped',
      awaiting_decision: 'Waiting for your choice',
      paused: 'Paused',
      completed: 'Complete',
      completed_with_skips: 'Complete (some files skipped)',
      completed_with_errors: 'Complete with errors',
      unavailable: 'Folder unavailable',
      idle: 'Waiting to start'
    };
    const state = labels[job.status] || job.status;
    jobStatus.textContent = job.status === 'idle'
      ? 'Index status: Waiting to start.'
      : `${base?.name || 'Knowledge source'}: ${state} — ${progress}${pending ? ` — ${pending}` : errors}${resumeAt}${current}${warnings}`;
    jobStatus.className = `small ${
      warnings && ['completed', 'completed_with_skips'].includes(job.status) ? 'text-warning'
        : ['completed', 'completed_with_skips', 'running', 'queued'].includes(job.status) ? 'text-success'
        : ['paused', 'pausing', 'awaiting_decision'].includes(job.status) ? 'text-warning'
          : ['failed', 'completed_with_errors', 'unavailable'].includes(job.status) ? 'text-danger'
            : 'text-muted'
    }`;

    const active = ['queued', 'running', 'pausing', 'stopping'].includes(job.status);
    const blocksMutation = ['queued', 'running', 'pausing', 'paused', 'stopping', 'awaiting_decision'].includes(job.status);
    const selectedJob = !job.base_id || job.base_id === selectedSourceId;
    container.querySelector('[data-action="index"]').disabled = active || !selectedSourceId;
    container.querySelector('[data-action="pause"]').disabled = !active || job.status === 'pausing';
    container.querySelector('[data-action="stop"]').disabled =
      !['queued', 'running', 'pausing', 'paused', 'awaiting_decision'].includes(job.status);
    container.querySelector('[data-action="resume"]').disabled =
      !selectedJob || !['paused', 'unavailable', 'failed'].includes(job.status);
    container.querySelector('[data-action="clear"]').disabled = !selectedSourceId || blocksMutation;
    const errorActions = container.querySelector('[data-role="error-actions"]');
    errorActions.hidden = job.status !== 'awaiting_decision';
    container.querySelector('[data-role="error-question"]').textContent = job.pending_error
      ? 'Retry this file, skip only it, or skip all remaining errors?'
      : '';
    errorActions.querySelectorAll('button').forEach(button => {
      button.disabled = job.status !== 'awaiting_decision';
    });
    sourceList.querySelectorAll('[data-source-action="delete"]').forEach(button => {
      const source = button.closest('[data-source-id]');
      button.disabled = blocksMutation && job.status !== 'paused' && job.base_id === source.dataset.sourceId;
    });
  }

  async function refreshJob() {
    try {
      renderJob(await Api.getRagScan());
    } catch (error) {
      jobStatus.textContent = `Could not load indexing status: ${error.message}`;
      jobStatus.className = 'small text-danger';
    }
  }

  sourceList.addEventListener('click', async event => {
    const button = event.target.closest('[data-source-action]');
    if (!button) return;
    const item = button.closest('[data-source-id]');
    const source = sources.find(entry => entry.id === item.dataset.sourceId);
    if (!source) return;
    const action = button.dataset.sourceAction;
    button.disabled = true;
    try {
      if (action === 'select') {
        await Api.selectRagBase(source.id);
        selectedSourceId = source.id;
        await loadSources();
        showStatus(`Selected "${source.name}".`, 'success');
      } else if (action === 'open') {
        await Api.openRagBaseFolder(source.id);
        showStatus(`Opened folder for "${source.name}".`, 'success');
      } else if (action === 'rename') {
        const name = prompt('New knowledge source name:', source.name);
        if (name === null || !name.trim()) {
          button.disabled = false;
          return;
        }
        await Api.renameRagBase(source.id, name);
        await loadSources();
        showStatus(`Renamed source to "${name.trim()}".`, 'success');
      } else if (action === 'archive' || action === 'restore') {
        if (action === 'archive' &&
            !confirm(`Archive "${source.name}"? Its folder and index will be kept.`)) {
          button.disabled = false;
          return;
        }
        if (action === 'archive') await Api.archiveRagBase(source.id);
        else {
          await Api.restoreRagBase(source.id);
          selectedSourceId = source.id;
        }
        await loadSources();
        showStatus(`${action === 'archive' ? 'Archived' : 'Restored'} "${source.name}".`, 'success');
      } else if (action === 'delete') {
        if (!confirm(`Delete "${source.name}" and its index? The folder and its files will remain.`)) {
          button.disabled = false;
          return;
        }
        const result = await Api.deleteRagBase(source.id);
        await loadSources();
        showStatus(`Deleted "${result.name}" and ${result.documents_removed} indexed document(s).`, 'success');
      }
    } catch (error) {
      showStatus(`Could not ${action} source: ${error.message}`, 'danger');
      button.disabled = false;
    }
  });

  container.querySelector('[data-role="add-source"]').addEventListener('submit', async event => {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const button = form.querySelector('[type="submit"]');
    button.disabled = true;
    try {
      const added = await Api.addRagBase(String(data.get('name')).trim(), String(data.get('path')).trim());
      selectedSourceId = added.id;
      await loadSources();
      form.reset();
      showStatus(`Saved "${added.name}" for ${added.path}.`, 'success');
    } catch (error) {
      showStatus(`Could not save source: ${error.message}`, 'danger');
    } finally {
      button.disabled = false;
    }
  });

  selectedActionButtons.forEach(button => button.addEventListener('click', async () => {
    const action = button.dataset.action;
    if (action.startsWith('error-') && !AppState.ragScanJob?.pending_error) return;
    if (action === 'clear' &&
        !confirm(`Clear the saved index for "${sources.find(source => source.id === selectedSourceId)?.name}"? The source folder and files will remain.`)) return;
    button.disabled = true;
    try {
      if (action === 'index') {
        const job = await Api.reindexRag(selectedSourceId);
        renderJob(job);
        if (job.status === 'paused') {
          showStatus('The previous scan is still paused. Resume it to retry unfinished files.', 'warning');
        } else if (['queued', 'running'].includes(job.status)) {
          showStatus('Indexing started.', 'success');
        } else {
          showStatus(`The scan was not started (status: ${job.status}).`, 'warning');
        }
      } else if (action === 'pause') {
        renderJob(await Api.pauseRagScan());
        showStatus('Indexing paused after the current file finishes.', 'success');
      } else if (action === 'stop') {
        renderJob(await Api.stopRagScan());
        showStatus('Indexing will stop after the current file finishes.', 'success');
      } else if (action === 'resume') {
        renderJob(await Api.resumeRagScan());
        showStatus('Indexing resumed.', 'success');
      } else if (action === 'clear') {
        const result = await Api.clearRagIndex(selectedSourceId);
        await loadSources();
        showStatus(`Cleared ${result.removed} indexed document(s).`, 'success');
      } else if (action.startsWith('error-')) {
        const decision = {
          'error-retry': 'retry',
          'error-skip': 'skip',
          'error-skip-all': 'skip_all'
        }[action];
        renderJob(await Api.decideRagScanError(decision));
        showStatus(
          decision === 'retry' ? 'Retrying the failed file.'
            : decision === 'skip' ? 'Skipped this file; Novous will ask again if another file fails.'
              : 'Skipping all remaining file errors automatically.',
          'success'
        );
      }
    } catch (error) {
      showStatus(`Could not ${action}: ${error.message}`, 'danger');
    } finally {
      await refreshJob();
    }
  }));

  loadSources()
    .then(refreshJob)
    .catch(error => showStatus(`Could not load knowledge sources: ${error.message}`, 'danger'));
  const interval = setInterval(() => {
    if (!container.isConnected) {
      clearInterval(interval);
      return;
    }
    refreshJob();
  }, 1500);
}
