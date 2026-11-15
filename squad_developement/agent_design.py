"""Create and edit squad-local agent definitions."""

import os
import tempfile

import yaml

from core_engine.logic_core_engine import agent_library, tool_catalog
from core_engine.schemas_core_engine import AgentProfile
from squad_developement import flow_design

GENERIC_PROMPT_PATH = flow_design.ROOT / "generic_prompt.md"


def get_generic_prompt() -> str:
    if GENERIC_PROMPT_PATH.is_symlink():
        raise ValueError("The generic prompt cannot be a symbolic link.")
    return GENERIC_PROMPT_PATH.read_text(encoding="utf-8")


def save_generic_prompt(content: str) -> str:
    if not content.strip() or len(content.encode("utf-8")) > 1_000_000:
        raise ValueError("The generic prompt must contain text and be at most 1 MB.")
    get_generic_prompt()
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="", dir=GENERIC_PROMPT_PATH.parent, delete=False) as handle:
            temporary = handle.name
            handle.write(content)
        os.replace(temporary, GENERIC_PROMPT_PATH)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)
    return content


def generate_text(model: str, prompt: str) -> str:
    """One-off completion for the Prompt Starter's AI variable-fill dialog."""
    from core_engine.logic_core_engine import serialized_ollama_chat
    from modelProfiles.logic_modelProfiles import model_profiles

    model = str(model or "").strip()
    if not model:
        raise ValueError("Choose a model for generation.")
    prompt = str(prompt or "")
    if not prompt.strip():
        raise ValueError("The generation prompt must not be empty.")
    if len(prompt) > 20_000:
        raise ValueError("The generation prompt must be at most 20,000 characters.")
    result = serialized_ollama_chat(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        options={"num_ctx": model_profiles.effective(model)["num_ctx"]},
    )
    return str((result.get("message") or {}).get("content") or "")


def format_pasted_yaml(content: str) -> str:
    """Format a complete YAML mapping without discarding comments or aliases."""
    if len(content.encode("utf-8")) > 1_000_000:
        raise ValueError("Pasted YAML must be at most 1 MB.")
    if "#" in content:
        raise ValueError("YAML containing comments or '#' is left unchanged to preserve its contents.")
    tokens = list(yaml.scan(content))
    if any(isinstance(token, (yaml.tokens.AnchorToken, yaml.tokens.AliasToken,
                              yaml.tokens.TagToken, yaml.tokens.DirectiveToken)) for token in tokens):
        raise ValueError("YAML with anchors, aliases, tags or directives is left unchanged.")
    node = yaml.compose(content, Loader=yaml.SafeLoader)
    if not isinstance(node, yaml.nodes.MappingNode):
        raise ValueError("Paste a complete YAML mapping to format it.")

    def check_keys(current):
        if isinstance(current, yaml.nodes.MappingNode):
            current.flow_style = False
            keys = set()
            for key, value in current.value:
                if not isinstance(key, yaml.nodes.ScalarNode) or key.tag != "tag:yaml.org,2002:str":
                    raise ValueError("YAML with non-string or complex keys is left unchanged.")
                if key.value in keys:
                    raise ValueError("YAML with duplicate keys is left unchanged.")
                keys.add(key.value)
                check_keys(value)
        elif isinstance(current, yaml.nodes.SequenceNode):
            current.flow_style = False
            for value in current.value:
                check_keys(value)

    check_keys(node)
    return yaml.serialize(
        node,
        allow_unicode=True,
        explicit_start=any(isinstance(token, yaml.tokens.DocumentStartToken) for token in tokens),
        explicit_end=any(isinstance(token, yaml.tokens.DocumentEndToken) for token in tokens),
    )


