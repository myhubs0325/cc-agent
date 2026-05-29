from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from cc_agent.onboarding import NewUserSetupRequest, OnboardingDocumentParser, ParsedOnboardingSource
from cc_agent.ui.styles import apply_card_effect


class OnboardingWizardWidget(QWidget):
    submitted = Signal(object)
    cancelled = Signal()

    def __init__(self, document_parser: OnboardingDocumentParser, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._document_parser = document_parser
        self._parsed_source: ParsedOnboardingSource | None = None

        self._installer_input = QLineEdit()
        self._installer_input.setPlaceholderText("选择 DBit Octopus 安装包")

        self._source_input = QLineEdit()
        self._source_input.setPlaceholderText("选择资料文件或模板目录")
        self._source_input.textChanged.connect(lambda _text: self._clear_parsed_source())

        self._preview = QPlainTextEdit()
        self._preview.setObjectName("wizardPreview")
        self._preview.setReadOnly(True)
        self._preview.setPlaceholderText("解析完成后，这里会显示关键字段、代理模式和提醒。")

        card = QFrame()
        card.setObjectName("taskCard")
        apply_card_effect(card)

        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(28, 28, 28, 28)
        card_layout.setSpacing(20)
        card_layout.addWidget(self._build_header())
        card_layout.addLayout(self._build_steps())
        card_layout.addLayout(self._build_form())
        card_layout.addWidget(self._preview, 1)
        card_layout.addLayout(self._build_actions())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(card)

    def reset(self) -> None:
        self._installer_input.clear()
        self._source_input.clear()
        self._preview.clear()
        self._parsed_source = None

    def _build_header(self) -> QWidget:
        wrapper = QWidget()
        layout = QVBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        title = QLabel("一键安装向导")
        title.setObjectName("panelTitle")

        helper = QLabel("把资料准备、识别确认和执行入口放在同一页，不再额外弹出提示窗口。")
        helper.setObjectName("mutedLabel")
        helper.setWordWrap(True)

        layout.addWidget(title)
        layout.addWidget(helper)
        return wrapper

    def _build_steps(self) -> QHBoxLayout:
        layout = QHBoxLayout()
        layout.setSpacing(10)
        for text in ("1 选择安装包", "2 选择资料", "3 确认并开始"):
            chip = QLabel(text)
            chip.setObjectName("chipLabel")
            layout.addWidget(chip)
        layout.addStretch(1)
        return layout

    def _build_form(self) -> QGridLayout:
        form = QGridLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(14)
        form.addWidget(self._surface_label("安装包路径"), 0, 0)
        form.addWidget(self._installer_input, 0, 1)
        form.addLayout(self._installer_buttons(), 0, 2)
        form.addWidget(self._surface_label("资料路径"), 1, 0)
        form.addWidget(self._source_input, 1, 1)
        form.addLayout(self._source_buttons(), 1, 2)
        return form

    def _build_actions(self) -> QHBoxLayout:
        back_button = QPushButton("返回")
        back_button.setObjectName("ghostButton")
        back_button.clicked.connect(self.cancelled.emit)

        parse_button = QPushButton("解析资料")
        parse_button.setObjectName("secondaryButton")
        parse_button.clicked.connect(self._parse_source)

        start_button = QPushButton("确认并开始")
        start_button.setObjectName("primaryButton")
        start_button.clicked.connect(self._submit)

        layout = QHBoxLayout()
        layout.setSpacing(10)
        layout.addWidget(back_button)
        layout.addStretch(1)
        layout.addWidget(parse_button)
        layout.addWidget(start_button)
        return layout

    def _installer_buttons(self) -> QHBoxLayout:
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        button = QPushButton("选择文件")
        button.setObjectName("ghostButton")
        button.clicked.connect(self._choose_installer)
        layout.addWidget(button)
        return layout

    def _source_buttons(self) -> QHBoxLayout:
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)

        file_button = QPushButton("选择文件")
        file_button.setObjectName("ghostButton")
        file_button.clicked.connect(self._choose_source_file)

        folder_button = QPushButton("选择目录")
        folder_button.setObjectName("ghostButton")
        folder_button.clicked.connect(self._choose_source_directory)

        layout.addWidget(file_button)
        layout.addWidget(folder_button)
        return layout

    def _choose_installer(self) -> None:
        path = self._open_file_dialog(
            title="选择安装包",
            name_filter="安装包 (*.exe *.msi);;所有文件 (*.*)",
        )
        if path:
            self._installer_input.setText(path)

    def _choose_source_file(self) -> None:
        path = self._open_file_dialog(
            title="选择资料文件",
            name_filter="资料文件 (*.csv *.txt *.docx *.xlsx);;所有文件 (*.*)",
        )
        if path:
            self._source_input.setText(path)

    def _choose_source_directory(self) -> None:
        path = self._open_directory_dialog("选择资料模板目录")
        if path:
            self._source_input.setText(path)

    def _parse_source(self) -> None:
        source_path = self._source_input.text().strip()
        if not source_path:
            self._show_inline_message("资料缺失", "请先选择资料文件或模板目录。")
            return
        try:
            parsed = self._document_parser.parse(source_path)
        except Exception as exc:
            self._show_inline_message("解析失败", str(exc))
            return
        self._parsed_source = parsed
        self._preview.setPlainText(self._build_preview(parsed))

    def _submit(self) -> None:
        installer_path = self._installer_input.text().strip()
        source_path = self._source_input.text().strip()
        if not installer_path:
            self._show_inline_message("安装包缺失", "请先选择安装包。")
            return
        if not source_path:
            self._show_inline_message("资料缺失", "请先选择资料文件或模板目录。")
            return
        if self._parsed_source is None:
            self._parse_source()
            if self._parsed_source is None:
                return
        request = NewUserSetupRequest(
            installer_path=str(Path(installer_path).expanduser()),
            source_path=str(Path(source_path).expanduser()),
            parsed_source=self._parsed_source,
        )
        self.submitted.emit(request)

    def _clear_parsed_source(self) -> None:
        self._parsed_source = None
        self._preview.clear()

    def _show_inline_message(self, title: str, message: str) -> None:
        self._preview.setPlainText(f"[{title}]\n{message}")

    @staticmethod
    def _build_preview(parsed_source: ParsedOnboardingSource) -> str:
        profile = parsed_source.profile
        preview_lines = [
            f"资料类型: {parsed_source.source_kind}",
            f"公司/品牌: {profile.company_name or profile.customer_name or '-'}",
            f"平台: {profile.platform or '-'}",
            f"代理类型: {profile.proxy_mode or '未指定'}",
            f"AdsPower 账号: {profile.adspower_username or '未填写'}",
            f"代理用户 ID: {profile.proxy_user_id or '-'}",
            "",
            "已识别字段:",
        ]
        for candidate in parsed_source.candidates[:20]:
            mapped = f" -> {candidate.mapped_field}" if candidate.mapped_field else ""
            preview_lines.append(f"{candidate.source_label}: {candidate.value}{mapped}")
        if parsed_source.warnings:
            preview_lines.extend(["", "提醒:"])
            preview_lines.extend(f"- {warning}" for warning in parsed_source.warnings)
        return "\n".join(preview_lines)

    @staticmethod
    def _surface_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("surfaceTitle")
        return label

    def _open_file_dialog(self, *, title: str, name_filter: str) -> str:
        dialog = QFileDialog(self, title)
        dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
        dialog.setNameFilter(name_filter)
        dialog.setOption(QFileDialog.Option.DontUseNativeDialog, True)
        dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
        if dialog.exec():
            files = dialog.selectedFiles()
            if files:
                return files[0]
        return ""

    def _open_directory_dialog(self, title: str) -> str:
        dialog = QFileDialog(self, title)
        dialog.setFileMode(QFileDialog.FileMode.Directory)
        dialog.setOption(QFileDialog.Option.ShowDirsOnly, True)
        dialog.setOption(QFileDialog.Option.DontUseNativeDialog, True)
        dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
        if dialog.exec():
            files = dialog.selectedFiles()
            if files:
                return files[0]
        return ""
