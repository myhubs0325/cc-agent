from __future__ import annotations

from dataclasses import dataclass

from cc_agent.planner.capability_router import CapabilityRouter


@dataclass(slots=True)
class ParsedIntent:
    command: str
    target: str


class IntentParser:
    def __init__(self, router: CapabilityRouter) -> None:
        self._router = router

    def parse(self, command: str) -> ParsedIntent:
        return ParsedIntent(command=command, target=self._router.suggest_target(command))
