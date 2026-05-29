from __future__ import annotations

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QGraphicsDropShadowEffect, QWidget


def build_stylesheet(theme: str = "light") -> str:
    palette = _dark_palette() if theme == "dark" else _light_palette()
    return f"""
QMainWindow, QWidget {{
    background: {palette["window_bg"]};
    color: {palette["text"]};
    font-family: "SF Pro Display", "Segoe UI", "PingFang SC", "Microsoft YaHei";
    font-size: 10.5pt;
}}

QFrame#heroPanel,
QFrame#statusCard,
QFrame#logCard,
QFrame#taskCard,
QFrame#historyCard,
QFrame#metricCard {{
    background: {palette["card_bg"]};
    border: 1px solid {palette["card_border"]};
    border-radius: 22px;
}}

QFrame#heroPanel {{
    background: {palette["hero_bg"]};
    border-color: {palette["hero_border"]};
}}

QLabel#eyebrowLabel {{
    color: {palette["accent"]};
    font-size: 8.6pt;
    font-weight: 700;
}}

QLabel#heroTitle {{
    color: {palette["text_strong"]};
    font-size: 25pt;
    font-weight: 700;
}}

QLabel#heroSubtitle,
QLabel#mutedLabel {{
    color: {palette["text_muted"]};
    font-size: 10pt;
}}

QLabel#chipLabel {{
    background: {palette["chip_bg"]};
    color: {palette["chip_text"]};
    padding: 7px 12px;
    border: 1px solid {palette["chip_border"]};
    border-radius: 13px;
    font-size: 8.9pt;
    font-weight: 600;
}}

QLabel#panelTitle {{
    color: {palette["text_strong"]};
    font-size: 13.5pt;
    font-weight: 700;
}}

QLabel#metricLabel {{
    color: {palette["text_soft"]};
    font-size: 8.8pt;
    font-weight: 600;
}}

QLabel#metricValue {{
    color: {palette["text_strong"]};
    font-size: 17pt;
    font-weight: 700;
}}

QLabel#statusBadge {{
    color: white;
    padding: 6px 12px;
    border-radius: 12px;
    font-size: 8.8pt;
    font-weight: 700;
}}

QLabel#statusSummary,
QLabel#surfaceTitle {{
    color: {palette["text_strong"]};
    font-size: 10pt;
    font-weight: 600;
}}

QLineEdit,
QTextEdit#commandEditor,
QPlainTextEdit#logOutput,
QPlainTextEdit#wizardPreview,
QPlainTextEdit#fieldPreview,
QListWidget#historyList {{
    background: {palette["surface_bg"]};
    border: 1px solid {palette["surface_border"]};
    border-radius: 18px;
    padding: 12px 14px;
    selection-background-color: {palette["accent"]};
}}

QLineEdit {{
    min-height: 22px;
}}

QPlainTextEdit#logOutput,
QPlainTextEdit#wizardPreview,
QPlainTextEdit#fieldPreview {{
    font-family: "Cascadia Mono", "Consolas";
    font-size: 9.2pt;
}}

QPushButton {{
    border-radius: 16px;
    padding: 10px 18px;
    font-weight: 700;
    min-height: 22px;
}}

QPushButton#primaryButton {{
    background: {palette["accent"]};
    color: white;
    border: 1px solid {palette["accent_border"]};
}}

QPushButton#primaryButton:hover {{
    background: {palette["accent_hover"]};
}}

QPushButton#successButton {{
    background: {palette["button_success_bg"]};
    color: white;
    border: 1px solid {palette["button_success_border"]};
}}

QPushButton#successButton:hover {{
    background: {palette["button_success_hover"]};
}}

QPushButton#warningButton {{
    background: {palette["button_warning_bg"]};
    color: white;
    border: 1px solid {palette["button_warning_border"]};
}}

QPushButton#warningButton:hover {{
    background: {palette["button_warning_hover"]};
}}

QPushButton#secondaryButton {{
    background: {palette["button_secondary_bg"]};
    color: {palette["text_strong"]};
    border: 1px solid {palette["button_secondary_border"]};
}}

QPushButton#secondaryButton:hover,
QPushButton#ghostButton:hover {{
    background: {palette["button_hover_bg"]};
}}

QPushButton#ghostButton {{
    background: {palette["button_ghost_bg"]};
    color: {palette["text"]};
    border: 1px solid {palette["button_ghost_border"]};
}}

QPushButton:disabled {{
    background: {palette["button_disabled_bg"]};
    color: {palette["button_disabled_text"]};
    border: 1px solid {palette["button_disabled_bg"]};
}}

QListWidget#historyList {{
    outline: none;
    padding: 2px;
}}

QListWidget#historyList::item {{
    margin: 0 0 10px 0;
    padding: 14px 16px;
    background: {palette["history_item_bg"]};
    border: 1px solid {palette["history_item_border"]};
    border-radius: 16px;
}}

QListWidget#historyList::item:selected {{
    background: {palette["selection_bg"]};
    color: {palette["text_strong"]};
    border-color: {palette["selection_border"]};
}}

QScrollBar:vertical {{
    width: 10px;
    background: transparent;
    margin: 8px 3px 8px 0;
}}

QScrollBar::handle:vertical {{
    background: {palette["scrollbar"]};
    min-height: 36px;
    border-radius: 5px;
}}

QScrollBar::add-line:vertical,
QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical,
QScrollBar::sub-page:vertical {{
    background: none;
    border: none;
}}

QSplitter::handle {{
    background: transparent;
    width: 18px;
}}
"""


