import assert from 'node:assert/strict';
import test from 'node:test';
import { formatPastedMarkdown } from '../frontend/static/js/studio_paste_markdown.js';

test('formats a flattened agent prompt without changing its wording', () => {
  const input = '--- id: search-agent name: Search Orchestrator description: Primary orchestrator that initiates the search pipeline by saving the topic to scratchpad and calling the query-decomposer sub-agent. mode: agent model: qwen2.5-coder:latest tools: - call_subagent - write_agent_scratchpad environment: workspace version: 1 --- # Search Orchestrator ## role You are Search Orchestrator. Your sole role is to initiate the sequential research pipeline. ## purpose Receive the user\'s research question, save it to the shared scratchpad memory, and delegate execution to `query-decomposer`. ## boundaries - Save the user\'s research topic to scratchpad memory using `write_agent_scratchpad`. - Call `call_subagent` with `subagent_id: "query-decomposer"`. - Do not attempt to read scratchpad or execute searches; once `query-decomposer` is called, your job is complete.';
  const expected = `---
id: search-agent
name: Search Orchestrator
description: Primary orchestrator that initiates the search pipeline by saving the topic to scratchpad and calling the query-decomposer sub-agent.
mode: agent
model: qwen2.5-coder:latest
tools:
  - call_subagent
  - write_agent_scratchpad
environment: workspace
version: 1
---

# Search Orchestrator

## role

You are Search Orchestrator. Your sole role is to initiate the sequential research pipeline.

## purpose

Receive the user's research question, save it to the shared scratchpad memory, and delegate execution to \`query-decomposer\`.

## boundaries

- Save the user's research topic to scratchpad memory using \`write_agent_scratchpad\`.
- Call \`call_subagent\` with \`subagent_id: "query-decomposer"\`.
- Do not attempt to read scratchpad or execute searches; once \`query-decomposer\` is called, your job is complete.`;
  assert.equal(formatPastedMarkdown(input), expected);
});

test('formats explicit plain-text section labels and preserves fenced code', () => {
  const input = 'ROLE\nHelp.  \nPurpose:\nAnswer.\n```yaml\nROLE\n```\n  ROLE\n';
  assert.equal(formatPastedMarkdown(input), '## ROLE\nHelp.  \n## Purpose\nAnswer.\n```yaml\nROLE\n```\n  ROLE\n');
  assert.equal(formatPastedMarkdown('ROLE\r\nHelp.\r\nPurpose:\r\nAnswer.'), '## ROLE\nHelp.\n## Purpose\nAnswer.');
  assert.equal(formatPastedMarkdown('# Already Markdown\r\n\r\nText.\r\n'), '# Already Markdown\r\n\r\nText.\r\n');
});

test('leaves already formatted, arbitrary, and ambiguous text intact', () => {
  for (const input of [
    '# Title\n\n## role\n\nSome text.  \n- a\n- b\n',
    'Some unstructured prose with - dashes and # hashes.',
    '--- id: x name: X tools: - first - second --- # X ## unknown Something',
    '--- id: x name: "quoted" tools: - first - second --- # X ## role Something',
    '```text\n```not-a-closer\nROLE\n```\n',
  ]) assert.equal(formatPastedMarkdown(input), input);
});

test('ignores heading and list markers inside inline code', () => {
  const input = '--- id: x name: X tools: [] --- # X ## role Use `## role` as text. ## boundaries - Use `a - b`. - Finish.';
  assert.equal(formatPastedMarkdown(input), '---\nid: x\nname: X\ntools: []\n---\n\n# X\n\n## role\n\nUse `## role` as text.\n\n## boundaries\n\n- Use `a - b`.\n- Finish.');
  const multiTick = '--- id: x name: X tools: [] --- # X ## role Use ``a ` ## fake - b`` as text. ## boundaries - One. - Two.';
  const result = formatPastedMarkdown(multiTick);
  assert.ok(result.includes('Use ``a ` ## fake - b`` as text.'));
  assert.ok(result.includes('- One.\n- Two.'));
});

test('formats the reported search agent with its complete output format heading', () => {
  const input = "--- id: search-agent name: Search Orchestrator description: Primary orchestrator that initiates the search pipeline and synthesizes scratchpad findings into a cited report. mode: agent model: qwen2.5-coder:latest tools: - call_subagent - read_agent_scratchpad - write_agent_scratchpad environment: workspace version: 1 --- # Search Orchestrator ## role You are Search Orchestrator. You initiate research pipelines and synthesize multi-agent findings. ## purpose Start the sequential research pipeline by saving the user's topic to scratchpad memory, calling `query-decomposer`, and compiling the final report once all sub-agents finish. ## boundaries - Write the initial user question to the scratchpad, then call `query-decomposer`. - Do not execute searches yourself; wait for the sub-agent sequence to complete. - Read the final findings from the scratchpad and output a structured, cited answer. ## output format Lead with an executive answer, followed by detailed bullet points synthesized from the scratchpad evidence, and end with cited source URLs.";
  const expected = `---
id: search-agent
name: Search Orchestrator
description: Primary orchestrator that initiates the search pipeline and synthesizes scratchpad findings into a cited report.
mode: agent
model: qwen2.5-coder:latest
tools:
  - call_subagent
  - read_agent_scratchpad
  - write_agent_scratchpad
environment: workspace
version: 1
---

# Search Orchestrator

## role

You are Search Orchestrator. You initiate research pipelines and synthesize multi-agent findings.

## purpose

Start the sequential research pipeline by saving the user's topic to scratchpad memory, calling \`query-decomposer\`, and compiling the final report once all sub-agents finish.

## boundaries

- Write the initial user question to the scratchpad, then call \`query-decomposer\`.
- Do not execute searches yourself; wait for the sub-agent sequence to complete.
- Read the final findings from the scratchpad and output a structured, cited answer.

## output format

Lead with an executive answer, followed by detailed bullet points synthesized from the scratchpad evidence, and end with cited source URLs.`;
  assert.equal(formatPastedMarkdown(input), expected);
  const escaped = '\\' + input.replaceAll('_', '\\_').replaceAll('`', '\\`');
  assert.equal(formatPastedMarkdown(escaped), expected);
  assert.equal(formatPastedMarkdown(`Existing draft\n\n${input}\n`), `Existing draft\n\n${expected}\n`);
  assert.equal(formatPastedMarkdown(`\`\`\`text\n${input}\n\`\`\``), `\`\`\`text\n${input}\n\`\`\``);
});
