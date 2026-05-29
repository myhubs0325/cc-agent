from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from cc_agent.domain.enums import RunStatus
from cc_agent.storage.repositories import RunCheckpointRecord, RunRecord


def status_text(status: RunStatus) -> str:
    return {
        RunStatus.PENDING: "待命",
        RunStatus.RUNNING: "执行中",
        RunStatus.SUCCEEDED: "已完成",
        RunStatus.WAITING_INPUT: "等待处理",
        RunStatus.FAILED: "失败",
        RunStatus.BLOCKED: "已拦截",
    }.get(status, status.value.upper())


def status_color(status: RunStatus) -> str:
    return {
        RunStatus.PENDING: "#64748b",
        RunStatus.RUNNING: "#2563eb",
        RunStatus.SUCCEEDED: "#059669",
        RunStatus.WAITING_INPUT: "#d97706",
        RunStatus.FAILED: "#dc2626",
        RunStatus.BLOCKED: "#7c3aed",
    }.get(status, "#64748b")


@dataclass(slots=True)
class ButtonState:
    enabled: bool
    tooltip: str = ""


@dataclass(slots=True)
class TaskPanelState:
    daily_start: ButtonState
    onboarding: ButtonState
    resume: ButtonState


@dataclass(slots=True)
class StatusOverride:
    status: RunStatus
    summary: str
    stage_summary: str


@dataclass(slots=True)
class OperationUiState:
    status: RunStatus
    badge_text: str
    badge_color: str
    summary: str
    stage_summary: str
    checkpoint_metric: str
    task_panel: TaskPanelState


_IDLE_SUMMARY = "系统已就绪，可以开始任务。"
_IDLE_STAGE = "当前阶段：空闲"
_RUNNING_TOOLTIP = "当前任务正在执行，请等待执行结束。"
_NO_CHECKPOINT_TOOLTIP = "当前没有待继续任务。"
_RESUME_IN_PROGRESS_TOOLTIP = "正在从上次等待点继续执行。"


def resolve_operation_ui_state(
    *,
    busy: bool,
    latest_run: RunRecord | None,
    latest_checkpoint: RunCheckpointRecord | None,
    override: StatusOverride | None = None,
    checkpoint_in_use: bool = False,
) -> OperationUiState:
    has_resumable_checkpoint = (
        latest_checkpoint is not None
        and not checkpoint_in_use
        and _is_checkpoint_active_for_latest_run(latest_checkpoint, latest_run)
    )

    if busy:
        display = override or StatusOverride(
            status=RunStatus.RUNNING,
            summary="任务正在执行，请等待完成。",
            stage_summary="当前阶段：执行中",
        )
    elif has_resumable_checkpoint and latest_checkpoint is not None:
        display = StatusOverride(
            status=RunStatus.WAITING_INPUT,
            summary=latest_checkpoint.summary,
            stage_summary="当前阶段：等待人工处理后继续",
        )
    elif override is not None and not (
        override.status == RunStatus.WAITING_INPUT and not has_resumable_checkpoint
    ):
        display = override
    elif latest_run is not None:
        status = _parse_run_status(latest_run.status)
        if status == RunStatus.WAITING_INPUT:
            display = StatusOverride(
                status=RunStatus.PENDING,
                summary="当前没有待继续任务，可开始新任务。",
                stage_summary=_IDLE_STAGE,
            )
        else:
            display = StatusOverride(
                status=status,
                summary=latest_run.summary,
                stage_summary=_stage_summary_for_status(status),
            )
    else:
        display = StatusOverride(
            status=RunStatus.PENDING,
            summary=_IDLE_SUMMARY,
            stage_summary=_IDLE_STAGE,
        )

    resume_tooltip = _NO_CHECKPOINT_TOOLTIP
    if has_resumable_checkpoint and latest_checkpoint is not None:
        resume_tooltip = latest_checkpoint.summary
    elif checkpoint_in_use:
        resume_tooltip = _RESUME_IN_PROGRESS_TOOLTIP

    running_action = ButtonState(enabled=not busy, tooltip="" if not busy else _RUNNING_TOOLTIP)
    task_panel = TaskPanelState(
        daily_start=running_action,
        onboarding=running_action,
        resume=ButtonState(enabled=has_resumable_checkpoint and not busy, tooltip=resume_tooltip),
    )

    checkpoint_metric = "处理中" if checkpoint_in_use else "待继续" if has_resumable_checkpoint else "无"

    return OperationUiState(
        status=display.status,
        badge_text=status_text(display.status),
        badge_color=status_color(display.status),
        summary=display.summary,
        stage_summary=display.stage_summary,
        checkpoint_metric=checkpoint_metric,
        task_panel=task_panel,
    )


def _parse_run_status(status: str) -> RunStatus:
    try:
        return RunStatus(status)
    except ValueError:
        return RunStatus.PENDING


def _stage_summary_for_status(status: RunStatus) -> str:
    if status == RunStatus.PENDING:
        return _IDLE_STAGE
    return f"当前阶段：{status_text(status)}"


def _is_checkpoint_active_for_latest_run(
    checkpoint: RunCheckpointRecord,
    latest_run: RunRecord | None,
) -> bool:
    if latest_run is None:
        return True
    try:
        checkpoint_created_at = datetime.fromisoformat(checkpoint.created_at)
        latest_run_created_at = datetime.fromisoformat(latest_run.created_at)
    except ValueError:
        return True
    return checkpoint_created_at >= latest_run_created_at
