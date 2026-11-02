"""Core-engine domain logic: the agent library, the Think-Act-Observe runtime, the tool
catalog with its built-in tools, the human-in-the-loop questionnaire registry and
chat-session management.

Module singletons (bottom of file): ``agent_library``, ``tool_catalog`` (plus the
``tool`` decorator used by tool modules), ``questionnaire`` and ``chat_service``.
"""

import ast
import importlib
import importlib.util
import inspect
import json
import operator
import re
import sys
import threading
import time
import uuid
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, List

import ollama
import yaml

from core_engine import logic_context_window as context_window
from core_engine.schemas_core_engine import AgentProfile
from editor.logic_editor import editor_files, session_manager
from editor.schemas_editor import EditorEventType
from modelProfiles.logic_modelProfiles import model_profiles

MAX_NUM_CTX = 32768
DEFAULT_MODEL = "qwen2.5-coder:latest"
# Tools a profile must keep when its tool cap is smaller than its tool list.
PINNED_TOOLS = {"rag-presenter": ("call_subagent", "read_agent_scratchpad")}

# The agent whose tool call is running, so shared tools can record who wrote what.
_CURRENT_AGENT_ID: ContextVar[str] = ContextVar("current_agent_id", default="")


def current_agent_id() -> str:
    return _CURRENT_AGENT_ID.get()

AGENT_MD_TEMPLATE = """# {name}

## role
You are {name}, a Novous workspace agent. You act as a specialist in your domain and own every request end to end. Always think before answering and verify any claim you reuse.

## purpose
{purpose}

## boundaries
- Stay within the scope described in your purpose.
- Do not fabricate facts, citations, or tool results.
- Treat the workspace directory as the default destination for files. File tool paths are relative to the workspace root: use a bare filename for a file in its root, and do not add a `workspace/` prefix.
- For file requests, use write_file with the requested content; use create_file only for an intentionally empty file.
- Treat tool errors as failures, never as success. Claim a file was created or updated only after the write tool reports success.
- Never share secrets, credentials, or private user data.
- Report errors honestly instead of guessing.

## output format
Lead with the direct answer, then follow with brief supporting detail. Use bullet lists for three or more items, use markdown headings for long responses, and keep every reply concise.
"""


# --- Agent definition parsing (pure helpers) ---------------------------------------

def parse_markdown_sections(md_text: str) -> dict:
    sections = {}
    current_title = None
    current_lines = []

    for line in md_text.splitlines():
        if line.startswith("## "):
            if current_title:
                sections[current_title] = "\n".join(current_lines).strip()
            current_title = line[3:].strip().lower()
            current_lines = []
        elif current_title is not None:
            current_lines.append(line)

    if current_title:
        sections[current_title] = "\n".join(current_lines).strip()

    return sections


def _coerce_yaml_scalar(value: str):
    text = (value or "").strip()
    if text == "":
        return ""
    lower = text.lower()
    if lower in {"true", "false"}:
        return lower == "true"
    if lower in {"null", "~"}:
        return None
    if text.startswith(('"', "'")) and text.endswith(('"', "'")):
        return text[1:-1]
    if text.startswith("[") and text.endswith("]"):
        candidate = text.replace("'", '"')
        try:
            return json.loads(candidate)
        except Exception:
            return text
    if text.startswith("{") and text.endswith("}"):
        candidate = text.replace("'", '"')
        try:
            return json.loads(candidate)
        except Exception:
            return text
    if text.startswith("0") and text.count(".") == 0:
        try:
            return int(text)
        except ValueError:
            pass
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return text


def parse_yaml_frontmatter(raw_text: str) -> dict:
    if raw_text is None:
        return {}
    stripped = raw_text.lstrip()
    if not stripped.startswith("---"):
        return {}
    lines = raw_text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    end_index = None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            end_index = index
            break
    if end_index is None:
        return {}

    data: dict[str, object] = {}
    current_key: str | None = None
    current_list: list | None = None
    nested_map: dict | None = None

    for line in lines[1:end_index]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indentation = len(line) - len(line.lstrip(" "))
        stripped_line = line.strip()

        if indentation == 0:
            key, sep, value = stripped_line.partition(":")
            if not sep:
                continue
            key = key.strip()
            value = value.strip()
            current_key = key
            current_list = None
            nested_map = None
            if value == "":
                data[key] = []
                current_list = data[key]
            else:
                data[key] = _coerce_yaml_scalar(value)
                current_key = None
            continue

        if current_key is None:
            continue

        if stripped_line.startswith("- "):
            item = stripped_line[2:].strip()
            if item.endswith(":"):
                item_key = item[:-1].strip()
                entry = {item_key: {}}
                if not isinstance(data[current_key], list):
                    data[current_key] = []
                data[current_key].append(entry)
                nested_map = entry[item_key]
                current_list = None
            else:
                if not isinstance(data[current_key], list):
                    data[current_key] = []
                data[current_key].append(_coerce_yaml_scalar(item))
                nested_map = None
                current_list = None
            continue

        if nested_map is not None and ":" in stripped_line:
            nested_key, nested_value = stripped_line.split(":", 1)
            nested_key = nested_key.strip()
            nested_value = nested_value.strip()
            nested_map[nested_key] = _coerce_yaml_scalar(nested_value) if nested_value else ""
            continue

        if current_list is not None and ":" in stripped_line:
            list_key, list_value = stripped_line.split(":", 1)
            list_key = list_key.strip()
            list_value = list_value.strip()
            if list_key and isinstance(current_list, list):
                current_list.append({list_key: _coerce_yaml_scalar(list_value) if list_value else ""})

    return data


