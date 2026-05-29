from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator


class Database:
    def __init__(self, path: Path) -> None:
        self._path = path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self._path)
        try:
            yield connection
        except Exception:
            connection.rollback()
            raise
        else:
            connection.commit()
        finally:
            connection.close()

    def initialize(self) -> Path | None:
        try:
            self._initialize_schema()
            return None
        except sqlite3.DatabaseError:
            backup_path = self.backup_corrupt_database()
            self._initialize_schema()
            return backup_path

    def backup_corrupt_database(self) -> Path | None:
        if not self._path.exists():
            return None

        timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")
        backup_path = self._path.with_name(f"{self._path.stem}.corrupt.{timestamp}{self._path.suffix}")
        self._move_if_exists(self._path, backup_path)

        for suffix in ("-wal", "-shm", "-journal"):
            sidecar_path = Path(f"{self._path}{suffix}")
            backup_sidecar_path = Path(f"{backup_path}{suffix}")
            self._move_if_exists(sidecar_path, backup_sidecar_path)

        return backup_path

    def _initialize_schema(self) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY,
                    command TEXT NOT NULL,
                    target TEXT NOT NULL,
                    status TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS run_checkpoints (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    target TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    wait_reason TEXT NOT NULL,
                    task_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.commit()

    @staticmethod
    def _move_if_exists(source: Path, destination: Path) -> None:
        if source.exists():
            source.rename(destination)
