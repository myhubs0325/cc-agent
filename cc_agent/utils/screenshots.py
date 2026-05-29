from pathlib import Path
from uuid import uuid4


def next_screenshot_path(target_dir: Path) -> Path:
    target_dir.mkdir(parents=True, exist_ok=True)
    return target_dir / f"{uuid4()}.png"
