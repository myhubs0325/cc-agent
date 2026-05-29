from __future__ import annotations

from typing import Iterable

from cc_agent.domain.models import TaskStep


def build_system_prompt() -> str:
    return (
        "You are a Windows desktop task planner. "
        "Return JSON only. "
        "Do not invent execution results. "
        "Plan using the smallest safe set of steps. "
        "Each step must include adapter, action, description, and params. "
        "Allowed adapters are clash_verge, adspower, dbit_octopus, and wps."
    )


def build_user_prompt(command: str, target: str, fallback_steps: Iterable[TaskStep]) -> str:
    fallback_lines = [
        f"- adapter={step.adapter}, action={step.action}, description={step.description}"
        for step in fallback_steps
    ]
    fallback_text = "\n".join(fallback_lines) if fallback_lines else "- none"
    return (
        f"User command: {command}\n"
        f"Suggested target: {target}\n"
        "Fallback steps:\n"
        f"{fallback_text}\n"
        "Return JSON with shape:\n"
        '{'
        '"target": "...", '
        '"summary": "...", '
        '"steps": [{"adapter": "...", "action": "...", "description": "...", "params": {}}]'
        '}'
    )
