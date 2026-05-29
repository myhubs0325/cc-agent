from __future__ import annotations

from PySide6.QtWidgets import QMessageBox, QWidget


class ConfirmDialog:
    @staticmethod
    def ask(parent: QWidget | None, reasons: list[str]) -> bool:
        box = QMessageBox(parent)
        box.setWindowTitle("\u9700\u8981\u786e\u8ba4")
        box.setIcon(QMessageBox.Warning)
        box.setText("\u8fd9\u4e2a\u4efb\u52a1\u5728\u6267\u884c\u524d\u9700\u8981\u4f60\u786e\u8ba4\u3002")
        box.setInformativeText("\n".join(reasons))
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        box.setDefaultButton(QMessageBox.No)
        return box.exec() == QMessageBox.Yes
