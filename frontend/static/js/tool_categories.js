import { esc } from './shell.js';

const CATEGORIES = [
  { label: 'Memory & scratchpads', names: [
    'write_coding_scratchpad', 'read_coding_scratchpad',
    'write_chat_scratchpad', 'read_chat_scratchpad',
    'write_squad_scratchpad', 'read_squad_scratchpad', 'clear_squad_scratchpad',
  ] },
  { label: 'Files & workspace', names: [
    'map_files', 'read_file', 'write_file', 'delete_file', 'create_folder',
    'search_workspace', 'project_status',
  ] },
  { label: 'Agents & handoffs', names: ['call_subagent', 'agent_info'] },
  { label: 'Knowledge & research', names: [
    'get_knowledge_catalog', 'search_knowledge_branch', 'list_knowledge_branch_documents',
    'read_knowledge_branch_document', 'rag_agent_tool',
  ] },
  { label: 'Code & editor', names: [
    'editor', 'extract_docstrings', 'run_python_code', 'send_code_to_editor',
    'run_code_in_editor', 'preview_editor_code', 'inspect_http_routes', 'preview_code_change',
  ] },
  { label: 'User interaction & utilities', names: ['ask_user_window', 'ask_user_questions', 'calculator'] },
  { label: 'Other tools', names: [] },
];

const byName = new Map(CATEGORIES.flatMap(category => category.names.map(name => [name, category.label])));

export function toolCategory(tool) {
  return byName.get(tool.name) || 'Other tools';
}

export function groupTools(tools) {
  const grouped = new Map(CATEGORIES.map(category => [category.label, []]));
  for (const tool of tools) grouped.get(toolCategory(tool)).push(tool);
  return [...grouped].filter(([, items]) => items.length)
    .map(([label, items]) => [label, items.sort((a, b) => a.name.localeCompare(b.name))]);
}

export function toolOptionGroups(tools) {
  return groupTools(tools).map(([label, items]) =>
    `<optgroup label="${esc(label)}">${items.map(tool =>
      `<option value="${esc(tool.name)}" title="${esc(Array.isArray(tool.description) ? tool.description.join(' ') : tool.description || '')}">${esc(tool.name)} · ${esc(tool.provider || 'Other')}</option>`
    ).join('')}</optgroup>`).join('');
}
