from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from cc_agent.ui.state_resolver import TaskPanelState
from cc_agent.ui.styles import apply_card_effect


class TaskPanel(QWidget):
    daily_start_requested = Signal()
    onboarding_requested = Signal()
    resume_requested = Signal()

    def __init__(self, *, show_daily_start: bool = True) -> None:
        super().__init__()
        self._default_subtitle = "保留两个主入口：适合日常执行的一键启动，以及适合交付流程的一键安装。"
        self._fallback_subtitle = "当前未检测到可直接启动的 Clash Verge、AdsPower 和 DBit 环境，仅保留一键安装和继续等待任务入口。"

        self._title = QLabel("任务工作台")
        self._title.setObjectName("panelTitle")

        self._subtitle = QLabel(self._default_subtitle)
        self._subtitle.setObjectName("mutedLabel")
        self._subtitle.setWordWrap(True)

        self._daily_start_button = QPushButton("一键启动")
        self._daily_start_button.setObjectName("primaryButton")
        self._daily_start_button.clicked.connect(lambda _checked=False: self.daily_start_requested.emit())

        self._onboarding_button = QPushButton("一键安装")
        self._onboarding_button.setObjectName("successButton")
        self._onboarding_button.clicked.connect(lambda _checked=False: self.onboarding_requested.emit())

        self._resume_button = QPushButton("继续等待任务")
        self._resume_button.setObjectName("warningButton")
        self._resume_button.setEnabled(False)
        self._resume_button.clicked.connect(lambda _checked=False: self.resume_requested.emit())

        action_row = QHBoxLayout()
        action_row.setSpacing(12)
        action_row.addWidget(self._daily_start_button)
        action_row.addWidget(self._onboarding_button)
        action_row.addStretch(1)
        action_row.addWidget(self._resume_button)

        card = QFrame()
        card.setObjectName("taskCard")
        apply_card_effect(card)

        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(24, 24, 24, 24)
        card_layout.setSpacing(18)
        card_layout.addWidget(self._title)
        card_layout.addWidget(self._subtitle)
        card_layout.addSpacing(4)
        card_layout.addLayout(action_row)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(card)

        self.set_daily_start_visible(show_daily_start)

    def apply_state(self, state: TaskPanelState) -> None:
        self._daily_start_button.setEnabled(state.daily_start.enabled)
        self._daily_start_button.setToolTip(state.daily_start.tooltip)
        self._onboarding_button.setEnabled(state.onboarding.enabled)
        self._onboarding_button.setToolTip(state.onboarding.tooltip)
        self._resume_button.setEnabled(state.resume.enabled)
        self._resume_button.setToolTip(state.resume.tooltip)

    def set_daily_start_visible(self, visible: bool) -> None:
        self._daily_start_button.setVisible(visible)
        self._subtitle.setText(self._default_subtitle if visible else self._fallback_subtitle)
