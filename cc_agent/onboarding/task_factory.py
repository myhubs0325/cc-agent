from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from cc_agent.domain.models import TaskSpec, TaskStep
from cc_agent.onboarding.profile_schema import NewUserSetupContext, NewUserSetupRequest


_STEP_DEFINITIONS: tuple[tuple[str, str], ...] = (
    ("validate_setup_inputs", "校验安装包和资料来源"),
    ("parse_onboarding_source", "解析资料并写入安装上下文"),
    ("verify_adspower_ready", "检查 AdsPower 是否已就绪"),
    ("verify_proxy_ready", "检查代理资源是否已准备"),
    ("create_adspower_environments", "创建或补全 AdsPower 环境"),
    ("install_dbit_octopus", "安装或校验 DbitOCT"),
    ("prepare_non_vpn_environment", "执行非 VPN 的基础准备"),
    ("export_dbit_setup_blueprint", "生成 Dbit 配置蓝图"),
    ("open_dbit_configuration_workspace", "打开 Dbit 配置工作区"),
    ("apply_dbit_setup_blueprint", "写入 Dbit 配置蓝图"),
    ("finish_manual_handoff", "输出人工接管说明"),
)


class OnboardingTaskFactory:
    def __init__(self, context_dir: Path) -> None:
        self._context_dir = context_dir
        self._context_dir.mkdir(parents=True, exist_ok=True)

    def create(self, request: NewUserSetupRequest, user_command: str | None = None) -> TaskSpec:
        context = NewUserSetupContext(
            installer_path=request.installer_path,
            source_path=request.source_path,
            source_kind=request.parsed_source.source_kind,
            parsed_profile=request.parsed_source.profile,
            parse_warnings=request.parsed_source.warnings,
        )
        context_path = self._write_context(context)
        return self.build_task(
            context_path=context_path,
            user_command=user_command or f"新用户一键安装: {request.source_path}",
            resume_from=None,
        )

    def build_task(self, context_path: str, user_command: str, resume_from: str | None) -> TaskSpec:
        steps: list[TaskStep] = []
        start_index = 0
        if resume_from is not None:
            step_names = [name for name, _description in _STEP_DEFINITIONS]
            start_index = step_names.index(resume_from)
        for action, description in _STEP_DEFINITIONS[start_index:]:
            steps.append(
                TaskStep(
                    adapter="onboarding",
                    action=action,
                    description=description,
                    params={"context_path": context_path},
                )
            )
        return TaskSpec(
            user_command=user_command,
            target="new_user_setup",
            summary="已规划新用户一键安装向导任务。",
            steps=steps,
        )

    def build_resume_task(self, context_path: str, resume_from: str) -> TaskSpec:
        return self.build_task(
            context_path=context_path,
            user_command="继续新用户一键安装流程",
            resume_from=resume_from,
        )

    def _write_context(self, context: NewUserSetupContext) -> str:
        path = self._context_dir / f"new_user_setup_{uuid4().hex}.json"
        path.write_text(context.model_dump_json(indent=2), encoding="utf-8")
        return str(path)
