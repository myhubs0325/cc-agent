from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from cc_agent.bootstrap import ApplicationContext
from cc_agent.domain.enums import RunStatus
from cc_agent.ui.onboarding_wizard import OnboardingWizardWidget
from cc_agent.ui.run_history import RunHistoryWidget
from cc_agent.ui.state_resolver import StatusOverride, resolve_operation_ui_state, status_text
from cc_agent.ui.styles import apply_card_effect, build_stylesheet
from cc_agent.ui.task_panel import TaskPanel


logger = logging.getLogger(__name__)


class _TaskWorker(QObject):
    finished = Signal(object)
    failed = Signal(str)
    progress = Signal(dict)

    def __init__(
        self,
        execute_fn,
        payload=None,
        *,
        keyword_payload: dict | None = None,
        has_positional_payload: bool = True,
    ) -> None:
        super().__init__()
        self._execute_fn = execute_fn
        self._payload = payload
        self._keyword_payload = keyword_payload or {}
        self._has_positional_payload = has_positional_payload

    def run(self) -> None:
        try:
            if self._has_positional_payload:
                result = self._execute_fn(self._payload, progress_callback=self.progress.emit, **self._keyword_payload)
            else:
                result = self._execute_fn(progress_callback=self.progress.emit, **self._keyword_payload)
        except Exception as exc:  # pragma: no cover - UI threading wrapper
            self.failed.emit(str(exc))
            return
        self.finished.emit(result)


class _LogEmitter(QObject):
    message = Signal(str)


class _QtLogHandler(logging.Handler):
    def __init__(self, emitter: _LogEmitter) -> None:
        super().__init__(level=logging.INFO)
        self._emitter = emitter

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
        except Exception:
            message = record.getMessage()
        self._emitter.message.emit(message)


