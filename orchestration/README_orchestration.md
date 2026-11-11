# Sub-Agent Orchestration

The orchestration pillar coordinates a primary lead and ordered, single-purpose background sub-agents. It keeps complex utility work (retrieval, code analysis, security auditing, document processing) out of the primary conversation while preserving a shared, observable handoff.

## Architecture

```
server.py
  └── interface_orchestration.py        /api/orchestration/* (squads, runs, SSE stream)
        └── logic_orchestration.py
              ├── PipelineRegistry       code-squad -> CodeSquadPipeline; other squads -> ordered workflow
              ├── OrchestrationRunService  serialized background runs
              ├── Sequential squad workflow  lead -> ordered sub-agents -> lead synthesis
              ├── CodeSquadPipeline      plan -> build -> test -> fix for the CodeBuilder editor
              ├── SubAgentRunner         library/*.md and subAgentLibrary/*.yaml turns (+ streamed turns)
              │     └── core_engine (AgentLibrary, Agent, ToolCatalog)
              ├── RagRetriever           deterministic retrieval worker
              │     ├── ragMemory (RagMemoryService)
              │     └── scratchpadMemory (ScratchpadService)
              ├── SquadRegistry          orchestration/data/orchestration_squads.json + metrics
              ├── TelemetryBus           bounded event history, SSE subscribe with resume
              ├── OrchestrationRunResults  LRU of completed results (100)
              ├── CodeBuilderRunState    per-trace editor buffer, preview, execution, handoffs, cancel
              ├── CodeHandoffNormalizer  handoff field aliases and failure evidence
              └── schemas_orchestration.py
```

## Primary Agents vs. Sub-Agents

| Type | Location | Format | Purpose |
| :--- | :--- | :--- | :--- |
| Primary agent | `library/{agent_id}.md` | Markdown with YAML frontmatter | Handles the user conversation, synthesizes findings, and edits workspace files when asked. |
| Sub-agent | `subAgentLibrary/{subagent_id}.yaml` | Pure YAML | Executes one background domain task and writes its payload to the shared scratchpad. |

In a squad, the primary agent is the lead and is not one of the ordered member steps. Squad members are sub-agents, run one at a time; the orchestrator provides their task and the prior result handoff. Sub-agents write results with `write_agent_scratchpad` and do not edit workspace files directly.

Outside a squad run, primary agents can call `call_subagent` and read results with `read_agent_scratchpad`.

For regular `/api/chat` and `/api/chat/stream` turns with `rag-presenter`, the chat runtime
executes `call_subagent("rag-retriever", ...)` and reads `latest_subagent_result` before
asking the primary model to answer. This handoff is enforced by the runtime rather than
depending on the model to choose the tool call.

### Sub-Agent YAML Contract

```yaml
id: example-worker
name: Example Worker
description: One-line description of the background task.
type: subagent
mode: agent
model: qwen2.5-coder:latest
environment: workspace
squad: example-squad
version: 1
tools:
  - read_file
  - write_agent_scratchpad
prompt: |
  # Example Worker Prompt

  ## role
  ...
  ## purpose
  ...
  ## boundaries
  ...
  ## output format & scratchpad handoff
  ...
```

The file is parsed with `yaml.safe_load()`; no frontmatter delimiters are used.

## Prompt Design Principles

1. **Single responsibility:** each sub-agent owns one task and one output shape.
2. **Explicit tool contract:** list only the tools the worker needs and describe when to call them.
3. **Evidence over prose:** require tool results as the basis for every claim.
4. **Structured handoff:** require a `write_agent_scratchpad` call with a stable `task_type` and JSON payload.
5. **No silent failure:** report tool errors and missing evidence instead of fabricating results.
6. **Hardware safety:** prefer a small number of sequential workers on local Ollama models.
7. **Primary synthesis:** the primary agent reads the scratchpad and owns the final user-facing answer.

## Scratchpad Handoff

Results are stored atomically in `scratchpadMemory/data/scratchpad.json`.

```json
{
  "subagent_id": "rag-retriever",
  "task_type": "rag_retrieval",
  "status": "success",
  "payload": {
    "query": "leadership styles",
    "knowledge_branch_id": "branch-id",
    "results": []
  },
  "updated_at": "2026-10-28T00:00:00+00:00"
}
```

