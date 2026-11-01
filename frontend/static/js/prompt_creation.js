import { Api } from './api.js';

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[c]));
}

function appendMarkdownInline(parent, text) {
  const pattern = /(`[^`]+`|\*\*[^*]+\*\*|__[^_]+__|\*[^*]+\*|_[^_]+_|~~[^~]+~~|\[([^\]]+)\]\(([^)\s]+)\))/g;
  let lastIndex = 0;
  let match;

  while ((match = pattern.exec(text)) !== null) {
    parent.appendChild(document.createTextNode(text.slice(lastIndex, match.index)));
    const token = match[0];
    let element;

    if (token.startsWith('`')) {
      element = document.createElement('code');
      element.textContent = token.slice(1, -1);
    } else if (token.startsWith('**') || token.startsWith('__')) {
      element = document.createElement('strong');
      element.textContent = token.slice(2, -2);
    } else if (token.startsWith('~~')) {
      element = document.createElement('del');
      element.textContent = token.slice(2, -2);
    } else if (token.startsWith('*') || token.startsWith('_')) {
      element = document.createElement('em');
      element.textContent = token.slice(1, -1);
    } else {
      element = document.createElement('a');
      element.textContent = match[2];
      try {
        const url = new URL(match[3], window.location.href);
        if (['http:', 'https:', 'mailto:'].includes(url.protocol)) {
          element.href = url.href;
          element.rel = 'noopener noreferrer';
        } else {
          element = document.createTextNode(match[2]);
        }
      } catch {
        element = document.createTextNode(match[2]);
      }
    }

    parent.appendChild(element);
    lastIndex = pattern.lastIndex;
  }

  parent.appendChild(document.createTextNode(text.slice(lastIndex)));
}

function appendMarkdown(parent, markdown) {
  const lines = String(markdown ?? '').replace(/\r\n?/g, '\n').split('\n');
  let paragraph = [];
  let list = null;
  let codeLines = null;
  const isTableDelimiter = line =>
    /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(line);
  const splitTableRow = line =>
    line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map(cell => cell.trim());

  const flushParagraph = () => {
    if (!paragraph.length) return;
    const block = document.createElement('p');
    paragraph.forEach((line, index) => {
      if (index) block.appendChild(document.createTextNode(' '));
      appendMarkdownInline(block, line);
    });
    parent.appendChild(block);
    paragraph = [];
  };
  const flushList = () => {
    if (list) parent.appendChild(list);
    list = null;
  };

  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    const fence = line.match(/^\s*```([^`]*)$/);
    if (codeLines) {
      if (fence) {
        const code = document.createElement('code');
        code.textContent = codeLines.join('\n');
        const pre = document.createElement('pre');
        pre.appendChild(code);
        parent.appendChild(pre);
        codeLines = null;
      } else {
        codeLines.push(line);
      }
      continue;
    }
    if (fence) {
      flushParagraph();
      flushList();
      codeLines = [];
      continue;
    }
    if (!line.trim()) {
      flushParagraph();
      flushList();
      continue;
    }

    if (/^\s{0,3}(?:---+|___+|\*\*\*+)\s*$/.test(line)) {
      flushParagraph();
      flushList();
      parent.appendChild(document.createElement('hr'));
      continue;
    }

    if (line.includes('|') && lines[index + 1] && isTableDelimiter(lines[index + 1])) {
      flushParagraph();
      flushList();
      const headers = splitTableRow(line);
      const table = document.createElement('table');
      table.className = 'prompt-markdown-table';
      const thead = document.createElement('thead');
      const headerRow = document.createElement('tr');
      headers.forEach(text => {
        const cell = document.createElement('th');
        cell.scope = 'col';
        appendMarkdownInline(cell, text);
        headerRow.appendChild(cell);
      });
      thead.appendChild(headerRow);
      table.appendChild(thead);

      const tbody = document.createElement('tbody');
      for (index += 2; index < lines.length && lines[index].includes('|'); index += 1) {
        const row = document.createElement('tr');
        splitTableRow(lines[index]).forEach((text, cellIndex) => {
          const cell = document.createElement('td');
          if (headers[cellIndex]) appendMarkdownInline(cell, text);
          row.appendChild(cell);
        });
        tbody.appendChild(row);
      }
      table.appendChild(tbody);
      parent.appendChild(table);
      index -= 1;
      continue;
    }

    const heading = line.match(/^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$/);
    if (heading) {
      flushParagraph();
      flushList();
      const element = document.createElement(`h${heading[1].length}`);
      appendMarkdownInline(element, heading[2]);
      parent.appendChild(element);
      continue;
    }

    const item = line.match(/^\s{0,3}([-*+]|\d+[.)])\s+(.+)$/);
    if (item) {
      flushParagraph();
      const ordered = /^\d/.test(item[1]);
      if (!list || (list.tagName === 'OL') !== ordered) {
        flushList();
        list = document.createElement(ordered ? 'ol' : 'ul');
      }
      const element = document.createElement('li');
      const task = item[2].match(/^\[([ xX])\]\s+(.+)$/);
      if (task) {
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.checked = task[1].toLowerCase() === 'x';
        checkbox.disabled = true;
        checkbox.setAttribute('aria-label', checkbox.checked ? 'Completed task' : 'Incomplete task');
        element.classList.add('prompt-markdown-task');
        element.append(checkbox, document.createTextNode(' '));
        appendMarkdownInline(element, task[2]);
      } else {
        appendMarkdownInline(element, item[2]);
      }
      list.appendChild(element);
      continue;
    }

    flushList();
    const quote = line.match(/^\s{0,3}>\s?(.*)$/);
    if (quote) {
      flushParagraph();
      const blockquote = document.createElement('blockquote');
      appendMarkdownInline(blockquote, quote[1]);
      parent.appendChild(blockquote);
      continue;
    }

    paragraph.push(line.trim());
  }

  if (codeLines) {
    const code = document.createElement('code');
    code.textContent = codeLines.join('\n');
    const pre = document.createElement('pre');
    pre.appendChild(code);
    parent.appendChild(pre);
  }
  flushParagraph();
  flushList();
}

