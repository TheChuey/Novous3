import { createIcon } from './icons.js';

const TEST_FUNCTIONS = [
  { id: 'runTestSuite', label: 'Run Test Suite', icon: 'play-circle', group: 'Testing', action: null },
  { id: 'debugAgent', label: 'Debug Agent', icon: 'bug', group: 'Testing', action: null },
  { id: 'promptInspector', label: 'Prompt Inspector', icon: 'terminal', group: 'Testing', action: null },
  { id: 'outputEvaluator', label: 'Output Evaluator', icon: 'check-square', group: 'Testing', action: null },
  { id: 'benchmark', label: 'Benchmark', icon: 'gauge', group: 'Testing', action: null },
  { id: 'apiLogs', label: 'API Logs', icon: 'file-code', group: 'Testing', action: null },
  { id: 'diagnostics', label: 'Diagnostics', icon: 'activity', group: 'Testing', action: null },
  { id: 'wipeChat', label: 'Wipe Chat', icon: 'trash-2', group: 'Conversation', action: null },
  { id: 'viewScratchpad', label: 'View Scratchpad', icon: 'file-code', group: 'Scratchpad', action: null },
  { id: 'saveScratchpad', label: 'Save Scratchpad', icon: 'save', group: 'Scratchpad', action: null },
  { id: 'clearScratchpad', label: 'Clear Scratchpad', icon: 'trash-2', group: 'Scratchpad', action: null },
  { id: 'viewKnowledgeCatalog', label: 'Knowledge TOC', icon: 'database', group: 'Knowledge', action: null },
  { id: 'viewRag', label: 'Knowledge Sources', icon: 'database', group: 'Knowledge', action: null }
];

export function buildTestPanel(containerId, actions = {}) {
  const container = typeof containerId === 'string'
    ? document.getElementById(containerId)
    : containerId;

  if (!container) {
    console.error('Test panel container not found:', containerId);
    return;
  }

  container.replaceChildren();
  const groups = new Map();
  const groupFor = name => {
    if (!groups.has(name)) {
      const section = document.createElement('section');
      section.className = 'test-group';
      const heading = document.createElement('h4');
      heading.className = 'test-group-title';
      heading.textContent = name;
      const buttons = document.createElement('div');
      buttons.className = 'test-group-buttons';
      section.append(heading, buttons);
      container.appendChild(section);
      groups.set(name, { section, buttons });
    }
    return groups.get(name);
  };

  TEST_FUNCTIONS.forEach((test) => {
    const action = actions[test.id] || test.action;
    const button = document.createElement('button');
    const label = document.createElement('span');

    button.className = 'test-function';
    button.type = 'button';
    button.dataset.testFunction = test.id;
    label.textContent = test.label;
    button.append(createIcon(test.icon), label);

    if (typeof action !== 'function') {
      button.classList.add('disabled');
      button.disabled = true;
      button.title = 'Not implemented yet';
    } else {
      button.title = test.label;
      button.addEventListener('click', action);
    }

    groupFor(test.group).buttons.appendChild(button);
  });

  const todoSection = document.createElement('div');
  todoSection.className = 'todo-selector-section';

  const todoLabel = document.createElement('label');
  todoLabel.className = 'model-picker-label small text-muted';
  todoLabel.htmlFor = 'todo-file-select';
  todoLabel.textContent = 'Select To-Do File';

  const todoSelect = document.createElement('select');
  todoSelect.id = 'todo-file-select';
  todoSelect.className = 'cb-select';
  todoSelect.setAttribute('aria-label', 'Select a To-Do file');

  const refreshTodoFiles = document.createElement('button');
  refreshTodoFiles.id = 'todo-file-refresh';
  refreshTodoFiles.className = 'cb-btn';
  refreshTodoFiles.type = 'button';
  refreshTodoFiles.append(createIcon('refresh'), document.createTextNode('Refresh lists'));
  refreshTodoFiles.title = 'Refresh Markdown to-do lists';

  const loadingOption = document.createElement('option');
  loadingOption.value = '';
  loadingOption.textContent = 'Loading Markdown lists...';
  todoSelect.appendChild(loadingOption);

  todoSection.append(todoLabel, todoSelect, refreshTodoFiles);
  // The to-do list feeds Run Test Suite, so it sits in the Testing group.
  groupFor('Testing').section.appendChild(todoSection);
}
