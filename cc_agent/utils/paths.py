from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from cc_agent.domain.models import AppConfig


@dataclass(slots=True)
class AppPaths:
    install_root: Path
    data_root: Path
    config_dir: Path
    default_config_dir: Path
    artifacts_root: Path
    logs_dir: Path
    screenshots_dir: Path
    exports_dir: Path
    database_path: Path

    @classmethod
    def from_config(
        cls,
        install_root: Path,
        data_root: Path,
        config_dir: Path,
        config: AppConfig,
    ) -> "AppPaths":
        artifacts_root = data_root / config.artifacts_dir
        return cls(
            install_root=install_root,
            data_root=data_root,
            config_dir=config_dir,
            default_config_dir=install_root / "config",
            artifacts_root=artifacts_root,
            logs_dir=artifacts_root / "logs",
            screenshots_dir=artifacts_root / "screenshots",
            exports_dir=artifacts_root / "exports",
            database_path=data_root / config.database_path,
        )

    def ensure(self) -> None:
        self.data_root.mkdir(parents=True, exist_ok=True)
        if not self.config_dir.exists() and self.default_config_dir.exists():
            shutil.copytree(self.default_config_dir, self.config_dir)
        else:
            self.config_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts_root.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.screenshots_dir.mkdir(parents=True, exist_ok=True)
        self.exports_dir.mkdir(parents=True, exist_ok=True)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
