from __future__ import annotations

from cc_agent.domain.models import PlanReview, SafetyConfig, TaskSpec
from cc_agent.safety.policies import command_is_blocked, step_requires_confirmation


class SafetyGuard:
    def __init__(self, config: SafetyConfig) -> None:
        self._config = config

    def review(self, task: TaskSpec) -> PlanReview:
        review = PlanReview()
        if command_is_blocked(task.user_command, self._config):
            review.blocked = True
            review.reasons.append("Command matches a blocked policy keyword.")
            return review
        for step in task.steps:
            if step_requires_confirmation(step, self._config):
                review.requires_confirmation = True
                review.reasons.append(f"Step '{step.description}' requires confirmation.")
        return review