The engine keeps a bounded `subagent_history` and exposes the newest entry as `latest_subagent_result`. `read_memory("latest_subagent_result")` falls back to the legacy `latest_rag_result` key.

The deterministic `rag-retriever` handoff always writes one of three statuses:
`success` when relevant passages are found, `nothing_found` when the search completes
without relevant passages, or `error` when retrieval fails. Errors include a message
in the payload. Search skips incompatible legacy vectors while reporting their count;
it fails explicitly if no indexed vectors are compatible with the query embedding.

## Squad execution

Squads use one controlled workflow: the primary lead, ordered sub-agent steps, then lead synthesis.
Only one orchestration run is active at a time, and Ollama chat and embedding requests share a
process-wide inference lock. The legacy `fanout` and `router` values are accepted at older API
boundaries but normalized to sequential execution; the builder no longer exposes those choices.

Each sub-agent receives the task and, after the first step, only the immediately preceding
sub-agent's `result` handoff. If the sub-agent does not write that handoff, the orchestrator records
its returned text under the sub-agent ID and the title `result`. The lead receives the final
handoff, not the full conversation or the entire scratchpad. Full run entries remain available in
the scratchpad viewer. Dynamic `call_subagent` tool use is withheld during squad runs; the
orchestrator owns the order. The model prompts remain editable for experimentation.

The analysis workflow runs `problem-statement-builder` before `inquisitive`, followed by the
reserved programmatic `questionnaire` step, `problem-analyst`, and `problem-report-builder`.
The questionnaire is not a prompt-based agent: it presents the questions, collects the user's
answers, and stores the `questionnaire` / `result` handoff. The primary `problem-orchestrator`
then synthesizes the completed report. Legacy Thinker registrations are normalized to this order.

## Code Development Squad pipeline

`code-squad` (primary agent `code-planer`, sub-agents `code-builder` → `tester` → `code-troubleshooter`) runs through `CodeSquadPipeline`, not the patterns above. It drives the CodeBuilder editor:

