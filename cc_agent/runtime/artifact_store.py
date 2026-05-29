from __future__ import annotations

from pathlib import Path

from cc_agent.domain.models import ExecutionResult


class ArtifactStore:
    def __init__(self, logs_dir: Path) -> None:
        self._logs_dir = logs_dir

    def persist(self, result: ExecutionResult) -> Path:
        path = self._logs_dir / f"{result.run_id}.json"
        path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
        return path
