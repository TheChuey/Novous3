/**
 * Models section: the context window and tool limit each installed model runs with.
 * Suggested values follow the model's size; saving a profile overrides them. Changes apply to
 * the next message, and the chat keeps its history.
 */
import { Api } from '../api.js';
import { esc } from '../shell.js';

export function renderModelsView(container) {
  container.innerHTML = `
    <div class="sq-page">
      <header class="sq-page-head">
        <div>
          <h2>Models</h2>
          <p class="text-muted">Context window and tool limit for each installed model. Smaller models do best with a smaller window and fewer tools.</p>
        </div>
        <button type="button" class="btn" data-act="refresh">Refresh</button>
      </header>
      <p class="sq-msg sq-msg-error hidden" data-role="status" role="alert"></p>
      <div class="model-profile-list" data-role="list"><p class="text-muted">Loading models…</p></div>
    </div>`;

  const list = container.querySelector('[data-role="list"]');
  const status = container.querySelector('[data-role="status"]');

  function showStatus(message) {
    status.textContent = message;
    status.classList.toggle('hidden', !message);
  }

  function modelCard(model) {
    const size = model.parameter_size || (model.size_b ? `${model.size_b}B` : 'size unknown');
    const native = model.native_context || 131072;
    const suggested = model.suggested;
    const capabilities = model.capabilities.map(c => `<span class="badge">${esc(c)}</span>`).join(' ');
    const notes = [];
    if (!model.available) notes.push('Ollama could not describe this model, so suggested values are used.');
    if (model.capabilities.length && !model.capabilities.includes('tools')) {
      notes.push('This model does not list tool support, so tool calls may fail.');
    }
    return `
      <article class="card model-profile-card" data-model="${esc(model.model)}">
        <div class="model-profile-head">
          <div>
            <h3>${esc(model.model)}</h3>
            <p class="text-muted small">${esc(size)} · model maximum ${model.native_context ? `${model.native_context} tokens` : 'unknown'}</p>
          </div>
          <span class="badge ${model.source === 'saved' ? 'badge-accent' : ''}">${model.source === 'saved' ? 'Saved' : 'Suggested'}</span>
        </div>
        <p class="model-profile-caps">${capabilities}</p>
        ${notes.map(note => `<p class="sq-msg sq-msg-error">${esc(note)}</p>`).join('')}
        <form class="model-profile-form" data-role="form">
          <label class="sq-field">
            <span>Context window (tokens)</span>
            <input class="form-input" type="number" name="num_ctx" min="2048" max="${native}" step="256"
                   value="${model.num_ctx}" required>
          </label>
          <label class="sq-field">
            <span>Reply reserve (tokens)</span>
            <input class="form-input" type="number" name="reply_reserve" min="256" step="128"
                   value="${model.reply_reserve}" required>
          </label>
          <label class="sq-field">
            <span>Tools sent (blank = all)</span>
            <input class="form-input" type="number" name="max_tools" min="0" max="64"
                   value="${model.max_tools ?? ''}" placeholder="all">
          </label>
          <div class="model-profile-actions">
            <button type="submit" class="btn btn-sm btn-primary">Save</button>
            <button type="button" class="btn btn-sm" data-act="reset" ${model.source === 'suggested' ? 'disabled' : ''}>Reset to suggested</button>
            <span class="text-muted small">Suggested: ${suggested.num_ctx} tokens, ${suggested.max_tools ?? 'all'} tools</span>
          </div>
          <p class="sq-msg sq-msg-error hidden" data-role="error" role="alert"></p>
        </form>
      </article>`;
  }

  async function load() {
    showStatus('');
    try {
      const data = await Api.getModelProfiles();
      if (data.error) showStatus(data.error);
      list.innerHTML = data.models.length
        ? data.models.map(modelCard).join('')
        : '<p class="text-muted">No installed models found. Pull a model in Ollama, then refresh.</p>';
    } catch (err) {
      list.innerHTML = '';
      showStatus(`Could not load models: ${err.message}`);
    }
  }

  container.querySelector('[data-act="refresh"]').addEventListener('click', load);

  list.addEventListener('submit', async event => {
    event.preventDefault();
    const form = event.target.closest('[data-role="form"]');
    const card = form.closest('[data-model]');
    const error = form.querySelector('[data-role="error"]');
    const data = new FormData(form);
    const maxTools = String(data.get('max_tools') ?? '').trim();
    error.classList.add('hidden');
    try {
      await Api.saveModelProfile(card.dataset.model, {
        num_ctx: Number(data.get('num_ctx')),
        reply_reserve: Number(data.get('reply_reserve')),
        max_tools: maxTools === '' ? null : Number(maxTools)
      });
      await load();
    } catch (err) {
      error.textContent = err.message;
      error.classList.remove('hidden');
    }
  });

  list.addEventListener('click', async event => {
    const button = event.target.closest('[data-act="reset"]');
    if (!button) return;
    const card = button.closest('[data-model]');
    try {
      await Api.resetModelProfile(card.dataset.model);
      await load();
    } catch (err) {
      showStatus(`Could not reset ${card.dataset.model}: ${err.message}`);
    }
  });

  load();
}
