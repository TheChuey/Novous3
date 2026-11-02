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
