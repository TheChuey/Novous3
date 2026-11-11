/**
 * Novous Unified API Client Wrapper
 */
async function jsonOrThrow(res) {
  let data = null;
  try { data = await res.json(); } catch (_) { /* empty body */ }
  if (!res.ok) {
    const detail = (data && (data.detail || data.message)) || res.statusText;
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
  }
  return data;
}

function get(url) { return fetch(url).then(jsonOrThrow); }
function send(method, url, body) {
  return fetch(url, {
    method,
    headers: body !== undefined ? { 'Content-Type': 'application/json' } : {},
    body: body !== undefined ? JSON.stringify(body) : undefined
  }).then(jsonOrThrow);
}

const SAVE_TYPES = {
  md: { description: 'Markdown file', mime: 'text/markdown' },
  html: { description: 'Web page', mime: 'text/html' },
};

export const Api = {
  async saveMarkdown(content, filename) {
    return this.saveTextFile(content, filename, 'md');
  },

  /** Save text through the browser's save dialog, or download it where that is unavailable. */
  async saveTextFile(content, filename, kind = 'md') {
    const { description, mime } = SAVE_TYPES[kind] || SAVE_TYPES.md;
    if (typeof window.showSaveFilePicker === 'function') {
      const handle = await window.showSaveFilePicker({
        suggestedName: filename,
        types: [{ description, accept: { [mime]: [`.${kind}`] } }]
      });
      const writable = await handle.createWritable();
      await writable.write(new Blob([content], { type: `${mime};charset=utf-8` }));
      await writable.close();
      return { filename, locationChosen: true };
    }

    const objectUrl = URL.createObjectURL(
      new Blob([content], { type: `${mime};charset=utf-8` })
    );
    const link = document.createElement('a');
    link.href = objectUrl;
    link.download = filename;
    link.click();
    setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
    return { filename, locationChosen: false };
  },

  // --- System ---
  async getHealth() {
    return get('/api/health');
  },

  // --- Agents & Chat (Core Engine) ---
  async getAgents(environment = 'workspace') {
    return get(`/api/agents?environment=${encodeURIComponent(environment)}`);
  },

  async createAgent(agentId, name, description, mode = 'chat', squad = '', environment = 'workspace', environmentName = '') {
    return send('POST', '/api/agents/create', {
      agent_id: agentId, name, description, mode, squad, environment, environment_name: environmentName
    });
  },

  async deleteAgent(agentId, environment = 'workspace') {
    return send('DELETE', `/api/agents/${encodeURIComponent(agentId)}?environment=${encodeURIComponent(environment)}`);
  },

  async getAgentTools(kind, agentId) {
    return get(`/api/agent-tools/${encodeURIComponent(kind)}/${encodeURIComponent(agentId)}`);
  },

  async getAgentToolFit(agentId, environment = 'workspace') {
    return get(`/api/agents/${encodeURIComponent(agentId)}/tool-fit?environment=${encodeURIComponent(environment)}`);
  },

  async setAgentTools(kind, agentId, tools, mode = null, subagents = null) {
    return send('PUT', `/api/agent-tools/${encodeURIComponent(kind)}/${encodeURIComponent(agentId)}`, { tools, mode, subagents });
  },

  async getCodeBuilderAgents() {
    return get('/api/codebuilder/agents');
  },

  async sendMessage(message, agentId, model = 'qwen2.5-coder:latest', sessionId = null, environment = 'workspace') {
    return send('POST', '/api/chat', {
      message, agent_id: agentId, model, session_id: sessionId, environment
    });
  },

  async getAgentScratchpad() {
    return get('/api/agent-memory/scratchpad');
  },

  async clearAgentScratchpad() {
    return send('DELETE', '/api/agent-memory/scratchpad');
  },

  async getCodeScratchpad(filters = {}) {
    const params = new URLSearchParams(
      Object.entries(filters).filter(([, value]) => value !== undefined && value !== '')
    );
    const query = params.toString();
    return get(`/api/agent-memory/code-scratchpad${query ? `?${query}` : ''}`);
  },

  async clearCodeScratchpad() {
    return send('DELETE', '/api/agent-memory/code-scratchpad');
  },

  async getChatScratchpad(sessionId = '') {
    const query = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : '';
    return get(`/api/agent-memory/chat-scratchpad${query}`);
  },

  async listChatScratchpadSessions() {
    return get('/api/agent-memory/chat-scratchpad/sessions');
  },

  async clearChatScratchpad(sessionId = '') {
    const query = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : '';
    return send('DELETE', `/api/agent-memory/chat-scratchpad${query}`);
  },

  async getSquadScratchpadRuns() {
    return get('/api/agent-memory/squad-scratchpads');
  },

  async getSquadScratchpadRun(runId) {
    return get(`/api/agent-memory/squad-scratchpads/${encodeURIComponent(runId)}`);
  },

  async getRagDocuments(baseId) {
    const query = baseId ? `?base_id=${encodeURIComponent(baseId)}` : '';
    return get(`/api/rag/documents${query}`);
  },

  async addRagText(title, text, baseId) {
    return send('POST', '/api/rag/documents', { title, text, base_id: baseId });
  },

  async deleteRagDocument(path, baseId) {
    return send('POST', '/api/rag/documents/delete', { path, base_id: baseId });
  },

  async getRagBases() {
    return get('/api/rag/bases');
  },

  async openRagBaseFolder(baseId) {
    return send('POST', `/api/rag/bases/${encodeURIComponent(baseId)}/open-folder`);
  },

  async addRagBase(name, path) {
    return send('POST', '/api/rag/bases', { name, path });
  },

  async renameRagBase(baseId, name) {
    return send('PUT', `/api/rag/bases/${encodeURIComponent(baseId)}`, { name });
  },

  async deleteRagBase(baseId) {
    return send('DELETE', `/api/rag/bases/${encodeURIComponent(baseId)}`);
  },

  async selectRagBase(baseId) {
    return send('PUT', `/api/rag/bases/${encodeURIComponent(baseId)}/select`);
  },

  async archiveRagBase(baseId) {
    return send('POST', `/api/rag/bases/${encodeURIComponent(baseId)}/archive`);
  },

  async restoreRagBase(baseId) {
    return send('POST', `/api/rag/bases/${encodeURIComponent(baseId)}/restore`);
  },

  async getRagConfig() {
    return get('/api/rag/config');
  },

  async setRagConfig(path) {
    return send('PUT', '/api/rag/config', { path });
  },

  async reindexRag(baseId) {
    const query = baseId ? `?base_id=${encodeURIComponent(baseId)}` : '';
    return send('POST', `/api/rag/reindex${query}`);
  },

  async getRagScan() {
    return get('/api/rag/scan');
  },

  async pauseRagScan() {
    return send('POST', '/api/rag/scan/pause');
  },

  async stopRagScan() {
    return send('POST', '/api/rag/scan/stop');
  },

  async resumeRagScan() {
    return send('POST', '/api/rag/scan/resume');
  },

  async decideRagScanError(decision) {
    return send('POST', '/api/rag/scan/decision', { decision });
  },

  async scheduleRagResume(resumeAt) {
    return send('POST', '/api/rag/scan/schedule', { resume_at: resumeAt });
  },

  async clearRagIndex(baseId) {
    const query = baseId ? `?base_id=${encodeURIComponent(baseId)}` : '';
    return send('DELETE', `/api/rag/index${query}`);
  },

  async streamMessage(
    message,
    agentId,
    model = 'qwen2.5-coder:latest',
    sessionId = null,
    environment = 'workspace',
    onEvent = () => {},
    signal
  ) {
    const response = await fetch('/api/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message, agent_id: agentId, model, session_id: sessionId, environment
      }),
      signal
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      const detail = payload.detail || payload.message || response.statusText;
      throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
    }
    if (!response.body) throw new Error('The chat stream returned no response body.');

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let completed = null;
    const consume = async frame => {
      const data = frame.split(/\r?\n/)
        .filter(line => line.startsWith('data:'))
        .map(line => line.slice(5).trimStart())
        .join('\n');
      if (!data) return;
      const event = JSON.parse(data);
      await onEvent(event);
      if (event.type === 'error') throw new Error(event.detail || 'The agent request failed.');
      if (event.type === 'done') completed = event;
    };

    try {
      for (;;) {
        const { value, done } = await reader.read();
        buffer += decoder.decode(value, { stream: !done });
        const frames = buffer.split(/\r?\n\r?\n/);
        buffer = frames.pop() || '';
        for (const frame of frames) await consume(frame);
        if (done) break;
      }
      if (buffer.trim()) await consume(buffer);
    } finally {
      reader.releaseLock();
    }
    if (!completed) throw new Error('The agent stream ended before returning a completed response.');
    return completed;
  },

  async mapFolderPage(path, maxDepth, includeFiles, offset) {
    return send('POST', '/api/tools/map-page', {
      path, max_depth: maxDepth, include_files: includeFiles, offset
    });
  },

  async resetChat(sessionId) {
    return send('POST', `/api/chat/reset?session_id=${encodeURIComponent(sessionId)}`);
  },

  async getPendingQuestions() {
    return get('/api/chat/pending-questions');
  },

  async submitAnswers(requestId, answers) {
    return send('POST', '/api/chat/submit-answers', { request_id: requestId, answers });
  },

  async exportSession(sessionId) {
    const safeSessionId = String(sessionId).replace(/[^A-Za-z0-9_.-]/g, '_');
    const suggestedName = `novous_${safeSessionId}_${new Date().toISOString().replace(/[:.]/g, '-')}.md`;
    let pickerResult = null;
    if (typeof window.showSaveFilePicker === 'function') {
      pickerResult = window.showSaveFilePicker({
        suggestedName,
        types: [{
          description: 'Markdown file',
          accept: { 'text/markdown': ['.md'] }
        }]
      }).then(handle => ({ handle }), error => ({ error }));
    }

    const res = await fetch('/api/chat/export', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId })
    });
    if (!res.ok) await jsonOrThrow(res);
    const blob = await res.blob();
    const disposition = res.headers.get('Content-Disposition') || '';
    const filenameMatch = disposition.match(/filename="?([^";]+)"?/i);
    const filename = filenameMatch ? filenameMatch[1] : suggestedName;

    if (pickerResult) {
      const result = await pickerResult;
      if (result.error) throw result.error;
      const writable = await result.handle.createWritable();
      await writable.write(blob);
      await writable.close();
      return { filename, locationChosen: true };
    }

    const objectUrl = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = objectUrl;
    link.download = filename;
    link.click();
    setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
    return { filename, locationChosen: false };
  },

  async getTools() {
    return get('/api/tools');
  },

  async getToolLibrary() {
    return get('/api/tools/library');
  },

  async getToolDetails(toolName) {
    return get(`/api/tools/${encodeURIComponent(toolName)}/details`);
  },

  async updateTool(toolName, source) {
    return send('PUT', `/api/tools/${encodeURIComponent(toolName)}`, { source });
  },

  async saveToolPrompt(toolName, promptDescription) {
    return send('PUT', `/api/tools/${encodeURIComponent(toolName)}/prompt`, { prompt_description: promptDescription });
  },

  async getWorkbenchTool(toolName) {
    return get(`/api/testing/tool-workbench/tools/${encodeURIComponent(toolName)}`);
  },

  async deleteWorkbenchTool(toolName) {
    return send('DELETE', `/api/testing/tool-workbench/tools/${encodeURIComponent(toolName)}`);
  },

  async listTestSuites(category = '') {
    const query = category ? `?category=${encodeURIComponent(category)}` : '';
    return get(`/api/testing/suites${query}`);
  },

  async saveTestSuite(suite) {
    return send('POST', '/api/testing/suites', suite);
  },

  async deleteTestSuite(category, slug) {
    return send('DELETE', `/api/testing/suites/${encodeURIComponent(category)}/${encodeURIComponent(slug)}`);
  },

  async getTestSuiteResults(category, slug) {
    return get(`/api/testing/suites/${encodeURIComponent(category)}/${encodeURIComponent(slug)}/results`);
  },

  async saveTestSuiteRun(category, slug, run) {
    return send('POST', `/api/testing/suites/${encodeURIComponent(category)}/${encodeURIComponent(slug)}/results`, run);
  },

  async getModels() {
    return get('/api/models');
  },

  async getChatContext(agentId, sessionId, model, environment) {
    const params = new URLSearchParams({ agent_id: agentId, session_id: sessionId, environment });
    if (model) params.set('model', model);
    return get(`/api/chat/context?${params}`);
  },

  async getModelProfiles() {
    return get('/api/model-profiles');
  },

  async saveModelProfile(model, values) {
    return send('PUT', `/api/model-profiles/${encodeURIComponent(model)}`, values);
  },

  async resetModelProfile(model) {
    return send('DELETE', `/api/model-profiles/${encodeURIComponent(model)}`);
  },

  // --- Filesystem & Editor ---
  async getFileTree(path = '') {
    return get(`/api/directory/tree?path=${encodeURIComponent(path)}`);
  },

  async readFile(path) {
    return get(`/api/file/read?path=${encodeURIComponent(path)}`);
  },

  async readManagedFile(path) {
    return get(`/api/file-manager/read?path=${encodeURIComponent(path)}`);
  },

  async getManagedFileTree(path = '') {
    return get(`/api/file-manager/tree?path=${encodeURIComponent(path)}`);
  },

  async getFileManagerRoots() {
    return get('/api/file-manager/roots');
  },

  async selectFileManagerRoot(path) {
    return send('POST', '/api/file-manager/roots/select', { path });
  },

  async manageFileManagerRoots(action, paths) {
    return send('POST', '/api/file-manager/roots/manage', { action, paths });
  },

  async writeManagedFile(path, content) {
    return send('POST', '/api/file-manager/write', { path, content });
  },

  async createManagedPath(path, kind = 'file') {
    return send('POST', '/api/file-manager/create', { path, kind });
  },

  async deleteManagedPath(path) {
    return send('POST', '/api/file-manager/delete', { path });
  },

  async writeFile(path, content) {
    return send('POST', '/api/file/write', { path, content });
  },

  async createPath(path, kind = 'file') {
    return send('POST', '/api/file/create', { path, kind });
  },

  async deletePath(path, kind = 'file') {
    return send('POST', '/api/file/delete', { path, kind });
  },

  async getEditorSessions() {
    return get('/api/editor/sessions');
  },

  // --- Squads ---
  async getSquads() { return get('/api/squads'); },

  async getOrchestrationSquads() {
    return get('/api/orchestration/squads');
  },

  async getLatestOrchestrationRun(squadId) {
    return get(`/api/orchestration/squads/${encodeURIComponent(squadId)}/latest`);
  },

  async getOrchestrationCatalog() {
    return get('/api/orchestration/catalog');
  },

  async createSubagent(subagentId, name, description = '') {
    return send('POST', '/api/orchestration/subagents', { subagent_id: subagentId, name, description });
  },

  async deleteSubagent(subagentId) {
    return send('DELETE', `/api/orchestration/subagents/${encodeURIComponent(subagentId)}`);
  },

  async createOrchestrationSquad(squad) {
    return send('POST', '/api/orchestration/squads', squad);
  },

  async deleteOrchestrationSquad(squadId) {
    return send('DELETE', `/api/orchestration/squads/${encodeURIComponent(squadId)}`);
  },

  async startOrchestrationRun(squadId, userQuery, workflowAction = 'all', parameters = {}, pattern = null) {
    return send('POST', '/api/orchestration/run/start', {
      squad_id: squadId,
      user_query: userQuery,
      workflow_action: workflowAction,
      parameters,
      ...(pattern ? { pattern } : {})
    });
  },

  async updateSquad(squadId, fields) {
    return send('PATCH', `/api/orchestration/squads/${encodeURIComponent(squadId)}`, fields);
  },

  // --- Squad Architect ---
  async getSquadsOverview() {
    return get('/api/squads-architect/overview');
  },

  async getSquadAgent(agentId) {
    return get(`/api/squads-architect/agents/${encodeURIComponent(agentId)}`);
  },

  async getAgentPrompt(agentId) {
    return get(`/api/squads-architect/agents/${encodeURIComponent(agentId)}/prompt`);
  },

  async setAgentPrompt(agentId, prompt) {
    return send('PUT', `/api/squads-architect/agents/${encodeURIComponent(agentId)}/prompt`, { prompt });
  },

  async getSquadBlueprint(squadId) {
    return get(`/api/squads-architect/blueprints/${encodeURIComponent(squadId)}`);
  },

  async validateSquadBlueprint(blueprint) {
    return send('POST', '/api/squads-architect/blueprints/validate', blueprint);
  },

  async saveSquadBlueprint(blueprint) {
    return send('POST', '/api/squads-architect/blueprints', blueprint);
  },

  async createSquadMember(kind, agentId, name, description = '') {
    return send('POST', '/api/squads-architect/members', { kind, agent_id: agentId, name, description });
  },

  async attachAgentTool(agentId, tool) {
    return send('POST', '/api/squads-architect/tools/attach', { agent_id: agentId, tool });
  },

  async detachAgentTool(agentId, tool) {
    return send('POST', '/api/squads-architect/tools/detach', { agent_id: agentId, tool });
  },

  async getOrchestrationRunResult(traceId) {
    return get(`/api/orchestration/runs/${encodeURIComponent(traceId)}`);
  },

  async getOrchestrationRunScratchpad(traceId) {
    return get(`/api/orchestration/runs/${encodeURIComponent(traceId)}/scratchpad`);
  },
  async getOrchestrationEditorPreview(traceId) {
    return get(`/api/orchestration/runs/${encodeURIComponent(traceId)}/editor-preview`);
  },
  async getOrchestrationEditorExecution(traceId) {
    return get(`/api/orchestration/runs/${encodeURIComponent(traceId)}/editor-execution`);
  },
  async createSquad(name, description = '') {
    return send('POST', '/api/squads/create', { name, description });
  },
  async deleteSquad(name) {
    return send('POST', '/api/squads/delete', { name });
  },

  // --- Testing & Prompt Builder ---

  async executeToolTest(toolCode, functionInput = {}, timeoutSeconds = 5) {
    return send('POST', '/api/testing/tool-workbench/execute', {
      tool_code: toolCode, function_input: functionInput, timeout_seconds: timeoutSeconds
    });
  },

  async generateToolFunction(model, description) {
    return send('POST', '/api/testing/tool-workbench/generate-function', { model, description });
  },

  async getToolTestHarness(toolCode) {
    return send('POST', '/api/testing/tool-workbench/test-harness', { tool_code: toolCode });
  },

  async testToolWithLlm(model, toolCode, userPrompt, timeoutSeconds = 5, options = {}) {
    return send('POST', '/api/testing/tool-workbench/test-with-llm', {
      model, tool_code: toolCode, user_prompt: userPrompt, timeout_seconds: timeoutSeconds,
      execute: options.execute ?? true, description: options.description ?? null
    });
  },

  async promoteTestedTool(toolCode, functionInput = {}, timeoutSeconds = 5) {
    return send('POST', '/api/testing/tool-workbench/promote', {
      tool_code: toolCode, function_input: functionInput, timeout_seconds: timeoutSeconds
    });
  }
};
