/**
 * Context window panel: what the model is given for a chat session, how full it is, and how
 * many more turns fit before the runtime starts dropping the oldest ones. Built with DOM calls
 * and textContent, so message text from the model is never parsed as markup.
 */

const SEGMENTS = [
  { key: 'system', label: 'System prompt' },
  { key: 'tools', label: 'Tool schemas' },
  { key: 'history', label: 'Conversation' },
  { key: 'reply_reserve', label: 'Reply reserve' },
  { key: 'free', label: 'Free' }
];

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

export function contextLevel(percent) {
  if (percent >= 95) return 'crit';
  if (percent >= 80) return 'warn';
  return 'ok';
}

/** The compact form the chat reply carries, taken from a full report. */
export function summarizeReport(report) {
  return {
    percent: report.percent,
    turns_remaining: report.forecast.turns_remaining,
    turns_dropped: report.forecast.turns_dropped,
    sentence: report.forecast.sentence
  };
}

export function contextChipText(summary) {
  const outcome = summary.turns_dropped
    ? 'older turns dropped'
    : `~${summary.turns_remaining} turns left`;
  return `Context ${Math.round(summary.percent)}% · ${outcome}`;
}

function usageBar(report) {
  const total = Math.max(report.num_ctx, report.used_tokens, 1);
  const bar = el('div', 'ctx-bar');
  bar.setAttribute('role', 'progressbar');
  bar.setAttribute('aria-label', 'Context window usage');
  bar.setAttribute('aria-valuemin', '0');
  bar.setAttribute('aria-valuemax', '100');
  bar.setAttribute('aria-valuenow', String(Math.min(100, Math.round(report.percent))));
  for (const segment of SEGMENTS) {
    const tokens = report.segments[segment.key] || 0;
    if (tokens <= 0) continue;
    const part = el('span', `ctx-seg ctx-seg-${segment.key}`);
    part.style.width = `${(tokens / total) * 100}%`;
    part.title = `${segment.label}: ${tokens} tokens`;
    bar.append(part);
  }
  return bar;
}

function legend(report) {
  const list = el('div', 'ctx-legend');
  for (const segment of SEGMENTS) {
    const tokens = report.segments[segment.key] || 0;
    const item = el('span', 'ctx-legend-item');
    item.append(el('span', `ctx-swatch ctx-seg-${segment.key}`), `${segment.label} ${tokens}`);
    list.append(item);
  }
  return list;
}

function forecast(report) {
  const box = el('div', 'ctx-forecast');
  box.append(el('p', 'ctx-forecast-sentence', report.forecast.sentence));
  box.append(el('p', 'text-muted small',
    `${report.forecast.note} Average turn about ${report.forecast.average_turn_tokens} tokens; ` +
    `this window holds about ${report.forecast.turns_capacity} turns of history.`));
  return box;
}

function warnings(report) {
  if (!report.warnings.length) return null;
  const list = el('ul', 'ctx-warnings');
  for (const warning of report.warnings) {
    list.append(el('li', `ctx-warning ctx-warning-${warning.level}`, warning.message));
  }
  return list;
}

function messageRows(report) {
  const dropped = report.messages.filter(row => !row.fits).length;
  const details = el('details', 'ctx-messages');
  details.open = true;
  details.append(el('summary', null,
    `Messages (${report.messages.length}${dropped ? `, ${dropped} no longer fit` : ''})`));
  if (!report.messages.length) {
    details.append(el('p', 'text-muted small', 'No messages yet. Send a message to start the conversation.'));
    return details;
  }
  for (const row of report.messages) {
    const line = el('div', `ctx-row${row.fits ? '' : ' ctx-row-dropped'}`);
    const role = row.tool_name ? `${row.role} · ${row.tool_name}` : row.role;
    line.append(
      el('span', 'ctx-turn', `T${row.turn}`),
      el('span', 'ctx-role', role),
      el('span', 'ctx-tokens', `${row.tokens} tok`),
      el('span', 'ctx-state', row.fits ? '' : 'dropped'),
      el('span', 'ctx-preview', row.preview || '(empty)')
    );
    details.append(line);
  }
  return details;
}

const FAILURE_LABEL = {
  unknown_tool: 'unknown tool', bad_arguments: 'bad arguments', tool_error: 'tool error',
  model_error: 'model error', rounds_exhausted: 'out of tool rounds'
};

function toolHealth(health) {
  const box = el('div', 'ctx-tool-health');
  box.append(el('h4', null, 'Tool health'));
  const calls = health.tool_calls || 0;
  const failed = health.tool_errors || 0;
  box.append(el('p', 'text-muted small',
    `${calls} tool call(s), ${failed} failed.` +
    (health.rounds_exhausted ? ' The model ran out of tool rounds at least once.' : '')));

  const kinds = Object.entries(health.failures || {}).filter(([, count]) => count > 0);
  if (kinds.length) {
    const list = el('ul', 'ctx-warnings');
    for (const [kind, count] of kinds) {
      list.append(el('li', 'ctx-warning ctx-warning-warning', `${count} × ${FAILURE_LABEL[kind] || kind}`));
    }
    box.append(list);
  }

  const recent = health.recent_failures || [];
  if (recent.length) {
    const details = el('details', 'ctx-messages');
    details.append(el('summary', null, `Recent failures (${recent.length})`));
    for (const failure of [...recent].reverse()) {
      const line = el('div', 'ctx-row');
      line.append(
        el('span', 'ctx-role', FAILURE_LABEL[failure.kind] || failure.kind),
        el('span', 'ctx-preview', failure.tool ? `${failure.tool}: ${failure.message}` : failure.message)
      );
      details.append(line);
    }
    box.append(details);
  }
  return box;
}

export function renderContextReport(report) {
  const root = el('div', 'ctx-report');
  const native = report.native_context ? ` · model maximum ${report.native_context}` : '';
  root.append(el('p', 'ctx-summary',
    `${report.model} · window ${report.num_ctx} tokens${native} · ` +
    `${report.used_tokens} used (${report.percent}%)`));
  root.append(usageBar(report), legend(report), forecast(report));

  const warningList = warnings(report);
  if (warningList) root.append(warningList);

  if (report.measured_prompt_tokens) {
    root.append(el('p', 'text-muted small',
      `Measured by Ollama: last prompt ${report.measured_prompt_tokens} tokens. ` +
      `Estimates are scaled by ${report.calibration}.`));
  }
  if (report.tool_health) root.append(toolHealth(report.tool_health));
  root.append(messageRows(report));
  return root;
}