export function renderPromptCreationView(container) {
  container.innerHTML = `
    <div class="prompt-creation-flow">
      <section class="card prompt-chat-card" aria-labelledby="pc-chat-title">
        <div class="codebuilder-chat-heading">
          <h2 id="pc-chat-title">Chat Agent</h2>
          <span id="pc-chat-status" class="text-muted small">Loading agents…</span>
          <span class="prompt-chat-font-controls" role="group" aria-label="Chat text size">
            <button type="button" class="btn btn-sm" id="pc-font-smaller" title="Smaller text" aria-label="Decrease chat text size">A−</button>
            <button type="button" class="btn btn-sm" id="pc-font-larger" title="Larger text" aria-label="Increase chat text size">A+</button>
          </span>
        </div>
        <p class="text-muted small prompt-chat-note">
          Chat with an available agent to clarify and shape your prompt. Responses use cleaner Markdown formatting when possible.
        </p>
        <div id="pc-chat-messages" class="codebuilder-chat-messages" aria-live="polite">
          <div class="codebuilder-chat-message system">Describe the prompt you want to create, and we’ll work through it together.</div>
        </div>
        <form id="pc-chat-form" class="codebuilder-chat-form">
          <label class="codebuilder-model-picker">
            <span class="text-muted small">Agent</span>
            <select id="pc-chat-agent" class="form-select" aria-label="Choose chat agent">
              <option value="">Loading agents…</option>
            </select>
            <span class="text-muted small">Model</span>
            <select id="pc-chat-model" class="form-select" aria-label="Choose model">
              <option value="">Loading models…</option>
            </select>
          </label>
          <textarea id="pc-chat-input" class="form-input" rows="3"
                    placeholder="What should this prompt help its agent do?"
                    aria-label="Message to chat agent"></textarea>
          <div class="codebuilder-chat-actions">
            <button id="pc-chat-clear" class="btn btn-sm" type="button">Clear</button>
            <button class="btn btn-primary" type="submit" disabled>Send</button>
          </div>
        </form>
      </section>
    </div>`;

  const chatAgentSel = container.querySelector('#pc-chat-agent');
  const chatModelSel = container.querySelector('#pc-chat-model');
  const chatInput = container.querySelector('#pc-chat-input');
  const chatMessages = container.querySelector('#pc-chat-messages');
  const chatStatus = container.querySelector('#pc-chat-status');
  const chatForm = container.querySelector('#pc-chat-form');
  const flow = container.querySelector('.prompt-creation-flow');

  let promptAgents = [];
  let selectedChatAgent = null;
  let chatPending = false;

  async function loadPromptAgents() {
    try {
      const [workspaceData, codeBuilderData, promptData] = await Promise.all([
        Api.getAgents(),
        Api.getCodeBuilderAgents(),
        Api.getAgents('prompt-creation')
      ]);
      const label = environment => ({ codebuilder: 'CodeBuilder', 'prompt-creation': 'Prompt Creation' })[environment] || 'Workspace';
      const agents = [
        ...(workspaceData.agents || []).map(agent => ({ ...agent, environment: 'workspace' })),
        ...(codeBuilderData.agents || []).map(agent => ({ ...agent, environment: 'codebuilder' })),
        ...(promptData.agents || []).map(agent => ({ ...agent, environment: 'prompt-creation' }))
      ];
      promptAgents = agents;
      chatAgentSel.innerHTML = agents.length
        ? agents.map(a => `<option value="${esc(a.environment)}:${esc(a.id)}">${esc(a.name)} — ${label(a.environment)}</option>`).join('')
        : '<option value="">No agents available</option>';
      let lastAgent = null;
      try { lastAgent = localStorage.getItem('novous2.prompt-creation.lastChatAgent'); } catch { /* storage unavailable */ }
      selectedChatAgent = agents.find(agent => `${agent.environment}:${agent.id}` === lastAgent)
        || agents.find(agent => agent.environment === 'workspace') || agents[0] || null;
      if (selectedChatAgent) {
        chatAgentSel.value = `${selectedChatAgent.environment}:${selectedChatAgent.id}`;
        chatStatus.textContent = `Agent: ${selectedChatAgent.name}`;
      } else {
        chatStatus.textContent = 'No agents available.';
      }

      await loadPromptChatModels();
      updateChatControls();
    } catch (err) {
      chatAgentSel.innerHTML = `<option value="">${esc(err.message)}</option>`;
      chatStatus.textContent = 'Could not load agents.';
    }
  }

  function updateChatControls() {
    const sendButton = chatForm.querySelector('button[type="submit"]');
    sendButton.disabled = chatPending || !selectedChatAgent || !chatModelSel.value || chatModelSel.disabled;
    chatInput.disabled = chatPending;
    chatAgentSel.disabled = chatPending || !promptAgents.length;
  }

  function appendChatMessage(role, content) {
    const message = document.createElement('div');
    message.className = `codebuilder-chat-message ${role}`;
    if (role === 'assistant') {
      message.classList.add('prompt-rich-markdown');
      appendMarkdown(message, content);
    } else {
      message.textContent = content;
    }
    chatMessages.appendChild(message);
    chatMessages.scrollTop = chatMessages.scrollHeight;
  }

  const chatFontKey = 'novous2.prompt-creation.chat-font-size';
  let chatFontSize = 14;
  try {
    const storedSize = Number(localStorage.getItem(chatFontKey));
    if (Number.isFinite(storedSize) && storedSize >= 11 && storedSize <= 32) chatFontSize = storedSize;
  } catch (error) {
    console.warn('Could not load saved chat text size.', error);
  }
  function setChatFontSize(size) {
    chatFontSize = Math.min(32, Math.max(11, size));
    flow.style.setProperty('--prompt-chat-font-size', `${chatFontSize}px`);
    try {
      localStorage.setItem(chatFontKey, String(chatFontSize));
    } catch (error) {
      console.warn('Could not save chat text size.', error);
    }
  }
  setChatFontSize(chatFontSize);
  container.querySelector('#pc-font-smaller').addEventListener('click', () => setChatFontSize(chatFontSize - 1));
  container.querySelector('#pc-font-larger').addEventListener('click', () => setChatFontSize(chatFontSize + 2));

  async function loadPromptChatModels() {
    chatModelSel.disabled = true;
    chatModelSel.innerHTML = '<option value="">Loading models…</option>';
    try {
      const data = await Api.getModels();
      const models = (data.models || []).slice().sort();
      if (!models.length) {
        chatModelSel.innerHTML = '<option value="">No installed models</option>';
        chatStatus.textContent = data.error || 'No installed models detected.';
        updateChatControls();
        return;
      }
      chatModelSel.innerHTML = models.map(model =>
        `<option value="${esc(model)}">${esc(model)}</option>`
      ).join('');
      const preferred = selectedChatAgent?.model || data.default;
      chatModelSel.value = models.includes(preferred) ? preferred : models[0];
      chatModelSel.disabled = false;
      updateChatControls();
    } catch (err) {
      chatModelSel.innerHTML = `<option value="">${esc(err.message)}</option>`;
      chatStatus.textContent = 'Could not load models.';
      updateChatControls();
    }
  }

  loadPromptAgents();

  chatAgentSel.addEventListener('change', () => {
    const [environment, agentId] = chatAgentSel.value.split(':', 2);
    try { localStorage.setItem('novous2.prompt-creation.lastChatAgent', chatAgentSel.value); } catch { /* storage unavailable */ }
    selectedChatAgent = promptAgents.find(agent =>
      agent.environment === environment && agent.id === agentId
    ) || null;
    chatMessages.replaceChildren();
    if (selectedChatAgent) {
      appendChatMessage('system', `Chatting with ${selectedChatAgent.name}.`);
      chatStatus.textContent = `Agent: ${selectedChatAgent.name}`;
    } else {
      chatStatus.textContent = 'Select an agent to start.';
    }
    updateChatControls();
    loadPromptChatModels();
  });

  chatModelSel.addEventListener('change', updateChatControls);

  chatForm.addEventListener('submit', async event => {
    event.preventDefault();
    const message = chatInput.value.trim();
    if (!message || chatPending || !selectedChatAgent || !chatModelSel.value) return;

    appendChatMessage('user', message);
    chatInput.value = '';
    chatPending = true;
    chatStatus.textContent = `Waiting for ${selectedChatAgent.name}…`;
    updateChatControls();
    const sessionId = `prompt-creation-${selectedChatAgent.environment}-${selectedChatAgent.id}`;
    try {
      const response = await Api.sendMessage(
        `${message}\n\nWhen you write a prompt, reply with only the prompt as a Markdown document: one "# Title" line, a one-sentence description, then "## " headings named only for what they contain (for example "## Role", never "## Section: Role" or "## Role Section") each followed by plain-text instructions. No preamble or closing commentary.`,
        selectedChatAgent.id,
        chatModelSel.value,
        sessionId,
        selectedChatAgent.environment
      );
      const reply = String(response.reply || '').trim();
      appendChatMessage('assistant', reply || 'The agent returned an empty response.');
      chatStatus.textContent = `${selectedChatAgent.name} · ${response.model || chatModelSel.value}`;
    } catch (err) {
      appendChatMessage('system', `Request failed: ${err.message}`);
      chatStatus.textContent = 'Request failed.';
    } finally {
      chatPending = false;
      updateChatControls();
      chatInput.focus();
    }
  });

  chatInput.addEventListener('keydown', event => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      chatForm.requestSubmit();
    }
  });

  container.querySelector('#pc-chat-clear').addEventListener('click', async () => {
    if (selectedChatAgent) {
      const sessionId = `prompt-creation-${selectedChatAgent.environment}-${selectedChatAgent.id}`;
      try {
        await Api.resetChat(sessionId);
      } catch (err) {
        chatStatus.textContent = `Could not clear chat: ${err.message}`;
        return;
      }
    }
    chatMessages.replaceChildren();
    appendChatMessage('system', selectedChatAgent
      ? `Chat cleared. Continue with ${selectedChatAgent.name}.`
      : 'Chat cleared.');
    updateChatControls();
    chatInput.focus();
  });
}
