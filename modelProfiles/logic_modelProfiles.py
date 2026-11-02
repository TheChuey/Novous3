"""Per-model profiles: the context window and tool cap each installed Ollama model runs with.

Suggested values come from the model's size and native context. A saved profile overrides them.
What Ollama reports about a model is read once per process and cached."""

import json
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Any

import ollama

COMPONENT_ROOT = Path(__file__).resolve().parent
DEFAULT_NUM_CTX = 32768
MIN_NUM_CTX = 2048
MAX_NUM_CTX_LIMIT = 131072
MIN_REPLY_RESERVE = 256
MAX_TOOLS_LIMIT = 64

_TAG_SIZE = re.compile(r"^[a-z]?(\d+(?:\.\d+)?)b$")
_PARAMETER_SIZE = re.compile(r"^(\d+(?:\.\d+)?)\s*([BM])$", re.IGNORECASE)


def _get(obj: Any, key: str) -> Any:
    """Read a field from an Ollama response object or a plain dict."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def size_from_name(model: str) -> float | None:
    """Billions of parameters from the tag, e.g. 'qwen2.5-coder:7b' -> 7, 'gemma4:e2b' -> 2."""
    tag = model.split(":", 1)[1] if ":" in model else ""
    match = _TAG_SIZE.match(tag.split("-", 1)[0].lower())
    return float(match.group(1)) if match else None


def size_from_parameter_size(text: str | None) -> float | None:
    """Billions of parameters from Ollama's parameter_size text, e.g. '7.6B' or '500M'."""
    match = _PARAMETER_SIZE.match((text or "").strip())
    if not match:
        return None
    value = float(match.group(1))
    return value if match.group(2).upper() == "B" else value / 1000


