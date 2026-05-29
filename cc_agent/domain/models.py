from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from .enums import RiskLevel, RunStatus


class TaskStep(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    adapter: str
    action: str
    description: str
    params: dict[str, Any] = Field(default_factory=dict)
    risk_level: RiskLevel = RiskLevel.LOW
    confirmation_required: bool = False


class TaskSpec(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    user_command: str
    target: str
    summary: str
    steps: list[TaskStep]
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class StepResult(BaseModel):
    step_id: str
    status: RunStatus
    message: str
    data: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[str] = Field(default_factory=list)


class ExecutionResult(BaseModel):
    run_id: str
    status: RunStatus
    summary: str
    step_results: list[StepResult] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    artifacts: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class PlanReview(BaseModel):
    requires_confirmation: bool = False
    blocked: bool = False
    reasons: list[str] = Field(default_factory=list)


class AppConfig(BaseModel):
    title: str = "CC Local Agent"
    artifacts_dir: str = "artifacts"
    database_path: str = "artifacts/state.sqlite3"
    integrations: dict[str, dict[str, Any]] = Field(default_factory=dict)


class LlmConfig(BaseModel):
    provider: str = "mock"
    base_url: str | None = None
    model: str = "gpt-4.1-mini"
    api_key_env: str = "LLM_API_KEY"
    temperature: float = 0.1
    request_timeout_seconds: float = 60.0


class SafetyConfig(BaseModel):
    confirmation_actions: list[str] = Field(default_factory=list)
    blocked_keywords: list[str] = Field(default_factory=list)
