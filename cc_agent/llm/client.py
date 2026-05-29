from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod

import httpx

from cc_agent.domain.models import LlmConfig, TaskSpec, TaskStep
from cc_agent.llm.prompts import build_system_prompt, build_user_prompt
from cc_agent.llm.schemas import PlannedTaskPayload


class PlannerClient(ABC):
    @abstractmethod
    def plan(self, command: str, target: str, fallback_steps: list[TaskStep]) -> TaskSpec:
        raise NotImplementedError


class MockPlannerClient(PlannerClient):
    def plan(self, command: str, target: str, fallback_steps: list[TaskStep]) -> TaskSpec:
        return TaskSpec(
            user_command=command,
            target=target,
            summary=f"Mock plan for {target}",
            steps=fallback_steps,
        )


class OpenAICompatiblePlannerClient(PlannerClient):
    def __init__(self, config: LlmConfig) -> None:
        self._config = config
        self._api_key = os.getenv(config.api_key_env, "")

    def plan(self, command: str, target: str, fallback_steps: list[TaskStep]) -> TaskSpec:
        if not self._api_key or not self._config.base_url:
            return MockPlannerClient().plan(command, target, fallback_steps)

        payload = {
            "model": self._config.model,
            "temperature": self._config.temperature,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": build_system_prompt()},
                {
                    "role": "user",
                    "content": build_user_prompt(command, target, fallback_steps),
                },
            ],
        }

        try:
            with httpx.Client(timeout=self._config.request_timeout_seconds) as client:
                response = client.post(
                    f"{self._config.base_url.rstrip('/')}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
            content = data["choices"][0]["message"]["content"]
            parsed = PlannedTaskPayload.model_validate(json.loads(content))
            steps = [
                TaskStep(
                    adapter=step.adapter,
                    action=step.action,
                    description=step.description,
                    params=step.params,
                )
                for step in parsed.steps
            ]
            if not steps:
                steps = fallback_steps
            return TaskSpec(
                user_command=command,
                target=parsed.target,
                summary=parsed.summary,
                steps=steps,
            )
        except Exception:
            return MockPlannerClient().plan(command, target, fallback_steps)
