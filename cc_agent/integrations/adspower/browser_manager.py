from __future__ import annotations

from typing import Any


def extract_debugger_address(payload: dict[str, Any]) -> str | None:
    data = payload.get("data", {})
    ws = data.get("ws", {})
    return ws.get("selenium")