class AgentLibrary:
    """Loads primary agents from ``library/*.md`` and sub-agents from ``subAgentLibrary/*.yaml``."""

    REPO_ROOT = Path(__file__).resolve().parent.parent
    LIBRARY_ROOT = REPO_ROOT / "library"
    SUBAGENT_LIBRARY_ROOT = REPO_ROOT / "subAgentLibrary"
    AGENT_ENVIRONMENTS = {
        "workspace": LIBRARY_ROOT,
        "codebuilder": LIBRARY_ROOT,
        "prompt-creation": LIBRARY_ROOT,
    }

    def normalize_agent_id(self, agent_id: str) -> str:
        clean = str(agent_id or "").strip().replace("\\", "/")
        if not clean or clean in (".", "..") or "/" in clean:
            raise ValueError(f"Invalid agent id: {agent_id!r}")
        return clean

    def _get_agents_root(self, environment: str = "workspace") -> Path:
        try:
            return self.AGENT_ENVIRONMENTS[environment]
        except KeyError as exc:
            raise ValueError(f"Unknown agent environment: {environment}") from exc

    def find_agent_file(self, agent_id: str, environment: str = "workspace") -> Path | None:
        clean = self.normalize_agent_id(agent_id)
        candidate = self._get_agents_root(environment) / f"{clean}.md"
        if candidate.is_file():
            return candidate
        return None

    def _library_file_for(self, agent_id: str) -> Path:
        clean = self.normalize_agent_id(agent_id)
        return self.LIBRARY_ROOT / f"{clean}.md"

    def parse_subagent_yaml(self, subagent_id: str) -> tuple[dict, str]:
        """Load a pure-YAML sub-agent definition from subAgentLibrary/."""
        clean = self.normalize_agent_id(subagent_id)
        subagent_file = self.SUBAGENT_LIBRARY_ROOT / f"{clean}.yaml"
        if not subagent_file.is_file():
            raise FileNotFoundError(f"Sub-agent definition not found: {subagent_file}")
        data = yaml.safe_load(subagent_file.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            raise ValueError(f"Sub-agent definition must be a YAML mapping: {subagent_file}")
        body = data.pop("prompt", "")
        data.setdefault("id", clean)
        if not data.get("type"):
            data["type"] = "subagent"
        return data, str(body or "")

    def _read_agent_markdown(self, md_path: Path) -> tuple[str, str]:
        text = md_path.read_text(encoding="utf-8") if md_path.exists() else ""
        if not text.startswith("---"):
            return "", text.strip("\n")
        lines = text.splitlines()
        if not lines or lines[0].strip() != "---":
            return "", text.strip("\n")
        end_index = None
        for index in range(1, len(lines)):
            if lines[index].strip() == "---":
                end_index = index
                break
        if end_index is None:
            return "", text.strip("\n")
        frontmatter = "\n".join(lines[1:end_index])
        body = "\n".join(lines[end_index + 1:]).strip("\n")
        return frontmatter, body

    def load_agent_definition_from_md(self, md_path: Path) -> AgentProfile:
        if not md_path.is_file():
            raise FileNotFoundError(f"Agent markdown file not found: {md_path}")
        raw_text = md_path.read_text(encoding="utf-8")
        frontmatter = parse_yaml_frontmatter(raw_text)
        body = raw_text
        if raw_text.startswith("---"):
            _, body = self._read_agent_markdown(md_path)

        sections = parse_markdown_sections(body)
        meta = dict(frontmatter)
        agent_id = meta.get("id") or md_path.stem
        profile = AgentProfile(
            id=str(agent_id),
            name=str(meta.get("name") or md_path.stem.replace("-", " ").title()),
            description=str(meta.get("description") or ""),
            mode=str(meta.get("mode") or "agent"),
            role=sections.get("role", ""),
            purpose=sections.get("purpose", ""),
            boundaries=sections.get("boundaries", ""),
            model=str(meta.get("model") or ""),
            tools=list(meta.get("tools") or []),
            extras={**sections, **({"max_tool_rounds": meta["max_tool_rounds"]} if "max_tool_rounds" in meta else {})},
        )

        prompt_parts = [
            f"You are {profile.name}.",
            f"ROLE:\n{profile.role}" if profile.role else "",
            f"PURPOSE:\n{profile.purpose}" if profile.purpose else "",
            f"BOUNDARIES:\n{profile.boundaries}" if profile.boundaries else "",
        ]

        for title, content in sections.items():
            if title not in ("role", "purpose", "boundaries") and content:
                prompt_parts.append(f"{title.upper()}:\n{content}")

        if profile.mode != "agent" or not profile.tools:
            prompt_parts.append(
                "NO TOOLS:\nYou have no tools, functions, or file access. Never output function "
                "calls, tool-call JSON, or claims of creating, writing, or saving files. "
                "Answer directly in plain Markdown text."
            )

        profile.system_prompt = "\n\n".join(p for p in prompt_parts if p)
        return profile

    def load_agent(self, agent_id: str, environment: str = "workspace") -> AgentProfile:
        clean = self.normalize_agent_id(agent_id)
        self._get_agents_root(environment)
        library_file = self.find_agent_file(clean, environment)
        if library_file:
            metadata = parse_yaml_frontmatter(library_file.read_text(encoding="utf-8"))
            if metadata.get("environment", "workspace") == environment:
                return self.load_agent_definition_from_md(library_file)
        raise FileNotFoundError(f"Agent not found in {environment}: {agent_id}")

    def list_agents(self, environment: str | None = None) -> list[dict]:
        seen: set[str] = set()
        agents: list[dict] = []
        roots: list[Path] = [self.LIBRARY_ROOT]

        if environment is not None:
            roots = [self._get_agents_root(environment)]

        for root in roots:
            if not root.is_dir():
                continue
            for child in sorted(root.iterdir(), key=lambda p: p.name.lower()):
                if child.suffix.lower() != ".md":
                    continue
                agent_id = child.stem
                if agent_id in seen:
                    continue
                try:
                    profile = self.load_agent_definition_from_md(child)
                    metadata = parse_yaml_frontmatter(child.read_text(encoding="utf-8"))
                except Exception:
                    continue
                agent_environment = metadata.get("environment", "workspace")
                if environment is not None and agent_environment != environment:
                    continue
                seen.add(agent_id)
                agents.append({
                    "id": profile.id,
                    "name": profile.name,
                    "description": profile.description,
                    "mode": profile.mode,
                    "model": profile.model,
                    "squad": "",
                    "tools": profile.tools,
                    "has_markdown": True,
                    "environment": agent_environment,
                    "path": child.relative_to(self.REPO_ROOT).as_posix(),
                })

        return agents

    # --- Tool assignment ------------------------------------------------------------

    TOOL_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

    def _definition_file(self, kind: str, agent_id: str) -> Path:
        clean = self.normalize_agent_id(agent_id)
        if kind == "prompt":
            path = self.LIBRARY_ROOT / f"{clean}.md"
        elif kind == "subagent":
            path = self.SUBAGENT_LIBRARY_ROOT / f"{clean}.yaml"
        else:
            raise ValueError(f"Unknown definition kind: {kind}")
        if not path.is_file():
            raise FileNotFoundError(f"Definition not found: {path.relative_to(self.REPO_ROOT).as_posix()}")
        return path

    def definition_kind(self, agent_id: str) -> str | None:
        """'prompt' for a library/ agent, 'subagent' for a subAgentLibrary/ worker, else None."""
        clean = self.normalize_agent_id(agent_id)
        if (self.LIBRARY_ROOT / f"{clean}.md").is_file():
            return "prompt"
        if (self.SUBAGENT_LIBRARY_ROOT / f"{clean}.yaml").is_file():
            return "subagent"
        return None

    def allowed_subagents(self, agent_id: str) -> list[str]:
        """Sub-agent ids an agent's '## Sub-agents' section allows; empty means unrestricted."""
        kind = self.definition_kind(agent_id)
        if kind is None:
            return []
        return [entry["id"] for entry in self.get_agent_tools(kind, agent_id)["subagents"]]

    @staticmethod
    def _yaml_bounds(kind: str, lines: list[str]) -> tuple[int, int]:
        """Line range holding the YAML mapping: the frontmatter of a prompt, the whole sub-agent file."""
        if kind == "subagent":
            return 0, len(lines)
        if not lines or lines[0].strip() != "---":
            raise ValueError("Prompt file has no YAML frontmatter.")
        for index in range(1, len(lines)):
            if lines[index].strip() == "---":
                return 1, index
        raise ValueError("Prompt frontmatter is not closed with '---'.")

    @staticmethod
    def _replace_top_level_key(lines: list[str], start: int, end: int, key: str,
                               replacement: list[str]) -> list[str]:
        """Swap one top-level YAML key (and its indented or '- ' continuation lines) for new lines."""
        pattern = re.compile(rf"^{re.escape(key)}:(\s|$)")
        for index in range(start, end):
            if pattern.match(lines[index]):
                stop = index + 1
                last = index + 1
                while stop < end and (not lines[stop].strip() or lines[stop][:1] in (" ", "\t", "-")):
                    stop += 1
                    if lines[stop - 1].strip():
                        last = stop
                return lines[:index] + replacement + lines[last:]
        # Key missing: add it before a sub-agent's prompt block, else at the end of the mapping.
        insert_at = end
        for index in range(start, end):
            if lines[index].startswith("prompt:"):
                insert_at = index
                break
        return lines[:insert_at] + replacement + lines[insert_at:]

    def _load_definition_yaml(self, kind: str, text: str) -> dict:
        lines = text.splitlines()
        start, end = self._yaml_bounds(kind, lines)
        data = yaml.safe_load("\n".join(lines[start:end])) or {}
        if not isinstance(data, dict):
            raise ValueError("Definition YAML must be a mapping.")
        return data

    def get_agent_tools(self, kind: str, agent_id: str) -> dict:
        path = self._definition_file(kind, agent_id)
        text = path.read_text(encoding="utf-8")
        data = self._load_definition_yaml(kind, text)
        _, body, _ = self._split_prompt_body(kind, text.splitlines())
        return {
            "kind": kind,
            "agent_id": path.stem,
            "path": path.relative_to(self.REPO_ROOT).as_posix(),
            "mode": str(data.get("mode") or "agent"),
            "tools": [str(tool) for tool in (data.get("tools") or [])],
            "subagents": self._parse_subagents_section(body or []),
        }

    TOOLS_SECTION_HEADING = "## Available Tools"
    TOOLS_HEADING_PATTERN = re.compile(r"^##\s+(available\s+)?tools\s*$", re.IGNORECASE)
    SUBAGENTS_SECTION_HEADING = "## Sub-agents"
    SUBAGENTS_HEADING_PATTERN = re.compile(r"^##\s+sub-?agents\s*$", re.IGNORECASE)
    SECTION_BOUNDARY_PATTERN = re.compile(r"^#{1,2}\s")
    USAGE_LABEL = "- When to use:"
    MAX_USAGE_LENGTH = 2000

    @staticmethod
    def _render_tools_section(tools: list[str]) -> list[str]:
        """The same '## Available Tools' layout Prompt Creation writes: name, provider, signature, description."""
        catalog = {meta["name"]: meta for meta in tool_catalog.list_tools()}
        entries = []
        for name in tools:
            meta = catalog.get(name)
            if meta is None:
                entries.append([f"### {name}", "- Not registered in the tool catalog"])
                continue
            description = meta.get("description") or ""
            if isinstance(description, list):
                description = " ".join(description)
            entry = [f"### {name}", f"- Provider: {meta['provider']}", f"- Signature: {meta['signature']}"]
            if description.strip():
                entry.append(description.strip())
            entries.append(entry)
        lines = [AgentLibrary.TOOLS_SECTION_HEADING]
        for entry in entries:
            lines += ["", *entry]
        return lines

    def _render_subagents_section(self, subagents: list[dict]) -> list[str]:
        """'## Sub-agents': which sub-agents ``call_subagent`` may reach and when; the Tools dialog reads it back."""
        lines = [
            self.SUBAGENTS_SECTION_HEADING,
            "",
            "Delegate work with `call_subagent(subagent_id, task_prompt, parameters)`. "
            "Only call the sub-agents listed here, using the exact ID.",
        ]
        for entry in subagents:
            data, _ = self.parse_subagent_yaml(entry["id"])
            name = " ".join(str(data.get("name") or entry["id"]).split())
            lines += ["", f"### {entry['id']}", f"- Name: {name}"]
            description = " ".join(str(data.get("description") or "").split())
            if description:
                lines.append(f"- What it does: {description}")
            lines.append(self.USAGE_LABEL)
            lines += entry["usage"].splitlines() or ["Use it when the task matches what this sub-agent does."]
        return lines

    @classmethod
    def _find_section(cls, body: list[str], pattern: re.Pattern) -> tuple[int, int] | None:
        start = next((i for i, line in enumerate(body) if pattern.match(line.strip())), None)
        if start is None:
            return None
        stop = next((i for i in range(start + 1, len(body)) if cls.SECTION_BOUNDARY_PATTERN.match(body[i])),
                    len(body))
        return start, stop

    @classmethod
    def _apply_section(cls, body: list[str], pattern: re.Pattern, section: list[str]) -> list[str]:
        """Replace the section matching ``pattern``; append it when missing, drop it when ``section`` is empty."""
        bounds = cls._find_section(body, pattern)
        before, after = (list(body), []) if bounds is None else (body[:bounds[0]], body[bounds[1]:])
        while before and not before[-1].strip():
            before.pop()
        result = before + ([""] if before and section else []) + section
        if after:
            result += [""] + after
        return result

    @classmethod
    def _parse_subagents_section(cls, body: list[str]) -> list[dict]:
        bounds = cls._find_section(body, cls.SUBAGENTS_HEADING_PATTERN)
        if bounds is None:
            return []
        entries, current, in_usage = [], None, False
        for line in body[bounds[0] + 1:bounds[1]]:
            if line.startswith("### "):
                current = {"id": line[4:].strip(), "usage": []}
                entries.append(current)
                in_usage = False
            elif current is not None and line.startswith(cls.USAGE_LABEL):
                in_usage = True
                inline = line[len(cls.USAGE_LABEL):].strip()
                if inline:
                    current["usage"].append(inline)
            elif current is not None and in_usage:
                current["usage"].append(line)
        return [{"id": entry["id"], "usage": "\n".join(entry["usage"]).strip()} for entry in entries]

    def _split_prompt_body(self, kind: str, lines: list[str]) -> tuple[list[str], list[str] | None, dict]:
        """Split a definition into (prefix, dedented prompt body, layout); body is None when there is none."""
        if kind == "prompt":
            _, end = self._yaml_bounds(kind, lines)
            return lines[:end + 1], lines[end + 1:], {"suffix": [], "indent": 0, "trailing": 0}
        # Sub-agent: the literal ``prompt: |`` block, edited in place with its indentation kept.
        header = next((i for i, line in enumerate(lines) if re.match(r"^prompt:\s*\|[-+]?\s*$", line)), None)
        if header is None:
            return lines, None, {}
        stop = header + 1
        while stop < len(lines) and (not lines[stop].strip() or lines[stop][:1] in (" ", "\t")):
            stop += 1
        block = lines[header + 1:stop]
        indent = next((len(line) - len(line.lstrip()) for line in block if line.strip()), 2)
        body = [line[indent:] if line.strip() else "" for line in block]
        trailing = 0
        while body and not body[-1]:
            body.pop()
            trailing += 1
        return lines[:header + 1], body, {"suffix": lines[stop:], "indent": indent, "trailing": trailing}

    @staticmethod
    def _join_prompt_body(prefix: list[str], body: list[str], layout: dict) -> list[str]:
        indent = " " * layout["indent"]
        return prefix + [(indent + line) if line else "" for line in body] + [""] * layout["trailing"] + layout["suffix"]

    def _clean_subagents(self, kind: str, agent_id: str, subagents: list[dict]) -> list[dict]:
        cleaned, seen = [], set()
        for entry in subagents:
            subagent_id = self.normalize_agent_id(str(entry.get("id") or ""))
            if subagent_id in seen:
                continue
            if kind == "subagent" and subagent_id == agent_id:
                raise ValueError("A sub-agent cannot list itself as a sub-agent.")
            if not (self.SUBAGENT_LIBRARY_ROOT / f"{subagent_id}.yaml").is_file():
                raise ValueError(f"Sub-agent not found in subAgentLibrary/: {subagent_id}")
            usage = str(entry.get("usage") or "").strip()
            if len(usage) > self.MAX_USAGE_LENGTH:
                raise ValueError(f"Usage note for {subagent_id} is longer than {self.MAX_USAGE_LENGTH} characters.")
            if any(line.lstrip().startswith("#") for line in usage.splitlines()):
                raise ValueError(f"Usage note for {subagent_id} cannot contain lines starting with '#'.")
            seen.add(subagent_id)
            cleaned.append({"id": subagent_id, "usage": usage})
        return cleaned

    def set_agent_tools(self, kind: str, agent_id: str, tools: list[str], mode: str | None = None,
                        subagents: list[dict] | None = None) -> dict:
        """Rewrite the ``tools`` (and optionally ``mode``) keys plus the prompt's generated
        '## Available Tools' and '## Sub-agents' sections, leaving the rest of the file intact.
        ``subagents=None`` keeps the sub-agents the prompt already lists."""
        clean_tools = list(dict.fromkeys(str(tool).strip() for tool in tools))
        invalid = [tool for tool in clean_tools if not self.TOOL_NAME_PATTERN.match(tool)]
        if invalid:
            raise ValueError(f"Invalid tool names: {', '.join(invalid)}")
        if mode is not None and mode not in ("chat", "agent"):
            raise ValueError(f"Invalid mode: {mode}")

        path = self._definition_file(kind, agent_id)
        agent_id = path.stem
        text = path.read_text(encoding="utf-8")
        newline = "\r\n" if "\r\n" in text else "\n"
        lines = text.splitlines()
        start, end = self._yaml_bounds(kind, lines)
        block = ["tools:", *(f"  - {tool}" for tool in clean_tools)] if clean_tools else ["tools: []"]
        lines = self._replace_top_level_key(lines, start, end, "tools", block)
        if mode is not None:
            start, end = self._yaml_bounds(kind, lines)
            lines = self._replace_top_level_key(lines, start, end, "mode", [f"mode: {mode}"])

        prefix, body, layout = self._split_prompt_body(kind, lines)
        if body is not None:
            if subagents is None:
                subagents = self._parse_subagents_section(body)
            chosen = self._clean_subagents(kind, agent_id, subagents) if "call_subagent" in clean_tools else []
            body = self._apply_section(body, self.TOOLS_HEADING_PATTERN,
                                       self._render_tools_section(clean_tools) if clean_tools else [])
            body = self._apply_section(body, self.SUBAGENTS_HEADING_PATTERN,
                                       self._render_subagents_section(chosen) if chosen else [])
            lines = self._join_prompt_body(prefix, body, layout)
        updated = newline.join(lines) + (newline if text.endswith(("\n", "\r")) else "")

        # Never write a file whose YAML no longer parses to exactly what was requested.
        before = self._load_definition_yaml(kind, text)
        data = self._load_definition_yaml(kind, updated)
        untouched = {key for key in before if key not in ("tools", "mode", "prompt")}
        if [str(tool) for tool in (data.get("tools") or [])] != clean_tools or (
            mode is not None and data.get("mode") != mode
        ) or any(data.get(key) != before[key] for key in untouched):
            raise ValueError("Could not update tools without changing other YAML; edit the file directly.")
        path.write_text(updated, encoding="utf-8", newline="")
        return self.get_agent_tools(kind, agent_id)


# --- Runtime -----------------------------------------------------------------------

class Agent:
    MAX_TOOL_ROUNDS = 6
    MAX_TOOL_STATS = 20
    TOOL_FAILURE_KINDS = ("unknown_tool", "bad_arguments", "tool_error")
    PARAMETER_DESCRIPTIONS = {
        "payload": (
            "JSON object string containing the structured handoff fields. "
            "Never use triple-quoted strings."
        ),
        "status": "One of: success, nothing_found, error.",
        "subagent_id": "The canonical sub-agent ID.",
        "task_type": "The task type requested by orchestration.",
        "proposed_content": "The complete proposed file or editor content.",
        "memory_type": "Leave empty to list saved items, or one of: latest_read, latest_map, latest_subagent_result.",
        "item": "A saved file path, or an id from the list, to read in pages.",
        "offset": "Character position to start the page at; use next_offset from the previous page. Default 0.",
        "max_chars": "Page size in characters, 500 to 20000. Default 4000.",
    }

    def __init__(self, model: str | None, tools: List[Callable], profile: AgentProfile, session=None):
        self.model = model or profile.model or "qwen2.5-coder:latest"
        self.profile = profile
        self.tools = {getattr(f, "name", None) or f.__name__: f for f in (tools or [])}
        self.messages: List[dict] = []
        self.session = session
        # Agents may raise the tool-round budget with `max_tool_rounds` in their definition.
        try:
            self.max_tool_rounds = max(1, int(profile.extras.get("max_tool_rounds", self.MAX_TOOL_ROUNDS)))
        except (AttributeError, TypeError, ValueError):
            self.max_tool_rounds = self.MAX_TOOL_ROUNDS
        self.tool_events: List[dict] = []
        self.tool_stats: dict[str, dict] = {}
        # Context budget, set from the model profile by ChatService; usage measured from Ollama.
        self.num_ctx = MAX_NUM_CTX
        self.tools_available = len(self.tools)
        self.calibration = 1.0
        self.last_usage: dict | None = None
        self.usage_totals = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}
        self.peak_prompt_tokens = 0
        self.llm_calls = 0
        self.failures: dict[str, int] = {}
        self.recent_failures: List[dict] = []

    def run_stats(self) -> dict[str, Any]:
        """What a run reports for this agent: window use, tool calls and failures, and a level."""
        window = self.num_ctx or 1
        estimated = round(context_window.estimate_request(self.messages, self.tool_schema_text()) * self.calibration)
        peak_tokens = self.peak_prompt_tokens or estimated
        percent_peak = round(peak_tokens / window * 100, 1)
        tool_failures = sum(self.failures.get(kind, 0) for kind in self.TOOL_FAILURE_KINDS)
        rounds_exhausted = self.failures.get("rounds_exhausted", 0) > 0
        model_error = self.failures.get("model_error", 0) > 0

        window_level, flags = context_window.window_status(percent_peak)
        if tool_failures:
            flags.append("tool_failures")
        if self.failures.get("unknown_tool", 0):
            flags.append("unknown_tool")
        if rounds_exhausted:
            flags.append("rounds_exhausted")
        if model_error:
            flags.append("model_error")

        if window_level == "crit" or rounds_exhausted or model_error:
            level = "crit"
        elif window_level == "warn" or tool_failures:
            level = "warn"
        else:
            level = "ok"
        return {
            "model": self.model,
            "num_ctx": self.num_ctx,
            "tools_active": len(self.tools),
            "tools_total": self.tools_available,
            "llm_calls": self.llm_calls,
            "prompt_tokens_last": (self.last_usage or {}).get("prompt_tokens"),
            "peak_prompt_tokens": peak_tokens,
            "completion_tokens": self.usage_totals["completion_tokens"],
            "percent_peak": percent_peak,
            "tool_calls": sum(s["calls"] for s in self.tool_stats.values()),
            "tool_errors": sum(s["errors"] for s in self.tool_stats.values()),
            "failures": dict(self.failures),
            "rounds_exhausted": rounds_exhausted,
            "level": level,
            "flags": flags,
        }

    def tool_schema_text(self) -> str:
        """The tool schemas as sent with each request (empty when no tools are sent)."""
        if self.profile.mode == "chat" or not self.tools:
            return ""
        return json.dumps([self._ollama_schema(t) for t in self.tools.values()], ensure_ascii=False)

    def _record_usage(self, response: Any, estimate: int) -> None:
        """Keep Ollama's measured prompt and completion sizes; calibrate the estimate to the prompt."""
        self.llm_calls += 1
        prompt = context_window.read_field(response, "prompt_eval_count")
        completion = context_window.read_field(response, "eval_count")
        if prompt is None and completion is None:
            return
        self.usage_totals["calls"] += 1
        self.usage_totals["prompt_tokens"] += prompt or 0
        self.usage_totals["completion_tokens"] += completion or 0
        self.peak_prompt_tokens = max(self.peak_prompt_tokens, prompt or 0)
        self.last_usage = {"prompt_tokens": prompt, "completion_tokens": completion}
        if prompt and estimate:
            low, high = context_window.CALIBRATION_RANGE
            self.calibration = min(high, max(low, prompt / estimate))

    def _text_tool_call(self, item: dict) -> dict | None:
        """Accept both flat and function-wrapped text tool-call objects."""
        function = item.get("function")
        source = function if isinstance(function, dict) else item
        tool_name = source.get("name")
        if tool_name not in self.tools:
            # Text that looks like a call to a missing tool still ends the turn as a plain answer;
            # it is counted here so the failure is visible.
            if isinstance(tool_name, str) and ("arguments" in source or "parameters" in source):
                self.note_failure("unknown_tool", tool_name[:60], f"Text call to unavailable tool '{tool_name[:60]}'.")
            return None
        if source is function:
            args = source.get("arguments") or source.get("parameters") or source.get("args") or {}
        else:
            args = source.get("parameters") or source.get("arguments") or source.get("args") or {}
        return {"function": {"name": tool_name, "arguments": args}}

    def _extract_text_tool_calls(self, content: str) -> List[dict]:
        text = (content or "").strip()
        calls = []
        decoder = json.JSONDecoder()
        index = 0
        while index < len(text):
            if text[index] not in "[{":
                index += 1
                continue
            try:
                parsed, end = decoder.raw_decode(text, index)
            except json.JSONDecodeError:
                index += 1
                continue

            items = parsed if isinstance(parsed, list) else [parsed]
            found_call = False
            for item in items:
                if not isinstance(item, dict):
                    continue
                call = self._text_tool_call(item)
                if call is None:
                    continue
                calls.append(call)
                found_call = True

            index = end if found_call else index + 1
        return calls

    def _format_rag_presenter_reply(self, content: str) -> str:
        if self.profile.id != "rag-presenter":
            return content

        decoder = json.JSONDecoder()
        index = 0
        while index < len(content):
            if content[index] != "{":
                index += 1
                continue
            try:
                parsed, end = decoder.raw_decode(content, index)
            except json.JSONDecodeError:
                index += 1
                continue
            index = end
            if not isinstance(parsed, dict) or parsed.get("name") != "format_answer":
                continue

            arguments = parsed.get("parameters") or parsed.get("arguments") or {}
            if not isinstance(arguments, dict):
                continue
            answer = str(arguments.get("answer") or "").strip()
            if not answer:
                continue

            formatted = answer
            evidence = arguments.get("evidence")
            sources = []
            if isinstance(evidence, list):
                for item in evidence:
                    if isinstance(item, dict) and item.get("path"):
                        path = str(item["path"])
                        if path not in sources:
                            sources.append(path)
            if sources:
                formatted += "\n\n**Sources**\n" + "\n".join(
                    f"- {source}" for source in sources
                )
            return formatted
        return content

    @staticmethod
    def _scratchpad_handoff(name: str, args: Any, result: Any) -> dict:
        """Summarize a scratchpad read or write for SCRATCHPAD_HANDOFF telemetry."""
        if name == "write_agent_scratchpad":
            source = args if isinstance(args, dict) else {}
            payload = source.get("payload")
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except json.JSONDecodeError:
                    payload = None
        else:
            try:
                memory = json.loads(result) if isinstance(result, str) else {}
            except json.JSONDecodeError:
                memory = {}
            source = memory.get("latest_subagent_result", {}) if isinstance(memory, dict) else {}
            source = source if isinstance(source, dict) else {}
            payload = source.get("payload", {})
        handoff = {
            "status": source.get("status", "success"),
            "subagent_id": source.get("subagent_id", ""),
            "direction": "write" if name == "write_agent_scratchpad" else "read",
        }
        if isinstance(payload, dict):
            for key in ("results", "issues_found"):
                if isinstance(payload.get(key), list):
                    handoff["result_count"] = len(payload[key])
                    break
        return handoff

    def note_failure(self, kind: str, tool_name: str, message: str) -> None:
        """Count a failure by kind and keep the last few with their messages (measurement only)."""
        self.failures[kind] = self.failures.get(kind, 0) + 1
        self.recent_failures.append({"tool": tool_name, "kind": kind, "message": str(message)[:200]})
        del self.recent_failures[:-10]

    def _stats_for(self, tool_name: str) -> dict:
        """Per-tool stats; names beyond MAX_TOOL_STATS share one bucket so model output can't grow it."""
        key = tool_name if tool_name in self.tool_stats or len(self.tool_stats) < self.MAX_TOOL_STATS else "(other)"
        return self.tool_stats.setdefault(key, {"calls": 0, "successes": 0, "errors": 0, "success_rate": 0.0})

    @staticmethod
    def _bind_error(tool_func: Callable, args: Any) -> str | None:
        """Why the arguments don't fit the tool's signature, or None when they do (or can't be checked)."""
        try:
            signature = inspect.signature(tool_func)
        except (TypeError, ValueError):
            return None
        try:
            if isinstance(args, dict):
                signature.bind(**args)
            else:
                signature.bind(args)
        except TypeError as exc:
            return str(exc)
        return None

    def act(self, tool_call: dict, origin: str) -> Any:
        from orchestration.logic_orchestration import telemetry_bus

        fn_info = tool_call.get("function", {})
        name = fn_info.get("name")
        args = fn_info.get("arguments", {})
        trace_context = telemetry_bus.current_context()
        trace_id, agent_id = trace_context or ("", "")
        tool_name = str(name or "unknown")[:60]

        def log_tool_end(status: str, duration_ms: int, error_kind: str = "") -> None:
            data = {"tool_name": tool_name, "status": status, "duration_ms": duration_ms}
            if error_kind:
                data["error_kind"] = error_kind
            telemetry_bus.log_event(trace_id, "TOOL_CALL_END", agent_id, tool_name, data)

        stats = self._stats_for(tool_name)
        stats["calls"] += 1
        started = time.perf_counter()

        def elapsed() -> int:
            return int((time.perf_counter() - started) * 1000)

        def fail(kind: str, message: str, reply: str, arguments: Any) -> str:
            stats["errors"] += 1
            stats["success_rate"] = stats["successes"] / stats["calls"]
            self.note_failure(kind, tool_name, message)
            self.tool_events.append({"tool": tool_name, "args": arguments, "status": "error",
                                     "error": message, "error_kind": kind, "origin": origin})
            if trace_context:
                log_tool_end("error", elapsed(), kind)
            return reply

        if name not in self.tools:
            return fail("unknown_tool", f"Tool '{tool_name}' is not available to this agent.",
                        f"Error: Tool '{name}' not found.", args)

        tool_func = self.tools[name]
        if trace_context:
            telemetry_bus.log_event(trace_id, "TOOL_CALL_START", agent_id, tool_name, {"tool_name": tool_name})
        try:
            if isinstance(args, str):
                args = json.loads(args)
        except json.JSONDecodeError as exc:
            message = f"Arguments are not valid JSON: {exc}"
            return fail("bad_arguments", message, f"Error executing tool '{name}': {message}", args)
        bind_error = self._bind_error(tool_func, args)
        if bind_error:
            return fail("bad_arguments", bind_error, f"Error executing tool '{name}': {bind_error}", args)

        try:
            agent_token = _CURRENT_AGENT_ID.set(self.profile.id)
            try:
                result = tool_func(**args) if isinstance(args, dict) else tool_func(args)
            finally:
                _CURRENT_AGENT_ID.reset(agent_token)
        except Exception as exc:
            if trace_context and name == "write_agent_scratchpad":
                telemetry_bus.log_event(
                    trace_id, "SCRATCHPAD_HANDOFF", agent_id, "scratchpad",
                    {
                        "status": "error",
                        "subagent_id": args.get("subagent_id", "") if isinstance(args, dict) else "",
                        "direction": "write",
                    },
                )
            return fail("tool_error", str(exc), f"Error executing tool '{name}': {str(exc)}", args)

        stats["successes"] += 1
        stats["success_rate"] = stats["successes"] / stats["calls"]
        event = {"tool": name, "args": args, "status": "success", "origin": origin}
        result_text = result if isinstance(result, str) else json.dumps(
            result, ensure_ascii=False, default=str
        )
        event["result"] = result_text[:3000]
        if len(result_text) > 3000:
            event["result_truncated"] = True
        if getattr(tool_func, "display_output", False) and isinstance(result, str):
            event["display"] = result
        self.tool_events.append(event)
        if trace_context:
            log_tool_end("success", elapsed())
            if name in {"write_agent_scratchpad", "read_agent_scratchpad"}:
                telemetry_bus.log_event(
                    trace_id, "SCRATCHPAD_HANDOFF", agent_id, "scratchpad",
                    self._scratchpad_handoff(name, args, result),
                )
        return result

    def observe(self, tool_name: str, result: Any) -> None:
        content = json.dumps(result) if not isinstance(result, str) else result
        self.messages.append({"role": "tool", "name": tool_name, "content": content})

    def think(self, user_input: str) -> str:
        if not self.messages or self.messages[0].get("role") != "system":
            self.messages.insert(0, {"role": "system", "content": self.profile.system_prompt})

        self.messages.append({"role": "user", "content": user_input})

        if self.profile.mode == "chat" or not self.tools:
            estimate = context_window.estimate_request(self.messages, "")
            res = ollama.chat(model=self.model, messages=self.messages, options={"num_ctx": self.num_ctx})
            self._record_usage(res, estimate)
            reply = self._format_rag_presenter_reply(res["message"]["content"])
            self.messages.append({"role": "assistant", "content": reply})
            return reply

        for _ in range(self.max_tool_rounds):
            estimate = context_window.estimate_request(self.messages, self.tool_schema_text())
            res = ollama.chat(
                model=self.model,
                messages=self.messages,
                tools=[self._ollama_schema(t) for t in self.tools.values()],
                options={"num_ctx": self.num_ctx}
            )
            self._record_usage(res, estimate)
            msg = res["message"]
            self.messages.append(msg)

            native_calls = msg.get("tool_calls")
            text_calls = self._extract_text_tool_calls(msg.get("content", "")) if not native_calls else []
            tool_calls = native_calls or text_calls

            if not tool_calls:
                return self._format_rag_presenter_reply(msg.get("content", ""))

            origin = "native" if native_calls else "text_json"
            for call in tool_calls:
                tool_name = call["function"]["name"]
                result = self.act(call, origin)
                self.observe(tool_name, result)

        self.note_failure("rounds_exhausted", "", "Maximum tool rounds reached without a final answer.")
        return "(Executed maximum tool rounds without final text summary.)"

    def think_stream(self, user_input: str):
        """Like think(), but yields token / tool_event dicts as they happen."""
        if not self.messages or self.messages[0].get("role") != "system":
            self.messages.insert(0, {"role": "system", "content": self.profile.system_prompt})
        self.messages.append({"role": "user", "content": user_input})

        use_tools = self.profile.mode != "chat" and bool(self.tools)
        extra = {"tools": [self._ollama_schema(t) for t in self.tools.values()]} if use_tools else {}
        final_text = ""

        for _ in range(self.max_tool_rounds if use_tools else 1):
            content = ""
            native_calls: List[dict] = []
            estimate = context_window.estimate_request(self.messages, self.tool_schema_text())
            usage_chunk = None
            for chunk in ollama.chat(model=self.model, messages=self.messages, stream=True,
                                     options={"num_ctx": self.num_ctx}, **extra):
                if context_window.read_field(chunk, "prompt_eval_count") is not None:
                    usage_chunk = chunk
                msg = chunk["message"]
                piece = msg.get("content") or ""
                if piece:
                    content += piece
                for call in msg.get("tool_calls") or []:
                    native_calls.append(call)
            self._record_usage(usage_chunk, estimate)

            assistant = {"role": "assistant", "content": content}
            if native_calls:
                assistant["tool_calls"] = native_calls
            self.messages.append(assistant)
            final_text = content

            text_calls = [] if native_calls or not use_tools else self._extract_text_tool_calls(content)
            tool_calls = native_calls or text_calls
            if not tool_calls:
                if content:
                    content = self._format_rag_presenter_reply(content)
                    self.messages[-1]["content"] = content
                    final_text = content
                    yield {"type": "token", "text": content}
                break

            origin = "native" if native_calls else "text_json"
            for call in tool_calls:
                name = call["function"]["name"]
                before = len(self.tool_events)
                result = self.act(call, origin)
                self.observe(name, result)
                for event in self.tool_events[before:]:
                    yield {"type": "tool_event", "event": event}
        else:
            final_text = "(Executed maximum tool rounds without final text summary.)"
            self.note_failure("rounds_exhausted", "", "Maximum tool rounds reached without a final answer.")

        yield {"type": "done", "reply": final_text}

    def _ollama_schema(self, func: Callable) -> dict:
        sig = inspect.signature(func)
        doc = inspect.getdoc(func) or ""
        properties = {}
        required = []
        for param_name, param in sig.parameters.items():
            properties[param_name] = {
                "type": "string",
                "description": self.PARAMETER_DESCRIPTIONS.get(param_name, f"Parameter {param_name}"),
            }
            if param.default == inspect.Parameter.empty:
                required.append(param_name)
        return {
            "type": "function",
            "function": {
                "name": getattr(func, "name", None) or func.__name__,
                "description": doc.splitlines()[0] if doc else "",
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required
                }
            }
        }


