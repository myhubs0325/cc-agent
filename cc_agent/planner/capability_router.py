from __future__ import annotations

from cc_agent.domain.enums import RiskLevel
from cc_agent.domain.models import TaskStep


class CapabilityRouter:
    def suggest_target(self, command: str) -> str:
        lowered = command.lower()
        if any(
            token in lowered
            for token in (
                "new user",
                "one click install",
                "onboarding",
            )
        ) or ("\u65b0\u7528\u6237" in command and ("\u4e00\u952e\u5b89\u88c5" in command or "\u5b89\u88c5" in command)):
            return "new_user_setup"
        if any(
            token in lowered
            for token in (
                "clash",
                "verge",
                "\u4ee3\u7406",
                "\u7f51\u7edc",
            )
        ):
            return "clash_verge"
        if any(
            token in lowered
            for token in (
                "adspower",
                "browser",
                "url",
                "page",
                "site",
                "\u7f51\u9875",
                "\u6d4f\u89c8\u5668",
            )
        ):
            return "adspower"
        if any(
            token in lowered
            for token in (
                "dbit",
                "octopus",
                "\u516c\u53f8\u7cfb\u7edf",
                "\u5185\u90e8\u7cfb\u7edf",
            )
        ):
            return "dbit_octopus"
        if any(
            token in lowered
            for token in (
                "wps",
                "excel",
                "sheet",
                "word",
                "\u8868\u683c",
                "\u6587\u6863",
            )
        ):
            return "wps"
        return "adspower"

    def build_fallback_steps(self, command: str, target: str) -> list[TaskStep]:
        action = {
            "new_user_setup": "validate_setup_inputs",
            "clash_verge": "prepare_global_proxy",
            "adspower": "handle_browser_request",
            "dbit_octopus": "run_ui_flow",
            "wps": "handle_document_request",
        }.get(target, "handle_request")
        adapter = "onboarding" if target == "new_user_setup" else target
        risk = (
            RiskLevel.HIGH
            if any(token in command.lower() for token in ("delete", "submit", "send", "\u4ed8\u6b3e"))
            else RiskLevel.LOW
        )
        return [
            TaskStep(
                adapter=adapter,
                action=action,
                description=f"\u5728 {target} \u4e2d\u6267\u884c\u7528\u6237\u8bf7\u6c42",
                params={"command": command},
                risk_level=risk,
                confirmation_required=risk == RiskLevel.HIGH,
            )
        ]