def parse_prompt_starter_agent(content: str) -> dict:
    """Validate an edited agent definition and split its metadata from its prompt body."""
    if not content.strip() or len(content.encode("utf-8")) > 1_000_000:
        raise ValueError("The agent definition must contain text and be at most 1 MB.")
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("The Novous Agent definition must start with YAML frontmatter.")
    end_index = next(
        (index for index in range(1, len(lines)) if lines[index].strip() == "---"),
        None,
    )
    if end_index is None:
        raise ValueError("The agent definition is missing the closing YAML frontmatter delimiter.")
    metadata = yaml.safe_load("\n".join(lines[1:end_index]))
    if not isinstance(metadata, dict):
        raise ValueError("Agent frontmatter must be a YAML mapping.")

    agent_id = flow_design.clean_id(metadata.get("id"))
    name = metadata.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Agent frontmatter must include a name.")
    description = metadata.get("description", "")
    if description is None:
        description = ""
    if not isinstance(description, str):
        raise ValueError("Agent description must be text.")
    mode = metadata.get("mode", "agent")
    if mode not in ("chat", "agent"):
        raise ValueError("Agent mode must be 'chat' or 'agent'.")

    raw_tools = metadata.get("tools", [])
    if raw_tools is None:
        raw_tools = []
    if not isinstance(raw_tools, list):
        raise ValueError("Agent tools must be a YAML list.")
    tools = []
    for tool in raw_tools:
        if tool is None or tool == "":
            continue
        if not isinstance(tool, str) or not tool.strip():
            raise ValueError("Agent tool names must be non-empty text.")
        tools.append(tool.strip())
    if len(set(tools)) != len(tools):
        raise ValueError("Agent tools must not contain duplicates.")
    if mode == "chat" and tools:
        raise ValueError("Agent tools require mode 'agent'; set mode to 'agent' or clear the tool entries.")
    missing_tools = tool_catalog.unknown_tool_names(tools)
    if missing_tools:
        raise ValueError("Unregistered tools: " + ", ".join(missing_tools))

    prompt = "\n".join(lines[end_index + 1:]).strip("\n")
    if not prompt.strip():
        raise ValueError("The agent prompt body must not be empty.")
    return {
        "id": agent_id,
        "name": name.strip(),
        "description": description.strip(),
        "mode": mode,
        "tools": tools,
        "prompt": prompt,
    }


def agent_path(squad_id: str, agent_id: str, kind: str):
    flow_design.clean_id(agent_id)
    if kind not in ("agent", "subagent"):
        raise ValueError("Unknown agent kind.")
    path = flow_design.squad_path(squad_id) / f"{agent_id}.{'md' if kind == 'agent' else 'yaml'}"
    if path.is_symlink():
        raise ValueError("Agent files cannot be symbolic links.")
    return path


