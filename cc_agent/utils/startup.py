from __future__ import annotations

import ctypes
import sys
from pathlib import Path


def resolve_startup_log_path(data_root: Path) -> Path:
    return data_root / "artifacts" / "logs" / "startup.log"


def show_startup_error(message: str, *, title: str) -> None:
    if sys.platform == "win32":
        try:
            ctypes.windll.user32.MessageBoxW(None, message, title, 0x10)
            return
        except Exception:
            pass

    print(f"{title}: {message}", file=sys.stderr)
