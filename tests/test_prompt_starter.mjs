import assert from 'node:assert/strict';
import test from 'node:test';
import {
  starterVariables, renderStarter, replaceToolVariables, currentToolNames,
  setFrontmatterFields, starterEditorText, extractSubagentConfig,
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

test('tool selection writes real tool names into the YAML list while preserving prompt content', () => {
  const definition = '---\nid: "helper"\ntools: []\ndescription: "Useful helper"\n---\n# Helper\n';
  const selected = replaceToolVariables(definition, ['read_file', 'write_file']);
  assert.match(selected, /tools:\n {2}- read_file\n {2}- write_file\ndescription:/);
  assert.match(selected, /# Helper/);
  assert.deepEqual(currentToolNames(selected), ['read_file', 'write_file']);
  const updated = replaceToolVariables(selected, ['write_file']);
  assert.match(updated, /tools:\n {2}- write_file\ndescription:/);
  assert.deepEqual(currentToolNames(updated), ['write_file']);
  const cleared = replaceToolVariables(updated, []);
  assert.match(cleared, /tools: \[\]\ndescription:/);
  assert.match(cleared, /# Helper/);
  assert.deepEqual(currentToolNames(cleared), []);
});

test('currentToolNames ignores stale bracket placeholders and missing frontmatter', () => {
  assert.deepEqual(currentToolNames('---\ntools:\n  - "[TOOL 1]"\n  - read_file\n---\nbody'), ['read_file']);
  assert.deepEqual(currentToolNames('no frontmatter here'), []);
  assert.deepEqual(currentToolNames('---\nid: x\n---\nbody'), []);
});

test('setFrontmatterFields inserts new keys and updates existing ones', () => {
  const definition = '---\nid: "helper"\ntools: []\ndescription: "Useful helper"\n---\n# Helper\n';
  const withReminder = setFrontmatterFields(definition, [
    ['remind_every_turn', 'true'],
    ['tool_reminder', JSON.stringify('Always cite your sources.')],
  ]);
  assert.match(withReminder, /remind_every_turn: true/);
  assert.match(withReminder, /tool_reminder: "Always cite your sources\."/);
  assert.match(withReminder, /# Helper/);
  const updatedAgain = setFrontmatterFields(withReminder, [['remind_every_turn', 'false']]);
  assert.match(updatedAgain, /remind_every_turn: false/);
  assert.equal((updatedAgain.match(/remind_every_turn:/g) || []).length, 1);
  assert.throws(() => setFrontmatterFields('no frontmatter', [['x', 'y']]));
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