# --- Tool catalog --------------------------------------------------------------------

class ToolCatalog:
    """Registry of invocable agent tools and their provider bindings.

    Tools come from three places, registered in this order:
    first-party tool modules (``tools/logic_tools.py``), plugin files dropped into
    ``tools/`` (including ``tools/promoted_tools.py``), and the core built-ins below.
    """

    REPO_ROOT = Path(__file__).resolve().parent.parent
    TOOLS_DIR = REPO_ROOT / "tools"
    FIRST_PARTY_MODULES = ("tools.logic_tools",)
    NON_PLUGIN_FILES = {"__init__.py", "interface_tools.py", "schemas_tools.py", "logic_tools.py"}
    PROVIDER_BINDINGS = {
        "core_engine": "core_engine.interface_core_engine",
        "editor": "editor.interface_editor",
        "file_manager": "file_manager.interface_file_manager",
        "workspace": "workspace.interface_workspace",
        "test_environment": "test_environment.interface_test_environment",
        "codebuilder": "codebuilder.interface_codebuilder",
        "orchestration": "orchestration.interface_orchestration",
        "scratchpadMemory": "scratchpadMemory.interface_scratchpadMemory",
        "ragMemory": "ragMemory.interface_ragMemory",
        "tools": "tools.interface_tools",
        "tools.logic_tools": "tools.logic_tools",
        "tools.promoted_tools": "tools.promoted_tools",
        "core_engine.logic_core_engine": "core_engine.logic_core_engine",
    }

    def __init__(self):
        self.registry: dict[str, dict] = {}
        self.load_errors: dict[str, str] = {}
        self._loaded_files: set[Path] = set()
        self._declared: dict[str, list[tuple[str, str, Callable]]] = {}
        self._builtins: list[tuple[str, str, Callable]] = []

    def _register(self, func: Callable, tool_name: str, provider: str) -> Callable:
        if tool_name in self.registry and self.registry[tool_name]["function"] is not func:
            raise ValueError(
                f"Tool '{tool_name}' is already registered by provider '{self.registry[tool_name]['provider']}'."
            )
        doc = inspect.getdoc(func) or ""
        func.name = tool_name
        func.provider = provider
        self.registry[tool_name] = {
            "name": tool_name,
            "function": func,
            "provider": provider,
            "description": doc.splitlines() if doc else "",
            "signature": str(inspect.signature(func)),
        }
        return func

    def _declare(self, func: Callable, tool_name: str, provider: str) -> Callable:
        self._register(func, tool_name, provider)
        declared = self._declared.setdefault(func.__module__, [])
        declared[:] = [entry for entry in declared if entry[0] != tool_name]
        declared.append((tool_name, provider, func))
        return func

    def tool(self, name_or_func: str | Callable | None = None, provider: str = "core_engine"):
        """Decorator that registers a function as an invocable agent tool."""
        if callable(name_or_func):
            return self._declare(name_or_func, name_or_func.__name__, provider)

        def decorator(func: Callable) -> Callable:
            return self._declare(func, name_or_func or func.__name__, provider)

        return decorator

    def builtin(self, provider: str):
        """Decorator for core built-in tools; they are registered by ``load_tools``."""

        def decorator(func: Callable) -> Callable:
            func.name = func.__name__
            func.provider = provider
            self._builtins.append((func.__name__, provider, func))
            return func

        return decorator

    def load_tools(self, reload: bool = False) -> None:
        """Register first-party tool modules, plugin files in ``tools/`` and the built-ins."""
        if reload:
            self.registry.clear()
            self.load_errors.clear()
            self._loaded_files.clear()

        for module_name in self.FIRST_PARTY_MODULES:
            self._load_first_party(module_name, reload)

        if self.TOOLS_DIR.is_dir():
            for path in sorted(self.TOOLS_DIR.glob("*.py")):
                if path.name in self.NON_PLUGIN_FILES or path.name.startswith("_"):
                    continue
                if not reload and path.resolve() in self._loaded_files:
                    continue
                self._exec_plugin(path, f"tools.{path.stem}")

        for name, provider, func in self._builtins:
            try:
                self._register(func, name, provider)
                self.load_errors.pop(f"builtin:{name}", None)
            except ValueError as exc:
                self.load_errors[f"builtin:{name}"] = str(exc)

    def _load_first_party(self, module_name: str, reload: bool) -> None:
        """Import a first-party tool module once; on reload re-register its tools."""
        filename = module_name.rsplit(".", 1)[-1] + ".py"
        try:
            if module_name not in sys.modules:
                importlib.import_module(module_name)
            elif reload:
                for name, provider, func in self._declared.get(module_name, []):
                    self._register(func, name, provider)
            self.load_errors.pop(filename, None)
        except Exception as exc:
            self.load_errors[filename] = str(exc)

    def _exec_plugin(self, path: Path, module_name: str) -> None:
        """Execute one plugin file; a failing file leaves no partial registrations."""
        snapshot = dict(self.registry)
        self._declared.pop(module_name, None)
        try:
            spec = importlib.util.spec_from_file_location(module_name, path)
            if spec is None or spec.loader is None:
                raise ImportError(f"Could not load spec for '{path.name}'.")
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
            self._loaded_files.add(path.resolve())
            self.load_errors.pop(path.name, None)
        except Exception as exc:
            self.load_errors[path.name] = str(exc)
            for key in list(self.registry.keys()):
                if key not in snapshot:
                    self.registry.pop(key, None)
            sys.modules.pop(module_name, None)

    def list_tools(self) -> list[dict]:
        """Return catalog metadata for all registered tools."""
        self.load_tools()
        return [
            {
                "name": meta["name"],
                "provider": meta["provider"],
                "description": meta["description"],
                "signature": meta["signature"],
                "custom": meta.get("custom", False),
            }
            for meta in self.registry.values()
        ]

    def get_tools(self, names: list[str] | None = None) -> list[Callable]:
        """Resolve registered tool callables, filtered by an optional allow-list."""
        self.load_tools()
        if not names:
            return [meta["function"] for meta in self.registry.values()]
        resolved = []
        for name in names:
            meta = self.registry.get(name)
            if meta:
                resolved.append(meta["function"])
        return resolved

    def get_load_errors(self) -> dict[str, str]:
        """Return dictionary of tool load errors mapping filename to error message."""
        return dict(self.load_errors)

    def get_provider_bindings(self) -> dict[str, str]:
        """Return dictionary of provider doorway bindings."""
        return dict(self.PROVIDER_BINDINGS)

    def unknown_tool_names(self, names: list[str]) -> list[str]:
        """Return list of tool names that are not found in the registry."""
        self.load_tools()
        return [name for name in names if name not in self.registry]

    def get_catalog_report(self) -> dict:
        """Generate a comprehensive catalog report without raising exceptions.

        Returns:
            {
                tools: list[dict],
                providers: dict[str, str],
                load_errors: dict[str, str],
                missing_agent_tools: dict[str, list[str]],
                warnings: list[str]
            }
        """
        try:
            self.load_tools()
        except Exception:
            pass

        tools = [
            {
                "name": meta["name"],
                "provider": meta["provider"],
                "description": meta["description"],
                "signature": meta["signature"],
                "custom": meta.get("custom", False),
            }
            for meta in self.registry.values()
        ]
        providers = dict(self.PROVIDER_BINDINGS)
        load_errors = dict(self.load_errors)
        missing_agent_tools: dict[str, list[str]] = {}
        warnings: list[str] = []

        for filename, err in load_errors.items():
            warnings.append(f"Failed to load tool module '{filename}': {err}")

        agent_dirs = [agent_library.LIBRARY_ROOT]
        for agents_dir in agent_dirs:
            try:
                if agents_dir.is_dir():
                    for agent_file in sorted(agents_dir.glob("*.md")):
                        try:
                            raw = agent_file.read_text(encoding="utf-8")
                            meta = parse_yaml_frontmatter(raw)
                            agent_id = meta.get("id") or agent_file.stem
                            req_tools = meta.get("tools", [])
                            missing = [t for t in req_tools if t not in self.registry]
                            if missing:
                                missing_agent_tools[agent_id] = missing
                                warnings.append(
                                    f"Agent '{agent_id}' references missing tools: {', '.join(missing)}"
                                )
                        except Exception as e:
                            warnings.append(f"Failed to parse '{agent_file}': {e}")
            except Exception as e:
                warnings.append(f"Failed to scan agent directory '{agents_dir}': {e}")

        return {
            "tools": tools,
            "providers": providers,
            "load_errors": load_errors,
            "missing_agent_tools": missing_agent_tools,
            "warnings": warnings,
        }


