from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class RuntimeRoots:
    install_root: Path
    data_root: Path


def resolve_project_root() -> Path:
    return resolve_runtime_roots().install_root


def resolve_runtime_roots(app_dir_name: str = "CCLocalAgent") -> RuntimeRoots:
    if getattr(sys, "frozen", False):
        install_root = Path(sys.executable).resolve().parent
        local_app_data = os.getenv("LOCALAPPDATA")
        if local_app_data:
            data_root = Path(local_app_data) / app_dir_name
        else:
            data_root = Path.home() / "AppData" / "Local" / app_dir_name
        return RuntimeRoots(install_root=install_root, data_root=data_root)

    project_root = Path(__file__).resolve().parents[3]
    return RuntimeRoots(install_root=project_root, data_root=project_root)
