"""Pydantic schemas for generic orchestration tasks and the Code Development Squad."""

from dataclasses import dataclass, field
from typing import Any, List, Literal, Optional

from pydantic import BaseModel, Field


class TraceStep(BaseModel):
    step_number: int
    agent_id: str
    phase: str
    status: str
    details: str
    error_reason: Optional[str] = None
    timestamp: str


class OrchestrationRequest(BaseModel):
    trace_id: Optional[str] = Field(
        default=None,
        max_length=80,
        pattern=r"^[A-Za-z0-9_.-]+$",
        description="Optional trace ID assigned by the orchestration service",
    )
    squad_id: Optional[str] = Field(
        default=None, description="Registered squad to associate with this run"
    )
    pattern: Optional[Literal["sequential", "fanout", "router"]] = Field(
        default=None,
        description=(
            "Execution strategy: 'sequential' (local LLM hardware safety), 'fanout', or 'router'. "
            "Leave unset to use the registered squad's default_pattern."
        ),
    )
    primary_agent: str = Field(
        default="rag-presenter", description="Main user-facing agent ID from library/"
    )
    subagents: List[str] = Field(
        default_factory=list,
        description="Ordered squad member IDs: sub-agents from subAgentLibrary/ or primary agents from library/",
    )
    workflow_action: Literal["all", "plan", "build", "test", "fix", "troubleshoot"] = Field(
        default="all",
        description="Code Development Squad action; 'troubleshoot' is an alias for 'fix'.",
    )
    user_query: str = Field(..., description="User query or task prompt")
    parameters: dict[str, Any] = Field(default_factory=dict, description="Custom parameters")


SquadPattern = Literal["sequential", "fanout", "router"]


class SquadCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    description: str = Field(default="", max_length=500)
    primary_agent: str = Field(..., min_length=1, max_length=80)
    subagents: List[str] = Field(default_factory=list, max_length=20)
    default_pattern: SquadPattern = "sequential"
    max_parallel: int = Field(default=2, ge=1, le=4)


class SquadUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=80)
    description: Optional[str] = Field(default=None, max_length=500)
    primary_agent: Optional[str] = Field(default=None, min_length=1, max_length=80)
    subagents: Optional[List[str]] = Field(default=None, max_length=20)
    default_pattern: Optional[SquadPattern] = None
    max_parallel: Optional[int] = Field(default=None, ge=1, le=4)


class SubagentCreateRequest(BaseModel):
    subagent_id: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)


class OrchestrationResponse(BaseModel):
    trace_id: str
    pattern: str
    query: str
    status: str
    final_answer: str
    result_payload: Optional[Any] = None
    execution_trace: List[TraceStep] = Field(default_factory=list)


@dataclass
class CodeSquadRunContext:
    """Mutable state of one Code Development Squad run."""

    trace_id: str
    request: OrchestrationRequest
    code: str
    plan: str = ""
    model: str | None = None
    last_test: dict[str, Any] | None = None
    steps: list[TraceStep] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def query(self) -> str:
        return self.request.user_query
