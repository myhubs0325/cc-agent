from __future__ import annotations

import logging

from cc_agent.domain.enums import RunStatus
from cc_agent.domain.models import StepResult, TaskStep
from cc_agent.integrations.registry import IntegrationRegistry


logger = logging.getLogger(__name__)


class StepRunner:
    def __init__(self, registry: IntegrationRegistry) -> None:
        self._registry = registry

    def run(self, step: TaskStep) -> StepResult:
        try:
            adapter = self._registry.get(step.adapter)
            return adapter.execute(step)
        except Exception as exc:
            logger.exception("Step execution raised exception: %s.%s", step.adapter, step.action)
            return StepResult(
                step_id=step.id,
                status=RunStatus.FAILED,
                message=str(exc),
            )
