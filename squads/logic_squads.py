"""Squad Architect: design-time composition of agent squads.

A blueprint names a lead agent, its members (sub-agents or other primary agents), the
interaction pattern and the tools each member carries. This service checks a blueprint and
applies it through the packages that own each concern; it never runs an LLM itself:

- squad registration and patterns  -> ``orchestration.squad_registry``
- agent files, tools, allow-lists  -> ``core_engine.agent_library``
- new primary agents               -> ``workspace.workspace_workflow``
- registered tools                 -> ``core_engine.tool_catalog``
"""

from typing import Any

from core_engine.logic_core_engine import agent_library, tool_catalog
from orchestration.logic_orchestration import CODE_SQUAD_ID, squad_registry
from squads.schemas_squads import BlueprintValidation, SquadBlueprint, SquadMember
from workspace.logic_workspace import workspace_workflow

# Tools the orchestration patterns expect: the lead reads the scratchpad during synthesis and
# sub-agents hand their structured results back through it.
LEAD_HANDOFF_TOOL = "read_squad_scratchpad"
MEMBER_HANDOFF_TOOL = "write_squad_scratchpad"
DELEGATION_TOOL = "call_subagent"

_KIND_BY_DEFINITION = {"prompt": "agent", "subagent": "subagent"}


class SquadArchitect:
    """Builds, validates and applies squad blueprints."""

    # --- Reading -------------------------------------------------------------------

    def get_agent_prompt(self, agent_id: str) -> str | None:
        """Return the raw prompt text for a primary agent or sub-agent."""
        clean = agent_library.normalize_agent_id(agent_id)
        definition = agent_library.definition_kind(clean)
        if definition is None:
            return None
        if definition == "prompt":
            md_path = agent_library.LIBRARY_ROOT / f"{clean}.md"
            if not md_path.is_file():
                return None
            _, body = agent_library._read_agent_markdown(md_path)
            return body
        _, body = agent_library.parse_subagent_yaml(clean)
        return body

    def set_agent_prompt(self, agent_id: str, prompt_text: str) -> dict[str, Any]:
        """Replace the prompt body of a primary agent or sub-agent, keeping all metadata intact."""
        clean = agent_library.normalize_agent_id(agent_id)
        definition = agent_library.definition_kind(clean)
        if definition is None:
            raise FileNotFoundError(f"Agent not found: {agent_id}")
        path = agent_library._definition_file(definition, clean)
        text = path.read_text(encoding="utf-8")
        newline = "\r\n" if "\r\n" in text else "\n"
        lines = text.splitlines()
        prefix, _, layout = agent_library._split_prompt_body(definition, lines)
        body = prompt_text.splitlines()
        result = agent_library._join_prompt_body(prefix, body, layout)
        path.write_text(
            newline.join(result) + (newline if text.endswith(("\n", "\r")) else ""),
            encoding="utf-8", newline="",
        )
        return {"agent_id": clean, "saved": True}

    def describe_agent(self, agent_id: str) -> dict[str, Any] | None:
        """One node of the squad graph: identity, kind, mode, tools and delegation allow-list."""
        definition = agent_library.definition_kind(agent_id)
        if definition is None:
            return None
        assigned = agent_library.get_agent_tools(definition, agent_id)
        if definition == "prompt":
            profile = agent_library.load_agent_definition_from_md(agent_library.LIBRARY_ROOT / f"{agent_id}.md")
            name, description = profile.name, profile.description
        else:
            meta, _ = agent_library.parse_subagent_yaml(agent_id)
            name, description = meta.get("name") or agent_id, meta.get("description") or ""
        return {
            "id": assigned["agent_id"],
            "kind": _KIND_BY_DEFINITION[definition],
            "name": str(name),
            "description": str(description),
            "mode": assigned["mode"],
            "tools": assigned["tools"],
            "allowed_subagents": [entry["id"] for entry in assigned["subagents"]],
            "path": assigned["path"],
        }

    def list_agents(self) -> list[dict[str, Any]]:
        catalog = squad_registry.list_agent_catalog()
        nodes = []
        for entry in catalog["agents"] + catalog["subagents"]:
            try:
                node = self.describe_agent(entry["id"])
            except (OSError, ValueError):
                node = None
            if node is not None:
                nodes.append(node)
        return nodes

    def overview(self) -> dict[str, Any]:
        """Everything the Squads home screen needs in one call."""
        return {
            "squads": squad_registry.list_squads(),
            "agents": self.list_agents(),
            "tools": tool_catalog.list_tools(),
            "patterns": list(squad_registry.SQUAD_PATTERNS),
        }

    def get_blueprint(self, squad_id: str) -> dict[str, Any] | None:
        squad = squad_registry.get_squad(squad_id)
        if squad is None:
            return None
        return {
            "squad_id": squad["squad_id"],
            "name": squad["name"],
            "description": squad.get("description", ""),
            "pattern": squad["default_pattern"],
            "max_parallel": squad["max_parallel"],
            "lead": self.describe_agent(squad["primary_agent"]),
            "members": [self.describe_agent(member_id) for member_id in squad["subagents"]],
            "metrics": squad["metrics"],
            "dedicated_pipeline": squad["squad_id"] == CODE_SQUAD_ID,
        }

    # --- Validation ----------------------------------------------------------------

    def validate(self, blueprint: SquadBlueprint) -> BlueprintValidation:
        errors: list[str] = []
        warnings: list[str] = []
        known_tools = {meta["name"] for meta in tool_catalog.list_tools()}

        lead = self._describe_or_none(blueprint.lead.agent_id)
        if lead is None:
            errors.append(f"Lead agent not found: {blueprint.lead.agent_id}")
        elif lead["kind"] != "agent":
            errors.append(f"The lead must be a primary agent from library/, not a sub-agent: {lead['id']}")

        member_ids = [member.agent_id for member in blueprint.members]
        if len(set(member_ids)) != len(member_ids):
            errors.append("Squad members must not contain duplicates.")
        if blueprint.lead.agent_id in member_ids:
            errors.append("The lead cannot also be a squad member.")
        members = {}
        for member_id in member_ids:
            node = self._describe_or_none(member_id)
            if node is None:
                errors.append(f"Member not found: {member_id}")
            else:
                members[member_id] = node

        for node_spec in [blueprint.lead, *blueprint.members]:
            unknown = [tool for tool in node_spec.tools or [] if tool not in known_tools]
            if unknown:
                errors.append(f"{node_spec.agent_id}: unknown tools {', '.join(unknown)}")

        if blueprint.squad_id == CODE_SQUAD_ID and blueprint.pattern != "sequential":
            errors.append("code-squad always runs its dedicated sequential pipeline.")
        if blueprint.pattern == "router" and len(member_ids) < 2:
            errors.append("The router pattern needs at least two members to choose between.")
        if blueprint.pattern == "fanout" and len(member_ids) < 2:
            warnings.append("Fan-out with fewer than two members behaves like sequential.")
        if not member_ids:
            warnings.append("No members: the lead will answer alone after an empty scratchpad barrier.")

        if lead is not None:
            lead_tools = self._final_tools(blueprint.lead, lead, blueprint.lead_delegates)
            if LEAD_HANDOFF_TOOL not in lead_tools:
                warnings.append(f"The lead has no {LEAD_HANDOFF_TOOL}, so it cannot read member handoffs.")
            if lead_tools and lead["mode"] != "agent":
                warnings.append("The lead is in chat mode; it will be switched to agent mode so its tools run.")
            if blueprint.lead_delegates and not any(n["kind"] == "subagent" for n in members.values()):
                warnings.append("Delegation is on but the squad has no sub-agent members to call.")
        for member_id, node in members.items():
            spec = next(m for m in blueprint.members if m.agent_id == member_id)
            tools = self._final_tools(spec, node, False)
            if node["kind"] == "subagent" and MEMBER_HANDOFF_TOOL not in tools:
                warnings.append(f"{member_id} has no {MEMBER_HANDOFF_TOOL}, so its result will not reach the lead.")
        return BlueprintValidation(ok=not errors, errors=errors, warnings=warnings)

    # --- Applying ------------------------------------------------------------------

    def save_blueprint(self, blueprint: SquadBlueprint) -> dict[str, Any]:
        """Register or update the squad, then write each member's tools and the lead's allow-list."""
        validation = self.validate(blueprint)
        if not validation.ok:
            raise ValueError("; ".join(validation.errors))

        member_ids = [member.agent_id for member in blueprint.members]
        if blueprint.squad_id:
            squad = squad_registry.update_squad(
                blueprint.squad_id,
                name=blueprint.name,
                description=blueprint.description,
                primary_agent=blueprint.lead.agent_id,
                subagents=member_ids,
                default_pattern=blueprint.pattern,
                max_parallel=blueprint.max_parallel,
            )
            if squad is None:
                raise LookupError(f"Squad not found: {blueprint.squad_id}")
        else:
            squad = squad_registry.create_squad(
                blueprint.name, blueprint.description, blueprint.lead.agent_id,
                member_ids, blueprint.pattern, blueprint.max_parallel,
            )

        for spec in blueprint.members:
            if spec.tools is not None:
                self._write_tools(spec.agent_id, spec.tools)
        self._apply_lead(blueprint)
        return {
            "blueprint": self.get_blueprint(squad["squad_id"]),
            "warnings": validation.warnings,
        }

    def _apply_lead(self, blueprint: SquadBlueprint) -> None:
        """Write the lead's tools; with delegation on, its '## Sub-agents' allow-list becomes
        exactly the squad's sub-agent members (keeping any usage notes already written)."""
        if blueprint.lead.tools is None and not blueprint.lead_delegates:
            return
        lead = self.describe_agent(blueprint.lead.agent_id)
        tools = self._final_tools(blueprint.lead, lead, blueprint.lead_delegates)
        allow_list = None
        if blueprint.lead_delegates:
            usage = {
                entry["id"]: entry["usage"]
                for entry in agent_library.get_agent_tools("prompt", lead["id"])["subagents"]
            }
            allow_list = [
                {"id": member.agent_id, "usage": usage.get(member.agent_id, "")}
                for member in blueprint.members
                if agent_library.definition_kind(member.agent_id) == "subagent"
            ]
        agent_library.set_agent_tools(
            "prompt", lead["id"], tools, mode="agent" if tools else None, subagents=allow_list
        )

    @staticmethod
    def _final_tools(spec: SquadMember, node: dict[str, Any], delegates: bool) -> list[str]:
        tools = list(spec.tools) if spec.tools is not None else list(node["tools"])
        if delegates and DELEGATION_TOOL not in tools:
            tools.append(DELEGATION_TOOL)
        return tools

    def _describe_or_none(self, agent_id: str) -> dict[str, Any] | None:
        try:
            return self.describe_agent(agent_id)
        except (OSError, ValueError):
            return None

    def _write_tools(self, agent_id: str, tools: list[str]) -> dict[str, Any]:
        kind = agent_library.definition_kind(agent_id)
        if kind is None:
            raise FileNotFoundError(f"Agent not found: {agent_id}")
        known_tools = {meta["name"] for meta in tool_catalog.list_tools()}
        unknown = [tool for tool in tools if tool not in known_tools]
        if unknown:
            raise ValueError(f"Unknown tools: {', '.join(unknown)}")
        agent_library.set_agent_tools(kind, agent_id, tools, mode="agent" if tools else None)
        return self.describe_agent(agent_id)

    def attach_tool(self, agent_id: str, tool: str) -> dict[str, Any]:
        node = self.describe_agent(agent_id)
        if node is None:
            raise FileNotFoundError(f"Agent not found: {agent_id}")
        if tool in node["tools"]:
            return node
        return self._write_tools(agent_id, [*node["tools"], tool])

    def detach_tool(self, agent_id: str, tool: str) -> dict[str, Any]:
        node = self.describe_agent(agent_id)
        if node is None:
            raise FileNotFoundError(f"Agent not found: {agent_id}")
        return self._write_tools(agent_id, [name for name in node["tools"] if name != tool])

    def create_member(self, kind: str, agent_id: str, name: str, description: str = "") -> dict[str, Any]:
        """Scaffold a new primary agent (library/*.md) or sub-agent (subAgentLibrary/*.yaml)."""
        if kind == "agent":
            created = workspace_workflow.scaffold_agent(agent_id, name, description, mode="agent")
            return self.describe_agent(created["agent_id"])
        if kind == "subagent":
            created = squad_registry.create_subagent(agent_id, name, description)
            return self.describe_agent(created.get("subagent_id") or agent_id)
        raise ValueError(f"Unknown member kind: {kind}")


squad_architect = SquadArchitect()
