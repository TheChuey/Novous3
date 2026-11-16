import { Api } from './api.js';
import { esc } from './shell.js';
import { groupTools } from './tool_categories.js';

const VARIABLE = /\[([A-Z][A-Z0-9 _-]{0,63})\]/g;

// What each standard agent-prompt variable means; sent to the LLM so generated
// text and appended context are understood in their role within the prompt.
const STARTER_VARIABLE_DESCRIPTIONS = {
  'AGENT NAME': "The agent's display name.",
  'AGENT ID': "The agent's unique file id (lowercase, hyphenated).",
  'DESCRIPTION': 'A short description of what the agent does.',
  'ROLE': 'Who the agent is and its primary responsibility.',
  'PURPOSE': 'The specific goal this agent must accomplish.',
  'RESPONSIBILITIES': 'The list of duties the agent is responsible for.',
  'EXECUTION INSTRUCTIONS': 'Step-by-step instructions the agent follows when performing its task.',
  'BOUNDARIES': 'Restrictions: what the agent must not do.',
  'COMMUNICATION': 'How the agent reports and communicates results.',
  'SUB-AGENT NAME': "The sub-agent's display name.",
  'GOAL': 'The goal the agent works toward.',
  'TASK': 'The task to send to a sub-agent.',
  'OUTPUT FORMAT': 'The format the agent must use for its final answer.',
  'CONDITION': 'When the action should happen.',
};

// Default pre-prompts the chain-fill wizard starts each variable with.
const STARTER_VARIABLE_PREPROMPTS = {
  'ROLE': 'Write the Role section for this agent: who the agent is and its primary responsibility, in 2-4 sentences addressed to the agent ("You are…").',
  'PURPOSE': 'Write the Purpose section: the specific goal this agent must accomplish, in 1-3 sentences.',
  'RESPONSIBILITIES': 'Write the Responsibilities section: a bullet list of 3-6 concrete duties this agent is responsible for.',
  'EXECUTION INSTRUCTIONS': 'Write the Execution Instructions section: a short numbered list of steps the agent follows when performing its task.',
  'BOUNDARIES': 'Write the Boundaries section: a bullet list of restrictions describing what the agent must not do.',
  'COMMUNICATION': 'Write the Communication section: a short bullet list of how the agent reports and communicates results.',
  'DESCRIPTION': 'Write a one-sentence description of what this agent does.',
  'OUTPUT FORMAT': 'Write the Output Format section: the exact format the agent must use for its final answer.',
};

export function starterVariables(text) {
  return [...new Set([...text.matchAll(VARIABLE)].map(match => match[1]))];
}

export function renderStarter(text, values) {
  return text.replace(VARIABLE, (match, name) => {
    const value = (values[name] || values[name.replace(/-/g, ' ')])?.trim();
    if (value) return value;
    return /^TOOL \d+$/.test(name) ? '' : match;
  });
}

export function replaceToolVariables(text, indexes) {
  const lines = text.replace(/\r\n/g, '\n').split('\n');
  if (lines[0]?.trim() !== '---') throw new Error('Add tools only to an agent definition with YAML frontmatter.');
  let end = lines.findIndex((line, index) => index > 0 && line.trim() === '---');
  if (end < 0) throw new Error('The agent definition is missing its YAML frontmatter closing delimiter.');
  let toolsIndex = lines.findIndex((line, index) => index > 0 && index < end && /^tools\s*:/.test(line));
  if (toolsIndex < 0) {
    lines.splice(end, 0, indexes.length ? 'tools:' : 'tools: []');
    if (!indexes.length) return lines.join('\n');
    toolsIndex = end;
    end += 1;
  }
  const toolsLine = lines[toolsIndex].trim();
  if (/^tools\s*:\s*\[\s*\]$/.test(toolsLine)) {
    if (!indexes.length) return lines.join('\n');
    lines[toolsIndex] = 'tools:';
  } else if (!/^tools\s*:\s*$/.test(toolsLine)) {
    throw new Error('The tools field must be an empty list or a block list before adding a tool.');
  }
  let blockEnd = toolsIndex + 1;
  while (blockEnd < end && (!lines[blockEnd].trim() || /^\s+-\s/.test(lines[blockEnd]))) blockEnd += 1;
  if (blockEnd < end && /^\s/.test(lines[blockEnd])) {
    throw new Error('The tools field contains an unsupported YAML entry.');
  }
  lines.splice(toolsIndex + 1, blockEnd - toolsIndex - 1, ...indexes.map(index => `  - "[TOOL ${index}]"`));
  if (!indexes.length) lines[toolsIndex] = 'tools: []';
  return lines.join('\n');
}

export function addToolParameterVariables(instructions, toolIndex, descriptions = {}) {
  let inParameters = false;
  return instructions.split(/\r?\n/).map(line => {
    if (line.trim() === 'Parameters:') {
      inParameters = true;
      return line;
    }
    if (inParameters && line.trim().startsWith('Return format:')) inParameters = false;
    const match = inParameters && line.match(/^(-\s+)(.+?):\s*(.*)$/);
    if (!match) return line;
    const parameterName = match[2].replace(/\s*\(required\)$/i, '');
    const tokenName = parameterName.toUpperCase()
      .replace(/[^A-Z0-9 _-]/g, ' ')
      .replace(/\s+/g, ' ')
      .trim() || 'VALUE';
    const variable = `TOOL ${toolIndex} ${tokenName}`.slice(0, 63).trimEnd();
    if (match[3]?.trim()) descriptions[variable] = match[3].trim();
    return `${match[1]}${match[2]}: [${variable}]${match[3] ? ` - ${match[3]}` : ''}`;
  }).join('\n');
}

export function starterEditorText(node) {
  if (node.nodeType === 3) return node.nodeValue;
  if (node.nodeType !== 1 && node.nodeType !== 11) return '';
  if (node.nodeType === 1) {
    if (node.hasAttribute('data-placeholder')) return `[${node.dataset.placeholder}]`;
    if (node.tagName === 'BR') return '\n';
  }
  let text = '';
  for (const child of node.childNodes) {
    const block = child.nodeType === 1 && (child.tagName === 'DIV' || child.tagName === 'P');
    if (block && text && !text.endsWith('\n')) text += '\n';
    text += starterEditorText(child);
  }
  return text;
}

async function choiceRequest(method, body, path = '') {
  const response = await fetch(`/api/squad-development/prompt-starter/choices${path}`, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Could not load Prompt Starter choices.');
  return data.choices;
}

export function extractSubagentConfig(values) {
  const id = (values['SUB-AGENT ID'] || values['SUBAGENT ID'] || '').trim();
  if (!id) return null;
  const usage = [values['CONDITION'], values['TASK'], values['HOW TO USE THE RESULT']]
    .filter(v => v?.trim()).map(v => v.trim()).join('\n') || '';
  return { id, usage };
}

function toAgentId(name) {
  return name.trim().toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 64) || 'new-agent';
}

function normalizeAgentId(value) {
  return String(value || '').trim().toLowerCase()
    .replace(/[^a-z0-9_-]+/g, '-')
    .replace(/^[-_]+|[-_]+$/g, '')
    .slice(0, 64);
}

