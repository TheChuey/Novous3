/**
 * Runs section: start a squad workflow, follow the live trace and inspect handoffs.
 */
import { Api } from '../api.js';
import { esc, navigate } from '../shell.js';
import { appendMarkdown } from '../markdown.js';
import { createAgentMeters, runHealthHtml } from '../agent_meters.js';
import { PATTERN_INFO, codeChangeFromEvent, describeEvent, startSquadRun } from './run_client.js';
import { openSquadScratchpad } from '../squad_scratchpad.js';

let activeRun = null;
const recentRuns = [];
let lastSquadId = null;
let markdownTexts = [];

function markdownSlot(text, className = '') {
  markdownTexts.push(String(text ?? ''));
  return `<div class="sq-md prompt-rich-markdown ${className}" data-md="${markdownTexts.length - 1}"></div>`;
}

export function stopRunStream() {
  activeRun?.close();
  activeRun = null;
}

function stepRows(steps = []) {
  return steps.map(step => `
    <tr class="${step.status === 'ERROR' ? 'is-error' : ''}">
      <td>${step.step_number}</td><td>${esc(step.agent_id)}</td><td>${esc(step.phase)}</td>
      <td>${esc(step.status)}</td><td>${esc(step.error_reason || step.details)}</td>
    </tr>`).join('');
}

function payloadSections(payload) {
  if (!payload || typeof payload !== 'object') {
    return payload == null ? '' : `<h4>Result payload</h4><pre class="sq-pre">${esc(JSON.stringify(payload, null, 2))}</pre>`;
  }
  const parts = [];
  if (payload.routing) {
    parts.push(`<h4>Routing decision</h4>
      <p>Chose <strong>${esc(payload.routing.chosen.join(', '))}</strong>${payload.routing.skipped?.length ? `, skipped ${esc(payload.routing.skipped.join(', '))}` : ''}.</p>
      ${payload.routing.reason ? `<p class="text-muted small">${esc(payload.routing.reason)}</p>` : ''}`);
  }
  const outputs = payload.fanout_results || payload.member_results;
  if (outputs && Object.keys(outputs).length) {
    parts.push(`<h4>Member outputs</h4>${Object.entries(outputs).map(([id, text]) => `
      <details class="sq-output"><summary>${esc(id)}</summary>${markdownSlot(text)}</details>`).join('')}`);
  }
  if (payload.fanout_errors && Object.keys(payload.fanout_errors).length) {
    parts.push(`<h4>Member errors</h4>${Object.entries(payload.fanout_errors).map(([id, error]) =>
      `<p class="sq-msg sq-msg-error">${esc(id)}: ${esc(error)}</p>`).join('')}`);
  }
  const rest = payload.routing || payload.fanout_results ? payload.scratchpad_payload : payload;
  if (rest != null) parts.push(`<h4>Scratchpad payload</h4><pre class="sq-pre">${esc(JSON.stringify(rest, null, 2))}</pre>`);
  return parts.join('');
}

