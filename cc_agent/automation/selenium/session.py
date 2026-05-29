from __future__ import annotations

from typing import Any


def extract_selenium_endpoint(payload: dict[str, Any]) -> str | None:
    return payload.get("data", {}).get("ws", {}).get("selenium")