def suggested_profile(size_b: float | None, native_context: int | None) -> dict[str, Any]:
    """Starting values for a model with no saved profile."""
    if size_b is None or size_b >= 20:
        num_ctx, max_tools = DEFAULT_NUM_CTX, None
    elif size_b < 4:
        num_ctx, max_tools = 8192, 4
    elif size_b < 9:
        num_ctx, max_tools = 16384, 8
    else:
        num_ctx, max_tools = DEFAULT_NUM_CTX, 12
    if native_context:
        num_ctx = min(num_ctx, native_context)
    return {"num_ctx": num_ctx, "reply_reserve": min(2048, num_ctx // 4), "max_tools": max_tools}


def _write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=path.parent,
            prefix=".model_profiles.", suffix=".tmp", delete=False,
        ) as temp_file:
            temp_path = Path(temp_file.name)
            json.dump(data, temp_file, ensure_ascii=False, indent=2)
            temp_file.write("\n")
        os.replace(temp_path, path)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()


class ModelProfileService:
    """Thread-safe saved profiles, keyed by model name, in modelProfiles/data/model_profiles.json."""

    DATA_DIR = COMPONENT_ROOT / "data"
    PROFILES_PATH = DATA_DIR / "model_profiles.json"

    def __init__(self):
        self._lock = threading.RLock()
        self._facts: dict[str, dict[str, Any]] = {}

    # --- Ollama facts -------------------------------------------------------------

    def _fetch_facts(self, model: str) -> dict[str, Any] | None:
        """Ask Ollama about one model. None when Ollama cannot be reached."""
        try:
            info = ollama.show(model)
        except Exception:
            return None
        details = _get(info, "details")
        modelinfo = _get(info, "modelinfo") or {}
        architecture = modelinfo.get("general.architecture") if isinstance(modelinfo, dict) else None
        native = modelinfo.get(f"{architecture}.context_length") if architecture else None
        parameter_size = _get(details, "parameter_size")
        return {
            "model": model,
            "available": True,
            "parameter_size": parameter_size,
            "family": _get(details, "family"),
            "native_context": int(native) if native else None,
            "capabilities": list(_get(info, "capabilities") or []),
            "size_b": size_from_name(model) or size_from_parameter_size(parameter_size),
        }

    def facts(self, model: str) -> dict[str, Any]:
        """What is known about a model. Failures are not cached, so a later call retries."""
        with self._lock:
            cached = self._facts.get(model)
        if cached is not None:
            return cached
        fetched = self._fetch_facts(model)
        if fetched is None:
            return {"model": model, "available": False, "parameter_size": None, "family": None,
                    "native_context": None, "capabilities": [], "size_b": size_from_name(model)}
        with self._lock:
            self._facts[model] = fetched
        return fetched

    # --- Saved profiles -----------------------------------------------------------

    def _read(self) -> dict[str, dict[str, Any]]:
        if not self.PROFILES_PATH.is_file():
            return {}
        try:
            data = json.loads(self.PROFILES_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Could not read model profiles '{self.PROFILES_PATH}': {exc}") from exc
        if not isinstance(data, dict):
            raise RuntimeError(f"Model profiles must contain a JSON object: {self.PROFILES_PATH}")
        return data

    def effective(self, model: str) -> dict[str, Any]:
        """The values a chat with this model runs with: saved over suggested, clamped to the model."""
        facts = self.facts(model)
        native = facts.get("native_context")
        suggested = suggested_profile(facts.get("size_b"), native)
        saved = self._read().get(model)
        values = {**suggested, **(saved or {})}
        num_ctx = min(values["num_ctx"], native) if native else values["num_ctx"]
        return {
            "model": model,
            "num_ctx": num_ctx,
            "reply_reserve": min(values["reply_reserve"], num_ctx // 2),
            "max_tools": values["max_tools"],
            "source": "saved" if saved else "suggested",
            "suggested": suggested,
            "native_context": native,
            "size_b": facts.get("size_b"),
            "parameter_size": facts.get("parameter_size"),
            "capabilities": facts.get("capabilities", []),
            "available": facts.get("available", False),
        }

    def list_models(self) -> dict[str, Any]:
        """Installed chat models with their profiles. Embedding-only models are left out."""
        try:
            listed = ollama.list().get("models") or []
            names = sorted({_get(entry, "model") or _get(entry, "name") for entry in listed} - {None})
            error = None
        except Exception as exc:
            names, error = [], f"Could not list models: {exc}"
        chat_models = []
        for name in names:
            capabilities = self.facts(name).get("capabilities") or []
            if capabilities and "completion" not in capabilities:
                continue
            chat_models.append(self.effective(name))
        return {"models": chat_models, "error": error}

    def save(self, model: str, num_ctx: int, reply_reserve: int, max_tools: int | None) -> dict[str, Any]:
        native = self.facts(model).get("native_context")
        upper = native or MAX_NUM_CTX_LIMIT
        if not MIN_NUM_CTX <= num_ctx <= upper:
            raise ValueError(f"num_ctx must be between {MIN_NUM_CTX} and {upper} for this model.")
        if not MIN_REPLY_RESERVE <= reply_reserve <= num_ctx // 2:
            raise ValueError(f"reply_reserve must be between {MIN_REPLY_RESERVE} and {num_ctx // 2}.")
        if max_tools is not None and not 0 <= max_tools <= MAX_TOOLS_LIMIT:
            raise ValueError(f"max_tools must be empty or between 0 and {MAX_TOOLS_LIMIT}.")
        with self._lock:
            data = self._read()
            data[model] = {"num_ctx": num_ctx, "reply_reserve": reply_reserve, "max_tools": max_tools}
            _write_json_atomic(self.PROFILES_PATH, data)
        return self.effective(model)

    def reset(self, model: str) -> dict[str, Any]:
        with self._lock:
            data = self._read()
            if data.pop(model, None) is not None:
                _write_json_atomic(self.PROFILES_PATH, data)
        return self.effective(model)


model_profiles = ModelProfileService()
