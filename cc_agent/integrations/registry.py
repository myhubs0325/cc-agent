from __future__ import annotations

from cc_agent.integrations.base import BaseAdapter


class IntegrationRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, BaseAdapter] = {}

    def register(self, adapter: BaseAdapter) -> None:
        self._adapters[adapter.name] = adapter

    def get(self, name: str) -> BaseAdapter:
        return self._adapters[name]

    def names(self) -> list[str]:
        return sorted(self._adapters)
