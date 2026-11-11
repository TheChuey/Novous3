"""Pydantic schemas for a lead, ordered sub-agent members and their tool assignments."""

from typing import List, Literal, Optional

from pydantic import BaseModel, Field

SquadPattern = Literal["sequential", "fanout", "router"]
MemberKind = Literal["agent", "subagent"]


class SquadMember(BaseModel):
    agent_id: str = Field(..., min_length=1, max_length=80)
    tools: Optional[List[str]] = Field(
        default=None, max_length=40, description="Tools to assign; None keeps the agent's current tools"
    )


class SquadBlueprint(BaseModel):
    squad_id: Optional[str] = Field(default=None, description="Existing squad to update; None creates one")
    name: str = Field(..., min_length=1, max_length=80)
    description: str = Field(default="", max_length=500)
    pattern: SquadPattern = Field(
        default="sequential",
        description="Execution is sequential; fanout and router are accepted as legacy aliases.",
    )
    lead: SquadMember
    members: List[SquadMember] = Field(default_factory=list, max_length=20)
    lead_delegates: bool = Field(
        default=False,
        description="Deprecated; dynamic delegation is disabled during squad runs.",
    )


class BlueprintValidation(BaseModel):
    ok: bool
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class MemberCreateRequest(BaseModel):
    kind: MemberKind
    agent_id: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)


class ToolAssignmentRequest(BaseModel):
    agent_id: str = Field(..., min_length=1, max_length=80)
    tool: str = Field(..., min_length=1, max_length=80)


class AgentPromptRequest(BaseModel):
    prompt: str = Field(..., max_length=100000)