function normalizeAgentFrontmatterId(content, agentId) {
  const lines = content.split(/\r\n|\n|\r/);
  if (lines[0]?.trim() !== '---') return content;
  const end = lines.findIndex((line, index) => index > 0 && line.trim() === '---');
  if (end < 0) return content;
  const idLine = lines.findIndex((line, index) => index > 0 && index < end && /^[ \t]*id[ \t]*:/.test(line));
  if (idLine < 0) return content;
  const match = lines[idLine].match(/^([ \t]*id[ \t]*:[ \t]*)(?:(['"])(.*?)\2|([^#]*?))(?:[ \t]+#.*)?[ \t]*$/);
  if (!match) return content;
  const sourceId = (match[3] ?? match[4] ?? '').trim();
  if (normalizeAgentId(sourceId) !== agentId) return content;
  lines[idLine] = `${match[1]}${agentId}`;
  return lines.join('\n');
}

export function createPromptStarter(container, { embed = false } = {}) {
  const host = document.createElement('div');
  const rootTag = embed ? 'div' : 'dialog';
  const rootClass = embed ? 'sd-starter-embed' : 'sd-starter-dialog';
  const windowControls = embed ? '' : `<div class="sd-starter-window-controls">
        <button type="button" class="sd-wc-btn" data-starter="popout" title="Pop out to separate window">
          <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M9 2h5v5"/><path d="M14 2L8 8"/><path d="M12 9v4a1 1 0 01-1 1H3a1 1 0 01-1-1V5a1 1 0 011-1h4"/></svg>
        </button>
        <button type="button" class="sd-wc-btn" data-starter="minimize" title="Minimize">
          <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 8h10"/></svg>
        </button>
        <button type="button" class="sd-wc-btn" data-starter="maximize" title="Maximize">
          <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="2" y="2" width="12" height="12" rx="1"/></svg>
        </button>
        <button type="button" class="sd-wc-btn sd-wc-close" data-starter="close" title="Close">
          <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 4l8 8M12 4l-8 8"/></svg>
        </button>
      </div>`;
  host.innerHTML = `<${rootTag} class="${rootClass}" aria-labelledby="sd-starter-title">
    <header class="sd-starter-head">
      <div><h2 id="sd-starter-title">Prompt Starter</h2>
      <p class="small text-muted">Edit the Markdown directly. Select a tool or sub-agent in the text, and fill the other variables on the right.</p></div>
      ${windowControls}
    </header>
    <div class="sd-starter-template-bar">
      <label class="small" for="sd-starter-tpl-select">Template:</label>
      <select id="sd-starter-tpl-select" class="form-select form-select-sm" data-starter="tpl-select">
        <option value="agent">Default (Novous Generic Prompt)</option>
        <option value="subagent">Default (Novous Sub-agent YAML)</option>
      </select>
      <button type="button" class="btn btn-sm" data-starter="tpl-save" title="Save current editor as a reusable template">Save as Template</button>
      <button type="button" class="btn btn-sm" data-starter="tpl-delete" title="Delete the selected template" disabled>Delete</button>
      <button type="button" class="btn btn-sm" data-starter="tpl-load-generic" title="Load the generic prompt from the server">Load Generic</button>
    </div>
    <p class="small text-danger" role="alert" data-starter="error"></p>
    <div class="sd-starter-layout">
      <section class="sd-starter-main">
        <div class="sd-starter-editor-toolbar">
          <h3 data-starter="editor-heading">Agent definition</h3>
          <button type="button" class="btn btn-sm" data-starter="add-variable">+ Add variable</button>
          <button type="button" class="btn btn-sm" data-starter="add-tool">+ Add tools</button>
        </div>
        <div class="sd-starter-editor" data-starter="editor" contenteditable="true" role="textbox" aria-multiline="true" aria-label="Prompt Starter Markdown editor" spellcheck="false"
             title="Add a placeholder like [NEW VARIABLE] to create its own field. Click a variable token to jump to its field."></div>
      </section>
      <div class="sd-starter-divider" data-starter="layout-divider" title="Drag to resize"></div>
      <aside class="sd-starter-fields" aria-label="Prompt variables">
        <div class="sd-starter-fields-head">
          <h3>Variables</h3>
          <button type="button" class="btn btn-sm" data-starter="ai-fill-toolbar" title="Fill a variable with an LLM response">AI Fill</button>
          <button type="button" class="btn btn-sm btn-primary" data-starter="ai-chain-fill"
                  title="Generate every unfilled variable in order; each step uses the already-accepted ones as context">Chain Fill</button>
          <button type="button" class="btn btn-sm" data-starter="save-all-choices">Save All</button>
        </div>
        <div data-starter="fields"></div>
        <details class="sd-starter-ai-results" data-starter="ai-results" open>
          <summary>AI results (<span data-starter="ai-results-count">0</span>)</summary>
          <p class="small text-muted">Held LLM responses with their pre-prompt and target variable. Use one to copy it into the prompt.</p>
          <div data-starter="ai-results-list"></div>
        </details>
      </aside>
    </div>
    <dialog class="sd-starter-tool-picker" data-starter="tool-picker" aria-labelledby="sd-starter-tool-picker-title">
      <section class="sd-starter-tool-picker-panel">
        <div class="sd-starter-tool-picker-head">
          <h3 id="sd-starter-tool-picker-title">Choose tools</h3>
          <button type="button" class="btn btn-sm" data-starter="tool-picker-close">Close</button>
        </div>
        <p class="small text-muted">Select one or more tools. Their descriptions and parameter variables will be added to the prompt.</p>
        <input class="form-input" type="search" data-starter="tool-filter" placeholder="Filter tools…" aria-label="Filter tools">
        <div class="editor-tools-bulk">
          <span class="small text-muted" data-starter="tool-count"></span>
          <span class="spacer"></span>
          <button type="button" class="btn btn-sm" data-starter="tool-select-all">Select all</button>
          <button type="button" class="btn btn-sm" data-starter="tool-clear-all">Clear all</button>
        </div>
        <div class="editor-tools-list" data-starter="tool-options" role="group" aria-label="Available tools"></div>
        <p class="small text-danger" data-starter="tool-picker-error" role="alert"></p>
        <div class="workspace-save-actions">
          <button type="button" class="btn" data-starter="tool-picker-cancel">Cancel</button>
          <button type="button" class="btn btn-primary" data-starter="tool-picker-apply">Add selected tools</button>
        </div>
      </section>
    </dialog>
    <dialog class="sd-starter-ai-dialog" data-starter="ai-dialog" aria-labelledby="sd-starter-ai-title">
      <section class="sd-starter-ai-panel">
        <div class="sd-starter-tool-picker-head">
          <h3 id="sd-starter-ai-title">AI fill variable</h3>
          <button type="button" class="btn btn-sm" data-starter="ai-cancel">Close</button>
        </div>
        <div class="sd-starter-ai-pane" data-starter="ai-request">
          <label class="small">Variable to fill
            <select class="form-select" data-starter="ai-target" aria-label="Variable to fill"></select>
          </label>
          <label class="small">Model
            <select class="form-select" data-starter="ai-model" aria-label="Model"></select>
          </label>
          <label class="small" for="sd-starter-ai-description">What this variable is (sent to the LLM with the pre-prompt)</label>
          <div class="sd-starter-ai-preprompt-row">
            <input id="sd-starter-ai-description" class="form-input" data-starter="ai-description"
                   placeholder="e.g. Who the agent is and its primary responsibility">
            <button type="button" class="btn btn-sm" data-starter="ai-save-description"
                    title="Save this description for this variable so it is reused as context">Save</button>
          </div>
          <label class="small" for="sd-starter-ai-preprompt">Pre-prompt to the LLM</label>
          <textarea id="sd-starter-ai-preprompt" class="form-input" rows="5" data-starter="ai-preprompt"
                    placeholder="Describe what the model should write for this variable, e.g. 'Write the role description for an agent.'"></textarea>
          <div class="sd-starter-choice-list" data-starter="ai-preprompt-list" aria-label="Saved pre-prompts" hidden></div>
          <div class="sd-starter-ai-preprompt-row">
            <button type="button" class="btn btn-sm" data-starter="ai-save-preprompt"
                    title="Save this pre-prompt for this variable so you can reuse it later">Save pre-prompt</button>
          </div>
          <label class="small" for="sd-starter-ai-addition">Append to pre-prompt (optional)</label>
          <input id="sd-starter-ai-addition" class="form-input" data-starter="ai-addition"
                 placeholder="e.g. engineer — added to the end of the pre-prompt before sending">
          <div data-starter="ai-context-wrap" hidden>
            <p class="small text-muted">Append these variables as context:</p>
            <div class="sd-starter-ai-context" data-starter="ai-context"></div>
          </div>
          <div class="workspace-save-actions">
            <button type="button" class="btn" data-starter="ai-cancel">Cancel</button>
            <button type="button" class="btn btn-primary" data-starter="ai-generate">Generate</button>
          </div>
        </div>
        <div class="sd-starter-ai-pane" data-starter="ai-response" hidden>
          <label class="small" for="sd-starter-ai-response-text">LLM response (edit before saving)</label>
          <textarea id="sd-starter-ai-response-text" class="form-input sd-starter-ai-response" rows="12"
                    data-starter="ai-response-text"></textarea>
          <div class="workspace-save-actions">
            <button type="button" class="btn btn-sm" data-starter="ai-back">Back</button>
            <button type="button" class="btn btn-sm" data-starter="ai-markdown">Format as Markdown</button>
            <span class="spacer"></span>
            <button type="button" class="btn" data-starter="ai-cancel">Cancel</button>
            <button type="button" class="btn btn-sm" data-starter="ai-save">Save to variable</button>
            <button type="button" class="btn btn-sm" data-starter="ai-hold"
                    title="Hold this response (with its pre-prompt and variable) in the AI results list">Add to list</button>
            <button type="button" class="btn btn-primary" data-starter="ai-accept-next" hidden
                    title="Fill this variable and generate the next one in the chain">Accept &amp; next</button>
          </div>
        </div>
        <div class="sd-starter-ai-pane" data-starter="ai-complete" hidden>
          <label class="small" for="sd-starter-ai-finished">Finished prompt (all variables filled)</label>
          <textarea id="sd-starter-ai-finished" class="form-input sd-starter-ai-response" rows="14"
                    data-starter="ai-finished" readonly></textarea>
          <div class="workspace-save-actions">
            <button type="button" class="btn btn-sm" data-starter="ai-copy-finished">Copy prompt</button>
            <button type="button" class="btn btn-sm" data-starter="ai-save-finished">Save prompt (.md)</button>
            <button type="button" class="btn btn-sm btn-primary" data-starter="ai-insert-finished"
                    title="Replace each filled variable's placeholder in the Markdown editor with its generated text">Add to prompt</button>
            <span class="spacer"></span>
            <button type="button" class="btn" data-starter="ai-clear-restart"
                    title="Clear the generated variables (the seed stays) and run the chain again">Clear &amp; restart</button>
            <button type="button" class="btn btn-primary" data-starter="ai-cancel">Done</button>
          </div>
        </div>
        <p class="small text-muted" role="status" data-starter="ai-status"></p>
        <p class="small text-danger" role="alert" data-starter="ai-error"></p>
      </section>
    </dialog>
    <div class="sd-starter-picker" data-starter="picker" hidden>
      <label for="sd-starter-picker-select" data-starter="picker-label"></label>
      <select id="sd-starter-picker-select" class="form-select" data-starter="picker-select"></select>
      <input class="form-input" data-starter="picker-value" aria-label="Custom selection" placeholder="Or enter a value">
      <div class="sd-starter-picker-actions">
        <button type="button" class="btn btn-sm" data-starter="picker-save">Save choice</button>
        <button type="button" class="btn btn-sm" data-starter="picker-create-subagent" hidden>Create new sub-agent</button>
        <button type="button" class="btn btn-sm" data-starter="picker-clear">Clear</button>
        <button type="button" class="btn btn-sm" data-starter="picker-close">Done</button>
      </div>
    </div>
    <div class="sd-starter-create-form" data-starter="create-form" hidden>
      <h4 data-starter="create-form-title">Create Agent</h4>
      <div class="sd-starter-create-row" data-starter="create-squad-row" hidden>
        <label class="small">Squad Name <input class="form-input form-input-sm" data-starter="create-squad-name" placeholder="e.g. Analysis Squad"></label>
      </div>
      <div class="sd-starter-create-row">
        <label class="small" data-starter="create-name-label"><span>Agent Name</span> <input class="form-input form-input-sm" data-starter="create-name" placeholder="e.g. Report Builder"></label>
        <label class="small">Agent ID <input class="form-input form-input-sm" data-starter="create-id" placeholder="auto-generated"></label>
        <label class="small" data-starter="create-kind-label">Kind
          <select class="form-select form-select-sm" data-starter="create-kind">
            <option value="agent">Agent (Markdown)</option>
            <option value="subagent">Sub-agent (YAML)</option>
          </select>
        </label>
      </div>
      <div class="sd-starter-create-row">
        <button type="button" class="btn btn-sm btn-primary" data-starter="create-confirm">Create</button>
        <button type="button" class="btn btn-sm" data-starter="create-load-agent"
                title="Load a saved agent definition file into the editor to create an agent from it">Load agent…</button>
        <button type="button" class="btn btn-sm" data-starter="create-save-agent"
                title="Save the agent definition as a Markdown file without creating it in the library">Save agent (.md)</button>
        <button type="button" class="btn btn-sm" data-starter="create-cancel">Cancel</button>
        <span class="small text-muted" data-starter="create-status"></span>
      </div>
    </div>
    <footer class="sd-starter-actions">
      <button type="button" class="btn btn-sm" data-starter="reset" disabled>Reset</button>
      <button type="button" class="btn btn-sm" data-starter="copy" disabled>Copy prompt</button>
      <button type="button" class="btn btn-sm" data-starter="save-prompt" disabled
              title="Save the finished prompt as a Markdown file">Save prompt</button>
      <span class="sd-starter-actions-sep"></span>
      <button type="button" class="btn btn-sm btn-primary" data-starter="apply-to-editor" hidden disabled>Apply to Editor</button>
      <button type="button" class="btn btn-sm btn-primary" data-starter="create-agent" disabled>Create Agent</button>
      <button type="button" class="btn btn-sm btn-primary" data-starter="create-squad" disabled>Create New Squad</button>
      <span class="small" role="status" data-starter="status"></span>
    </footer>
  </${rootTag}>`;
  const dialog = host.firstElementChild;
  container.append(dialog);
  // Embedded panels have no modal/backdrop semantics or window chrome; these
  // helpers let the rest of the module call the same show/close/open points
  // regardless of whether `dialog` is a real <dialog> or a plain <div>.
  function showHost() { if (!embed) dialog.showModal(); }
  function closeHost() { if (!embed) dialog.close(); }
  function isHostOpen() { return embed || dialog.open; }
  const fields = dialog.querySelector('[data-starter="fields"]');
  const editor = dialog.querySelector('[data-starter="editor"]');
  const picker = dialog.querySelector('[data-starter="picker"]');
  const pickerLabel = dialog.querySelector('[data-starter="picker-label"]');
  const pickerSelect = dialog.querySelector('[data-starter="picker-select"]');
  const pickerValue = dialog.querySelector('[data-starter="picker-value"]');
  const pickerClearButton = dialog.querySelector('[data-starter="picker-clear"]');
  const pickerCreateSubagent = dialog.querySelector('[data-starter="picker-create-subagent"]');
  const editorHeading = dialog.querySelector('[data-starter="editor-heading"]');
  const addToolButton = dialog.querySelector('[data-starter="add-tool"]');
  const toolPicker = dialog.querySelector('[data-starter="tool-picker"]');
  const toolFilter = dialog.querySelector('[data-starter="tool-filter"]');
  const toolCount = dialog.querySelector('[data-starter="tool-count"]');
  const toolOptions = dialog.querySelector('[data-starter="tool-options"]');
  const toolPickerError = dialog.querySelector('[data-starter="tool-picker-error"]');
  const error = dialog.querySelector('[data-starter="error"]');
  const status = dialog.querySelector('[data-starter="status"]');
  const tplSelect = dialog.querySelector('[data-starter="tpl-select"]');
  const tplDeleteBtn = dialog.querySelector('[data-starter="tpl-delete"]');
  const createForm = dialog.querySelector('[data-starter="create-form"]');
  const createFormTitle = dialog.querySelector('[data-starter="create-form-title"]');
  const createSquadRow = dialog.querySelector('[data-starter="create-squad-row"]');
  const createSquadNameInput = dialog.querySelector('[data-starter="create-squad-name"]');
  const createNameInput = dialog.querySelector('[data-starter="create-name"]');
  const createNameLabel = dialog.querySelector('[data-starter="create-name-label"] span');
  const createIdInput = dialog.querySelector('[data-starter="create-id"]');
  const createKindSelect = dialog.querySelector('[data-starter="create-kind"]');
  const createKindLabel = dialog.querySelector('[data-starter="create-kind-label"]');
  const createStatus = dialog.querySelector('[data-starter="create-status"]');
  const createAgentButton = dialog.querySelector('[data-starter="create-agent"]');
  const createSquadButton = dialog.querySelector('[data-starter="create-squad"]');
  const applyToEditorButton = dialog.querySelector('[data-starter="apply-to-editor"]');
  const aiDialog = dialog.querySelector('[data-starter="ai-dialog"]');
  const aiRequestPane = dialog.querySelector('[data-starter="ai-request"]');
  const aiResponsePane = dialog.querySelector('[data-starter="ai-response"]');
  const aiTargetSelect = dialog.querySelector('[data-starter="ai-target"]');
  const aiModelSelect = dialog.querySelector('[data-starter="ai-model"]');
  const aiPreprompt = dialog.querySelector('[data-starter="ai-preprompt"]');
  const aiPrepromptList = dialog.querySelector('[data-starter="ai-preprompt-list"]');
  const aiAddition = dialog.querySelector('[data-starter="ai-addition"]');
  const aiDescription = dialog.querySelector('[data-starter="ai-description"]');
  const aiResultsCount = dialog.querySelector('[data-starter="ai-results-count"]');
  const aiResultsList = dialog.querySelector('[data-starter="ai-results-list"]');
  const aiContextWrap = dialog.querySelector('[data-starter="ai-context-wrap"]');
  const aiContextList = dialog.querySelector('[data-starter="ai-context"]');
  const aiResponseText = dialog.querySelector('[data-starter="ai-response-text"]');
  const aiCompletePane = dialog.querySelector('[data-starter="ai-complete"]');
  const aiFinished = dialog.querySelector('[data-starter="ai-finished"]');
  const aiStatus = dialog.querySelector('[data-starter="ai-status"]');
  const aiErrorLabel = dialog.querySelector('[data-starter="ai-error"]');
  const aiTitle = dialog.querySelector('#sd-starter-ai-title');
  const aiGenerateBtn = dialog.querySelector('[data-starter="ai-generate"]');
  const aiAcceptNextBtn = dialog.querySelector('[data-starter="ai-accept-next"]');
  const values = {};
  let choices = {};
  let tools = [];
  let subagents = [];
  let templates = [];
  let starterDefaults = null;
  let disposed = false;
  let maximized = false;
  let preMaxRect = null;
  let popoutWindow = null;
  let catalogReady = false;
  let variablesKey = '';
  let pickerName = '';
  let toolInstructionsRequest = 0;
  let toolInstructionEntries = [];
  let toolInstructionsState = 'ready';
  let createMode = '';
  let pendingSubagentCreation = null;
  let toolPickerSelection = new Set();
  let toolPickerReturnFocus = null;
  let applyTarget = null;
  const maximizeBtn = dialog.querySelector('[data-starter="maximize"]');

  function toggleMaximize() {
    maximized = !maximized;
    if (maximized) {
      preMaxRect = {
        top: dialog.style.top, left: dialog.style.left,
        width: dialog.style.width, height: dialog.style.height,
        maxWidth: dialog.style.maxWidth, maxHeight: dialog.style.maxHeight,
        margin: dialog.style.margin, inset: dialog.style.inset,
      };
      dialog.style.margin = '0';
      dialog.style.inset = '0';
      dialog.style.top = '0';
      dialog.style.left = '0';
      dialog.style.width = '100vw';
      dialog.style.height = '100vh';
      dialog.style.maxWidth = '100vw';
      dialog.style.maxHeight = '100vh';
      dialog.style.borderRadius = '0';
      dialog.classList.add('sd-starter-maximized');
    } else {
      if (preMaxRect) {
        Object.assign(dialog.style, preMaxRect);
      } else {
        dialog.style.removeProperty('margin');
        dialog.style.removeProperty('inset');
        dialog.style.removeProperty('top');
        dialog.style.removeProperty('left');
        dialog.style.removeProperty('width');
        dialog.style.removeProperty('height');
        dialog.style.removeProperty('max-width');
        dialog.style.removeProperty('max-height');
      }
      dialog.style.borderRadius = '';
      dialog.classList.remove('sd-starter-maximized');
    }
    maximizeBtn.title = maximized ? 'Restore' : 'Maximize';
    maximizeBtn.innerHTML = maximized
      ? '<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="1" y="4" width="9" height="9" rx="1"/><path d="M5 4V3a1 1 0 011-1h7a1 1 0 011 1v7a1 1 0 01-1 1h-1"/></svg>'
      : '<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="2" y="2" width="12" height="12" rx="1"/></svg>';
  }

  function doMinimize() {
    closeHost();
    dialog.classList.add('sd-starter-minimized');
    let bar = container.querySelector('.sd-starter-minimized-bar');
    if (!bar) {
      bar = document.createElement('div');
      bar.className = 'sd-starter-minimized-bar';
      bar.innerHTML = '<span>Prompt Starter</span><button type="button" class="btn btn-sm" data-action="restore-starter">Restore</button>';
      bar.querySelector('button').addEventListener('click', () => {
        bar.remove();
        dialog.classList.remove('sd-starter-minimized');
        showHost();
      });
      container.append(bar);
    }
  }

  function doPopout() {
    const promptText = editorText();
    const currentValues = { ...values };
    const w = Math.min(1400, screen.availWidth - 100);
    const h = Math.min(900, screen.availHeight - 100);
    const left = Math.round((screen.availWidth - w) / 2);
    const top = Math.round((screen.availHeight - h) / 2);
    popoutWindow = window.open('', '_blank',
      `width=${w},height=${h},left=${left},top=${top},menubar=no,toolbar=no,status=no,scrollbars=yes,resizable=yes`);
    if (!popoutWindow) {
      showError(new Error('Could not open pop-out window. Check your browser pop-up blocker settings.'));
      return;
    }
    closeHost();

    const doc = popoutWindow.document;
    doc.title = 'Prompt Starter — Novous';
    const parentStyles = document.querySelectorAll('link[rel="stylesheet"], style');
    let styleHTML = '';
    parentStyles.forEach(el => {
      if (el.tagName === 'LINK') {
        const href = el.getAttribute('href');
        if (href) styleHTML += `<link rel="stylesheet" href="${new URL(href, location.origin).href}">`;
      } else {
        styleHTML += `<style>${el.textContent}</style>`;
      }
    });
    doc.head.innerHTML = `<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">${styleHTML}`;
    doc.body.style.margin = '0';
    doc.body.style.background = 'var(--bg-primary)';
    doc.body.style.color = 'var(--text-main)';
    doc.body.className = document.body.className;

    const wrapper = doc.createElement('div');
    wrapper.className = 'sd-starter-popout-host';
    doc.body.append(wrapper);

    dialog.classList.add('sd-starter-popout-mode');
    closeHost();
    wrapper.append(dialog);
    showHost();

    const popoutControls = dialog.querySelector('.sd-starter-window-controls');
    const popinBtn = doc.createElement('button');
    popinBtn.type = 'button';
    popinBtn.className = 'sd-wc-btn';
    popinBtn.title = 'Pop back into main window';
    popinBtn.dataset.starter = 'popin';
    popinBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M7 14H3a1 1 0 01-1-1V5a1 1 0 011-1h4"/><path d="M7 9l5-5"/><path d="M9 4h3v3"/></svg>';
    const popoutBtn = popoutControls.querySelector('[data-starter="popout"]');
    popoutBtn.hidden = true;
    popoutControls.insertBefore(popinBtn, popoutControls.firstChild);

    popoutWindow.addEventListener('beforeunload', () => {
      if (disposed) return;
      doPopin();
    });
  }

  function doPopin() {
    if (!popoutWindow) return;
    closeHost();
    dialog.classList.remove('sd-starter-popout-mode');
    const popinBtn = dialog.querySelector('[data-starter="popin"]');
    if (popinBtn) popinBtn.remove();
    const popoutBtn = dialog.querySelector('[data-starter="popout"]');
    if (popoutBtn) popoutBtn.hidden = false;
    container.append(dialog);
    try { popoutWindow.close(); } catch {}
    popoutWindow = null;
    if (maximized) {
      maximized = false;
      dialog.classList.remove('sd-starter-maximized');
      dialog.style.removeProperty('border-radius');
      preMaxRect = null;
    }
    showHost();
  }

  function showError(err) { error.textContent = err.message || String(err); }
  function isToolParameterVariable(name) { return /^TOOL \d+ [A-Z0-9 _-]+$/.test(name); }
  function isToolPickerVariable(name) { return /^TOOL \d+$/.test(name); }
  function isCatalogVariable(name) {
    return isToolPickerVariable(name) || name === 'SUB-AGENT ID' || name === 'SUBAGENT ID';
  }
  function isSubagentVariable(name) { return name === 'SUB-AGENT ID' || name === 'SUBAGENT ID'; }
  function updateCreateKindLabels() {
    const kindLabel = createKindSelect.value === 'subagent' ? 'Sub-agent' : 'Agent';
    createAgentButton.textContent = `Create ${kindLabel}`;
    createNameLabel.textContent = `${kindLabel} Name`;
    if (!createForm.hidden && createMode !== 'subagent') {
      createFormTitle.textContent = createMode === 'squad'
        ? `Create New Squad + First ${kindLabel}` : `Create New ${kindLabel}`;
    }
    addToolButton.hidden = !editorText().trimStart().startsWith('---');
  }
  function editorText() { return starterEditorText(editor); }
  function promptBodyText() {
    const text = editorText();
    const marker = /\n{2,}## Available Tools[ \t]*\r?\n/.exec(text);
    return marker ? text.slice(0, marker.index).replace(/\s+$/, '') : text;
  }
  function finishedPrompt() { return renderStarter(editorText(), values); }
  // Bakes every filled, non-tool variable's text directly into the editor, leaving tool
  // placeholders (TOOL N / TOOL N <PARAM>) as live tokens so tool management keeps working.
  function bakeFilledVariables(text) {
    return text.replace(VARIABLE, (match, name) => {
      if (isToolPickerVariable(name) || isToolParameterVariable(name)) return match;
      const value = (values[name] || values[name.replace(/-/g, ' ')])?.trim();
      return value || match;
    });
  }
  function withToolInstructions(body, instructions) {
    return `${body.replace(/\s+$/, '')}\n\n## Available Tools\n\n${instructions}\n`;
  }
  function renderEditor(text) {
    const fragment = document.createDocumentFragment();
    let last = 0;
    for (const match of text.matchAll(VARIABLE)) {
      fragment.append(document.createTextNode(text.slice(last, match.index)));
      const token = document.createElement('button');
      token.type = 'button';
      token.contentEditable = 'false';
      token.className = 'sd-starter-token';
      token.dataset.placeholder = match[1];
      token.setAttribute('aria-label', `${match[1]}: ${values[match[1]]?.trim() || 'unfilled'}`);
      if (isCatalogVariable(match[1])) token.setAttribute('aria-haspopup', 'true');
      token.textContent = values[match[1]]?.trim() || match[0];
      fragment.append(token);
      last = match.index + match[0].length;
    }
    fragment.append(document.createTextNode(text.slice(last)));
    editor.replaceChildren(fragment);
  }
  function updateTokens(name) {
    editor.querySelectorAll('[data-placeholder]').forEach(token => {
      if (token.dataset.placeholder !== name) return;
      token.textContent = values[name]?.trim() || `[${name}]`;
      token.setAttribute('aria-label', `${name}: ${values[name]?.trim() || 'unfilled'}`);
    });
  }
  function selectedToolNames() {
    return Object.entries(values)
      .filter(([name, value]) => /^TOOL \d+$/.test(name) && value?.trim())
      .map(([variable, value]) => ({ index: Number(variable.slice(5)), name: value.trim() }))
      .sort((left, right) => left.index - right.index);
  }
  async function refreshToolInstructions() {
    const request = ++toolInstructionsRequest;
    const selected = selectedToolNames().filter(entry => tools.some(tool => tool.name === entry.name));
    const body = promptBodyText();
    toolInstructionEntries = [];
    renderEditor(body);
    renderFields();
    if (!selected.length || !body.trimStart().startsWith('---')) {
      toolInstructionsState = 'ready';
      return;
    }
    toolInstructionsState = 'loading';
    try {
      const entries = await Promise.all(selected.map(async ({ index, name }) => {
        const result = await Api.getStarterToolInstructions(name);
        const lines = result.content.split(/\r?\n/);
        const parameterDescriptions = {};
        const description = addToolParameterVariables(lines.slice(1).join('\n').trim(), index, parameterDescriptions);
        return { name, description, parameterDescriptions };
      }));
      if (request !== toolInstructionsRequest || disposed) return;
      toolInstructionEntries = entries;
      const currentBody = promptBodyText();
      const instructions = entries.map(entry => `### ${entry.name}\n${entry.description}`).join('\n\n');
      renderEditor(withToolInstructions(currentBody, instructions));
      toolInstructionsState = 'ready';
      renderFields();
    } catch (err) {
      if (request !== toolInstructionsRequest || disposed) return;
      toolInstructionsState = 'error';
      showError(err);
    }
  }
  function openPickerForVariable(name, token) {
    if (isToolPickerVariable(name)) {
      openToolPicker(token);
      return;
    }
    if (!catalogReady) {
      showError(new Error('Tool and sub-agent choices are not available yet. Close and reopen Prompt Starter to retry.'));
      return;
    }
    pickerName = name;
    pickerLabel.textContent = `Choose ${name}`;
    pickerSelect.setAttribute('aria-label', `Choose ${name}`);
    pickerSelect.innerHTML = `<option value="">Choose a value</option>${optionsFor(name)}`;
    pickerSelect.value = values[name] || '';
    pickerValue.value = values[name] || '';
    pickerCreateSubagent.hidden = !isSubagentVariable(name);
    pickerClearButton.textContent = 'Clear';
    picker.hidden = false;
    const rect = token.getBoundingClientRect();
    const bounds = dialog.getBoundingClientRect();
    picker.style.left = `${Math.max(8, Math.min(rect.left - bounds.left, bounds.width - picker.offsetWidth - 8))}px`;
    picker.style.top = `${Math.max(8, Math.min(rect.bottom - bounds.top + 4, bounds.height - picker.offsetHeight - 8))}px`;
    pickerSelect.focus();
  }
  function updateToolPickerState() {
    toolOptions.querySelectorAll('.editor-tool-group').forEach(group => {
      const inputs = [...group.querySelectorAll('input[name="starter-tools"]')];
      const selectedCount = inputs.filter(input => toolPickerSelection.has(input.value)).length;
      const groupCheck = group.querySelector('.editor-tool-group-check');
      groupCheck.checked = inputs.length > 0 && selectedCount === inputs.length;
      groupCheck.indeterminate = selectedCount > 0 && selectedCount < inputs.length;
      group.querySelector('.editor-tool-group-count').textContent = `${selectedCount}/${inputs.length}`;
      inputs.forEach(input => { input.checked = toolPickerSelection.has(input.value); });
    });
    toolCount.textContent = `${toolPickerSelection.size} of ${tools.length} selected`;
  }
  function renderToolPickerOptions() {
    const query = toolFilter.value.trim().toLowerCase();
    const grouped = groupTools(tools)
      .map(([category, entries]) => [category, entries.filter(tool => [
        category, tool.name, tool.provider, tool.description,
      ].flat().join(' ').toLowerCase().includes(query))])
      .filter(([, entries]) => entries.length);
    toolOptions.replaceChildren(...grouped.map(([category, entries]) => {
      const group = document.createElement('details');
      group.className = 'editor-tool-group';
      group.open = Boolean(query);
      const summary = document.createElement('summary');
      const groupCheck = document.createElement('input');
      groupCheck.type = 'checkbox';
      groupCheck.className = 'editor-tool-group-check';
      groupCheck.setAttribute('aria-label', `Select all ${category} tools`);
      groupCheck.addEventListener('click', event => event.stopPropagation());
      groupCheck.addEventListener('change', () => {
        entries.forEach(tool => groupCheck.checked
          ? toolPickerSelection.add(tool.name)
          : toolPickerSelection.delete(tool.name));
        updateToolPickerState();
      });
      const title = document.createElement('span');
      title.className = 'editor-tool-group-title';
      title.textContent = category;
      const count = document.createElement('span');
      count.className = 'editor-tool-group-count text-muted small';
      summary.append(groupCheck, title, count);
      group.appendChild(summary);
      for (const tool of entries) {
        const label = document.createElement('label');
        label.className = 'editor-tool-option';
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.name = 'starter-tools';
        checkbox.value = tool.name;
        checkbox.addEventListener('change', () => {
          checkbox.checked ? toolPickerSelection.add(tool.name) : toolPickerSelection.delete(tool.name);
          updateToolPickerState();
        });
        const copy = document.createElement('span');
        const name = document.createElement('strong');
        name.textContent = tool.name;
        copy.appendChild(name);
        const description = Array.isArray(tool.description)
          ? tool.description.join(' ').trim()
          : String(tool.description || '').trim();
        if (description) {
          const detail = document.createElement('span');
          detail.className = 'text-muted small';
          detail.textContent = description;
          copy.appendChild(detail);
        }
        label.append(checkbox, copy);
        group.appendChild(label);
      }
      return group;
    }));
    if (!grouped.length) {
      const empty = document.createElement('p');
      empty.className = 'small text-muted';
      empty.textContent = 'No tools match this filter.';
      toolOptions.appendChild(empty);
    }
    updateToolPickerState();
  }
  function openToolPicker(trigger = addToolButton) {
    if (!catalogReady) {
      showError(new Error('The tool catalog is not available yet. Close and reopen Prompt Starter to retry.'));
      return;
    }
    toolPickerSelection = new Set(selectedToolNames().map(tool => tool.name));
    toolPickerReturnFocus = trigger;
    toolPickerError.textContent = '';
    toolFilter.value = '';
    renderToolPickerOptions();
    toolPicker.showModal();
    toolFilter.focus();
  }
  function closeToolPicker() {
    if (toolPicker.open) toolPicker.close();
    if (toolPickerReturnFocus?.isConnected) toolPickerReturnFocus.focus();
    else addToolButton.focus();
    toolPickerReturnFocus = null;
  }
  function applyToolSelection() {
    const currentTools = selectedToolNames();
    const indexByName = new Map(currentTools.map(tool => [tool.name, tool.index]));
    const frontmatterNumbers = [...editorText().matchAll(/\[TOOL (\d+)\]/g)].map(match => Number(match[1]));
    let nextIndex = Math.max(0, ...frontmatterNumbers);
    const assignments = [...toolPickerSelection].map(name => ({
      name,
      index: indexByName.get(name) || ++nextIndex,
    }));
    try {
      const updatedText = replaceToolVariables(editorText(), assignments.map(tool => tool.index));
      const retained = new Set(assignments.map(tool => `TOOL ${tool.index}`));
      Object.keys(values).forEach(name => {
        if (/^TOOL \d+$/.test(name) && !retained.has(name)) delete values[name];
      });
      assignments.forEach(tool => { values[`TOOL ${tool.index}`] = tool.name; });
      variablesKey = '';
      renderEditor(updatedText);
      renderFields();
      closeToolPicker();
      refreshToolInstructions();
    } catch (err) {
      toolPickerError.textContent = err.message || String(err);
    }
  }
  function addToolVariable() {
    openToolPicker();
  }

  // --- AI variable fill -----------------------------------------------------
  let aiModelsLoaded = false;
  let aiBusy = false;
  let lastAiPrompt = '';
  const aiResultsKey = 'novous2.starter.ai-results';
  let aiResults = [];
  try {
    const stored = JSON.parse(localStorage.getItem(aiResultsKey) || '[]');
    if (Array.isArray(stored)) aiResults = stored.filter(entry => entry && typeof entry.text === 'string');
  } catch { /* storage unavailable */ }
  function persistAiResults() {
    try { localStorage.setItem(aiResultsKey, JSON.stringify(aiResults.slice(0, 50))); }
    catch { /* storage unavailable */ }
  }
  function renderAiResults() {
    aiResultsCount.textContent = String(aiResults.length);
    aiResultsList.replaceChildren(...aiResults.map((entry, index) => {
      const row = document.createElement('div');
      row.className = 'sd-starter-ai-result';
      const head = document.createElement('div');
      head.className = 'sd-starter-ai-result-head';
      const variable = document.createElement('strong');
      variable.textContent = entry.variable || '';
      const when = document.createElement('span');
      when.className = 'text-muted small';
      when.textContent = entry.time ? new Date(entry.time).toLocaleString() : '';
      head.append(variable, when);
      const prePrompt = document.createElement('p');
      prePrompt.className = 'small text-muted sd-starter-ai-result-preprompt';
      prePrompt.textContent = entry.prePrompt || '';
      prePrompt.title = entry.prePrompt || '';
      const preview = document.createElement('p');
      preview.className = 'small sd-starter-ai-result-text';
      preview.textContent = entry.text;
      const actions = document.createElement('div');
      actions.className = 'sd-starter-ai-result-actions';
      for (const [action, label, title] of [
        ['ai-result-use', 'To prompt', `Copy this response into the ${entry.variable || ''} variable`],
        ['ai-result-copy', 'Copy', 'Copy the response text to the clipboard'],
        ['ai-result-reuse', 'Reuse', 'Reopen the AI dialog with this variable and pre-prompt'],
        ['ai-result-delete', 'Delete', 'Remove this entry from the list'],
      ]) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = action === 'ai-result-delete' ? 'btn btn-sm btn-danger' : 'btn btn-sm';
        button.dataset.starter = action;
        button.dataset.index = String(index);
        button.textContent = label;
        button.title = title;
        actions.appendChild(button);
      }
      row.append(head, prePrompt, preview, actions);
      return row;
    }));
  }
  function aiPrepromptKey(name) {
    return `AI PRE-PROMPT ${name}`.slice(0, 64).trimEnd();
  }
  function aiDescriptionKey(name) {
    return `AI DESCRIPTION ${name}`.slice(0, 64).trimEnd();
  }
  // A variable's description: the user-saved one, else a tool parameter's, else the built-in default.
  function variableDescription(name) {
    const saved = (choices[aiDescriptionKey(name)] || []).slice(-1)[0];
    if (saved) return saved;
    const parameterDescriptions = Object.assign({}, ...toolInstructionEntries.map(entry => entry.parameterDescriptions));
    return parameterDescriptions[name] || STARTER_VARIABLE_DESCRIPTIONS[name] || '';
  }
  function populateAiPreprompts() {
    const saved = choices[aiPrepromptKey(aiTargetSelect.value)] || [];
    aiPrepromptList.replaceChildren(...saved.map(value => {
      const row = document.createElement('div');
      row.className = 'sd-starter-choice-row';
      const use = document.createElement('button');
      use.type = 'button';
      use.className = 'sd-starter-choice';
      use.dataset.starter = 'ai-use-preprompt';
      use.title = value;
      use.textContent = truncateOption(value);
      const remove = document.createElement('button');
      remove.type = 'button';
      remove.className = 'sd-starter-choice-delete';
      remove.dataset.starter = 'ai-delete-preprompt';
      remove.title = 'Delete this saved pre-prompt';
      remove.setAttribute('aria-label', 'Delete saved pre-prompt');
      remove.textContent = '×';
      row.append(use, remove);
      return row;
    }));
    aiPrepromptList.hidden = !saved.length;
  }
  function showAiPane(pane) {
    aiRequestPane.hidden = pane !== 'request';
    aiResponsePane.hidden = pane !== 'response';
    aiCompletePane.hidden = pane !== 'complete';
  }
  function aiShowError(err) { aiErrorLabel.textContent = err ? (err.message || String(err)) : ''; }
  function renderAiContext() {
    const target = aiTargetSelect.value;
    // Selected tools (bare TOOL N) are useful context for writing role/responsibilities text;
    // their injected parameter placeholders (TOOL N <PARAM>) are just documentation tokens and
    // would only add noise, so they're left out here.
    const entries = Object.entries(values)
      .filter(([name, value]) => name !== target && value?.trim() && !isToolParameterVariable(name));
    aiContextWrap.hidden = !entries.length;
    aiContextList.replaceChildren(...entries.map(([name, value]) => {
      const label = document.createElement('label');
      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.value = name;
      const copy = document.createElement('span');
      const title = document.createElement('strong');
      title.textContent = name;
      copy.appendChild(title);
      const described = variableDescription(name);
      if (described) {
        const meaning = document.createElement('span');
        meaning.className = 'small';
        meaning.textContent = described;
        copy.appendChild(meaning);
      }
      const preview = document.createElement('span');
      preview.className = 'text-muted small';
      preview.textContent = value.trim().slice(0, 120);
      copy.appendChild(preview);
      label.append(checkbox, copy);
      return label;
    }));
  }
  async function loadAiModels() {
    if (aiModelsLoaded) return;
    aiModelSelect.innerHTML = '<option value="">Loading models…</option>';
    try {
      const data = await Api.getModels();
      if (disposed) return;
      const models = data.models || [];
      if (!models.length) {
        aiModelSelect.innerHTML = '<option value="">No models installed</option>';
        if (data.error) aiShowError(new Error(data.error));
        return;
      }
      aiModelSelect.replaceChildren(...models.map(name => new Option(name, name)));
      aiModelSelect.value = models.includes(data.default) ? data.default : models[0];
      aiModelsLoaded = true;
    } catch (err) {
      aiModelSelect.innerHTML = '<option value="">No models</option>';
      if (!disposed) aiShowError(err);
    }
  }
  function openAiDialog(targetName = '', prePrompt = null) {
    const variables = starterVariables(editorText())
      .filter(name => !isCatalogVariable(name) && !isToolParameterVariable(name));
    if (!variables.length) {
      showError(new Error('Add a variable placeholder to the prompt before using AI fill.'));
      return;
    }
    aiShowError(null);
    aiStatus.textContent = '';
    aiAcceptNextBtn.hidden = true;
    aiTitle.textContent = 'AI fill variable';
    aiTargetSelect.replaceChildren(...variables.map(name => new Option(name, name)));
    aiTargetSelect.value = variables.includes(targetName) ? targetName : variables[0];
    aiTargetSelect.disabled = Boolean(targetName) && variables.includes(targetName);
    if (prePrompt !== null) aiPreprompt.value = prePrompt;
    aiAddition.value = '';
    aiDescription.value = variableDescription(aiTargetSelect.value);
    populateAiPreprompts();
    renderAiContext();
    showAiPane('request');
    if (!aiDialog.open) aiDialog.showModal();
    loadAiModels();
    aiPreprompt.focus();
  }
  async function aiGenerate(button) {
    if (aiBusy) return;
    const prePrompt = aiPreprompt.value.trim();
    if (!prePrompt) { aiShowError(new Error('Enter a pre-prompt for the LLM.')); return; }
    const model = aiModelSelect.value;
    if (!model) { aiShowError(new Error('Choose a model.')); return; }
    const chosen = [...aiContextList.querySelectorAll('input:checked')].map(input => input.value);
    const addition = aiAddition.value.trim();
    let prompt = addition ? `${prePrompt}\n\n${addition}` : prePrompt;
    lastAiPrompt = prompt;
    const target = aiTargetSelect.value;
    const targetDescription = aiDescription.value.trim();
    prompt += `\n\nYou are writing the value for the "${target}" variable of an AI agent prompt.`;
    if (targetDescription) prompt += ` ${target} means: ${targetDescription}`;
    prompt += ' Output only the text for this variable — no section heading and no preamble.';
    if (chosen.length) {
      prompt += '\n\nContext — already-filled variables of this agent prompt:\n' + chosen.map(name => {
        const described = variableDescription(name);
        return `- ${name}${described ? ` (${described})` : ''}: ${values[name].trim()}`;
      }).join('\n');
    }
    aiBusy = true;
    button.disabled = true;
    aiShowError(null);
    aiStatus.textContent = 'Generating…';
    try {
      const data = await Api.generateStarterText(model, prompt);
      if (disposed) return;
      aiResponseText.value = (data.text || '').trim();
      showAiPane('response');
      aiStatus.textContent = aiResponseText.value ? '' : 'The model returned an empty response.';
    } catch (err) {
      if (!disposed) { aiShowError(err); aiStatus.textContent = ''; }
    } finally {
      aiBusy = false;
      button.disabled = false;
    }
  }
  async function aiFormatMarkdown(button) {
    if (aiBusy) return;
    const text = aiResponseText.value.trim();
    if (!text) { aiShowError(new Error('There is no response text to format.')); return; }
    const model = aiModelSelect.value;
    if (!model) { aiShowError(new Error('Choose a model.')); return; }
    aiBusy = true;
    button.disabled = true;
    aiShowError(null);
    aiStatus.textContent = 'Formatting as Markdown…';
    try {
      const data = await Api.generateStarterText(model,
        'Convert the following text to clean, well-structured Markdown. '
        + 'Output only the Markdown with no preamble and no surrounding code fences.\n\n' + text);
      if (disposed) return;
      const formatted = (data.text || '').trim();
      if (formatted) aiResponseText.value = formatted;
      aiStatus.textContent = formatted ? 'Formatted as Markdown.' : 'The model returned an empty response.';
    } catch (err) {
      if (!disposed) { aiShowError(err); aiStatus.textContent = ''; }
    } finally {
      aiBusy = false;
      button.disabled = false;
    }
  }
  // Fills the variable's text area in the Variables panel (and the value used by the
  // finished prompt) without rewriting the token shown in the Markdown editor.
  function aiApplyToVariable(variable, text) {
    values[variable] = text;
    showVariableField(variable, true);
    fields.querySelector('[data-starter-value]')?.scrollIntoView({ block: 'nearest' });
  }
  function aiSave() {
    const target = aiTargetSelect.value;
    if (!target) return;
    aiApplyToVariable(target, aiResponseText.value.trim());
    aiDialog.close();
    status.textContent = `Saved the LLM response to ${target}.`;
  }

  // --- Chain fill: generate the remaining variables in order, feeding each
  // accepted section into the context of the next one. ---------------------
  let chainQueue = [];
  let chainTotal = 0;
  const CHAIN_SEED_VARIABLES = ['AGENT NAME', 'AGENT ID', 'DESCRIPTION', 'SUB-AGENT NAME', 'SUB-AGENT ID'];
  function defaultPreprompt(name) {
    const saved = (choices[aiPrepromptKey(name)] || []).slice(-1)[0];
    return saved || STARTER_VARIABLE_PREPROMPTS[name]
      || `Write the value for the ${name} section of this agent prompt.`;
  }
  function chainStepSetup(name) {
    aiTargetSelect.value = name;
    aiTargetSelect.disabled = true;
    aiDescription.value = variableDescription(name);
    aiPreprompt.value = defaultPreprompt(name);
    aiAddition.value = '';
    populateAiPreprompts();
    renderAiContext();
    aiContextList.querySelectorAll('input').forEach(input => { input.checked = true; });
    aiTitle.textContent = `Chain fill — ${name} (${chainTotal - chainQueue.length + 1}/${chainTotal})`;
    aiAcceptNextBtn.hidden = false;
  }
  function startChainFill() {
    const variables = starterVariables(editorText())
      .filter(name => !isCatalogVariable(name) && !isToolParameterVariable(name));
    const queue = variables.filter(name => !values[name]?.trim() && !CHAIN_SEED_VARIABLES.includes(name));
    if (!queue.length) {
      showError(new Error('All variables are already filled; nothing to chain-fill.'));
      return;
    }
    if (!CHAIN_SEED_VARIABLES.some(name => values[name]?.trim())
        && !variables.some(name => values[name]?.trim())) {
      showError(new Error('Fill AGENT NAME and DESCRIPTION first — the chain uses them as the seed context.'));
      return;
    }
    chainQueue = queue;
    chainTotal = queue.length;
    openAiDialog(queue[0]);
    if (!aiDialog.open) { chainQueue = []; return; }
    chainStepSetup(queue[0]);
    aiStatus.textContent = 'Review the pre-prompt and press Generate to start the chain.';
  }
  function endChain() {
    chainQueue = [];
    chainTotal = 0;
    aiTitle.textContent = 'AI fill variable';
    aiAcceptNextBtn.hidden = true;
  }
  function aiAcceptNext() {
    const target = aiTargetSelect.value;
    const text = aiResponseText.value.trim();
    if (!text) { aiShowError(new Error('There is no response text to accept.')); return; }
    aiApplyToVariable(target, text);
    chainQueue = chainQueue.filter(name => name !== target);
    if (!chainQueue.length) {
      chainQueue = [];
      chainTotal = 0;
      aiAcceptNextBtn.hidden = true;
      aiTitle.textContent = 'Chain fill — complete';
      aiFinished.value = finishedPrompt();
      showAiPane('complete');
      aiStatus.textContent = 'Every variable is filled. Review the finished prompt below.';
      status.textContent = 'Chain fill complete — every variable is filled.';
      return;
    }
    chainStepSetup(chainQueue[0]);
    showAiPane('request');
    aiGenerate(aiGenerateBtn);
  }
  function aiHold() {
    const text = aiResponseText.value.trim();
    if (!text) { aiShowError(new Error('There is no response text to hold.')); return; }
    const target = aiTargetSelect.value;
    aiResults.unshift({ variable: target, prePrompt: lastAiPrompt, text, time: Date.now() });
    if (aiResults.length > 50) aiResults.length = 50;
    persistAiResults();
    renderAiResults();
    aiDialog.close();
    status.textContent = `Held the LLM response for ${target} in AI results.`;
  }
  async function aiSaveDescription(button) {
    const name = aiTargetSelect.value;
    const value = aiDescription.value.trim();
    if (!value) { aiShowError(new Error('Enter a description before saving it.')); return; }
    if (value.length > 2000) { aiShowError(new Error('A description must be at most 2000 characters.')); return; }
    button.disabled = true;
    try {
      const key = aiDescriptionKey(name);
      const previous = (choices[key] || []).slice(-1)[0];
      if (previous && previous !== value) {
        // One description per variable: replace the stored one instead of accumulating.
        try { await choiceRequest('POST', { variable: key, value: previous }, '/delete'); } catch { /* keep going */ }
      }
      choices = await choiceRequest('POST', { variable: key, value });
      if (disposed) return;
      renderAiContext();
      aiStatus.textContent = `Description saved for ${name}.`;
    } catch (err) { if (!disposed) aiShowError(err); }
    finally { button.disabled = false; }
  }
  async function aiSavePreprompt(button) {
    const value = aiPreprompt.value.trim();
    if (!value) { aiShowError(new Error('Enter a pre-prompt before saving it.')); return; }
    if (value.length > 2000) { aiShowError(new Error('A saved pre-prompt must be at most 2000 characters.')); return; }
    button.disabled = true;
    try {
      choices = await choiceRequest('POST', { variable: aiPrepromptKey(aiTargetSelect.value), value });
      if (disposed) return;
      populateAiPreprompts();
      aiStatus.textContent = `Pre-prompt saved for ${aiTargetSelect.value}.`;
    } catch (err) { if (!disposed) aiShowError(err); }
    finally { button.disabled = false; }
  }
  const truncateOption = value => (value.length > 60 ? `${value.slice(0, 60)}…` : value);
  function optionsFor(name) {
    let catalog = '';
    if (isSubagentVariable(name)) {
      catalog = `<optgroup label="Sub-agents">${subagents.map(agent =>
        `<option value="${esc(agent.id)}" title="${esc(agent.description || '')}">${esc(agent.name || agent.id)} (${esc(agent.id)})</option>`).join('')}</optgroup>`;
    }
    const saved = choices[name] || [];
    return `${catalog}${saved.length ? `<optgroup label="Saved choices">${saved.map(value =>
      `<option value="${esc(value)}" title="${esc(value)}">${esc(truncateOption(value))}</option>`).join('')}</optgroup>` : ''}`;
  }
  let currentVariable = '';
  function renderFields() {
    const variables = starterVariables(editorText())
      .filter(name => !isCatalogVariable(name));
    const parameterDescriptions = Object.assign({}, ...toolInstructionEntries.map(entry => entry.parameterDescriptions));
    if (!variables.includes(currentVariable)) currentVariable = variables[0] || '';
    const key = variables.map(name => `${name}:${parameterDescriptions[name] || ''}`).join('\n') + `\n@${currentVariable}`;
    if (variablesKey === key) return;
    variablesKey = key;
    if (!variables.length) {
      fields.innerHTML = '<p class="small text-muted">Add an uppercase placeholder in the prompt to create a field.</p>';
      return;
    }
    const name = currentVariable;
    const index = variables.indexOf(name);
    const description = parameterDescriptions[name];
    fields.innerHTML = `<div class="sd-starter-field-nav">
        <button type="button" class="btn btn-sm" data-starter="field-prev" title="Previous variable"${index === 0 ? ' disabled' : ''}>‹</button>
        <select class="form-select" data-starter="field-jump" aria-label="Choose variable">
          ${variables.map(variable =>
            `<option value="${esc(variable)}"${variable === name ? ' selected' : ''}>${esc(variable)}${values[variable]?.trim() ? ' ✓' : ''}</option>`).join('')}
        </select>
        <span class="small text-muted sd-starter-field-count">${index + 1}/${variables.length}</span>
        <button type="button" class="btn btn-sm" data-starter="field-next" title="Next variable"${index === variables.length - 1 ? ' disabled' : ''}>›</button>
      </div>
      <div class="sd-starter-field" data-variable="${esc(name)}">
        ${description ? `<p class="sd-starter-field-description">${esc(description)}</p>` : ''}
        ${(choices[name] || []).length ? `<div class="sd-starter-choice-list" aria-label="Saved choices for ${esc(name)}">
          ${(choices[name] || []).map(value => `<div class="sd-starter-choice-row">
            <button type="button" class="sd-starter-choice" data-starter="use-choice" title="${esc(value)}">${esc(truncateOption(value))}</button>
            <button type="button" class="sd-starter-choice-delete" data-starter="delete-choice"
                    title="Delete this saved choice" aria-label="Delete saved choice">×</button>
          </div>`).join('')}
        </div>` : ''}
        <textarea class="form-input sd-starter-field-value" rows="8" data-starter-value
                  aria-label="Value for ${esc(name)}">${esc(values[name] || '')}</textarea>
        <div class="sd-starter-field-actions">
          <button type="button" class="btn btn-sm" data-starter="save-choice">Save choice</button>
          <button type="button" class="btn btn-sm" data-starter="ai-fill" title="Fill ${esc(name)} with an LLM response">AI</button>
        </div>
      </div>`;
  }
  function showVariableField(name, focus = false) {
    currentVariable = name;
    variablesKey = '';
    renderFields();
    if (focus) fields.querySelector('[data-starter-value]')?.focus();
  }

  // --- Template selector ---
  function renderTemplateSelect() {
    const current = tplSelect.value;
    tplSelect.innerHTML = `<option value="agent">Default (Novous Generic Prompt)</option>`
      + `<option value="subagent">Default (Novous Sub-agent YAML)</option>`
      + templates.map(t => `<option value="template:${esc(t.name)}">${esc(t.name)}</option>`).join('');
    if (current === 'agent' || current === 'subagent'
        || (current.startsWith('template:') && templates.some(t => t.name === current.slice(9)))) {
      tplSelect.value = current;
    } else {
      tplSelect.value = 'agent';
    }
    tplDeleteBtn.disabled = !tplSelect.value.startsWith('template:');
  }

  async function loadTemplates() {
    try {
      const data = await Api.getStarterTemplates();
      if (disposed) return;
      templates = data.templates || [];
      renderTemplateSelect();
    } catch { /* non-critical */ }
  }

  function applyTemplate(name) {
    const isCustom = name.startsWith('template:');
    const templateName = isCustom ? name.slice(9) : '';
    const tpl = isCustom ? templates.find(t => t.name === templateName) : null;
    const kind = name === 'subagent' ? 'subagent' : 'agent';
    if (isCustom && !tpl) return;
    if (!isCustom) {
      if (!starterDefaults) {
        showError(new Error('Novous prompt defaults are not available yet.'));
        return;
      }
      createKindSelect.value = kind;
      updateCreateKindLabels();
    }
    const body = tpl ? tpl.body : starterDefaults[kind].prompt;
    Object.keys(values).forEach(key => delete values[key]);
    variablesKey = '';
    picker.hidden = true;
    // Tools can only be added to definitions with YAML frontmatter, custom or default.
    addToolButton.hidden = !body.trimStart().startsWith('---');
    toolInstructionsRequest += 1;
    renderEditor(body);
    editorHeading.textContent = isCustom ? 'Prompt' : kind === 'subagent' ? 'Sub-agent prompt' : 'Agent definition';
    renderFields();
    refreshToolInstructions();
  }

  // --- Create form helpers ---
  function showCreateForm(mode) {
    createMode = mode;
    const isSquad = mode === 'squad';
    const isSubagent = mode === 'subagent';
    createFormTitle.textContent = isSquad ? 'Create New Squad + First Agent'
      : isSubagent ? 'Create New Sub-agent' : 'Create New Agent';
    createSquadRow.hidden = !isSquad;
    createSquadNameInput.value = '';
    createNameInput.value = values['AGENT NAME'] || values['SUB-AGENT NAME'] || '';
    const idVariable = isSubagent ? 'SUB-AGENT ID' : 'AGENT ID';
    createIdInput.value = normalizeAgentId(values[idVariable] || toAgentId(createNameInput.value))
      || toAgentId(createNameInput.value);
    createKindLabel.hidden = isSubagent;
    createKindSelect.disabled = isSubagent;
    if (isSubagent) createKindSelect.value = 'subagent';
    createStatus.textContent = '';
    createForm.hidden = false;
    updateCreateKindLabels();
    if (isSquad) createSquadNameInput.focus();
    else createNameInput.focus();
  }

  createIdInput.addEventListener('change', () => {
    createIdInput.value = normalizeAgentId(createIdInput.value) || toAgentId(createNameInput.value);
  });

  function hideCreateForm() {
    createForm.hidden = true;
    createMode = '';
    createStatus.textContent = '';
  }

  function beginSubagentCreation() {
    pendingSubagentCreation = {
      text: promptBodyText(),
      values: { ...values },
      template: tplSelect.value,
      kind: createKindSelect.value,
      targetName: pickerName,
    };
    picker.hidden = true;
    applyTemplate('subagent');
    showCreateForm('subagent');
  }

  function restorePendingSubagent(selectedId = '') {
    if (!pendingSubagentCreation) return;
    const pending = pendingSubagentCreation;
    pendingSubagentCreation = null;
    Object.keys(values).forEach(key => delete values[key]);
    Object.assign(values, pending.values);
    variablesKey = '';
    tplSelect.value = pending.template;
    createKindSelect.value = pending.kind;
    createKindSelect.disabled = false;
    createKindLabel.hidden = false;
    updateCreateKindLabels();
    renderEditor(pending.text);
    renderFields();
    refreshToolInstructions();
    if (selectedId) {
      values[pending.targetName] = selectedId;
      pickerSelect.value = selectedId;
      pickerValue.value = selectedId;
      updateTokens(pending.targetName);
    }
    picker.hidden = true;
  }

  const layoutDivider = dialog.querySelector('[data-starter="layout-divider"]');
  const layout = dialog.querySelector('.sd-starter-layout');
  const mainSection = dialog.querySelector('.sd-starter-main');
  {
    let dragging = false, startX = 0, startW = 0;
    layoutDivider.addEventListener('mousedown', e => {
      dragging = true;
      startX = e.clientX;
      startW = mainSection.getBoundingClientRect().width;
      document.body.classList.add('sd-resizing');
      e.preventDefault();
    });
    document.addEventListener('mousemove', e => {
      if (!dragging) return;
      const layoutW = layout.getBoundingClientRect().width;
      const w = Math.max(280, Math.min(layoutW - 220, startW + (e.clientX - startX)));
      mainSection.style.flex = 'none';
      mainSection.style.width = `${w}px`;
    });
    document.addEventListener('mouseup', () => {
      if (!dragging) return;
      dragging = false;
      document.body.classList.remove('sd-resizing');
    });
  }

  // Window chrome (maximize/minimize/popout and edge-resize) only applies to the
  // floating modal dialog; an embedded panel just fills its host element.
  if (!embed) {
    dialog.querySelector('.sd-starter-head').addEventListener('dblclick', e => {
      if (e.target.closest('.sd-starter-window-controls')) return;
      toggleMaximize();
    });

    // --- Dialog window resize from any edge/corner ---
    const EDGES = ['n', 's', 'e', 'w', 'ne', 'nw', 'se', 'sw'];
    for (const edge of EDGES) {
      const handle = document.createElement('div');
      handle.className = `sd-starter-resize sd-resize-${edge}`;
      handle.dataset.resize = edge;
      dialog.append(handle);
    }
    let dir = null, startX = 0, startY = 0, startRect = null;
    const MIN_W = 480, MIN_H = 360;

    dialog.addEventListener('mousedown', e => {
      const handle = e.target.closest('[data-resize]');
      if (!handle) return;
      dir = handle.dataset.resize;
      startX = e.clientX;
      startY = e.clientY;
      startRect = dialog.getBoundingClientRect();
      dialog.style.margin = '0';
      dialog.style.inset = 'unset';
      dialog.style.top = `${startRect.top}px`;
      dialog.style.left = `${startRect.left}px`;
      dialog.style.width = `${startRect.width}px`;
      dialog.style.height = `${startRect.height}px`;
      dialog.style.maxWidth = 'none';
      dialog.style.maxHeight = 'none';
      document.body.classList.add('sd-resizing');
      e.preventDefault();
    });
    document.addEventListener('mousemove', e => {
      if (!dir) return;
      const dx = e.clientX - startX;
      const dy = e.clientY - startY;
      let top = startRect.top, left = startRect.left;
      let w = startRect.width, h = startRect.height;

      if (dir.includes('e')) w = Math.max(MIN_W, startRect.width + dx);
      if (dir.includes('w')) { w = Math.max(MIN_W, startRect.width - dx); left = startRect.right - w; }
      if (dir.includes('s')) h = Math.max(MIN_H, startRect.height + dy);
      if (dir.includes('n')) { h = Math.max(MIN_H, startRect.height - dy); top = startRect.bottom - h; }

      dialog.style.top = `${top}px`;
      dialog.style.left = `${left}px`;
      dialog.style.width = `${w}px`;
      dialog.style.height = `${h}px`;
    });
    document.addEventListener('mouseup', () => {
      if (!dir) return;
      dir = null;
      document.body.classList.remove('sd-resizing');
    });
  }

  renderEditor('');
  renderFields();
  editor.addEventListener('input', renderFields);
  let lastEditorRange = null;
  document.addEventListener('selectionchange', () => {
    const sel = window.getSelection();
    if (sel?.rangeCount && editor.contains(sel.anchorNode)) {
      lastEditorRange = sel.getRangeAt(0).cloneRange();
    }
  });
  function restoreEditorCursor() {
    if (lastEditorRange) {
      const sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(lastEditorRange);
    }
  }
  function insertPlainText(text) {
    if (document.execCommand('insertText', false, text)) return;
    const selection = window.getSelection();
    if (!selection?.rangeCount || !editor.contains(selection.anchorNode)) {
      showError(new Error('Could not insert text into the Markdown editor.'));
      return;
    }
    const range = selection.getRangeAt(0);
    range.deleteContents();
    const node = document.createTextNode(text);
    range.insertNode(node);
    range.setStartAfter(node);
    range.collapse(true);
    selection.removeAllRanges();
    selection.addRange(range);
    renderFields();
  }
  editor.addEventListener('beforeinput', event => {
    if (event.inputType === 'insertParagraph' || event.inputType === 'insertLineBreak') {
      event.preventDefault();
      insertPlainText('\n');
    }
  });
  editor.addEventListener('paste', event => {
    event.preventDefault();
    if (!event.clipboardData) { showError(new Error('Could not read the pasted text.')); return; }
    insertPlainText(event.clipboardData.getData('text/plain').replace(/\r\n?/g, '\n'));
  });
  editor.addEventListener('focusout', event => {
    if (editor.contains(event.relatedTarget)) return;
    setTimeout(() => {
      if (disposed || editor.contains(document.activeElement)) return;
      const walker = document.createTreeWalker(editor, NodeFilter.SHOW_TEXT, {
        acceptNode: node => node.parentElement.closest('[data-placeholder]') ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT,
      });
      let node;
      while ((node = walker.nextNode())) {
        if (starterVariables(node.nodeValue).length) {
          const scrollTop = editor.scrollTop;
          renderEditor(editorText());
          editor.scrollTop = scrollTop;
          break;
        }
      }
    }, 0);
  });
  editor.addEventListener('click', event => {
    const token = event.target.closest('[data-placeholder]');
    if (!token) return;
    const name = token.dataset.placeholder;
    if (/^TOOL \d+$/.test(name)) {
      openToolPicker(token);
      return;
    }
    if (!isCatalogVariable(name)) {
      showVariableField(name, true);
      return;
    }
    openPickerForVariable(name, token);
  });
  pickerSelect.addEventListener('change', () => {
    if (!pickerSelect.value) return;
    pickerValue.value = pickerSelect.value;
    values[pickerName] = pickerSelect.value;
    updateTokens(pickerName);
  });
  pickerValue.addEventListener('input', () => {
    values[pickerName] = pickerValue.value;
    updateTokens(pickerName);
  });
  picker.addEventListener('keydown', event => {
    if (event.key === 'Escape') {
      event.preventDefault();
      event.stopPropagation();
      picker.hidden = true;
      editor.focus();
    }
  });
  toolFilter.addEventListener('input', renderToolPickerOptions);
  aiDialog.addEventListener('close', endChain);
  aiTargetSelect.addEventListener('change', () => {
    aiDescription.value = variableDescription(aiTargetSelect.value);
    renderAiContext();
    populateAiPreprompts();
  });
  renderAiResults();
  toolPicker.addEventListener('cancel', event => {
    event.preventDefault();
    closeToolPicker();
  });
  dialog.addEventListener('cancel', event => {
    if (!picker.hidden) { event.preventDefault(); picker.hidden = true; editor.focus(); }
    else if (pendingSubagentCreation) {
      event.preventDefault();
      restorePendingSubagent();
      hideCreateForm();
    }
  });
  fields.addEventListener('input', event => {
    const input = event.target.closest('[data-starter-value]');
    if (!input) return;
    const name = input.closest('[data-variable]').dataset.variable;
    values[name] = input.value;
    updateTokens(name);
  });
  fields.addEventListener('change', event => {
    const jump = event.target.closest('[data-starter="field-jump"]');
    if (jump) { showVariableField(jump.value, true); return; }
    const select = event.target.closest('[data-starter-choice]');
    if (!select || !select.value) return;
    const field = select.closest('[data-variable]');
    field.querySelector('[data-starter-value]').value = select.value;
    values[field.dataset.variable] = select.value;
    updateTokens(field.dataset.variable);
  });

  // --- Template bar events ---
  tplSelect.addEventListener('change', () => {
    tplDeleteBtn.disabled = !tplSelect.value.startsWith('template:');
    applyTemplate(tplSelect.value);
    status.textContent = tplSelect.value.startsWith('template:')
      ? `Loaded template: ${tplSelect.value.slice(9)}`
      : `Loaded ${tplSelect.value === 'subagent' ? 'Novous sub-agent YAML' : 'Novous Generic Prompt'} default.`;
  });

  // --- Auto-fill ID from name ---
  createNameInput.addEventListener('input', () => {
    createIdInput.value = toAgentId(createNameInput.value);
  });
  createKindSelect.addEventListener('change', updateCreateKindLabels);

  dialog.addEventListener('click', async event => {
    const button = event.target.closest('[data-starter]');
    if (!button || button.tagName !== 'BUTTON') return;
    const action = button.dataset.starter;
    error.textContent = '';
    status.textContent = '';
    if (action === 'close') {
      if (popoutWindow) { doPopin(); return; }
      if (pendingSubagentCreation) restorePendingSubagent();
      if (toolPicker.open) closeToolPicker();
      picker.hidden = true;
      hideCreateForm();
      applyTarget = null;
      createAgentButton.hidden = false;
      createSquadButton.hidden = false;
      applyToEditorButton.hidden = true;
      if (maximized) { maximized = false; dialog.classList.remove('sd-starter-maximized'); dialog.style.borderRadius = ''; preMaxRect = null; }
      closeHost();
      return;
    }
    if (action === 'minimize') { doMinimize(); return; }
    if (action === 'maximize') { toggleMaximize(); return; }
    if (action === 'popout') { doPopout(); return; }
    if (action === 'popin') { doPopin(); return; }
    if (action === 'tool-picker-close' || action === 'tool-picker-cancel') {
      closeToolPicker();
      return;
    }
    if (action === 'tool-select-all' || action === 'tool-clear-all') {
      toolOptions.querySelectorAll('input[name="starter-tools"]').forEach(input => {
        if (action === 'tool-select-all') toolPickerSelection.add(input.value);
        else toolPickerSelection.delete(input.value);
      });
      updateToolPickerState();
      return;
    }
    if (action === 'tool-picker-apply') {
      applyToolSelection();
      return;
    }
    if (action === 'picker-close') { picker.hidden = true; return; }
    if (action === 'picker-clear') {
      values[pickerName] = '';
      pickerValue.value = '';
      pickerSelect.value = '';
      updateTokens(pickerName);
      refreshToolInstructions();
      return;
    }
    if (action === 'add-tool') {
      addToolVariable();
      return;
    }
    if (action === 'use-choice') {
      const field = button.closest('[data-variable]');
      if (!field) return;
      const name = field.dataset.variable;
      values[name] = button.title;
      field.querySelector('[data-starter-value]').value = button.title;
      updateTokens(name);
      return;
    }
    if (action === 'delete-choice') {
      const field = button.closest('[data-variable]');
      const value = button.closest('.sd-starter-choice-row')?.querySelector('.sd-starter-choice')?.title;
      if (!field || !value) return;
      if (!confirm(`Delete this saved choice for ${field.dataset.variable}?\n\n${value.length > 200 ? `${value.slice(0, 200)}…` : value}`)) return;
      button.disabled = true;
      try {
        choices = await choiceRequest('POST', { variable: field.dataset.variable, value }, '/delete');
        variablesKey = '';
        renderFields();
        status.textContent = `Deleted a saved choice for ${field.dataset.variable}.`;
      } catch (err) {
        if (!disposed) { showError(err); button.disabled = false; }
      }
      return;
    }
    if (action === 'field-prev' || action === 'field-next') {
      const variables = starterVariables(editorText()).filter(name => !isCatalogVariable(name));
      const next = variables[variables.indexOf(currentVariable) + (action === 'field-next' ? 1 : -1)];
      if (next) showVariableField(next, true);
      return;
    }
    if (action === 'ai-fill-toolbar') { openAiDialog(); return; }
    if (action === 'ai-chain-fill') { startChainFill(); return; }
    if (action === 'ai-accept-next') { aiAcceptNext(); return; }
    if (action === 'ai-copy-finished') {
      try { await navigator.clipboard.writeText(aiFinished.value); aiStatus.textContent = 'Finished prompt copied.'; }
      catch (err) { aiShowError(new Error(`Could not copy the prompt: ${err.message}.`)); }
      return;
    }
    if (action === 'ai-save-finished') {
      const baseName = toAgentId(values['AGENT ID'] || values['AGENT NAME'] || values['SUB-AGENT NAME'] || 'prompt-starter');
      button.disabled = true;
      try {
        const saved = await Api.saveTextFile(aiFinished.value, `${baseName}.md`, 'md');
        aiStatus.textContent = `Prompt saved as ${saved.filename}.`;
      } catch (err) {
        if (err?.name !== 'AbortError' && !disposed) aiShowError(new Error(`Could not save the prompt: ${err.message}.`));
      } finally { button.disabled = false; }
      return;
    }
    if (action === 'ai-insert-finished') {
      // Bake every currently-filled variable's text directly into the Markdown editor,
      // in one step, instead of leaving it sitting only in the Variables panel. Tool
      // placeholders are left alone so the tool picker keeps working on them.
      const scrollTop = editor.scrollTop;
      renderEditor(bakeFilledVariables(editorText()));
      editor.scrollTop = scrollTop;
      renderFields();
      aiStatus.textContent = 'Added each filled variable to the prompt.';
      status.textContent = 'Added each filled variable to the prompt.';
      return;
    }
    if (action === 'ai-clear-restart') {
      if (!confirm('Clear the generated variables and run the chain again? The seed (agent name, ID, description) is kept.')) return;
      starterVariables(editorText())
        .filter(name => !isCatalogVariable(name) && !isToolParameterVariable(name) && !CHAIN_SEED_VARIABLES.includes(name))
        .forEach(name => { delete values[name]; updateTokens(name); });
      variablesKey = '';
      renderFields();
      startChainFill();
      return;
    }
    if (action === 'ai-fill') {
      openAiDialog(button.closest('[data-variable]')?.dataset.variable || '');
      return;
    }
    if (action === 'ai-cancel') { aiDialog.close(); return; }
    if (action === 'ai-generate') { aiGenerate(button); return; }
    if (action === 'ai-back') { showAiPane('request'); return; }
    if (action === 'ai-markdown') { aiFormatMarkdown(button); return; }
    if (action === 'ai-save') { aiSave(); return; }
    if (action === 'ai-hold') { aiHold(); return; }
    if (action === 'ai-save-preprompt') { aiSavePreprompt(button); return; }
    if (action === 'ai-use-preprompt') { aiPreprompt.value = button.title; return; }
    if (action === 'ai-delete-preprompt') {
      const value = button.closest('.sd-starter-choice-row')?.querySelector('.sd-starter-choice')?.title;
      if (!value) return;
      if (!confirm(`Delete this saved pre-prompt for ${aiTargetSelect.value}?\n\n${value.length > 200 ? `${value.slice(0, 200)}…` : value}`)) return;
      button.disabled = true;
      try {
        choices = await choiceRequest('POST', { variable: aiPrepromptKey(aiTargetSelect.value), value }, '/delete');
        populateAiPreprompts();
        aiStatus.textContent = 'Saved pre-prompt deleted.';
      } catch (err) {
        if (!disposed) { aiShowError(err); button.disabled = false; }
      }
      return;
    }
    if (action === 'ai-save-description') { aiSaveDescription(button); return; }
    if (action.startsWith('ai-result-')) {
      const entry = aiResults[Number(button.dataset.index)];
      if (!entry) return;
      if (action === 'ai-result-use') {
        aiApplyToVariable(entry.variable, entry.text);
        status.textContent = `Copied the held response into ${entry.variable}.`;
      } else if (action === 'ai-result-copy') {
        try { await navigator.clipboard.writeText(entry.text); status.textContent = 'Response copied to the clipboard.'; }
        catch (err) { showError(new Error(`Could not copy the response: ${err.message}.`)); }
      } else if (action === 'ai-result-reuse') {
        openAiDialog(entry.variable, entry.prePrompt || '');
      } else if (action === 'ai-result-delete') {
        aiResults.splice(Number(button.dataset.index), 1);
        persistAiResults();
        renderAiResults();
      }
      return;
    }
    if (action === 'add-variable') {
      let name = prompt('Variable name:');
      if (!name) return;
      name = name.trim().toUpperCase().replace(/[^A-Z0-9 _-]/g, '').replace(/\s+/g, ' ').slice(0, 64);
      if (!name || !/^[A-Z]/.test(name)) {
        showError(new Error('Variable name must start with a letter (A-Z). Only uppercase letters, digits, spaces, hyphens and underscores are allowed.'));
        return;
      }
      editor.focus();
      restoreEditorCursor();
      insertPlainText(`[${name}]`);
      const scrollTop = editor.scrollTop;
      renderEditor(editorText());
      editor.scrollTop = scrollTop;
      renderFields();
      return;
    }
    if (action === 'save-all-choices') {
      const toSave = Object.entries(values)
        .filter(([name, val]) => val?.trim() && !isCatalogVariable(name) && !isToolParameterVariable(name));
      if (!toSave.length) { showError(new Error('No variable values to save.')); return; }
      button.disabled = true;
      status.textContent = 'Saving…';
      try {
        for (const [name, value] of toSave) {
          choices = await choiceRequest('POST', { variable: name, value: value.trim() });
        }
        variablesKey = '';
        renderFields();
        status.textContent = `Saved ${toSave.length} variable value${toSave.length > 1 ? 's' : ''}.`;
      } catch (err) { if (!disposed) showError(err); }
      finally { button.disabled = false; }
      return;
    }
    if (action === 'reset') {
      if (!confirm('Reset the starter template and its current values? Saved choices will remain.')) return;
      pendingSubagentCreation = null;
      picker.hidden = true;
      hideCreateForm();
      createKindSelect.disabled = false;
      createKindLabel.hidden = false;
      tplSelect.value = 'agent';
      tplDeleteBtn.disabled = true;
      applyTemplate('agent');
      status.textContent = 'Starter reset.';
      return;
    }
    if (action === 'picker-create-subagent') {
      beginSubagentCreation();
      return;
    }

    // --- Template actions ---
    if (action === 'tpl-save') {
      const body = promptBodyText().trim();
      if (!body) { showError(new Error('The editor is empty.')); return; }
      const name = prompt('Name for this template:');
      if (!name?.trim()) return;
      button.disabled = true;
      try {
        const data = await Api.saveStarterTemplate(name.trim(), body);
        if (disposed) return;
        templates = data.templates || [];
        renderTemplateSelect();
        tplSelect.value = `template:${name.trim()}`;
        tplDeleteBtn.disabled = false;
        status.textContent = `Template "${name.trim()}" saved.`;
      } catch (err) { if (!disposed) showError(err); }
      finally { button.disabled = false; }
      return;
    }
    if (action === 'tpl-delete') {
      if (!tplSelect.value.startsWith('template:')) return;
      const name = tplSelect.value.slice(9);
      if (!confirm(`Delete template "${name}"?`)) return;
      button.disabled = true;
      try {
        const data = await Api.deleteStarterTemplate(name);
        if (disposed) return;
        templates = data.templates || [];
        renderTemplateSelect();
        tplSelect.value = 'agent';
        applyTemplate('agent');
        status.textContent = `Template "${name}" deleted.`;
      } catch (err) { if (!disposed) showError(err); }
      finally { button.disabled = false; }
      return;
    }
    if (action === 'tpl-load-generic') {
      button.disabled = true;
      try {
        const data = await Api.getGenericPrompt();
        if (disposed) return;
        Object.keys(values).forEach(key => delete values[key]);
        variablesKey = '';
        picker.hidden = true;
        tplSelect.value = 'agent';
        tplDeleteBtn.disabled = true;
        renderEditor(data.prompt || '');
        renderFields();
        status.textContent = 'Loaded generic prompt from server.';
      } catch (err) { if (!disposed) showError(err); }
      finally { button.disabled = false; }
      return;
    }

    // --- Choice saving ---
    if (action === 'save-choice' || action === 'picker-save') {
      const field = button.closest('[data-variable]');
      const name = field ? field.dataset.variable : pickerName;
      const value = (field ? field.querySelector('[data-starter-value]').value : pickerValue.value).trim();
      if (!value) { showError(new Error('Enter a value before saving a choice.')); return; }
      button.disabled = true;
      try {
        const saved = await choiceRequest('POST', { variable: name, value });
        if (disposed) return;
        choices = saved;
        variablesKey = '';
        renderFields();
        if (!picker.hidden && pickerName === name) {
          pickerSelect.innerHTML = `<option value="">Choose a value</option>${optionsFor(name)}`;
          pickerSelect.value = values[name] || '';
        }
        status.textContent = `Choice saved for ${name}.`;
      } catch (err) { if (!disposed) showError(err); }
      finally { button.disabled = false; }
      return;
    }
    if (action === 'copy') {
      try { await navigator.clipboard.writeText(finishedPrompt()); status.textContent = 'Prompt copied.'; }
      catch (err) { showError(new Error(`Could not copy the prompt: ${err.message}.`)); }
      return;
    }
    if (action === 'save-prompt') {
      const promptText = finishedPrompt();
      if (!promptText.trim()) { showError(new Error('The prompt is empty; nothing to save.')); return; }
      const baseName = toAgentId(values['AGENT ID'] || values['AGENT NAME'] || values['SUB-AGENT NAME'] || 'prompt-starter');
      button.disabled = true;
      try {
        const saved = await Api.saveTextFile(promptText, `${baseName}.md`, 'md');
        status.textContent = `Prompt saved as ${saved.filename}.`;
      } catch (err) {
        if (err?.name !== 'AbortError' && !disposed) showError(new Error(`Could not save the prompt: ${err.message}.`));
      } finally { button.disabled = false; }
      return;
    }

    // --- Apply to Editor (apply mode) ---
    if (action === 'apply-to-editor') {
      if (!applyTarget) return;
      if (toolInstructionsState !== 'ready') {
        showError(new Error(toolInstructionsState === 'loading'
          ? 'Wait for tool descriptions to finish loading.'
          : 'Tool descriptions could not be loaded; retry the tool selection.'));
        return;
      }
      const promptText = finishedPrompt();
      if (!promptText.trim()) { showError(new Error('The finished prompt must not be empty.')); return; }
      const unresolved = starterVariables(promptText);
      if (unresolved.length && !confirm(`These variables are still unfilled: ${unresolved.join(', ')}. Continue?`)) return;

      button.disabled = true;
      status.textContent = 'Applying to editor…';
      try {
        let promptToSave = promptText;
        let agentTools = null;
        let agentToolMode = null;
        if (promptText.startsWith('---')) {
          const definition = await Api.parseStarterAgentDefinition(promptText);
          promptToSave = definition.prompt;
          agentTools = definition.tools;
          agentToolMode = definition.mode;
        }
        await Api.setAgentPrompt(applyTarget.agentId, promptToSave);
        if (agentTools !== null && agentTools.length) {
          await Api.setAgentTools('prompt', applyTarget.agentId, agentTools, agentToolMode);
        }
        status.textContent = '';
        closeHost();
        applyTarget.onComplete();
      } catch (err) {
        if (!disposed) showError(err);
      } finally { button.disabled = false; }
      return;
    }

    // --- Create Agent / Squad ---
    if (action === 'create-agent') {
      showCreateForm('agent');
      return;
    }
    if (action === 'create-squad') {
      showCreateForm('squad');
      return;
    }
    if (action === 'create-cancel') {
      hideCreateForm();
      restorePendingSubagent();
      return;
    }
    if (action === 'create-load-agent') {
      const fileInput = document.createElement('input');
      fileInput.type = 'file';
      fileInput.accept = '.md,.markdown,.yaml,.yml,.txt';
      fileInput.addEventListener('change', async () => {
        const file = fileInput.files?.[0];
        if (!file) return;
        try {
          const content = (await file.text()).replace(/\r\n?/g, '\n');
          if (!content.trim()) throw new Error(`${file.name} is empty.`);
          Object.keys(values).forEach(key => delete values[key]);
          variablesKey = '';
          picker.hidden = true;
          renderEditor(content);
          renderFields();
          refreshToolInstructions();
          addToolButton.hidden = !content.trimStart().startsWith('---');
          // Prefill the form from the frontmatter unless the values are still placeholders.
          const clean = text => text.trim().replace(/^["']|["']$/g, '');
          const idMatch = content.match(/^id:[ \t]*(.+)$/m);
          const nameMatch = content.match(/^name:[ \t]*(.+)$/m);
          if (nameMatch && !nameMatch[1].includes('[')) createNameInput.value = clean(nameMatch[1]);
          if (idMatch && !idMatch[1].includes('[')) createIdInput.value = clean(idMatch[1]);
          else if (createNameInput.value) createIdInput.value = toAgentId(createNameInput.value);
          createStatus.textContent = `Loaded ${file.name}. Review the definition, then press Create.`;
        } catch (err) { if (!disposed) showError(err); }
      });
      fileInput.click();
      return;
    }
    if (action === 'create-save-agent') {
      const agentName = createNameInput.value.trim();
      const agentId = normalizeAgentId(createIdInput.value) || toAgentId(agentName);
      createIdInput.value = agentId;
      if (!agentName) { showError(new Error('Enter a name for the agent.')); return; }
      const kind = createMode === 'subagent' ? 'subagent' : createKindSelect.value;
      const nameVariable = kind === 'subagent' ? 'SUB-AGENT NAME' : 'AGENT NAME';
      values[nameVariable] = agentName;
      updateTokens(nameVariable);
      values[kind === 'subagent' ? 'SUB-AGENT ID' : 'AGENT ID'] = agentId;
      updateTokens(kind === 'subagent' ? 'SUB-AGENT ID' : 'AGENT ID');
      const promptText = kind === 'agent'
        ? normalizeAgentFrontmatterId(finishedPrompt(), agentId)
        : finishedPrompt();
      if (!promptText.trim()) { showError(new Error('The finished prompt must not be empty.')); return; }
      const unresolved = starterVariables(promptText);
      if (unresolved.length && !confirm(`These variables are still unfilled: ${unresolved.join(', ')}. Save anyway?`)) return;
      button.disabled = true;
      try {
        const extension = kind === 'subagent' ? 'yaml' : 'md';
        const saved = await Api.saveTextFile(promptText, `${agentId}.${extension}`, 'md');
        createStatus.textContent = `Agent definition saved as ${saved.filename}.`;
      } catch (err) {
        if (err?.name !== 'AbortError' && !disposed) showError(new Error(`Could not save the agent: ${err.message}.`));
      } finally { button.disabled = false; }
      return;
    }
    if (action === 'create-confirm') {
      const mode = createMode;
      const agentName = createNameInput.value.trim();
      const agentId = normalizeAgentId(createIdInput.value) || toAgentId(agentName);
      createIdInput.value = agentId;
      const kind = mode === 'subagent' ? 'subagent' : createKindSelect.value;
      const squadName = createSquadNameInput.value.trim();
      const nameVariable = kind === 'subagent' ? 'SUB-AGENT NAME' : 'AGENT NAME';
      if (!agentName) { showError(new Error('Enter a name for the agent.')); return; }
      values[nameVariable] = agentName;
      updateTokens(nameVariable);
      if (kind === 'agent') {
        values['AGENT ID'] = agentId;
        updateTokens('AGENT ID');
      }
      if (kind === 'subagent') {
        values['SUB-AGENT ID'] = agentId;
        updateTokens('SUB-AGENT ID');
      }
      if (kind === 'agent' && toolInstructionsState !== 'ready') {
        showError(new Error(toolInstructionsState === 'loading'
          ? 'Wait for tool descriptions to finish loading before creating the agent.'
          : 'Tool descriptions could not be loaded; retry the tool selection before creating the agent.'));
        return;
      }
      const promptText = kind === 'agent'
        ? normalizeAgentFrontmatterId(finishedPrompt(), agentId)
        : finishedPrompt();
      if (!promptText.trim()) { showError(new Error('The finished prompt must not be empty.')); return; }
      if (promptText.startsWith('---') && kind !== 'agent') {
        showError(new Error('The Novous Agent YAML definition must be created as an agent.'));
        return;
      }
      if (mode === 'squad' && !squadName) {
        showError(new Error('Enter a name for the squad.')); return;
      }
      const unresolved = starterVariables(promptText);
      if (unresolved.length && !confirm(`These variables are still unfilled: ${unresolved.join(', ')}. Continue?`)) return;

      button.disabled = true;
      createStatus.textContent = 'Creating…';
      try {
        const fromSubagentPicker = mode === 'subagent' && Boolean(pendingSubagentCreation);
        let promptToSave = promptText;
        let description = '';
        let agentTools = null;
        let agentToolMode = null;
        if (kind === 'agent' && promptText.startsWith('---')) {
          createStatus.textContent = 'Validating agent definition…';
          const definition = await Api.parseStarterAgentDefinition(promptText);
          if (definition.id !== agentId || definition.name !== agentName) {
            throw new Error('Agent ID and name in the YAML frontmatter must match the creation form.');
          }
          promptToSave = definition.prompt;
          description = definition.description || '';
          agentTools = definition.tools;
          agentToolMode = definition.mode;
        }
        if (mode === 'squad') {
          createStatus.textContent = 'Creating squad…';
          await Api.createSquad(squadName);
        }
        createStatus.textContent = `Creating ${kind === 'subagent' ? 'sub-agent' : 'agent'}…`;
        const created = await Api.createSquadMember(kind, agentId, agentName, description);
        if (agentTools !== null) {
          createStatus.textContent = 'Saving selected tools…';
          await Api.setAgentTools('prompt', agentId, agentTools, agentToolMode);
        }
        createStatus.textContent = 'Setting prompt…';
        await Api.setAgentPrompt(agentId, promptToSave);
        if (kind === 'agent') {
          createStatus.textContent = 'Verifying agent is usable…';
          const { errors } = await Api.getAgents('workspace');
          if (errors && errors[agentId]) {
            throw new Error(`Agent "${agentName}" was saved but is not usable: ${errors[agentId]}`);
          }
        }
        if (fromSubagentPicker) {
          subagents = [...subagents.filter(agent => agent.id !== agentId), {
            id: created.id || agentId,
            name: created.name || agentName,
            description: created.description || '',
          }];
        }
        createStatus.textContent = '';
        hideCreateForm();
        if (fromSubagentPicker) {
          restorePendingSubagent(agentId);
          status.textContent = `Sub-agent "${agentName}" created and selected.`;
        } else if (mode === 'squad') {
          status.textContent = `Squad "${squadName}" and agent "${agentName}" created.`;
        } else {
          status.textContent = `${kind === 'subagent' ? 'Sub-agent' : 'Agent'} "${agentName}" created with prompt.`;
        }
      } catch (err) {
        createStatus.textContent = '';
        if (!disposed) showError(err);
      }
      finally { button.disabled = false; }
      return;
    }
  });

  return {
    async open(options = {}) {
      error.textContent = '';
      status.textContent = '';
      hideCreateForm();
      container.querySelector('.sd-starter-minimized-bar')?.remove();
      dialog.classList.remove('sd-starter-minimized', 'sd-starter-maximized', 'sd-starter-popout-mode');
      if (maximized) { maximized = false; preMaxRect = null; dialog.style.borderRadius = ''; }
      dialog.style.removeProperty('margin'); dialog.style.removeProperty('inset');
      dialog.style.removeProperty('top'); dialog.style.removeProperty('left');
      dialog.style.removeProperty('width'); dialog.style.removeProperty('height');
      dialog.style.removeProperty('max-width'); dialog.style.removeProperty('max-height');
      applyTarget = options.applyTo || null;
      if (applyTarget) {
        createAgentButton.hidden = true;
        createSquadButton.hidden = true;
        applyToEditorButton.hidden = false;
      } else {
        createAgentButton.hidden = false;
        createSquadButton.hidden = false;
        applyToEditorButton.hidden = true;
      }
      if (!starterDefaults) {
        try {
          starterDefaults = await Api.getStarterDefaults();
          if (disposed) return;
          tplSelect.value = 'agent';
          applyTemplate('agent');
        } catch (err) {
          if (!disposed) {
            showError(err);
            showHost();
          }
          return;
        }
      }
      if (applyTarget) {
        values['AGENT NAME'] = applyTarget.agentName || '';
        values['AGENT ID'] = applyTarget.agentId || '';
        variablesKey = '';
        renderFields();
        updateTokens('AGENT NAME');
        updateTokens('AGENT ID');
      }
      showHost();
      try {
        const [catalog, agents, saved] = await Promise.all([
          Api.getTools(), Api.getOrchestrationCatalog(), choiceRequest('GET'),
        ]);
        if (disposed || !isHostOpen()) return;
        tools = catalog.tools || [];
        subagents = agents.subagents || [];
        choices = saved;
        catalogReady = true;
        variablesKey = '';
        renderFields();
        dialog.querySelectorAll('.sd-starter-actions button').forEach(button => {
          if (button.hidden) return;
          button.disabled = false;
        });
      } catch (err) { if (!disposed) showError(err); }
      loadTemplates();
    },
    hasChanges() {
      const kind = tplSelect.value === 'subagent' ? 'subagent' : 'agent';
      const defaultPrompt = starterDefaults?.[kind]?.prompt || '';
      return editorText().replace(/\r\n/g, '\n') !== defaultPrompt
        || Object.values(values).some(value => value.trim());
    },
    dispose() {
      disposed = true;
      if (popoutWindow) { try { popoutWindow.close(); } catch {} popoutWindow = null; }
      dialog.remove();
      container.querySelector('.sd-starter-minimized-bar')?.remove();
    },
  };
}
