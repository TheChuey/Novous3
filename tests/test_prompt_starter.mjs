import assert from 'node:assert/strict';
import test from 'node:test';
import {
  starterVariables, renderStarter, replaceToolVariables, addToolParameterVariables,
  starterEditorText, extractSubagentConfig,
} from '../frontend/static/js/prompt_starter.js';

test('finds distinct variables and replaces every occurrence without touching other text', () => {
  const text = '# [AGENT NAME]\n## Purpose\n[PURPOSE]\n## Sub-agents\n### [SUB-AGENT ID]\n## Custom\n[EXTRA TASK] [EXTRA TASK]\n';
  const variables = starterVariables(text);
  assert.equal(variables.filter(name => name === 'SUB-AGENT ID').length, 1);
  assert.ok(variables.includes('EXTRA TASK'));
  const rendered = renderStarter(text, { 'SUB-AGENT ID': 'worker', 'EXTRA TASK': 'Review evidence' });
  assert.match(rendered, /### worker/);
  assert.match(rendered, /Review evidence Review evidence/);
  assert.match(rendered, /\[AGENT NAME\]/);
  assert.equal(renderStarter('[TASK]', { TASK: '$& $1' }), '$& $1');
});

test('ignores normal lowercase Markdown links and empty values', () => {
  assert.deepEqual(starterVariables('[link](https://example.com) [TASK]'), ['TASK']);
  assert.equal(renderStarter('[TASK]', { TASK: '  ' }), '[TASK]');
});

test('empty tool slots are omitted from generated YAML', () => {
  assert.equal(renderStarter('tools:\n  - "[TOOL 1]"\n  - "[TOOL 2]"\n', {
    'TOOL 1': 'read_file',
  }), 'tools:\n  - "read_file"\n  - ""\n');
});

test('tool selection replaces the YAML list while preserving prompt content', () => {
  const definition = '---\nid: "helper"\ntools: []\ndescription: "Useful helper"\n---\n# Helper\n';
  const selected = replaceToolVariables(definition, [1, 2]);
  assert.match(selected, /tools:\n  - "\[TOOL 1\]"\n  - "\[TOOL 2\]"\ndescription:/);
  assert.match(selected, /# Helper/);
  const updated = replaceToolVariables(selected, [2]);
  assert.match(updated, /tools:\n  - "\[TOOL 2\]"\ndescription:/);
  const cleared = replaceToolVariables(updated, []);
  assert.match(cleared, /tools: \[\]\ndescription:/);
  assert.match(cleared, /# Helper/);
});

test('tool parameters get fillable variables immediately after their colons', () => {
  const instructions = [
    'Parameters:',
    '- agents (required): The agent IDs to call.',
    '- timeout: Maximum wait in seconds.',
    '- kind: Entry kind to filter by, or empty for all kinds.',
    'Return format: JSON.',
  ].join('\n');
  const descriptions = {};
  const withVariables = addToolParameterVariables(instructions, 2, descriptions);
  assert.match(withVariables, /- agents \(required\): \[TOOL 2 AGENTS\] - The agent IDs to call\./);
  assert.match(withVariables, /- timeout: \[TOOL 2 TIMEOUT\] - Maximum wait in seconds\./);
  assert.match(withVariables, /- kind: \[TOOL 2 KIND\] - Entry kind to filter by, or empty for all kinds\./);
  assert.match(withVariables, /Return format: JSON\./);
  assert.deepEqual(starterVariables(withVariables), ['TOOL 2 AGENTS', 'TOOL 2 TIMEOUT', 'TOOL 2 KIND']);
  assert.equal(descriptions['TOOL 2 KIND'], 'Entry kind to filter by, or empty for all kinds.');
});

test('serializes inline controls as placeholders and preserves editable line breaks', () => {
  const text = value => ({ nodeType: 3, nodeValue: value });
  const element = (tagName, childNodes = [], placeholder) => ({
    nodeType: 1, tagName, childNodes,
    dataset: { placeholder },
    hasAttribute: name => name === 'data-placeholder' && placeholder !== undefined,
  });
  const editor = element('DIV', [
    text('# Heading\nUse '),
    element('BUTTON', [text('read_chat_scratchpad')], 'MEMORY TOOL'),
    text('.'),
    element('DIV', [element('BR')]),
    element('DIV', [text('Next line')]),
  ]);
  const template = starterEditorText(editor);
  assert.equal(template, '# Heading\nUse [MEMORY TOOL].\n\nNext line');
  assert.equal(renderStarter(template, { 'MEMORY TOOL': 'read_chat_scratchpad' }),
    '# Heading\nUse read_chat_scratchpad.\n\nNext line');
});

test('extractSubagentConfig returns null when no sub-agent is selected', () => {
  assert.equal(extractSubagentConfig({}), null);
  assert.equal(extractSubagentConfig({ 'SUB-AGENT ID': '' }), null);
  assert.equal(extractSubagentConfig({ 'SUB-AGENT ID': '   ' }), null);
});

test('extractSubagentConfig returns id and usage from filled values', () => {
  const config = extractSubagentConfig({
    'SUB-AGENT ID': 'rag-retriever',
    'CONDITION': 'User asks about indexed notes',
    'TASK': 'Pass the question as task_prompt',
    'HOW TO USE THE RESULT': 'Present the retrieved context to the user',
  });
  assert.deepEqual(config, {
    id: 'rag-retriever',
    usage: 'User asks about indexed notes\nPass the question as task_prompt\nPresent the retrieved context to the user',
  });
});

test('extractSubagentConfig handles partial usage fields', () => {
  const config = extractSubagentConfig({
    'SUB-AGENT ID': 'worker',
    'CONDITION': 'Always',
  });
  assert.deepEqual(config, { id: 'worker', usage: 'Always' });
});

test('extractSubagentConfig accepts SUBAGENT ID variant', () => {
  const config = extractSubagentConfig({ 'SUBAGENT ID': 'helper' });
  assert.deepEqual(config, { id: 'helper', usage: '' });
});
