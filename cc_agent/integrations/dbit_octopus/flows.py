from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def load_flow(config_dir: Path, flow_name: str) -> dict[str, Any]:
    path = config_dir / f"{flow_name}.yaml"
    if not path.exists():
        return {"name": flow_name, "steps": []}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {"name": flow_name, "steps": []}
