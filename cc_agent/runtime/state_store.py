from __future__ import annotations

from cc_agent.domain.enums import RunStatus


class RunStateStore:
    def __init__(self) -> None:
        self._states: dict[str, RunStatus] = {}

    def set_state(self, run_id: str, status: RunStatus) -> None:
        self._states[run_id] = status

    def get_state(self, run_id: str) -> RunStatus | None:
        return self._states.get(run_id)