# --- Human-in-the-loop questionnaires ------------------------------------------------

class QuestionnaireRegistry:
    """Connects the blocking ``ask_user_questions`` tool with the browser's answer endpoints."""

    def __init__(self):
        self.pending: dict[str, dict] = {}
        self._lock = threading.Lock()

    def create_request(self, questions: list[Any], session_id: str | None = None) -> dict:
        """Register a new questionnaire and return its record (with a wait Event)."""
        request_id = f"req_{uuid.uuid4().hex[:8]}"
        record = {
            "request_id": request_id,
            "questions": questions,
            "answers": {},
            "completed": False,
            "event": threading.Event(),
            "session_id": session_id,
        }
        with self._lock:
            self.pending[request_id] = record
        return record

    def submit_answers(self, request_id: str, answers: dict) -> bool:
        """Store user answers and unblock the waiting tool thread. Returns success."""
        with self._lock:
            record = self.pending.get(request_id)
            if record is None or record.get("completed"):
                return False
            record["answers"] = dict(answers or {})
            record["completed"] = True
            event = record["event"]
        event.set()
        return True

    def resolve_request(self, request_id: str) -> dict | None:
        """Remove and return a request record, or None if it is no longer active."""
        with self._lock:
            return self.pending.pop(request_id, None)

    def list_pending(self) -> list[dict]:
        """Return active questionnaires currently awaiting user input."""
        with self._lock:
            return [
                {
                    "request_id": request_id,
                    "questions": record.get("questions", []),
                    "session_id": record.get("session_id"),
                }
                for request_id, record in self.pending.items()
                if not record.get("completed")
            ]


