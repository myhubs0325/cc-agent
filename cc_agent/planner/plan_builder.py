from __future__ import annotations

import re

from cc_agent.domain.models import TaskSpec, TaskStep
from cc_agent.llm.client import PlannerClient
from cc_agent.onboarding import NewUserSetupRequest, OnboardingDocumentParser, OnboardingTaskFactory, ParsedOnboardingSource
from cc_agent.planner.capability_router import CapabilityRouter
from cc_agent.planner.intent_parser import IntentParser


class PlanBuilder:
    def __init__(
        self,
        intent_parser: IntentParser,
        router: CapabilityRouter,
        planner_client: PlannerClient,
        onboarding_document_parser: OnboardingDocumentParser | None = None,
        onboarding_task_factory: OnboardingTaskFactory | None = None,
    ) -> None:
        self._intent_parser = intent_parser
        self._router = router
        self._planner_client = planner_client
        self._onboarding_document_parser = onboarding_document_parser
        self._onboarding_task_factory = onboarding_task_factory

    def build(self, command: str) -> TaskSpec:
        parsed = self._intent_parser.parse(command)
        daily_routine_task = self._build_daily_routine_task(command)
        if daily_routine_task is not None:
            return daily_routine_task
        if parsed.target == "new_user_setup":
            setup_task = self._build_new_user_setup_task(command)
            if setup_task is not None:
                return setup_task
        rule_based_task = self._build_rule_based_task(command, parsed.target)
        if rule_based_task is not None:
            return rule_based_task
        fallback_steps = self._router.build_fallback_steps(command, parsed.target)
        return self._planner_client.plan(command, parsed.target, fallback_steps)

    def build_new_user_setup(
        self,
        installer_path: str,
        source_path: str,
        parsed_source: ParsedOnboardingSource | None = None,
    ) -> TaskSpec:
        if self._onboarding_task_factory is None or self._onboarding_document_parser is None:
            raise ValueError("当前环境未启用新用户安装任务工厂。")
        resolved_source = parsed_source or self._onboarding_document_parser.parse(source_path)
        request = NewUserSetupRequest(
            installer_path=installer_path,
            source_path=source_path,
            parsed_source=resolved_source,
        )
        return self._onboarding_task_factory.create(
            request,
            user_command=f"新用户一键安装 installer={installer_path} source={source_path}",
        )

    def _build_rule_based_task(self, command: str, target: str) -> TaskSpec | None:
        for builder in (
            self._build_clash_task,
            self._build_wps_task,
            self._build_adspower_task,
            self._build_dbit_task,
        ):
            task = builder(command, target)
            if task is not None:
                return task
        return None

    def _build_new_user_setup_task(self, command: str) -> TaskSpec | None:
        if self._onboarding_task_factory is None or self._onboarding_document_parser is None:
            return None
        lowered = command.lower()
        if not (
            ("\u65b0\u7528\u6237" in command and ("\u4e00\u952e\u5b89\u88c5" in command or "\u5b89\u88c5" in command))
            or "new user" in lowered
            or "one click install" in lowered
        ):
            return None
        installer_path = _extract_first_windows_path(command, (".exe", ".msi"))
        source_path = _extract_first_windows_path(command, (".csv", ".txt", ".docx", ".xlsx"))
        if source_path is None:
            all_paths = _extract_windows_any_paths(command)
            if installer_path and len(all_paths) >= 2:
                source_path = next((path for path in all_paths if path != installer_path), None)
        if not installer_path or not source_path:
            return None
        return self.build_new_user_setup(installer_path, source_path)

    def _build_daily_routine_task(self, command: str) -> TaskSpec | None:
        lowered = command.lower()
        has_all_apps = "clash" in lowered and "adspower" in lowered and ("dbit" in lowered or "octopus" in lowered)
        has_daily_keywords = any(token in command for token in ("\u65e5\u5e38", "\u542f\u52a8\u6211\u7684\u5de5\u4f5c", "\u5f00\u5c55\u6211\u7684\u65e5\u5e38\u5de5\u4f5c"))
        has_tiktok = any(token in lowered for token in ("tiktok", "tik tok", "tiki tok"))
        if not (has_all_apps or (has_daily_keywords and has_tiktok)):
            return None

        steps = [
            TaskStep(
                adapter="clash_verge",
                action="prepare_global_proxy",
                description="\u51c6\u5907 Clash Verge \u5168\u5c40\u4ee3\u7406\u73af\u5883",
            ),
            TaskStep(
                adapter="adspower",
                action="ensure_service_ready",
                description="\u542f\u52a8 AdsPower \u5e76\u7b49\u5f85\u672c\u5730\u670d\u52a1\u5c31\u7eea",
            ),
            TaskStep(
                adapter="dbit_octopus",
                action="run_ui_flow",
                description="\u542f\u52a8 DBIt Octopus \u5e76\u6253\u5f00 TikTok \u9009\u9879\u5361",
                params={"flow_name": "startup"},
            ),
        ]
        return TaskSpec(
            user_command=command,
            target="daily_routine",
            summary="\u5df2\u89c4\u5212\u65e5\u5e38\u542f\u52a8\u5de5\u4f5c\u6d41\u3002",
            steps=steps,
        )

    def _build_clash_task(self, command: str, target: str) -> TaskSpec | None:
        if target != "clash_verge":
            return None

        lowered = command.lower()
        if not any(token in lowered for token in ("clash", "verge")) and not any(
            token in command for token in ("\u4ee3\u7406", "\u5168\u5c40", "\u865a\u62df\u7f51\u5361")
        ):
            return None

        return TaskSpec(
            user_command=command,
            target="clash_verge",
            summary="\u5df2\u89c4\u5212 Clash Verge \u5168\u5c40\u4ee3\u7406\u51c6\u5907\u4efb\u52a1\u3002",
            steps=[
                TaskStep(
                    adapter="clash_verge",
                    action="prepare_global_proxy",
                    description="\u51c6\u5907 Clash Verge \u5168\u5c40\u4ee3\u7406\u73af\u5883",
                )
            ],
        )

    def _build_wps_task(self, command: str, target: str) -> TaskSpec | None:
        if target != "wps":
            return None

        paths = _extract_windows_paths(command, (".xlsx", ".xlsm", ".xls"))
        workbook_path = paths[0] if paths else None
        if not workbook_path:
            return None

        output_path = paths[1] if len(paths) > 1 else None
        sheet_name = _extract_sheet_name(command)
        column = _extract_column_ref(command)
        duplicate_keywords = (
            "duplicate",
            "repeat",
            "\u91cd\u590d",
            "\u53bb\u91cd",
        )
        duplicates_requested = any(token in command.lower() for token in duplicate_keywords[:2]) or any(
            token in command for token in duplicate_keywords[2:]
        )

        if column and duplicates_requested:
            step = TaskStep(
                adapter="wps",
                action="count_duplicates_in_column",
                description="\u7edf\u8ba1\u5de5\u4f5c\u7c3f\u6307\u5b9a\u5217\u7684\u91cd\u590d\u503c\u5e76\u5bfc\u51fa\u62a5\u544a",
                params={
                    "workbook_path": workbook_path,
                    "sheet_name": sheet_name,
                    "column": column or "A",
                    "output_path": output_path,
                    "skip_header": True,
                },
            )
            return TaskSpec(
                user_command=command,
                target="wps",
                summary="\u5df2\u89c4\u5212\u8868\u683c\u91cd\u590d\u503c\u7edf\u8ba1\u4efb\u52a1\u3002",
                steps=[step],
            )

        step = TaskStep(
            adapter="wps",
            action="inspect_workbook",
            description="\u68c0\u67e5\u5de5\u4f5c\u7c3f\u7684\u5de5\u4f5c\u8868\u7ed3\u6784",
            params={"workbook_path": workbook_path},
        )
        return TaskSpec(
            user_command=command,
            target="wps",
            summary="\u5df2\u89c4\u5212\u5de5\u4f5c\u7c3f\u7ed3\u6784\u68c0\u67e5\u4efb\u52a1\u3002",
            steps=[step],
        )

    def _build_adspower_task(self, command: str, target: str) -> TaskSpec | None:
        if target != "adspower":
            return None

        url = _extract_url(command)
        profile_id = _extract_labeled_value(
            command,
            (
                r"profile[_ ]?id\s*[:\uFF1A]?\s*([A-Za-z0-9_-]+)",
                r"user[_ ]?id\s*[:\uFF1A]?\s*([A-Za-z0-9_-]+)",
                r"\u8d44\u6599id\s*[:\uFF1A]?\s*([A-Za-z0-9_-]+)",
            ),
        )
        profile_no = _extract_labeled_value(
            command,
            (
                r"profile[_ ]?no\s*[:\uFF1A]?\s*([A-Za-z0-9_-]+)",
                r"serial(?:[_ ]?number)?\s*[:\uFF1A]?\s*([A-Za-z0-9_-]+)",
                r"\u7f16\u53f7\s*[:\uFF1A]?\s*([A-Za-z0-9_-]+)",
            ),
        )
        if not profile_id and not profile_no:
            return None

        if url:
            step = TaskStep(
                adapter="adspower",
                action="open_profile_url",
                description="\u542f\u52a8 AdsPower \u8d44\u6599\u5e76\u6253\u5f00\u76ee\u6807\u7f51\u5740",
                params={
                    "profile_id": profile_id,
                    "profile_no": profile_no,
                    "url": url,
                    "close_after": True,
                },
            )
            summary = "\u5df2\u89c4\u5212 AdsPower \u6253\u5f00\u76ee\u6807\u7f51\u9875\u4efb\u52a1\u3002"
        else:
            step = TaskStep(
                adapter="adspower",
                action="start_profile",
                description="\u542f\u52a8 AdsPower \u8d44\u6599",
                params={"profile_id": profile_id, "profile_no": profile_no},
            )
            summary = "\u5df2\u89c4\u5212 AdsPower \u8d44\u6599\u542f\u52a8\u4efb\u52a1\u3002"

        return TaskSpec(
            user_command=command,
            target="adspower",
            summary=summary,
            steps=[step],
        )

    def _build_dbit_task(self, command: str, target: str) -> TaskSpec | None:
        if target != "dbit_octopus":
            return None

        lowered = command.lower()
        flow_name = ""
        if "login" in lowered or "\u767b\u5f55" in command:
            flow_name = "login"
        elif "search" in lowered or "\u67e5\u8be2" in command or "\u641c\u7d22" in command:
            flow_name = "search"
        elif "export" in lowered or "\u5bfc\u51fa" in command:
            flow_name = "export"

        if not flow_name:
            return None

        return TaskSpec(
            user_command=command,
            target="dbit_octopus",
            summary=f"\u5df2\u89c4\u5212 DBIt Octopus \u6d41\u7a0b\uff1a{flow_name}\u3002",
            steps=[
                TaskStep(
                    adapter="dbit_octopus",
                    action="run_ui_flow",
                    description=f"\u6267\u884c DBIt Octopus \u6d41\u7a0b\uff1a{flow_name}",
                    params={"flow_name": flow_name},
                )
            ],
        )