class MainWindow(QMainWindow):
    def __init__(self, context: ApplicationContext) -> None:
        super().__init__()
        self._context = context
        self._current_theme = "light"
        self._hero_chips: dict[str, QLabel] = {}
        self._hero_subtitle: QLabel | None = None
        self._daily_start_available = self._configure_local_daily_start_support()
        self._busy = False
        self._status_override: StatusOverride | None = None
        self._checkpoint_in_use = False
        self._latest_runs = []
        self._worker_thread: QThread | None = None
        self._worker: _TaskWorker | None = None
        self._log_emitter = _LogEmitter()
        self._log_emitter.message.connect(self._append_runtime_log)
        self._runtime_log_handler = _QtLogHandler(self._log_emitter)
        self._runtime_log_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        logging.getLogger().addHandler(self._runtime_log_handler)

        self.setWindowTitle(context.app_config.title)
        self.resize(1400, 900)
        self.setStyleSheet(build_stylesheet(self._current_theme))

        self._task_panel = TaskPanel(show_daily_start=self._daily_start_available)
        self._task_panel.daily_start_requested.connect(self._handle_daily_start_requested)
        self._task_panel.onboarding_requested.connect(self._handle_onboarding_requested)
        self._task_panel.resume_requested.connect(self._handle_resume_requested)

        self._wizard_panel = OnboardingWizardWidget(self._context.onboarding_document_parser)
        self._wizard_panel.submitted.connect(self._handle_onboarding_submitted)
        self._wizard_panel.cancelled.connect(self._show_home_page)

        self._history = RunHistoryWidget()
        self._history_card = QFrame()
        self._history_card.setObjectName("historyCard")
        history_layout = QVBoxLayout(self._history_card)
        history_layout.setContentsMargins(22, 22, 22, 22)
        history_layout.addWidget(self._history)
        apply_card_effect(self._history_card)

        self._status_badge = QLabel("待命")
        self._status_badge.setObjectName("statusBadge")
        self._status_summary = QLabel("系统已就绪，可以开始任务。")
        self._status_summary.setObjectName("statusSummary")
        self._stage_summary = QLabel("当前阶段：空闲")
        self._stage_summary.setObjectName("mutedLabel")

        self._metric_run_count = self._build_metric_value("0")
        self._metric_checkpoint = self._build_metric_value("无")
        self._metric_artifacts = self._build_metric_value("0")

        self._log_output = QPlainTextEdit()
        self._log_output.setObjectName("logOutput")
        self._log_output.setReadOnly(True)
        self._log_output.setPlaceholderText("执行日志会显示在这里。")

        self._home_page = self._build_home_page()
        self._apply_daily_start_visibility()
        self._main_stack = QStackedWidget()
        self._main_stack.addWidget(self._home_page)
        self._main_stack.addWidget(self._wizard_panel)

        main_panel = QWidget()
        main_layout = QVBoxLayout(main_panel)
        main_layout.setSpacing(18)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(self._build_hero_panel())
        main_layout.addWidget(self._main_stack, 1)

        splitter = QSplitter()
        splitter.addWidget(main_panel)
        splitter.addWidget(self._history_card)
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 1)
        splitter.setHandleWidth(18)
        splitter.setChildrenCollapsible(False)
        splitter.setSizes([1080, 320])

        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(22, 22, 22, 22)
        container_layout.addWidget(splitter)
        self.setCentralWidget(container)

        self._refresh_history()

    def _handle_daily_start_requested(self) -> None:
        if not self._daily_start_available:
            self._set_status_override(
                RunStatus.PENDING,
                "当前未检测到可直接启动的 Clash Verge、AdsPower 和 DBit 本地环境。",
                "当前阶段：一键启动入口未启用",
            )
            self._append_log("一键启动未执行：未同时检测到 Clash Verge、AdsPower 和 DBit。")
            return
        command = "启动我的日常工作流程：先准备 Clash Verge 全局代理，再启动 AdsPower，然后打开 DBIt Octopus 的 TikTok 选项卡"
        self._append_log("正在规划: 一键启动")
        self._set_status_override(
            RunStatus.RUNNING,
            "正在规划一键启动任务。",
            "当前阶段：正在生成一键启动计划",
        )
        task = self._context.plan_builder.build(command)
        self._run_task(task)

    def _handle_onboarding_requested(self) -> None:
        if self._busy:
            self._append_log("忽略一键安装请求：当前任务仍在运行。")
            return
        self._wizard_panel.reset()
        self._main_stack.setCurrentWidget(self._wizard_panel)

    def _handle_onboarding_submitted(self, request) -> None:
        self._append_log("正在规划: 一键安装")
        self._set_status_override(
            RunStatus.RUNNING,
            "正在规划一键安装任务。",
            "当前阶段：正在生成安装计划",
        )
        task = self._context.plan_builder.build_new_user_setup(
            installer_path=request.installer_path,
            source_path=request.source_path,
            parsed_source=request.parsed_source,
        )
        self._show_home_page()
        self._run_task(task)

    def _handle_resume_requested(self) -> None:
        if self._busy:
            self._append_log("忽略继续等待任务请求：当前任务仍在运行。")
            return
        self._append_log("正在恢复上次等待任务。")
        try:
            checkpoint_record = self._context.run_repository.get_latest_checkpoint(target="new_user_setup")
        except Exception as exc:
            self._checkpoint_in_use = False
            self._set_status_override(RunStatus.FAILED, str(exc), "当前阶段：执行异常中断")
            self._append_log(f"继续等待任务失败：{exc}")
            self._refresh_history()
            return
        if checkpoint_record is None:
            self._checkpoint_in_use = False
            self._refresh_ui_state()
            self._append_log("当前没有可继续执行的等待任务。")
            return
        self._checkpoint_in_use = checkpoint_record is not None
        self._set_status_override(
            RunStatus.RUNNING,
            "正在从 checkpoint 继续执行。",
            "当前阶段：继续等待任务",
        )
        self._refresh_ui_state(checkpoint=checkpoint_record)
        if checkpoint_record is not None:
            self._append_log(f"继续阶段: {checkpoint_record.wait_reason}")
        self._start_worker(
            self._context.executor.resume_latest,
            keyword_payload={"target": "new_user_setup"},
            has_positional_payload=False,
        )

    def _run_task(self, task) -> None:
        review = self._context.safety_guard.review(task)
        if review.blocked:
            self._set_status_override(RunStatus.BLOCKED, "该任务被安全策略拦截。", "当前阶段：已拦截")
            self._append_log("任务已被安全策略拦截：")
            for reason in review.reasons:
                self._append_log(f"- {reason}")
            return
        if review.requires_confirmation:
            self._append_log("任务命中需要确认的安全规则，当前按无弹窗模式继续执行：")
            for reason in review.reasons:
                self._append_log(f"- {reason}")
        self._start_worker(self._context.executor.execute, task)

    def _start_worker(
        self,
        execute_fn,
        payload=None,
        *,
        keyword_payload: dict | None = None,
        has_positional_payload: bool = True,
    ) -> None:
        self._busy = True
        self._set_actions_enabled(False)
        self._refresh_ui_state()
        self._worker_thread = QThread(self)
        self._worker = _TaskWorker(
            execute_fn,
            payload,
            keyword_payload=keyword_payload,
            has_positional_payload=has_positional_payload,
        )
        self._worker.moveToThread(self._worker_thread)
        self._worker_thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._handle_worker_progress)
        self._worker.finished.connect(self._handle_worker_finished)
        self._worker.failed.connect(self._handle_worker_failed)
        self._worker.finished.connect(self._worker_thread.quit)
        self._worker.failed.connect(self._worker_thread.quit)
        self._worker_thread.finished.connect(self._cleanup_worker)
        self._worker_thread.start()

    def _handle_worker_progress(self, payload: dict) -> None:
        event = payload.get("event")
        if event == "step_started":
            step_index = int(payload.get("step_index", 0))
            total_steps = int(payload.get("total_steps", 0))
            step_description = str(payload.get("step_description", "")).strip()
            adapter = str(payload.get("adapter", "")).strip()
            action = str(payload.get("action", "")).strip()
            self._set_status_override(
                RunStatus.RUNNING,
                f"正在执行：{step_description}",
                f"当前阶段：步骤 {step_index}/{total_steps} · {step_description}",
            )
            self._append_log(f"执行中 [{step_index}/{total_steps}] {adapter}.{action} -> {step_description}")
            return
        if event == "step_finished":
            status = str(payload.get("status", "")).strip()
            message = str(payload.get("message", "")).strip()
            step_index = int(payload.get("step_index", 0))
            total_steps = int(payload.get("total_steps", 0))
            adapter = str(payload.get("adapter", "")).strip()
            action = str(payload.get("action", "")).strip()
            self._append_log(f"步骤完成 [{step_index}/{total_steps}] {adapter}.{action} -> {status}: {message}")

    def _handle_worker_finished(self, result) -> None:
        self._busy = False
        self._set_actions_enabled(True)
        self._checkpoint_in_use = False
        self._apply_result(result)

    def _handle_worker_failed(self, message: str) -> None:
        self._busy = False
        self._set_actions_enabled(True)
        self._checkpoint_in_use = False
        self._set_status_override(RunStatus.FAILED, message, "当前阶段：执行异常中断")
        self._append_log(f"执行失败：{message}")

    def _cleanup_worker(self) -> None:
        if self._worker is not None:
            self._worker.deleteLater()
        if self._worker_thread is not None:
            self._worker_thread.deleteLater()
        self._worker = None
        self._worker_thread = None

    def _apply_result(self, result) -> None:
        for step_result in result.step_results:
            self._append_log(f"{status_text(step_result.status)}: {step_result.message}")
        checkpoint_id = result.metadata.get("checkpoint_id")
        if checkpoint_id:
            self._append_log(
                f"已生成 checkpoint: {checkpoint_id} "
                f"({result.metadata.get('wait_reason', 'manual_input_required')})"
            )
        self._metric_artifacts.setText(str(len(result.artifacts)))
        if result.artifacts:
            self._append_log(f"产物文件: {', '.join(result.artifacts)}")
        self._set_status_override(
            result.status,
            result.summary,
            f"当前阶段：{status_text(result.status)}",
        )
        self._refresh_history()

        if result.status == RunStatus.WAITING_INPUT:
            self._append_log(f"任务进入等待状态：{result.summary}")
        elif result.status == RunStatus.FAILED:
            self._append_log(f"任务失败：{result.summary}")

    def _append_log(self, line: str) -> None:
        self._log_output.appendPlainText(line)

    def _append_runtime_log(self, line: str) -> None:
        if not line:
            return
        self._log_output.appendPlainText(line)

    def _refresh_history(self) -> None:
        self._latest_runs = self._context.run_repository.list_recent()
        self._history.set_runs(self._latest_runs)
        self._metric_run_count.setText(str(len(self._latest_runs)))
        self._refresh_ui_state()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        logging.getLogger().removeHandler(self._runtime_log_handler)
        self._runtime_log_handler.close()
        super().closeEvent(event)

    def _build_hero_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("heroPanel")
        apply_card_effect(panel)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(28, 26, 28, 26)
        layout.setSpacing(14)

        header = QHBoxLayout()
        header.setSpacing(12)

        left = QVBoxLayout()
        left.setSpacing(10)

        eyebrow = QLabel("CC LOCAL AGENT")
        eyebrow.setObjectName("eyebrowLabel")
        title = QLabel("更清晰的本地执行控制台")
        title.setObjectName("heroTitle")
        subtitle = QLabel(
            "把安装、配置、等待恢复和日志留痕放进一个界面。只保留最常用的两个主入口，其他状态信息放到右侧。"
        )
        self._hero_subtitle = subtitle
        subtitle.setWordWrap(True)
        subtitle.setObjectName("heroSubtitle")

        chips_row = QHBoxLayout()
        chips_row.setSpacing(10)
        for key, text in (
            ("daily_start", "一键启动"),
            ("onboarding", "一键安装"),
            ("resume", "等待恢复"),
            ("logs", "日志与蓝图"),
        ):
            chip = QLabel(text)
            chip.setObjectName("chipLabel")
            self._hero_chips[key] = chip
            chips_row.addWidget(chip)
        chips_row.addStretch(1)

        left.addWidget(eyebrow)
        left.addWidget(title)
        left.addWidget(subtitle)
        left.addLayout(chips_row)

        self._theme_button = QPushButton("切换深色")
        self._theme_button.setObjectName("ghostButton")
        self._theme_button.clicked.connect(self._toggle_theme)

        header.addLayout(left, 1)
        header.addWidget(self._theme_button, alignment=Qt.AlignmentFlag.AlignTop)
        layout.addLayout(header)
        return panel

    def _build_home_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(18)
        layout.addWidget(self._build_metrics_grid())
        layout.addWidget(self._task_panel)
        layout.addWidget(self._build_status_card())
        layout.addWidget(self._build_log_card(), 1)
        return page

    def _build_metrics_grid(self) -> QWidget:
        wrapper = QWidget()
        layout = QGridLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(16)
        layout.setVerticalSpacing(16)
        layout.addWidget(self._build_metric_card("最近任务", self._metric_run_count), 0, 0)
        layout.addWidget(self._build_metric_card("待继续任务", self._metric_checkpoint), 0, 1)
        layout.addWidget(self._build_metric_card("当前产物数", self._metric_artifacts), 0, 2)
        return wrapper

    def _build_metric_card(self, label_text: str, value_label: QLabel) -> QFrame:
        card = QFrame()
        card.setObjectName("metricCard")
        apply_card_effect(card)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(8)
        label = QLabel(label_text)
        label.setObjectName("metricLabel")
        layout.addWidget(label)
        layout.addWidget(value_label)
        return card

    def _build_status_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("statusCard")
        apply_card_effect(card)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(12)
        title = QLabel("当前任务状态")
        title.setObjectName("panelTitle")
        helper = QLabel("这里显示最近一次任务的状态、摘要、当前执行阶段和是否仍需人工接管。")
        helper.setObjectName("mutedLabel")
        helper.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(helper)
        layout.addWidget(self._status_badge, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self._status_summary)
        layout.addWidget(self._stage_summary)
        return card

    def _build_log_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("logCard")
        apply_card_effect(card)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(12)
        title = QLabel("执行日志")
        title.setObjectName("panelTitle")
        helper = QLabel("执行过程、等待原因、错误原因和产物路径都会写在这里。")
        helper.setObjectName("mutedLabel")
        helper.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(helper)
        layout.addWidget(self._log_output, 1)
        return card

    def _toggle_theme(self) -> None:
        self._current_theme = "dark" if self._current_theme == "light" else "light"
        self.setStyleSheet(build_stylesheet(self._current_theme))
        self._theme_button.setText("切换浅色" if self._current_theme == "dark" else "切换深色")

    def _show_home_page(self) -> None:
        self._main_stack.setCurrentWidget(self._home_page)

    def _set_actions_enabled(self, enabled: bool) -> None:
        self._wizard_panel.setEnabled(enabled)
        self._refresh_ui_state()

    def _set_status_override(self, status: RunStatus, summary: str, stage_summary: str) -> None:
        self._status_override = StatusOverride(status=status, summary=summary, stage_summary=stage_summary)
        self._refresh_ui_state()

    def _refresh_ui_state(self, checkpoint=None) -> None:
        latest_run = self._latest_runs[0] if self._latest_runs else None
        latest_checkpoint = checkpoint
        if latest_checkpoint is None:
            latest_checkpoint = self._context.run_repository.get_latest_checkpoint(target="new_user_setup")
        ui_state = resolve_operation_ui_state(
            busy=self._busy,
            latest_run=latest_run,
            latest_checkpoint=latest_checkpoint,
            override=self._status_override,
            checkpoint_in_use=self._checkpoint_in_use,
        )
        self._task_panel.apply_state(ui_state.task_panel)
        self._status_badge.setText(ui_state.badge_text)
        self._status_badge.setStyleSheet(f"background:{ui_state.badge_color};")
        self._status_summary.setText(ui_state.summary)
        self._stage_summary.setText(ui_state.stage_summary)
        self._metric_checkpoint.setText(ui_state.checkpoint_metric)

    @staticmethod
    def _build_metric_value(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("metricValue")
        return label

    def _apply_daily_start_visibility(self) -> None:
        self._task_panel.set_daily_start_visible(self._daily_start_available)
        daily_chip = self._hero_chips.get("daily_start")
        if daily_chip is not None:
            daily_chip.setVisible(self._daily_start_available)
        if self._hero_subtitle is not None:
            if self._daily_start_available:
                self._hero_subtitle.setText(
                    "把安装、配置、等待恢复和日志留痕放进一个界面。只保留最常用的两个主入口，其他状态信息放到右侧。"
                )
            else:
                self._hero_subtitle.setText(
                    "把安装、等待恢复和日志留痕放进一个界面。只有检测到本机已安装 Clash Verge、AdsPower 和 DBit 时，才显示一键启动入口。"
                )

    def _configure_local_daily_start_support(self) -> bool:
        clash_ready = self._configure_local_clash_verge_support()
        adspower_ready = self._has_local_adspower_support()
        dbit_ready = self._has_local_dbit_support()
        return clash_ready and adspower_ready and dbit_ready

    def _configure_local_clash_verge_support(self) -> bool:
        clash_config = self._context.app_config.integrations.setdefault("clash_verge", {})
        executable_path = self._resolve_local_clash_verge_executable(clash_config)
        verge_config_path = self._resolve_existing_path(
            [
                clash_config.get("verge_config_path"),
                _user_appdata_path("io.github.clash-verge-rev.clash-verge-rev", "verge.yaml"),
            ]
        )
        core_config_path = self._resolve_existing_path(
            [
                clash_config.get("core_config_path"),
                _user_appdata_path("io.github.clash-verge-rev.clash-verge-rev", "config.yaml"),
            ]
        )
        if executable_path is None or verge_config_path is None or core_config_path is None:
            return False

        clash_config["executable_path"] = str(executable_path)
        clash_config["verge_config_path"] = str(verge_config_path)
        clash_config["core_config_path"] = str(core_config_path)

        clash_adapter = self._context.registry.get("clash_verge")
        adapter_config = getattr(clash_adapter, "_config", None)
        if isinstance(adapter_config, dict):
            adapter_config.update(clash_config)
        return True

    def _has_local_adspower_support(self) -> bool:
        adapter = self._context.registry.get("adspower")
        resolver = getattr(adapter, "_resolved_executable_path", None)
        if not callable(resolver):
            return False
        try:
            path = resolver()
        except Exception:
            return False
        return isinstance(path, Path) and path.exists()

    def _has_local_dbit_support(self) -> bool:
        adapter = self._context.registry.get("dbit_octopus")
        resolver = getattr(adapter, "_resolve_existing_executable_path", None)
        if not callable(resolver):
            return False
        try:
            path = resolver()
        except Exception:
            return False
        return isinstance(path, Path) and path.exists()

    def _resolve_local_clash_verge_executable(self, clash_config: dict) -> Path | None:
        program_files = os.getenv("ProgramFiles")
        program_files_x86 = os.getenv("ProgramFiles(x86)")
        local_app_data = os.getenv("LOCALAPPDATA")
        which_path = shutil.which("clash-verge.exe")
        return self._resolve_existing_path(
            [
                clash_config.get("executable_path"),
                which_path,
                Path(program_files) / "Clash Verge" / "clash-verge.exe" if program_files else None,
                Path(program_files_x86) / "Clash Verge" / "clash-verge.exe" if program_files_x86 else None,
                Path(local_app_data) / "Programs" / "Clash Verge" / "clash-verge.exe" if local_app_data else None,
            ]
        )

    def _resolve_existing_path(self, candidates: list[object]) -> Path | None:
        for candidate in candidates:
            if candidate in (None, ""):
                continue
            path = Path(str(candidate)).expanduser()
            try:
                if path.exists():
                    return path
            except OSError:
                continue
        return None


def _user_appdata_path(*parts: str) -> Path | None:
    app_data = os.getenv("APPDATA")
    if not app_data:
        return None
    return Path(app_data).joinpath(*parts)