# --- Chat sessions ---------------------------------------------------------------------

class RequiredDelegationError(RuntimeError):
    pass


class SessionNotFoundError(LookupError):
    pass


def apply_model_limits(agent: Agent, profile: AgentProfile, model: str, pinned: tuple = ()) -> None:
    """Set an agent's model, context size and tool cap from its model profile.

    When the cap is smaller than the tool list, the pinned tools are always kept and the rest
    fill the remaining places in the list's own order. History is left untouched.
    """
    limits = model_profiles.effective(model)
    available = tool_catalog.get_tools(profile.tools) if profile.mode == "agent" else []
    total = len(available)
    cap = limits["max_tools"]
    if cap is not None:
        keep = {id(f) for f in available if (getattr(f, "name", None) or f.__name__) in pinned}
        for func in available:
            if len(keep) >= cap:
                break
            keep.add(id(func))
        available = [func for func in available if id(func) in keep]
    agent.model = model
    agent.num_ctx = limits["num_ctx"]
    agent.tools = {getattr(f, "name", None) or f.__name__: f for f in available}
    agent.tools_available = total


class ChatService:
    """Owns in-memory conversation sessions (keyed by session id) and runs agent turns."""

    def __init__(self):
        self.sessions: dict[str, Agent] = {}

    def get_agent(self, agent_id: str, model: str | None, session_id: str | None, environment: str):
        """Load (or reuse) the session's Agent; returns (agent, session_key, profile)."""
        profile = agent_library.load_agent(agent_id, environment)

        session_key = session_id or agent_id
        agent = self.sessions.get(session_key)
        if agent is not None and (
            agent.profile.system_prompt != profile.system_prompt
            or agent.profile.mode != profile.mode
            or agent.profile.tools != profile.tools
        ):
            # The agent definition changed since this session started; a stale
            # system prompt and history would keep the old behavior.
            self.sessions.pop(session_key, None)
            agent = None
        if agent is None:
            agent = Agent(model=None, tools=[], profile=profile, session=session_key)
            self.sessions[session_key] = agent
        # Model, context size and tool cap are applied to every turn, so changing the model
        # mid-chat takes effect while the history is kept.
        self._apply_model_limits(agent, profile, model or profile.model or DEFAULT_MODEL)
        return agent, session_key, profile

    def _apply_model_limits(self, agent: Agent, profile: AgentProfile, model: str) -> None:
        apply_model_limits(agent, profile, model, PINNED_TOOLS.get(profile.id, ()))

    def _context_report(self, agent: Agent, profile: AgentProfile) -> dict:
        limits = model_profiles.effective(agent.model)
        return context_window.build_report(
            model=agent.model,
            messages=agent.messages,
            system_prompt=profile.system_prompt,
            schema_text=agent.tool_schema_text(),
            tools_total=agent.tools_available,
            tools_active=len(agent.tools) if agent.profile.mode != "chat" else 0,
            num_ctx=agent.num_ctx,
            reply_reserve=limits["reply_reserve"],
            native_context=limits["native_context"],
            capabilities=limits["capabilities"],
            calibration=agent.calibration,
            measured_prompt_tokens=(agent.last_usage or {}).get("prompt_tokens"),
        )

    def context_summary(self, agent: Agent, profile: AgentProfile) -> dict:
        return context_window.summarize(self._context_report(agent, profile))

    def get_context(self, agent_id: str, model: str | None, session_id: str | None,
                    environment: str) -> dict:
        """Full context report for a session (created empty if it does not exist yet)."""
        agent, session_key, profile = self.get_agent(agent_id, model, session_id, environment)
        return {"session_id": session_key, "agent_id": agent_id, **self._context_report(agent, profile),
                "tool_health": self.agent_health(agent)}

    def prepare_agent_message(self, agent: Agent, profile: AgentProfile, message: str) -> str:
        if profile.id != "rag-presenter":
            return message

        required_tools = ("call_subagent", "read_agent_scratchpad")
        missing_tools = [name for name in required_tools if name not in agent.tools]
        if missing_tools:
            raise RequiredDelegationError(
                "RAG Presenter is missing required tools: " + ", ".join(missing_tools)
            )

        handoff = {
            "subagent_id": "rag-retriever",
            "task_prompt": message,
            "parameters": "{}",
        }
        retrieval_result = agent.act(
            {"function": {"name": "call_subagent", "arguments": handoff}},
            origin="orchestrator",
        )
        if str(retrieval_result).startswith("Error executing tool 'call_subagent'"):
            scratchpad_result = "Not read because the required sub-agent call failed."
        else:
            scratchpad_result = agent.act(
                {
                    "function": {
                        "name": "read_agent_scratchpad",
                        "arguments": {"memory_type": "latest_subagent_result", "max_chars": "20000"},
                    }
                },
                origin="orchestrator",
            )

        return (
            f"{message}\n\n"
            "Required RAG retrieval handoff (already executed for this turn). "
            "Do not invoke the sub-agent again. Treat errors or missing evidence "
            "as retrieval failures and report them honestly.\n"
            f"call_subagent result:\n{retrieval_result}\n"
            f"latest_subagent_result:\n{scratchpad_result}"
        )

    def run_single_agent(self, agent_id: str, message: str, model: str | None = None,
                         session_id: str | None = None,
                         environment: str = "workspace") -> dict:
        """Doorway function: run one agent turn through the Think-Act-Observe loop."""
        try:
            agent, session_key, profile = self.get_agent(agent_id, model, session_id, environment)
        except FileNotFoundError:
            raise
        except Exception as exc:
            return {
                "reply": f"[engine] Failed to initialize agent '{agent_id}': {exc}",
                "agent_id": agent_id,
                "session_id": session_id or agent_id,
                "model": model or "unknown",
                "tool_events": [],
                "tool_stats": {},
                "usage": {},
                "context": None,
                "health": None,
            }

        agent.tool_events = []
        try:
            prepared_message = self.prepare_agent_message(agent, profile, message)
            reply = agent.think(prepared_message)
        except RequiredDelegationError as exc:
            reply = f"[engine] Could not complete required agent setup: {exc}"
        except Exception as exc:  # Ollama down / model missing -> graceful error reply
            agent.note_failure("model_error", "", str(exc))
            reply = (f"[engine] Could not reach the model runtime: {exc}\n"
                     f"Start Ollama (e.g. `ollama serve`) and pull the model "
                     f"`{model or profile.model or 'qwen2.5-coder:latest'}`, then retry.")
            if agent.messages and agent.messages[-1].get("role") == "user":
                agent.messages.pop()

        return {
            "reply": reply,
            "agent_id": agent_id,
            "session_id": session_key,
            "model": agent.model,
            "tool_events": list(agent.tool_events),
            "tool_stats": agent.tool_stats,
            "usage": {"last": agent.last_usage, **agent.usage_totals},
            "context": self.context_summary(agent, profile),
            "health": self.agent_health(agent),
        }

    @staticmethod
    def agent_health(agent: Agent) -> dict:
        """Tool and run health for one agent: level, flags, failure counts and the latest failures."""
        stats = agent.run_stats()
        return {
            "level": stats["level"],
            "flags": stats["flags"],
            "tool_calls": stats["tool_calls"],
            "tool_errors": stats["tool_errors"],
            "failures": stats["failures"],
            "rounds_exhausted": stats["rounds_exhausted"],
            "recent_failures": list(agent.recent_failures),
        }

    def list_agents(self) -> list[dict]:
        """Doorway function: list all canonical agents from the central library."""
        return agent_library.list_agents("workspace")

    def reset_session(self, session_id: str) -> bool:
        return self.sessions.pop(session_id, None) is not None

    def publish_tool_event(self, event: dict, session_id: str) -> None:
        event_data = {
            "tool": event["tool"],
            "args": event.get("args", {}),
            "status": event["status"],
            "origin": event["origin"],
        }
        if event.get("error") is not None:
            event_data["error"] = event["error"]
        session_manager.publish(
            EditorEventType.TOOL_EXECUTED, event["tool"], session_id, **event_data,
        )

    def list_models(self) -> dict:
        """Doorway function: list Ollama models installed on this host."""
        error = None
        try:
            models = [m.get("model") or m.get("name") for m in ollama.list().get("models", [])]
        except Exception as exc:
            models = []
            error = str(exc)
        return {
            "models": sorted(models),
            "default": "qwen2.5-coder:latest",
            "error": error if not models else None,
        }

    def export_session(self, session_id: str) -> tuple[str, str]:
        """Render a session as Markdown; returns ``(filename, markdown)``."""
        agent = self.sessions.get(session_id)
        if agent is None:
            raise SessionNotFoundError(f"Session '{session_id}' not found.")

        now = datetime.now()
        timestamp = now.strftime("%Y%m%d_%H%M%S_%f")
        safe_session_id = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id).strip("._")
        if not safe_session_id:
            safe_session_id = "session"
        filename = f"novous_{safe_session_id}_{timestamp}.md"
        exported_at = now.strftime("%Y-%m-%d %H:%M:%S")
        agent_name = agent.profile.name or session_id

        lines = [
            f"# Novous Chat Session Log — {agent_name}",
            f"**Agent ID:** `{agent.profile.id}` | **Model:** `{agent.model}` | "
            f"**Session Key:** `{session_id}`",
            f"**Exported At:** `{exported_at}`",
            "",
            "---",
            "",
            "## Tool Execution Metrics",
            "",
        ]

        if agent.tool_stats:
            lines.extend([
                "| Tool Name | Total Calls | Successes | Errors | Success Rate |",
                "| :--- | :---: | :---: | :---: | :---: |",
            ])
            for tool_name, metrics in agent.tool_stats.items():
                lines.append(
                    f"| `{tool_name}` | {metrics['calls']} | {metrics['successes']} | "
                    f"{metrics['errors']} | {metrics['success_rate']:.1%} |"
                )
        else:
            lines.append("*No tools executed in this session.*")

        lines.extend(["", "---", "", "## Conversation History", ""])
        for msg in agent.messages:
            role = str(msg.get("role", "unknown")).upper()
            if role == "SYSTEM":
                continue
            content = msg.get("content") or ""
            lines.extend([f"### {role.title()}", "", str(content), ""])

        full_markdown = "\n".join(lines)
        return filename, full_markdown


