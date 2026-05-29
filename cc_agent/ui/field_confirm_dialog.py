from __future__ import annotations

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QPlainTextEdit, QVBoxLayout, QWidget

from cc_agent.onboarding import ParsedOnboardingSource


class FieldConfirmDialog(QDialog):
    def __init__(self, parsed_source: ParsedOnboardingSource, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("确认资料字段")
        self.resize(760, 600)

        title = QLabel("确认即将进入安装流程的关键字段")
        title.setObjectName("panelTitle")
        helper = QLabel("这里展示系统识别到的核心字段、代理模式和提醒。确认后才会真正开始执行。")
        helper.setObjectName("mutedLabel")
        helper.setWordWrap(True)

        preview = QPlainTextEdit()
        preview.setObjectName("fieldPreview")
        preview.setReadOnly(True)
        preview.setPlainText(self._build_preview(parsed_source))

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(14)
        layout.addWidget(title)
        layout.addWidget(helper)
        layout.addWidget(preview, 1)
        layout.addWidget(buttons)

    @staticmethod
    def _build_preview(parsed_source: ParsedOnboardingSource) -> str:
        profile = parsed_source.profile
        lines = [
            f"资料来源类型: {parsed_source.source_kind}",
            f"公司/客户: {profile.company_name or profile.customer_name or '-'}",
            f"Dbit 账号: {profile.dbit_username or '-'}",
            f"AdsPower 账号: {profile.adspower_username or '未填写'}",
            f"平台: {profile.platform or '-'}",
            f"平台账号: {profile.platform_username or '-'}",
            f"代理类型: {profile.proxy_mode or '未指定'}",
            f"代理用户ID: {profile.proxy_user_id or '-'}",
            f"代理地址: {(profile.proxy_host or '-') + ':' + (profile.proxy_port or '-') if profile.proxy_host or profile.proxy_port else '-'}",
            f"环境名称: {profile.environment_name or '-'}",
            f"安装目录: {profile.install_path or '-'}",
            "",
            "已识别字段:",
        ]
        if parsed_source.candidates:
            for candidate in parsed_source.candidates:
                mapped = f" -> {candidate.mapped_field}" if candidate.mapped_field else ""
                lines.append(f"- {candidate.source_label}: {candidate.value}{mapped}")
        else:
            lines.append("- 无")

        if parsed_source.warnings:
            lines.extend(["", "提醒:"])
            for warning in parsed_source.warnings:
                lines.append(f"- {warning}")
        return "\n".join(lines)

    @classmethod
    def confirm(cls, parent: QWidget | None, parsed_source: ParsedOnboardingSource) -> bool:
        dialog = cls(parsed_source, parent)
        return dialog.exec() == int(QDialog.DialogCode.Accepted)
