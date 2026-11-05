# Model profiles

Each installed Ollama model runs with a **context window** (`num_ctx`), a **reply reserve**
and a **tool limit** (`max_tools`). Suggested values follow the model's size (from its tag, or
Ollama's parameter size) and are capped at the model's native context. A saved profile
overrides them. Edit them on the **Models** page.

- Saved profiles: `modelProfiles/data/model_profiles.json` (gitignored).
- Facts about a model come from `ollama.show` and are cached per process. Failures are retried.
- Embedding-only models are left off the list.
- The tool limit keeps the first tools in an agent's own list. Tools a profile must keep
  (`PINNED_TOOLS` in `core_engine/logic_core_engine.py`) are always included.
- Changes apply on the next message. The chat keeps its history.

Routes: `GET /api/model-profiles`, `PUT /api/model-profiles/{model}`, `DELETE /api/model-profiles/{model}`.
The chat's context report is in `core_engine/logic_context_window.py`.

## Tool limits and tool checks

- A profile's tool limit can cut tools from an agent. The cut is checked for every
  model, in every chat turn and every squad run, so it applies to any installed model.
- An agent can keep specific tools with `pinned_tools` in its frontmatter:

  ```yaml
  pinned_tools:
    - ask_user_window
  ```

  Pinned tools are always kept. The rest fill the remaining places in the tool list's order.
- A run reports tools the cap cut as the flag `tools_dropped`, and listed tools that are
  not registered as `tools_missing`. Either flag puts the run at the warning level.
- Saving an agent in the editor shows the same check for every installed model in the
  status bar. The check is also at `GET /api/agents/{agent_id}/tool-fit`.
- The check reports only. It does not change the tool list or the cap.
