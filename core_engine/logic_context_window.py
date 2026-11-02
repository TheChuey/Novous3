"""Context window accounting: what a model is given, how full it is, and how many more turns fit.

Token counts are estimates (characters / 3.5). The engine calibrates them against the prompt size
Ollama reports. Nothing here trims history: the report shows what the runtime will drop."""

import json
import math
from typing import Any

CHARS_PER_TOKEN = 3.5
MESSAGE_OVERHEAD = 4
PREVIEW_CHARS = 160
AVERAGE_TURNS = 5
DEFAULT_TURN_TOKENS = 300
CALIBRATION_RANGE = (0.5, 2.0)


def read_field(obj: Any, key: str) -> Any:
    """Read a field from a plain dict or an Ollama message object."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def window_status(percent: float) -> tuple[str, list[str]]:
    """Level and flag for how full the window is; the thresholds are shared by chat and squads."""
    if percent >= 95:
        return "crit", ["context_full"]
    if percent >= 80:
        return "warn", ["context_filling"]
    return "ok", []


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / CHARS_PER_TOKEN) if text else 0


def message_text(message: Any) -> str:
    """Text the model reads for one message, including the arguments of any tool calls."""
    parts = [str(read_field(message, "content") or "")]
    for call in read_field(message, "tool_calls") or []:
        function = read_field(call, "function")
        arguments = read_field(function, "arguments")
        name = read_field(function, "name") or ""
        parts.append(f"{name} {json.dumps(arguments, ensure_ascii=False, default=str) if arguments else ''}")
    return "\n".join(part for part in parts if part)


def estimate_request(messages: list, schema_text: str) -> int:
    """Raw (uncalibrated) estimate of a request: every message plus the tool schemas."""
    return sum(estimate_tokens(message_text(m)) + MESSAGE_OVERHEAD for m in messages) + estimate_tokens(schema_text)


def _preview(text: str) -> str:
    return " ".join(text.split())[:PREVIEW_CHARS]


def build_report(*, model: str, messages: list, system_prompt: str, schema_text: str,
                 tools_total: int, tools_active: int, num_ctx: int, reply_reserve: int,
                 native_context: int | None, capabilities: list[str], calibration: float,
                 measured_prompt_tokens: int | None) -> dict[str, Any]:
    def scale(raw: int) -> int:
        return round(raw * calibration)

    system_tokens = scale(estimate_tokens(system_prompt) + MESSAGE_OVERHEAD)
    tools_tokens = scale(estimate_tokens(schema_text))
    fixed = system_tokens + tools_tokens + reply_reserve
    capacity = max(0, num_ctx - fixed)

    rows: list[dict[str, Any]] = []
    turn = 0
    for message in messages:
        role = read_field(message, "role")
        if role == "system":
            continue
        if role == "user":
            turn += 1
        text = message_text(message)
        rows.append({
            "index": len(rows),
            "turn": max(turn, 1),
            "role": role,
            "tool_name": read_field(message, "name") if role == "tool" else None,
            "chars": len(text),
            "tokens": scale(estimate_tokens(text) + MESSAGE_OVERHEAD),
            "preview": _preview(text),
            "fits": True,
        })

    # The runtime drops from the front, so keep the newest messages that fit the history budget.
    kept_from = len(rows)
    running = 0
    for index in range(len(rows) - 1, -1, -1):
        if running + rows[index]["tokens"] > capacity:
            break
        running += rows[index]["tokens"]
        kept_from = index
    for row in rows[:kept_from]:
        row["fits"] = False

    history_tokens = sum(row["tokens"] for row in rows)
    turn_tokens: dict[int, int] = {}
    for row in rows:
        turn_tokens[row["turn"]] = turn_tokens.get(row["turn"], 0) + row["tokens"]
    recent = [turn_tokens[t] for t in sorted(turn_tokens)[-AVERAGE_TURNS:]]
    average_turn = max(1, round(sum(recent) / len(recent))) if recent else DEFAULT_TURN_TOKENS

    dropped_turns = {row["turn"] for row in rows if not row["fits"]}
    in_memory = len(set(turn_tokens) - dropped_turns)
    turns_capacity = capacity // average_turn
    turns_remaining = (capacity - history_tokens) // average_turn if history_tokens <= capacity else 0

    used = fixed + history_tokens
    percent = round(used / num_ctx * 100, 1) if num_ctx else 0.0

    if dropped_turns:
        sentence = (f"{len(dropped_turns)} earlier turn(s) no longer fit, so the model runtime is dropping them. "
                    f"About {in_memory} turn(s) are still remembered.")
    else:
        sentence = f"About {turns_remaining} more average turns fit before the oldest one drops out."

    warnings: list[dict[str, str]] = []

    def warn(level: str, code: str, message: str) -> None:
        warnings.append({"level": level, "code": code, "message": message})

    if percent >= 95:
        warn("critical", "context_full", f"The context is {percent}% full.")
    elif percent >= 80:
        warn("warning", "context_filling", f"The context is {percent}% full.")
    if num_ctx and fixed / num_ctx > 0.4:
        warn("warning", "fixed_overhead",
             f"The system prompt and tools use {round(fixed / num_ctx * 100)}% of the window.")
    if dropped_turns:
        warn("critical", "turns_dropped",
             f"{len(dropped_turns)} earlier turn(s) no longer fit and are being dropped.")
    if measured_prompt_tokens and num_ctx and measured_prompt_tokens >= num_ctx * 0.95:
        warn("critical", "measured_full",
             f"The last prompt was {measured_prompt_tokens} tokens against a {num_ctx} window.")
    if native_context and num_ctx > native_context:
        warn("warning", "above_native", f"The window is larger than the model's native {native_context} tokens.")
    if capabilities and "tools" not in capabilities and tools_active:
        warn("warning", "no_tools_capability", "This model does not list tool support, so tool calls may fail.")
    if tools_active < tools_total:
        warn("info", "tools_capped",
             f"Only {tools_active} of {tools_total} tools are sent to this model.")

    return {
        "model": model,
        "num_ctx": num_ctx,
        "native_context": native_context,
        "used_tokens": used,
        "percent": percent,
        "segments": {
            "system": system_tokens,
            "tools": tools_tokens,
            "history": history_tokens,
            "reply_reserve": reply_reserve,
            "free": max(0, num_ctx - used),
        },
        "tools": {"total": tools_total, "active": tools_active},
        "calibration": round(calibration, 3),
        "measured_prompt_tokens": measured_prompt_tokens,
        "forecast": {
            "average_turn_tokens": average_turn,
            "turns_remaining": turns_remaining,
            "turns_capacity": turns_capacity,
            "turns_in_memory": in_memory,
            "turns_dropped": len(dropped_turns),
            "sentence": sentence,
            "note": "Estimate based on the last five turns. Turns that call tools use more room.",
        },
        "messages": rows,
        "warnings": warnings,
    }


def summarize(report: dict[str, Any]) -> dict[str, Any]:
    """The compact form sent with each chat reply."""
    forecast = report["forecast"]
    return {
        "percent": report["percent"],
        "used_tokens": report["used_tokens"],
        "num_ctx": report["num_ctx"],
        "turns_remaining": forecast["turns_remaining"],
        "turns_capacity": forecast["turns_capacity"],
        "turns_in_memory": forecast["turns_in_memory"],
        "turns_dropped": forecast["turns_dropped"],
        "sentence": forecast["sentence"],
        "warnings": report["warnings"],
    }
