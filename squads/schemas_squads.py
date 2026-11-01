"""Pydantic schemas for design-time squad blueprints (who is in a squad, how they interact,
and which tools each member carries)."""

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
    pattern: SquadPattern = "sequential"
    max_parallel: int = Field(default=2, ge=1, le=4)
    lead: SquadMember
    members: List[SquadMember] = Field(default_factory=list, max_length=20)
    lead_delegates: bool = Field(
        default=False,
        description="Give the lead call_subagent, restricted to the squad's sub-agent members",
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
