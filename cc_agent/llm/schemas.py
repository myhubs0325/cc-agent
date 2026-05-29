from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class PlannedStepPayload(BaseModel):
    adapter: str
    action: str
    description: str
    params: dict[str, Any] = Field(default_factory=dict)


class PlannedTaskPayload(BaseModel):
    target: str
    summary: str
    steps: list[PlannedStepPayload] = Field(default_factory=list)
