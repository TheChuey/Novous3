/**
 * Squad run client: starts an orchestration run and follows its SSE telemetry stream.
 * Shared by the Runs section and anything else that launches a squad.
 */
import { Api } from '../api.js';

export const PATTERN_INFO = {
  sequential: {
    label: 'Sequential',
    summary: 'Members run one after another, then the lead synthesizes from the scratchpad.'
  },
  fanout: {
    label: 'Fan-out',
    summary: 'Members work on the same task in parallel (bounded), and the lead merges every output.'
  },
  router: {
    label: 'Router',
    summary: 'The lead picks which members should handle the task; only those run.'
  }
};

/**
 * Start a run and stream its events. Resolves once the run is accepted.
 * @returns {Promise<{traceId: string, squadId: string, close: () => void}>}
 */
export async function startSquadRun({
  squadId, query, pattern = null, parameters = {}, onEvent = () => {}, onComplete = () => {}, onClosed = () => {}
}) {
  const started = await Api.startOrchestrationRun(squadId, query, 'all', parameters, pattern);
  const source = new EventSource(`/api/orchestration/stream/${encodeURIComponent(started.trace_id)}`);
  const seen = new Set();
  let finished = false;
  source.addEventListener('telemetry', message => {
    const event = JSON.parse(message.data);
    if (seen.has(event.sequence)) return;
    seen.add(event.sequence);
    onEvent(event);
    if (event.event_type === 'RUN_COMPLETE') {
      finished = true;
      source.close();
      onComplete(started.trace_id, event.data || {});
    }
  });
  source.addEventListener('error', () => {
    if (source.readyState === EventSource.CLOSED && !finished) onClosed();
  });
  return { traceId: started.trace_id, squadId: started.squad_id, close: () => source.close() };
}

/** One readable line per telemetry event; prompts and tool arguments are never in the stream. */
export function describeEvent(event) {
  const detail = event.data || {};
  const text = [
    `${event.sender} → ${event.receiver}`,
    detail.summary || '',
    detail.tool_name ? `tool=${detail.tool_name}` : '',
    detail.status ? `status=${detail.status}` : '',
    detail.duration_ms !== undefined ? `${detail.duration_ms}ms` : '',
    detail.path ? `path=${detail.path}` : '',
    detail.error || ''
  ].filter(Boolean).join(' · ');
  return {
    time: new Date(event.timestamp).toLocaleTimeString(),
    type: event.event_type,
    text,
    status: detail.status || ''
  };
}

/** Code-change preview carried by an event, if any (the editor can review those lines). */
export function codeChangeFromEvent(event) {
  const detail = event.data || {};
  if (!['CODE_CHANGE_PREVIEW', 'CODE_CHANGE_APPLIED'].includes(event.event_type) || !detail.path) return null;
  return {
    path: detail.path,
    changedLines: Array.isArray(detail.changed_lines) ? detail.changed_lines : [],
    fileExists: detail.file_exists !== false
  };
}