def create_agent(squad_id: str, agent_id: str, name: str, kind: str, prompt: str, tools: list[str] | None = None) -> dict:
    data = flow_design.read_squad(squad_id)
    agent_id = flow_design.clean_id(agent_id)
    if not name.strip():
        raise ValueError("An agent name is required.")
    if not prompt.strip() or len(prompt.encode("utf-8")) > 1_000_000:
        raise ValueError("The generic prompt must contain text and be at most 1 MB.")
    data["agents"].append({"id": agent_id, "kind": kind})
    flow_design.validate_flow(data)
    path = agent_path(squad_id, agent_id, kind)
    if path.exists():
        raise FileExistsError("An agent file already exists.")
    tools = tools or []
    if "call_subagent" in tools or tool_catalog.unknown_tool_names(tools):
        raise ValueError("Choose registered tools other than call_subagent.")
    if len(set(tools)) != len(tools):
        raise ValueError("Tools must not contain duplicates.")
    meta = {"id": agent_id, "name": name.strip(), "mode": "agent", "model": "qwen2.5-coder:latest", "tools": tools}
    content = ("---\n" + yaml.safe_dump(meta, sort_keys=False) + "---\n\n" + prompt
               if kind == "agent" else yaml.safe_dump({**meta, "type": "subagent", "prompt": prompt}, sort_keys=False))
    path.write_text(content, encoding="utf-8", newline="")
    try:
        (flow_design.squad_path(squad_id) / "squad.yaml").write_text(
            yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    except OSError:
        path.unlink()
        raise
    return data


def find_agent(squad_id: str, agent_id: str) -> dict:
    data = flow_design.read_squad(squad_id)
    match = next((item for item in data["agents"] if item["id"] == agent_id), None)
    if not match:
        raise FileNotFoundError("Agent not found in squad.")
    return match


def get_agent(squad_id: str, agent_id: str) -> dict:
    member = find_agent(squad_id, agent_id)
    path = agent_path(squad_id, agent_id, member["kind"])
    return {**member, "content": path.read_text(encoding="utf-8")}


def tool_instructions(tool: str) -> str:
    tool_catalog.load_tools()
    meta = tool_catalog.registry.get(tool)
    if not meta:
        raise ValueError(f"Tool not registered: {tool}")
    description = meta["description"]
    description = "\n".join(description) if isinstance(description, list) else str(description or "")
    parts = [f"## Tool: {tool}", description or "No tool documentation is available."]
    params = tool_catalog.tool_parameters(tool)
    if params:
        parts.append("")
        parts.append("Parameters:")
        for p in params:
            label = f"{p['name']} (required)" if p["required"] else p["name"]
            desc = p.get("description", "")
            if not p["required"] and "default" in p and desc and f"Default {p['default']}" not in desc:
                desc += f" Default {p['default']}."
            parts.append(f"- {label}: {desc}" if desc else f"- {label}")
    parts.append("")
    parts.append("Return format: See the tool documentation above. If it does not specify a format, "
                 "inspect the actual result; do not assume JSON.")
    return "\n".join(parts)


def append_instruction(prompt: str, heading: str, instruction: str) -> str:
    if any(line.strip() == heading for line in prompt.splitlines()):
        raise ValueError(f"{heading} is already in the prompt; edit it directly.")
    return prompt.rstrip() + "\n\n" + instruction + "\n"


def add_draft_tool(squad_id: str, agent_id: str, content: str, tool: str) -> str:
    member = find_agent(squad_id, agent_id)
    if tool == "call_subagent" or tool_catalog.unknown_tool_names([tool]):
        raise ValueError("Choose a registered tool other than call_subagent.")
    if len(content.encode("utf-8")) > 1_000_000:
        raise ValueError("Agent definition must be at most 1 MB.")
    if member["kind"] == "agent":
        lines = content.splitlines()
        if not lines or lines[0].strip() != "---":
            raise ValueError("Agent Markdown must have YAML frontmatter.")
        end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
        if end is None:
            raise ValueError("Agent frontmatter is missing its closing delimiter.")
        data = yaml.safe_load("\n".join(lines[1:end]))
        body = "\n".join(lines[end + 1:])
    else:
        data = yaml.safe_load(content)
        body = ""
    if not isinstance(data, dict) or data.get("id") != agent_id:
        raise ValueError("Agent definition must have the matching ID.")
    tools = data.get("tools", [])
    if not isinstance(tools, list) or not all(isinstance(item, str) for item in tools):
        raise ValueError("Agent tools must be a list of tool names.")
    if tool in tools:
        raise ValueError(f"Tool '{tool}' is already enabled in this agent.")
    tools.append(tool)
    data["tools"] = tools
    data["mode"] = "agent"
    instruction = tool_instructions(tool)
    if member["kind"] == "agent":
        if not any(line.strip() in (f"## Tool: {tool}", f"### Tool: {tool}") for line in body.splitlines()):
            body = append_instruction(body, f"## Tool: {tool}", instruction)
    else:
        if not isinstance(data.get("prompt"), str):
            raise ValueError("Sub-agent prompt must be text.")
        if not any(line.strip() in (f"## Tool: {tool}", f"### Tool: {tool}") for line in data["prompt"].splitlines()):
            data["prompt"] = append_instruction(data["prompt"], f"## Tool: {tool}", instruction)
    rendered = yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
    return "---\n" + rendered + "---\n" + body + "\n" if member["kind"] == "agent" else rendered


def add_draft_skill(squad_id: str, agent_id: str, content: str, skill_id: str) -> str:
    member = find_agent(squad_id, agent_id)
    skill = get_skill(squad_id, skill_id)
    instruction = f"## Skill: {skill_id}\n{skill}"
    if len(content.encode("utf-8")) > 1_000_000:
        raise ValueError("Agent definition must be at most 1 MB.")
    if member["kind"] == "agent":
        return append_instruction(content, f"## Skill: {skill_id}", instruction)
    data = yaml.safe_load(content)
    if not isinstance(data, dict) or data.get("id") != agent_id or not isinstance(data.get("prompt"), str):
        raise ValueError("Sub-agent definition must have the matching ID and a text prompt.")
    data["prompt"] = append_instruction(data["prompt"], f"## Skill: {skill_id}", instruction)
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def skill_path(squad_id: str, skill_id: str):
    flow_design.read_squad(squad_id)
    path = flow_design.squad_path(squad_id) / f"skill-{flow_design.clean_id(skill_id)}.md"
    if path.is_symlink():
        raise ValueError("Skill files cannot be symbolic links.")
    return path


def list_skills(squad_id: str) -> list[str]:
    flow_design.read_squad(squad_id)
    folder = flow_design.squad_path(squad_id)
    return sorted(path.stem[6:] for path in folder.glob("skill-*.md")
                  if path.is_file() and not path.is_symlink() and flow_design.ID_PATTERN.fullmatch(path.stem[6:]))


def get_skill(squad_id: str, skill_id: str) -> str:
    return skill_path(squad_id, skill_id).read_text(encoding="utf-8")


def save_skill(squad_id: str, skill_id: str, content: str) -> str:
    if not content.strip() or len(content.encode("utf-8")) > 1_000_000:
        raise ValueError("Skill instructions must contain text and be at most 1 MB.")
    path = skill_path(squad_id, skill_id)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="", dir=path.parent, delete=False) as handle:
            temporary = handle.name
            handle.write(content)
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)
    return content


