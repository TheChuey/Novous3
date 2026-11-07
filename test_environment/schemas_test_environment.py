"""Sandbox test-harness data contracts: Tool Workbench and sandbox package requests."""

from typing import Any, Literal

from pydantic import BaseModel, Field


class ToolWorkbenchRequest(BaseModel):
    tool_code: str = Field(..., min_length=1, max_length=50000)
    function_input: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: float = Field(5, ge=1, le=600)


class ToolWorkbenchLLMRequest(ToolWorkbenchRequest):
    model: str = Field(..., min_length=1, max_length=120)
    user_prompt: str = Field(..., min_length=1, max_length=5000)
    execute: bool = True
    description: str | None = Field(None, max_length=2000)


class ToolWorkbenchGenerateRequest(BaseModel):
    model: str = Field(..., min_length=1, max_length=120)
    description: str = Field(..., min_length=1, max_length=5000)


class SandboxInstallRequest(BaseModel):
    packages: list[str] = Field(..., min_length=1, max_length=20)


class SandboxCheckRequest(BaseModel):
    source: str = Field("", max_length=50000)
    modules: list[str] = Field(default_factory=list, max_length=20)


class SuiteExpect(BaseModel):
    contains: str | None = Field(None, max_length=500)
    tool: str | None = Field(None, max_length=120)


class SuiteStep(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000)
    enabled: bool = True
    expect: SuiteExpect | None = None


class SuiteSaveRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    category: Literal["squads", "tool_usage", "prompts"]
    description: str = Field("", max_length=500)
    agent_id: str = Field(..., min_length=1, max_length=120)
    model: str = Field(..., min_length=1, max_length=120)
    steps: list[SuiteStep] = Field(..., min_length=1, max_length=100)


class SuiteRunStep(BaseModel):
    text: str = Field(..., max_length=2000)
    status: Literal["passed", "failed", "completed", "request_failed"]
    reply: str = Field("", max_length=20000)
    duration_ms: int = Field(0, ge=0)
    tool_events: list[dict[str, Any]] = Field(default_factory=list, max_length=50)
    checks: list[dict[str, Any]] = Field(default_factory=list, max_length=20)


class SuiteRunRequest(BaseModel):
    run_at: str = Field(..., max_length=64)
    agent_id: str = Field(..., max_length=120)
    model: str = Field(..., max_length=120)
    duration_ms: int = Field(0, ge=0)
    passed: int = Field(0, ge=0)
    failed: int = Field(0, ge=0)
    steps: list[SuiteRunStep] = Field(..., max_length=100)
