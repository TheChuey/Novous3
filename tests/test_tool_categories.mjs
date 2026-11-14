import assert from 'node:assert/strict';
import test from 'node:test';
import { groupTools, toolCategory, toolOptionGroups } from '../frontend/static/js/tool_categories.js';

test('categorizes tools by purpose, not by their implementation provider', () => {
  const tools = [
    { name: 'write_squad_scratchpad', provider: 'scratchpadMemory' },
    { name: 'read_chat_scratchpad', provider: 'scratchpadMemory' },
    { name: 'create_folder', provider: 'core_engine' },
    { name: 'read_file', provider: 'tools' },
    { name: 'call_subagent', provider: 'orchestration' },
    { name: 'agent_info', provider: 'core_engine' },
    { name: 'rag_agent_tool', provider: 'core_engine' },
    { name: 'search_knowledge_branch', provider: 'ragMemory' },
    { name: 'editor', provider: 'editor' },
    { name: 'calculator', provider: 'core_engine' },
    { name: 'a_custom_tool', provider: 'custom' },
  ];
  const groups = groupTools(tools);
  assert.deepEqual(groups.map(([label]) => label), [
    'Memory & scratchpads', 'Files & workspace', 'Agents & handoffs',
    'Knowledge & research', 'Code & editor', 'User interaction & utilities', 'Other tools',
  ]);
  assert.deepEqual(groups[0][1].map(item => item.name), ['read_chat_scratchpad', 'write_squad_scratchpad']);
  assert.equal(toolCategory({ name: 'a_custom_tool' }), 'Other tools');
  assert.equal(groups.flatMap(([, items]) => items).length, tools.length);
  assert.equal(tools[0].name, 'write_squad_scratchpad');
});

test('groups selectors and escapes tool metadata', () => {
  const html = toolOptionGroups([
    { name: 'call_subagent', provider: 'orchestration', description: 'Delegate work' },
    { name: 'new<tool', provider: 'custom"provider', description: ['One &', 'two'] },
  ]);
  assert.match(html, /<optgroup label="Agents &amp; handoffs">/);
  assert.match(html, /value="call_subagent" title="Delegate work"/);
  assert.match(html, /<optgroup label="Other tools">/);
  assert.match(html, /value="new&lt;tool" title="One &amp; two"/);
  assert.match(html, /custom&quot;provider/);
  assert.equal(toolOptionGroups([]), '');
});