export function renderRunsView(container, payload = null) {
  if (payload?.squadId) lastSquadId = payload.squadId;
  container.innerHTML = `
    <div class="sq-page">
      <header class="sq-page-head">
        <div>
          <h2>Runs</h2>
          <p class="text-muted">Run one controlled workflow at a time and watch each handoff.</p>
        </div>
        <button type="button" class="btn" data-act="squad-scratchpads">Squad scratchpads</button>
      </header>
      <div class="sq-runs">
        <section class="card sq-run-form">
          <label class="sq-field"><span>Squad</span>
            <select class="form-select" data-role="squad"><option>Loading…</option></select></label>
          <p class="text-muted small" data-role="squad-summary"></p>
          <label class="sq-field"><span>Request</span>
            <textarea class="form-input" rows="5" data-role="query" placeholder="Describe the work for this squad"></textarea></label>
          <button type="button" class="btn btn-primary" data-act="run">▶ Run squad</button>
          <p class="small" data-role="status" role="status" aria-live="polite"></p>
          <h4 class="mt-3">This session</h4>
          <ul class="sq-recent" data-role="recent"></ul>
        </section>
        <section class="card sq-trace">
          <div class="card-head">
            <h3>Live trace</h3>
            <span class="text-muted small">Prompts, tool arguments and document text are never streamed.</span>
          </div>
          <div class="agent-meters" data-role="agents"></div>
          <ol class="sq-timeline" data-role="timeline"><li class="text-muted small">Start a run to see its trace.</li></ol>
        </section>
        <section class="card sq-result" data-role="result">
          <h3>Result</h3>
          <p class="text-muted small">The final answer and every hand-off appear here when the run completes.</p>
        </section>
      </div>
    </div>`;

  const squadSelect = container.querySelector('[data-role="squad"]');
  const summary = container.querySelector('[data-role="squad-summary"]');
  const query = container.querySelector('[data-role="query"]');
  const status = container.querySelector('[data-role="status"]');
  const timeline = container.querySelector('[data-role="timeline"]');
  const meters = createAgentMeters(container.querySelector('[data-role="agents"]'));
  meters.reset();
  const resultBox = container.querySelector('[data-role="result"]');
  const recent = container.querySelector('[data-role="recent"]');
  const runButton = container.querySelector('[data-act="run"]');
  container.querySelector('[data-act="squad-scratchpads"]').addEventListener('click', () => openSquadScratchpad());
  let squads = [];
  let codeChange = null;

  const currentSquad = () => squads.find(squad => squad.squad_id === squadSelect.value);

  function setStatus(text, kind = 'muted') {
    status.textContent = text;
    status.className = `small text-${kind}`;
  }

  function syncSquad() {
    const squad = currentSquad();
    if (!squad) return;
    lastSquadId = squad.squad_id;
    syncPattern();
  }

  function syncPattern() {
    const squad = currentSquad();
    summary.textContent = squad
      ? `${squad.primary_agent} leads ${squad.subagents.join(', ') || 'no members'}. ${PATTERN_INFO.sequential.summary}`
      : '';
  }

  function renderRecent() {
    recent.innerHTML = recentRuns.map(run => `
      <li><button type="button" class="sq-link" data-trace="${esc(run.traceId)}">${esc(run.squadName)}</button>
        <span class="badge ${run.status === 'error' ? 'badge-danger' : run.status === 'running' ? 'badge-warning' : 'badge-success'}">${esc(run.status)}</span>
        <span class="text-muted small">${esc(run.pattern)} · ${esc(run.time)}</span></li>`).join('')
      || '<li class="text-muted small">No runs yet.</li>';
  }

  function addTimelineEvent(event) {
    meters.onEvent(event);
    if (timeline.querySelector('.text-muted')) timeline.replaceChildren();
    const line = describeEvent(event);
    const item = document.createElement('li');
    item.className = `sq-event sq-event-${line.type.toLowerCase()}${line.status === 'error' ? ' is-error' : ''}`;
    item.innerHTML = `<span class="sq-event-time">${esc(line.time)}</span>
      <span class="sq-event-type">${esc(line.type.replaceAll('_', ' ').toLowerCase())}</span>
      <span class="sq-event-text">${esc(line.text)}</span>`;
    timeline.appendChild(item);
    while (timeline.children.length > 300) timeline.firstElementChild.remove();
    timeline.scrollTop = timeline.scrollHeight;
    const change = codeChangeFromEvent(event);
    if (change) codeChange = change;
  }

  async function showResult(traceId) {
    resultBox.innerHTML = '<h3>Result</h3><p class="text-muted small">Loading…</p>';
    let result;
    try {
      result = await Api.getOrchestrationRunResult(traceId);
    } catch (err) {
      resultBox.innerHTML = `<h3>Result</h3><p class="text-danger small">${esc(err.message)}</p>`;
      return;
    }
    markdownTexts = [];
    resultBox.innerHTML = `
      <div class="card-head">
        <h3>Result</h3>
        <span class="badge ${result.status === 'error' ? 'badge-danger' : 'badge-success'}">${esc(result.status)}</span>
      </div>
      <p class="text-muted small">${esc(PATTERN_INFO[result.pattern]?.label || result.pattern)} · trace ${esc(result.trace_id)}</p>
      ${runHealthHtml(result.agent_stats)}
      <h4>Final answer</h4>
      ${markdownSlot(result.final_answer || '(No final answer returned.)', 'sq-answer')}
      ${payloadSections(result.result_payload)}
      <h4>Execution steps</h4>
      <table class="sq-steps"><thead><tr><th>#</th><th>Agent</th><th>Phase</th><th>Status</th><th>Details</th></tr></thead>
        <tbody>${stepRows(result.execution_trace)}</tbody></table>
      <div class="sq-inspector-actions mt-2">
        <button type="button" class="btn btn-sm" data-act="squad-scratchpad">View squad scratchpad</button>
        ${result.scratchpad_snapshot && Object.keys(result.scratchpad_snapshot).length
          ? '<button type="button" class="btn btn-sm" data-act="scratchpad">View scratchpad</button>' : ''}
        ${codeChange?.fileExists && codeChange.changedLines.length
          ? `<button type="button" class="btn btn-sm" data-act="review">Review ${codeChange.changedLines.length} changed lines · ${esc(codeChange.path)}</button>` : ''}
        <button type="button" class="btn btn-sm" data-act="open-squad">Open squad</button>
      </div>
      <pre class="sq-pre hidden" data-role="scratchpad"></pre>`;
    resultBox.dataset.squad = result.squad_id || '';
    resultBox.querySelectorAll('[data-md]').forEach(slot => {
      appendMarkdown(slot, markdownTexts[Number(slot.dataset.md)]);
    });
    resultBox.dataset.trace = traceId;
    meters.setFromStats(result.agent_stats);
  }

  resultBox.addEventListener('click', async event => {
    const act = event.target.closest('button')?.dataset.act;
    if (act === 'squad-scratchpad') {
      await openSquadScratchpad(resultBox.dataset.trace);
    } else if (act === 'scratchpad') {
      const box = resultBox.querySelector('[data-role="scratchpad"]');
      try {
        box.textContent = JSON.stringify(await Api.getOrchestrationRunScratchpad(resultBox.dataset.trace), null, 2);
      } catch (err) {
        box.textContent = err.message;
      }
      box.classList.toggle('hidden');
    } else if (act === 'review') {
      navigate('editor', { path: codeChange.path, managedFile: true, previewLines: codeChange.changedLines });
    } else if (act === 'open-squad' && resultBox.dataset.squad) {
      navigate('squads', { squadId: resultBox.dataset.squad });
    }
  });

  recent.addEventListener('click', event => {
    const trace = event.target.closest('[data-trace]')?.dataset.trace;
    if (trace) showResult(trace);
  });
  squadSelect.addEventListener('change', syncSquad);

  runButton.addEventListener('click', async () => {
    const squad = currentSquad();
    const text = query.value.trim();
    if (!squad) return;
    if (!text) { setStatus('Enter a request first.', 'danger'); query.focus(); return; }
    stopRunStream();
    codeChange = null;
    timeline.replaceChildren();
    meters.reset();
    runButton.disabled = true;
    setStatus('Starting run…');
    const parameters = {};
    const record = {
      squadName: squad.name, pattern: 'sequential', status: 'running',
      time: new Date().toLocaleTimeString(), traceId: ''
    };
    try {
      activeRun = await startSquadRun({
        squadId: squad.squad_id,
        query: text,
        parameters,
        onEvent: addTimelineEvent,
        onComplete: async (traceId, detail) => {
          activeRun = null;
          record.status = detail.status === 'error' ? 'error' : 'success';
          renderRecent();
          setStatus(`Run ${detail.status || 'complete'} in ${detail.duration_ms ?? '?'} ms`, detail.status === 'error' ? 'danger' : 'success');
          await showResult(traceId);
        },
        onClosed: () => setStatus('Trace stream closed before completion. Open the run below once it finishes.', 'warning')
      });
      record.traceId = activeRun.traceId;
      recentRuns.unshift(record);
      recentRuns.splice(20);
      renderRecent();
      setStatus(`Running · trace ${activeRun.traceId}`);
    } catch (err) {
      setStatus(`Could not start run: ${err.message}`, 'danger');
    } finally {
      runButton.disabled = false;
    }
  });

  renderRecent();
  Api.getOrchestrationSquads().then(data => {
    squads = data.squads || [];
    squadSelect.innerHTML = squads.map(squad =>
      `<option value="${esc(squad.squad_id)}" ${squad.squad_id === lastSquadId ? 'selected' : ''}>${esc(squad.name)}</option>`).join('')
      || '<option value="">No squads yet</option>';
    runButton.disabled = !squads.length;
    syncSquad();
  }).catch(err => setStatus(`Could not load squads: ${err.message}`, 'danger'));
}
