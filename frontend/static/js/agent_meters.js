/**
 * Per-agent meters for a squad run: each agent's state, how full its model's context window got,
 * and how its tool calls went. Live telemetry fills the meters while a run is going; the run's
 * saved agent_stats (from the server) replace them once it finishes.
 */
import { esc } from './shell.js';

const STATE_LABEL = { idle: 'Waiting', running: 'Running', success: 'Done', error: 'Failed', done: 'Done' };
const LEVEL_LABEL = { ok: 'OK', warn: 'Watch', crit: 'Limit', unknown: 'Unknown' };
const LEVEL_RANK = { ok: 0, warn: 1, crit: 2 };
const FAILURE_LABEL = {
  unknown_tool: 'unknown tool', bad_arguments: 'bad arguments', tool_error: 'tool error', model_error: 'model error'
};
const FLAG_LABEL = {
  context_full: 'context full', context_filling: 'context filling', rounds_exhausted: 'out of tool rounds',
  tool_failures: 'tool failures', unknown_tool: 'unknown tool', model_error: 'model error'
};
const CRITICAL_FLAGS = new Set(['context_full', 'rounds_exhausted', 'model_error']);

function nonZero(failures = {}) {
  return Object.fromEntries(Object.entries(failures).filter(([, count]) => count > 0));
}

function mergeUsage(previous, data) {
  const level = LEVEL_RANK[data.level] > LEVEL_RANK[previous?.level] ? data.level : (previous?.level || data.level);
  const numCtx = data.num_ctx || previous?.numCtx || 0;
  const peak = Math.max(previous?.peak || 0, data.peak_prompt_tokens || 0);
  const flags = new Set([...(previous?.flags || []), ...String(data.flags || '').split(',').filter(Boolean)]);
  return {
    numCtx,
    peak,
    percent: numCtx ? Math.round(peak / numCtx * 1000) / 10 : 0,
    level,
    flags: [...flags],
    llmCalls: (previous?.llmCalls || 0) + (data.calls || 0),
    toolsActive: data.tools_active ?? previous?.toolsActive ?? null,
    toolsTotal: data.tools_total ?? previous?.toolsTotal ?? null
  };
}

function meterCard(agent) {
  const usage = agent.usage;
  const percent = usage ? Math.min(100, usage.percent) : 0;
  const level = usage?.level || 'ok';
  const failureKinds = Object.entries(agent.failures)
    .map(([kind, count]) => `${count} ${FAILURE_LABEL[kind] || kind}`).join(' · ');
  const tools = usage?.toolsTotal != null ? ` · tools ${usage.toolsActive}/${usage.toolsTotal}` : '';
  const flags = (usage?.flags || []).filter(flag => FLAG_LABEL[flag]).map(flag =>
    `<span class="badge ${CRITICAL_FLAGS.has(flag) ? 'badge-danger' : 'badge-warning'}">${esc(FLAG_LABEL[flag])}</span>`
  ).join(' ');
  const windowText = usage
    ? `Peak ${usage.percent}% of ${usage.numCtx} tokens`
    : 'No model usage yet';
  return `
    <article class="agent-meter" data-agent="${esc(agent.id)}">
      <div class="agent-meter-head">
        <strong>${esc(agent.id)}</strong>
        <span class="agent-meter-state" data-state="${esc(agent.state)}">${esc(STATE_LABEL[agent.state] || agent.state)}</span>
      </div>
      <div class="agent-meter-bar" role="progressbar" aria-label="Context window used by ${esc(agent.id)}"
           aria-valuemin="0" aria-valuemax="100" aria-valuenow="${Math.round(percent)}">
        <span class="agent-meter-fill" data-level="${esc(level)}" style="width: ${percent}%"></span>
      </div>
      <p class="text-muted small">${esc(windowText)} · ${agent.calls} tool calls · ${agent.errors ? `${agent.errors} failed` : 'no failures'}${failureKinds ? ` (${esc(failureKinds)})` : ''}${esc(tools)}</p>
      ${flags ? `<p class="agent-meter-flags">${flags}</p>` : ''}
    </article>`;
}

export function createAgentMeters(container) {
  const agents = new Map();

  function agentFor(id) {
    if (!agents.has(id)) {
      agents.set(id, { id, state: 'idle', calls: 0, errors: 0, failures: {}, usage: null });
    }
    return agents.get(id);
  }

  function render() {
    container.innerHTML = agents.size
      ? [...agents.values()].map(meterCard).join('')
      : '<p class="text-muted small">Agents appear here as the run starts.</p>';
  }

  function reset() {
    agents.clear();
    render();
  }

  /** Live telemetry: called for every event of the run, in order. */
  function onEvent(event) {
    const data = event.data || {};
    const id = data.agent_id || event.sender;
    if (!id || id === 'orchestrator' || id === 'unknown') return;
    const agent = agentFor(id);
    switch (event.event_type) {
      case 'AGENT_MESSAGE':
        if (data.status === 'running') agent.state = 'running';
        else if (data.status === 'error') agent.state = 'error';
        else if (data.status) agent.state = 'success';
        break;
      case 'TOOL_CALL_START':
        if (agent.state === 'idle') agent.state = 'running';
        break;
      case 'TOOL_CALL_END':
        agent.calls += 1;
        if (data.status === 'error') {
          agent.errors += 1;
          const kind = data.error_kind || 'tool_error';
          agent.failures[kind] = (agent.failures[kind] || 0) + 1;
        }
        break;
      case 'AGENT_USAGE':
        agent.usage = mergeUsage(agent.usage, data);
        break;
      default:
        return;
    }
    render();
  }

  /** A finished run's agent_stats, as saved on the server. */
  function setFromStats(stats) {
    agents.clear();
    for (const entry of stats?.agents || []) {
      agents.set(entry.agent_id, {
        id: entry.agent_id,
        state: 'done',
        calls: entry.tool_calls,
        errors: entry.tool_errors,
        failures: nonZero(entry.failures),
        usage: {
          numCtx: entry.num_ctx,
          peak: entry.peak_prompt_tokens,
          percent: entry.percent_peak,
          level: entry.level,
          flags: entry.flags || [],
          llmCalls: entry.llm_calls,
          toolsActive: entry.tools_active ?? null,
          toolsTotal: entry.tools_total ?? null
        }
      });
    }
    render();
  }

  return { reset, onEvent, setFromStats };
}

/** One line for a finished run: the worst window use, tool failures and scratchpad handoffs. */
export function runHealthHtml(stats) {
  const summary = stats?.summary;
  if (!summary) return '';
  const level = LEVEL_LABEL[summary.level] ? summary.level : 'unknown';
  return `<p class="run-health run-health-${esc(level)}"><strong>Run health ${esc(LEVEL_LABEL[level])}:</strong>
    worst context ${esc(summary.percent_peak)}% of its window · ${esc(summary.tool_calls)} tool calls, ${esc(summary.tool_errors)} failed ·
    ${esc(summary.scratchpad_reads)} scratchpad reads, ${esc(summary.scratchpad_writes)} writes.</p>`;
}