agent_library = AgentLibrary()
tool_catalog = ToolCatalog()
tool = tool_catalog.tool
questionnaire = QuestionnaireRegistry()
chat_service = ChatService()


# --- Built-in tools (registered by ToolCatalog.load_tools) -----------------------------

_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def _safe_eval(expr: str):
    def _eval(node):
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _OPERATORS:
            return _OPERATORS[type(node.op)](_eval(node.left), _eval(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _OPERATORS:
            return _OPERATORS[type(node.op)](_eval(node.operand))
        raise ValueError(f"Unsupported expression element: {ast.dump(node)}")

    return _eval(ast.parse(expr, mode="eval"))


@tool_catalog.builtin(provider="core_engine")
def calculator(expression: str) -> str:
    """Evaluate a basic arithmetic expression (numbers, + - * / // % ** and parentheses)."""
    result = _safe_eval(expression)
    return f"{expression} = {result}"


@tool_catalog.builtin(provider="core_engine")
def rag_agent_tool(
    query: str,
    relative_path: str = "",
    top_k: int = 3,
    knowledge_base_id: str = "",
) -> str:
    """Query a named knowledge source by ID, including archived sources, for relevant passages."""
    from ragMemory import interface_ragMemory as rag

    document_path = str(editor_files.resolve_project_path(relative_path)) if relative_path else ""
    return json.dumps(
        rag.search_knowledge(query, top_k, document_path, knowledge_base_id or None),
        ensure_ascii=False,
        indent=2,
    )


@tool_catalog.builtin(provider="core_engine")
def agent_info(agent_id: str) -> str:
    """Return the metadata and composed system prompt of a workspace agent."""
    profile = agent_library.load_agent(agent_id)
    return json.dumps(
        {
            "id": profile.id,
            "name": profile.name,
            "mode": profile.mode,
            "description": profile.description,
            "role": profile.role,
            "purpose": profile.purpose,
            "boundaries": profile.boundaries,
            "system_prompt": profile.system_prompt,
        },
        indent=2,
    )


@tool_catalog.builtin(provider="editor")
def extract_docstrings(path: str) -> str:
    """Parse Python code AST to extract module, class, and function docstrings."""
    try:
        abs_path = editor_files.resolve_project_path(path)
        code = abs_path.read_text(encoding="utf-8")
        parsed_tree = ast.parse(code)

        docstrings = {
            "file": path,
            "module_docstring": ast.get_docstring(parsed_tree),
            "classes": {},
            "functions": {},
        }

        for node in ast.walk(parsed_tree):
            if isinstance(node, ast.ClassDef):
                docstrings["classes"][node.name] = ast.get_docstring(node)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                docstrings["functions"][node.name] = ast.get_docstring(node)

        return json.dumps(docstrings, indent=2)
    except Exception as exc:
        return f"Error extracting docstrings from '{path}': {exc}"


@tool_catalog.builtin(provider="editor")
def search_workspace(query: str, extension: str = ".py") -> str:
    """Search workspace files for matching keyword or snippet."""
    try:
        matches = []
        ext = extension if extension.startswith(".") else f".{extension}"
        for file_path in editor_files.PROJECT_ROOT.rglob(f"*{ext}"):
            if not file_path.is_file() or file_path.name.startswith("."):
                continue
            try:
                rel = str(file_path.relative_to(editor_files.PROJECT_ROOT)).replace("\\", "/")
                content = file_path.read_text(encoding="utf-8", errors="ignore")
                for idx, line in enumerate(content.splitlines(), start=1):
                    if query.lower() in line.lower():
                        matches.append(f"{rel}:{idx} - {line.strip()}")
            except Exception:
                continue
        return (
            "\n".join(matches[:50])
            if matches
            else f"No matches found for query '{query}'."
        )
    except Exception as exc:
        return f"Error searching workspace: {exc}"


@tool_catalog.builtin(provider="codebuilder")
def run_python_code(code: str) -> str:
    """Run a Python snippet in CodeBuilder and return its output and diagnostics."""
    from codebuilder.execution import execute_python_code
    from codebuilder.schemas_codebuilder import CodeExecutionRequest

    from orchestration.logic_orchestration import codebuilder_run_state, telemetry_bus

    result = execute_python_code(CodeExecutionRequest(code=code))
    context = telemetry_bus.current_context()
    if context is not None:
        trace_id, agent_id = context
        source_matches_editor = codebuilder_run_state.matches_editor_source(trace_id, code)
        if codebuilder_run_state.save_execution(
            trace_id,
            code,
            result.model_dump(),
            allow_non_editor_source=agent_id == "tester",
        ):
            diagnostic_lines = sorted(
                {
                    item.line
                    for item in result.diagnostics
                    if source_matches_editor and item.line is not None and item.line > 0
                }
            )[:120]
            telemetry_bus.log_event(
                trace_id, "EDITOR_CODE_EXECUTION", agent_id, "editor",
                {
                    "status": "success" if result.status == "SUCCESS" else "error",
                    "diagnostic_lines": diagnostic_lines,
                },
            )
    return json.dumps(result.model_dump(), ensure_ascii=False)


@tool_catalog.builtin(provider="codebuilder")
def send_code_to_editor(code: str) -> str:
    """Queue generated Python code for insertion into the active CodeBuilder Monaco editor."""
    if not code.strip():
        raise ValueError("Code to send to the editor must not be empty.")
    return "CodeBuilder will insert this code into the active Monaco editor after the chat turn."


@tool_catalog.builtin(provider="codebuilder")
def run_code_in_editor() -> str:
    """Queue execution of the current contents of the active CodeBuilder editor."""
    return "CodeBuilder will run the active editor buffer after the chat turn."


@tool_catalog.builtin(provider="codebuilder")
def ask_user_questions(json_file_path: str, timeout_seconds: float = 300.0) -> str:
    """Pause the agent and ask the user a list of questions from a JSON file in the workspace."""
    from codebuilder.logic_codebuilder import codebuilder_service

    return codebuilder_service.ask_user(json_file_path, timeout_seconds)


@tool_catalog.builtin(provider="workspace")
def project_status() -> str:
    """Return the current project state metadata for this workspace."""
    from workspace.logic_workspace import project_state

    return json.dumps(project_state.get_project_state(), indent=2)


@tool_catalog.builtin(provider="codebuilder")
def preview_editor_code(proposed_content: str) -> str:
    """Preview complete proposed code in the active CodeBuilder editor without saving it."""
    from orchestration.logic_orchestration import (
        MAX_EDITOR_CONTENT_BYTES,
        codebuilder_run_state,
        telemetry_bus,
    )
    from tools.logic_tools import code_change_preview

    if not isinstance(proposed_content, str):
        raise ValueError("The editor preview must be text.")
    if len(proposed_content.encode("utf-8")) > MAX_EDITOR_CONTENT_BYTES:
        raise ValueError("The editor preview exceeds the 1 MB limit.")
    context = telemetry_bus.current_context()
    if context is None:
        raise ValueError("Editor-buffer previews are only available during a CodeBuilder workflow.")
    trace_id, agent_id = context
    original = codebuilder_run_state.get_editor_code(trace_id)
    if original is None:
        raise ValueError("The original CodeBuilder editor buffer is unavailable for this workflow.")
    lines = code_change_preview.changed_line_numbers(original, proposed_content, "after")
    if not codebuilder_run_state.save_preview(trace_id, proposed_content, lines):
        raise ValueError("The CodeBuilder workflow preview could not be saved.")
    telemetry_bus.log_event(trace_id, "EDITOR_CODE_PREVIEW", agent_id, "editor", {"changed_lines": lines})
    return json.dumps(
        {"status": "success" if lines else "nothing_found", "changed_lines": lines},
        ensure_ascii=False,
    )


_HTTP_METHOD_DECORATORS = {"get", "post", "put", "patch", "delete", "options", "head"}


@tool_catalog.builtin(provider="codebuilder")
def inspect_http_routes(path: str) -> str:
    """Statically list HTTP route decorators in a workspace Python file without running the server."""
    from tools.logic_tools import workspace_files

    if not isinstance(path, str) or not path.strip() or len(path) > 512:
        raise ValueError("Provide a workspace-relative Python file path.")
    if not path.lower().endswith(".py"):
        raise ValueError("HTTP route inspection only accepts Python source files.")
    source_info = workspace_files.read_file_bytes(path, 1_000_000)
    source = source_info["content"].decode("utf-8")
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as exc:
        raise ValueError(
            f"Could not inspect routes in {path}: Python syntax error at line {exc.lineno}."
        ) from exc

    routes = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                continue
            method = decorator.func.attr.lower()
            if method in _HTTP_METHOD_DECORATORS:
                method_names = [method.upper()]
            elif method in {"route", "api_route"}:
                methods_node = next(
                    (keyword.value for keyword in decorator.keywords if keyword.arg == "methods"),
                    None,
                )
                method_names = (
                    sorted(
                        {
                            item.value.upper()
                            for item in methods_node.elts
                            if isinstance(item, ast.Constant) and isinstance(item.value, str)
                        }
                    )
                    if isinstance(methods_node, (ast.List, ast.Tuple))
                    else []
                )
            else:
                continue
            path_node = decorator.args[0] if decorator.args else None
            routes.append(
                {
                    "line": decorator.lineno,
                    "function": node.name,
                    "route": path_node.value
                    if isinstance(path_node, ast.Constant) and isinstance(path_node.value, str)
                    else None,
                    "methods": method_names,
                    "router": decorator.func.value.id
                    if isinstance(decorator.func.value, ast.Name)
                    else None,
                }
            )
    routes.sort(key=lambda route: route["line"])
    return json.dumps({"path": source_info["path"], "routes": routes}, ensure_ascii=False, indent=2)


@tool_catalog.builtin(provider="core_engine")
def preview_code_change(path: str, proposed_content: str) -> str:
    """Preview changed line numbers for a workspace file without writing its contents."""
    from tools.logic_tools import code_change_preview

    return json.dumps(code_change_preview.preview(path, proposed_content), ensure_ascii=False)
