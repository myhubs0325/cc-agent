from __future__ import annotations

from PySide6.QtWidgets import QLabel, QListWidget, QListWidgetItem, QVBoxLayout, QWidget

from cc_agent.storage.repositories import RunRecord


def _status_label(status: str) -> str:
    return {
        "pending": "待处理",
        "running": "执行中",
        "succeeded": "已完成",
        "waiting_input": "等待输入",
        "failed": "执行失败",
        "blocked": "已拦截",
    }.get(status, status)


class RunHistoryWidget(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._title = QLabel("最近运行记录")
        self._title.setObjectName("panelTitle")

        self._subtitle = QLabel("这里保留最近的执行摘要。把鼠标停在条目上，可以看到原始指令。")
        self._subtitle.setWordWrap(True)
        self._subtitle.setObjectName("mutedLabel")

        self._count_label = QLabel("0 条记录")
        self._count_label.setObjectName("metricLabel")

        self._list = QListWidget()
        self._list.setObjectName("historyList")
        self._list.setSpacing(4)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        layout.addWidget(self._title)
        layout.addWidget(self._subtitle)
        layout.addWidget(self._count_label)
        layout.addWidget(self._list)

    def set_runs(self, runs: list[RunRecord]) -> None:
        self._count_label.setText(f"{len(runs)} 条记录")
        self._list.clear()
        for run in runs:
            item = QListWidgetItem(f"{run.created_at}  ·  {_status_label(run.status)}\n{run.summary}")
            item.setToolTip(run.command)
            self._list.addItem(item)
