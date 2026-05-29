from __future__ import annotations

from cc_agent.domain.models import SafetyConfig, TaskStep


def step_requires_confirmation(step: TaskStep, config: SafetyConfig) -> bool:
    action = step.action.lower()
    if step.confirmation_required:
        return True
    return any(keyword in action for keyword in config.confirmation_actions)


def command_is_blocked(command: str, config: SafetyConfig) -> bool:
    lowered = command.lower()
    return any(keyword.lower() in lowered for keyword in config.blocked_keywords)
