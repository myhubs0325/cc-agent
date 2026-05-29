from __future__ import annotations

from abc import ABC, abstractmethod

from cc_agent.domain.models import StepResult, TaskStep


class BaseAdapter(ABC):
    name: str

    @property
    @abstractmethod
    def capabilities(self) -> list[str]:
        raise NotImplementedError

    @abstractmethod
    def execute(self, step: TaskStep) -> StepResult:
        raise NotImplementedError