def _extract_windows_paths(command: str, suffixes: tuple[str, ...]) -> list[str]:
    matches: list[str] = []
    quoted_pattern = r'["\']([A-Za-z]:\\[^"\']+)["\']'
    for match in re.finditer(quoted_pattern, command):
        value = match.group(1)
        if value.lower().endswith(suffixes):
            matches.append(value)
    plain_pattern = r"([A-Za-z]:\\[^\s\"']+\.(?:xlsx|xlsm|xls))"
    for match in re.finditer(plain_pattern, command):
        value = match.group(1)
        if value not in matches:
            matches.append(value)
    return matches


def _extract_windows_any_paths(command: str) -> list[str]:
    matches: list[str] = []
    quoted_pattern = r'["\']([A-Za-z]:\\[^"\']+)["\']'
    for match in re.finditer(quoted_pattern, command):
        value = match.group(1)
        if value not in matches:
            matches.append(value)
    return matches


def _extract_first_windows_path(command: str, suffixes: tuple[str, ...]) -> str | None:
    paths = _extract_windows_paths(command, suffixes)
    return paths[0] if paths else None


def _extract_sheet_name(command: str) -> str | None:
    for pattern in (
        r"\b(sheet[0-9A-Za-z_]*)\b",
        r"sheet\s*[:\uFF1A]?\s*([A-Za-z0-9_]+)",
        r"\u5de5\u4f5c\u8868\s*[:\uFF1A]?\s*([A-Za-z0-9_]+)",
    ):
        match = re.search(pattern, command, flags=re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def _extract_column_ref(command: str) -> str | None:
    for pattern in (
        r"\bcolumn\s*[:\uFF1A]?\s*([A-Z]+)\b",
        r"\bcol\s*[:\uFF1A]?\s*([A-Z]+)\b",
        r"([A-Z]+)\s*\u5217",
    ):
        match = re.search(pattern, command, flags=re.IGNORECASE)
        if match:
            return match.group(1).upper()
    return None


def _extract_url(command: str) -> str | None:
    match = re.search(r"(https?://[^\s]+)", command, flags=re.IGNORECASE)
    return match.group(1) if match else None


def _extract_labeled_value(command: str, patterns: tuple[str, ...]) -> str | None:
    for pattern in patterns:
        match = re.search(pattern, command, flags=re.IGNORECASE)
        if match:
            return match.group(1)
    return None
