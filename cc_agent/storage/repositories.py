from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from cc_agent.domain.enums import RunStatus
from cc_agent.domain.models import TaskSpec
from cc_agent.storage.db import Database


@dataclass(slots=True)
class RunRecord:
    id: str
    command: str
    target: str
    status: str
    summary: str
    created_at: str
    updated_at: str


@dataclass(slots=True)
class RunCheckpointRecord:
    id: str
    run_id: str
    target: str
    summary: str
    wait_reason: str
    task_json: str
    created_at: str
    updated_at: str


class RunRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    def create_run(self, run_id: str, task: TaskSpec, status: RunStatus) -> None:
        now = datetime.now(UTC).isoformat()
        with self._database.connect() as connection:
            connection.execute(
                """
                INSERT INTO runs (id, command, target, status, summary, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    task.user_command,
                    task.target,
                    status.value,
                    task.summary,
                    now,
                    now,
                ),
            )
            connection.commit()

    def update_run(self, run_id: str, status: RunStatus, summary: str) -> None:
        now = datetime.now(UTC).isoformat()
        with self._database.connect() as connection:
            connection.execute(
                """
                UPDATE runs
                SET status = ?, summary = ?, updated_at = ?
                WHERE id = ?
                """,
                (status.value, summary, now, run_id),
            )
            connection.commit()

    def fail_incomplete_runs(self, summary: str = "上次执行异常中断，请重新运行。") -> int:
        now = datetime.now(UTC).isoformat()
        with self._database.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE runs
                SET status = ?, summary = ?, updated_at = ?
                WHERE status = ?
                """,
                (RunStatus.FAILED.value, summary, now, RunStatus.RUNNING.value),
            )
            connection.execute(
                """
                DELETE FROM run_checkpoints
                WHERE EXISTS (
                    SELECT 1
                    FROM runs
                    WHERE runs.target = run_checkpoints.target
                      AND runs.status = ?
                      AND runs.created_at >= run_checkpoints.created_at
                )
                """,
                (RunStatus.SUCCEEDED.value,),
            )
            connection.commit()
        return int(cursor.rowcount or 0)

    def list_recent(self, limit: int = 20) -> list[RunRecord]:
        with self._database.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, command, target, status, summary, created_at, updated_at
                FROM runs
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [RunRecord(*row) for row in rows]

    def save_checkpoint(
        self,
        checkpoint_id: str,
        run_id: str,
        target: str,
        summary: str,
        wait_reason: str,
        task: TaskSpec,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        with self._database.connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO run_checkpoints (
                    id, run_id, target, summary, wait_reason, task_json, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    checkpoint_id,
                    run_id,
                    target,
                    summary,
                    wait_reason,
                    task.model_dump_json(),
                    now,
                    now,
                ),
            )
            connection.commit()

    def get_checkpoint(self, checkpoint_id: str) -> RunCheckpointRecord | None:
        with self._database.connect() as connection:
            row = connection.execute(
                """
                SELECT id, run_id, target, summary, wait_reason, task_json, created_at, updated_at
                FROM run_checkpoints
                WHERE id = ?
                """,
                (checkpoint_id,),
            ).fetchone()
        return RunCheckpointRecord(*row) if row is not None else None

    def get_latest_checkpoint(self, target: str | None = None) -> RunCheckpointRecord | None:
        query = """
            SELECT id, run_id, target, summary, wait_reason, task_json, created_at, updated_at
            FROM run_checkpoints
        """
        params: tuple[object, ...] = ()
        if target is not None:
            query += " WHERE target = ?"
            params = (target,)
        query += " ORDER BY created_at DESC LIMIT 1"
        with self._database.connect() as connection:
            row = connection.execute(query, params).fetchone()
        return RunCheckpointRecord(*row) if row is not None else None

    def delete_checkpoint(self, checkpoint_id: str) -> None:
        with self._database.connect() as connection:
            connection.execute(
                "DELETE FROM run_checkpoints WHERE id = ?",
                (checkpoint_id,),
            )
            connection.commit()

    def cleanup_stale_checkpoints(self) -> int:
        with self._database.connect() as connection:
            cursor = connection.execute(
                """
                DELETE FROM run_checkpoints
                WHERE EXISTS (
                    SELECT 1
                    FROM runs
                    WHERE runs.target = run_checkpoints.target
                      AND runs.status = ?
                      AND runs.created_at >= run_checkpoints.created_at
                )
                """,
                (RunStatus.SUCCEEDED.value,),
            )
            connection.commit()
        return int(cursor.rowcount or 0)