def _light_palette() -> dict[str, str]:
    return {
        "window_bg": "#f5f5f7",
        "card_bg": "#ffffff",
        "card_border": "rgba(15, 23, 42, 0.08)",
        "hero_bg": "qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #ffffff, stop:1 #f7f9fd)",
        "hero_border": "rgba(0, 113, 227, 0.10)",
        "surface_bg": "#fbfbfd",
        "surface_border": "rgba(15, 23, 42, 0.08)",
        "text": "#424245",
        "text_strong": "#1d1d1f",
        "text_muted": "#6e6e73",
        "text_soft": "#8e8e93",
        "accent": "#0071e3",
        "accent_border": "#0077ed",
        "accent_hover": "#0077ed",
        "button_success_bg": "#15803d",
        "button_success_border": "#166534",
        "button_success_hover": "#166534",
        "button_warning_bg": "#d97706",
        "button_warning_border": "#b45309",
        "button_warning_hover": "#b45309",
        "chip_bg": "#f2f7ff",
        "chip_text": "#0071e3",
        "chip_border": "rgba(0, 113, 227, 0.12)",
        "button_secondary_bg": "#ffffff",
        "button_secondary_border": "rgba(29, 29, 31, 0.10)",
        "button_hover_bg": "#f0f2f5",
        "button_ghost_bg": "rgba(255, 255, 255, 0.82)",
        "button_ghost_border": "rgba(29, 29, 31, 0.10)",
        "button_disabled_bg": "#e5e5ea",
        "button_disabled_text": "#8e8e93",
        "selection_bg": "#e8f3ff",
        "selection_border": "rgba(0, 113, 227, 0.18)",
        "divider": "rgba(15, 23, 42, 0.08)",
        "history_item_bg": "#ffffff",
        "history_item_border": "rgba(15, 23, 42, 0.08)",
        "scrollbar": "rgba(60, 60, 67, 0.36)",
    }


def _dark_palette() -> dict[str, str]:
    return {
        "window_bg": "#111214",
        "card_bg": "#1c1c1e",
        "card_border": "rgba(255, 255, 255, 0.08)",
        "hero_bg": "qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #242426, stop:1 #1c1c1e)",
        "hero_border": "rgba(64, 156, 255, 0.18)",
        "surface_bg": "#232326",
        "surface_border": "rgba(255, 255, 255, 0.08)",
        "text": "#ebebf0",
        "text_strong": "#ffffff",
        "text_muted": "#aeaeb2",
        "text_soft": "#8e8e93",
        "accent": "#0a84ff",
        "accent_border": "#2997ff",
        "accent_hover": "#2997ff",
        "button_success_bg": "#16a34a",
        "button_success_border": "#15803d",
        "button_success_hover": "#15803d",
        "button_warning_bg": "#f59e0b",
        "button_warning_border": "#d97706",
        "button_warning_hover": "#d97706",
        "chip_bg": "rgba(10, 132, 255, 0.14)",
        "chip_text": "#7fc0ff",
        "chip_border": "rgba(10, 132, 255, 0.18)",
        "button_secondary_bg": "#2c2c2e",
        "button_secondary_border": "rgba(255, 255, 255, 0.08)",
        "button_hover_bg": "#343438",
        "button_ghost_bg": "#2c2c2e",
        "button_ghost_border": "rgba(255, 255, 255, 0.08)",
        "button_disabled_bg": "#2a2a2d",
        "button_disabled_text": "#727276",
        "selection_bg": "#153c66",
        "selection_border": "rgba(64, 156, 255, 0.28)",
        "divider": "rgba(255, 255, 255, 0.08)",
        "history_item_bg": "#232326",
        "history_item_border": "rgba(255, 255, 255, 0.08)",
        "scrollbar": "rgba(235, 235, 245, 0.32)",
    }


def apply_card_effect(widget: QWidget, *, dark: bool = False) -> None:
    shadow = QGraphicsDropShadowEffect(widget)
    shadow.setBlurRadius(36)
    shadow.setOffset(0, 12)
    shadow.setColor(QColor(0, 0, 0, 90 if dark else 26))
    widget.setGraphicsEffect(shadow)