| Action | What happens |
| :--- | :--- |
| `plan` | `code-planer` streams a short plan (no full code). It is saved as a `plan` note on the code scratchpad. |
| `build` | `code-builder` returns one complete ```python module, which the orchestrator applies to the editor. Without a plan in the request, the newest `plan` note is used. |
| `test` | The orchestrator compiles the buffer, import-runs it with `__name__` overridden (so the main guard does not fire), then runs the `tester`'s assert snippet straight after the module. Timeout 15 s. |
| `fix` | Tests first. On failure, `code-troubleshooter` gets the real failure evidence and returns the corrected module; fix → test repeats up to `MAX_FIX_ATTEMPTS` (3). A reply with no code block or an unchanged module ends the loop. `troubleshoot` is an alias. |
| `all` | plan → build → test → fix loop. |

The code sub-agents have no tools (`tools: []`): they answer with 1–3 short lines and one fenced Python block. The orchestrator applies the code, runs the checks, and records factual handoffs in `CodeBuilderRunState`, so small local models never have to emit large tool-call arguments. Where a traceback line can be mapped back to the editor, diagnostics are highlighted on that line.

The plan and build prompts include up to 3 recent `note` entries from the code scratchpad (`scratchpadMemory/data/code_scratchpad.json`). The pipeline posts its plan, code, test, fix, and a final `summary` entry there; chat coding agents read it with `read_code_scratchpad` and leave notes with `post_code_scratchpad`.

Each run records a squad run in the metrics and saves its result (including a `scratchpad_snapshot` built from its own handoffs) **before** emitting `RUN_COMPLETE`. `POST /api/orchestration/runs/{trace_id}/cancel` stops a run between steps or mid-stream with status `cancelled`.

All squad runs also have a separate, persistent run-scoped squad scratchpad. It
records agent responses and handoffs; the Runs and CodeBuilder pages provide a
viewer, filtering, notes, exports and an explicit save-or-discard close flow.
See [Scratchpad memory](../scratchpadMemory/README.md#squad-run-scratchpads).

`codingagent` keeps the tool-calling helpers (`preview_editor_code`, `preview_code_change`, `inspect_http_routes`, `run_python_code`, `write_file`), which also publish editor previews and changed lines when they run inside a trace.

## Live squad observability

`SquadRegistry` stores squad definitions in `orchestration/data/orchestration_squads.json` (built-in `rag-squad` and `code-squad` when the file is missing) and run metrics in `orchestration/data/orchestration_squad_metrics.json`: total, successful and failed runs, average and last duration, and `last_status` (`failing` above 30% failures, `degraded` above 10%, otherwise `healthy`). Files left in `agentMemory/data/` by the pre-refactor layout are moved on first start. Deleting a squad removes only its registration and metrics, never the agent definitions.

`TelemetryBus` events: `AGENT_MESSAGE`, `TOOL_CALL_START`, `TOOL_CALL_END`, `SCRATCHPAD_HANDOFF`, `ERROR`, `RUN_STARTED`, `RUN_COMPLETE`, `CODE_CHANGE_PREVIEW`, `CODE_CHANGE_APPLIED`, `EDITOR_CODE_PREVIEW`, `EDITOR_CODE_EXECUTION`, `EDITOR_CODE_UPDATE`, `AGENT_STARTED`, `AGENT_TOKEN`, `AGENT_DONE`, `STEP_STATUS`. Each type has an allow-list of data keys and statuses are restricted to a safe set. `AGENT_TOKEN` goes to live listeners only and is never replayed; `AGENT_DONE` carries the full text. Text is capped at 20,000 characters and editor content at 1 MB; trace history, listener queues and SSE lifetimes are bounded.

RAG and generic-pattern streams leave out prompts, tool arguments and document text. Code-squad events do include the agent's text and code (`AGENT_TOKEN`, `AGENT_DONE`, and `EDITOR_CODE_UPDATE`'s `content`).

## REST API

`POST /api/orchestration/run`

```json
{
  "pattern": "sequential",
  "primary_agent": "rag-presenter",
  "subagents": ["rag-retriever", "code-analyzer"],
  "user_query": "Summarize the indexed leadership notes",
  "parameters": {"top_k": 5}
}
```

The response contains `trace_id`, `pattern`, `query`, `status`, `final_answer`, `result_payload`, and the full `execution_trace`. Requests may also carry `squad_id`, an optional `trace_id`, and `workflow_action` (`all` | `plan` | `build` | `test` | `fix` | `troubleshoot`, code-squad only).

To start a workflow asynchronously, post a registered squad to `POST /api/orchestration/run/start`:

```json
{
  "squad_id": "code-squad",
  "workflow_action": "all",
  "user_query": "Write an add(a, b) function",
  "parameters": {"editor_code": "", "editor_path": ""}
}
```

The response contains a `trace_id` for the live SSE stream. The asynchronous endpoint uses the squad's registered topology rather than agents chosen by the browser. It returns 400 for an unknown squad, 413 for an editor buffer over 1 MB, and 429 when capacity is full.

| Method | Path | Purpose |
| :--- | :--- | :--- |
| GET | `/squads` | Registered squads with their metrics |
| POST | `/squads` | Create a squad (name, description, primary_agent, subagents) |
| DELETE | `/squads/{squad_id}` | Delete a squad registration |
| GET | `/squads/{squad_id}/latest` | Newest cached result for a squad |
| GET | `/catalog` | Selectable primary agents and `subAgentLibrary/` sub-agents |
| POST | `/run` | Synchronous run |
| POST | `/run/start` | Asynchronous run; returns a `trace_id` |
| GET | `/stream/{trace_id}` | SSE telemetry; resumes from `Last-Event-ID` or `?after=<sequence>` |
| GET | `/runs/{trace_id}` | Completed run result |
| POST | `/runs/{trace_id}/cancel` | Stop a running CodeBuilder workflow |
| GET | `/runs/{trace_id}/scratchpad` | Scratchpad snapshot captured for the run |
| GET | `/runs/{trace_id}/editor-preview` | The run's editor preview |
| GET | `/runs/{trace_id}/editor-execution` | The run's execution result and diagnostics |

All paths are under `/api/orchestration`.

## Testing

```bash
python -m pytest tests/test_orchestration.py tests/test_orchestration_observability.py tests/test_code_squad_pipeline.py -q
```

The suites cover YAML loading, the documentation contract, the sequential trace structure, the squad registry, telemetry and SSE, the endpoints, and the code-squad pipeline (code extraction, test-harness line mapping, the fix loop and cancellation). Agent turns are mocked so the tests do not require a running Ollama server.
