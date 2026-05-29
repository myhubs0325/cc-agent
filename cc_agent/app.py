from __future__ import annotations

import logging
import sys

from PySide6.QtWidgets import QApplication

from cc_agent.bootstrap import build_context
from cc_agent.ui.main_window import MainWindow
from cc_agent.utils.logging import configure_logging
from cc_agent.utils.runtime import resolve_runtime_roots
from cc_agent.utils.startup import resolve_startup_log_path, show_startup_error


def main() -> int:
    runtime_roots = resolve_runtime_roots()
    startup_log_path = resolve_startup_log_path(runtime_roots.data_root)
    configure_logging(startup_log_path)

    try:
        context = build_context(runtime_roots.install_root, runtime_roots.data_root)

        app = QApplication(sys.argv)
        app.setStyle("Fusion")
        window = MainWindow(context)
        window.show()
        return app.exec()
    except Exception as exc:
        logging.getLogger(__name__).exception("Application startup failed.")
        show_startup_error(
            (
                f"启动失败：{exc}\n\n"
                f"详细日志：{startup_log_path}\n"
                "如果本地配置或数据库损坏，可删除该目录下对应文件后重试。"
            ),
            title="CC 本地智能体启动失败",
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