def parse_agent(squad_id: str, agent_id: str, path_override=None) -> AgentProfile:
    member = find_agent(squad_id, agent_id)
    path = path_override or agent_path(squad_id, agent_id, member["kind"])
    if member["kind"] == "agent":
        raw = path.read_text(encoding="utf-8")
        lines = raw.splitlines()
        if not lines or lines[0].strip() != "---":
            raise ValueError("Agent Markdown must have YAML frontmatter.")
        end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
        if end is None:
            raise ValueError("Agent frontmatter is missing its closing delimiter.")
        metadata = yaml.safe_load("\n".join(lines[1:end]))
        if not isinstance(metadata, dict) or metadata.get("id") != agent_id:
            raise ValueError(f"Agent frontmatter must have id: {agent_id}.")
        tools = metadata.get("tools", [])
        if not isinstance(tools, list) or not all(isinstance(t, str) for t in tools):
            raise ValueError("Agent tools must be a list of tool names.")
        profile = agent_library.load_agent_definition_from_md(path, metadata_override=metadata)
    else:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Sub-agent definition must be a YAML mapping.")
        profile = AgentProfile(
            id=data.get("id", ""), name=data.get("name", ""), mode=data.get("mode", "agent"),
            model=data.get("model", ""), tools=data.get("tools", []), system_prompt=data.get("prompt", ""),
        )
    if profile.id != agent_id:
        raise ValueError(f"Agent definition must have id: {agent_id}.")
    if profile.mode not in ("agent", "chat"):
        raise ValueError("Agent mode must be agent or chat.")
    if not isinstance(profile.model, str) or not isinstance(profile.name, str):
        raise ValueError("Agent name and model must be text.")
    if not isinstance(profile.tools, list) or not all(isinstance(t, str) for t in profile.tools):
        raise ValueError("Agent tools must be a list of tool names.")
    if not isinstance(profile.system_prompt, str) or not profile.system_prompt.strip():
        raise ValueError("Agent prompt must not be empty.")
    missing = tool_catalog.unknown_tool_names(profile.tools)
    if missing:
        raise ValueError("Unregistered tools: " + ", ".join(missing))
    if "call_subagent" in profile.tools:
        raise ValueError("Rule-based runs use configured routes, not call_subagent.")
    return profile


def save_agent(squad_id: str, agent_id: str, content: str) -> dict:
    member = find_agent(squad_id, agent_id)
    path = agent_path(squad_id, agent_id, member["kind"])
    if not content.strip() or len(content.encode("utf-8")) > 1_000_000:
        raise ValueError("Agent definition must contain text and be at most 1 MB.")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="", dir=path.parent, delete=False) as handle:
            temporary = handle.name
            handle.write(content)
        from pathlib import Path
        parse_agent(squad_id, agent_id, Path(temporary))
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)
    return get_agent(squad_id, agent_id)
