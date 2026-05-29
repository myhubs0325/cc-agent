from __future__ import annotations

import ctypes
import subprocess
import time
from collections import deque
from importlib.util import find_spec
from pathlib import Path
from typing import Iterable

import win32con
import win32gui
from ctypes import wintypes

from pywinauto import Desktop
from pywinauto.base_wrapper import BaseWrapper
from pywinauto.keyboard import send_keys


class WindowsUiDriver:
    _SKIP_CHILDREN_CLASSES = {
        "Chrome_WidgetWin_0",
        "Chrome_WidgetWin_1",
        "Chrome_RenderWidgetHostHWND",
        "WRY_WEBVIEW",
        "Intermediate D3D Window",
    }

    def __init__(self) -> None:
        self._last_highlighted_rect: tuple[int, int, int, int] | None = None
        self._last_highlighted_at = 0.0

    def start_process(self, executable_path: str, startup_wait_seconds: float = 1.5) -> None:
        executable = Path(executable_path)
        if not executable.exists():
            raise ValueError(f"\u627e\u4e0d\u5230\u53ef\u6267\u884c\u6587\u4ef6: {executable}")
        if self._is_process_running(executable.name):
            if startup_wait_seconds > 0:
                time.sleep(min(startup_wait_seconds, 1.0))
            return
        subprocess.Popen([str(executable)], cwd=str(executable.parent))
        if startup_wait_seconds > 0:
            time.sleep(startup_wait_seconds)

    def connect(self, title_pattern: str, timeout_seconds: float = 20.0, backend: str = "uia") -> BaseWrapper:
        window = Desktop(backend=backend).window(title_re=title_pattern)
        window.wait("exists", timeout=timeout_seconds)
        return window.wrapper_object()

    def connect_any(
        self,
        title_patterns: Iterable[str],
        timeout_seconds: float = 20.0,
        backend: str = "uia",
    ) -> BaseWrapper:
        last_error: Exception | None = None
        for pattern in title_patterns:
            try:
                return self.connect(pattern, timeout_seconds=timeout_seconds, backend=backend)
            except Exception as exc:  # pragma: no cover - the caller only needs the last error
                last_error = exc
        if last_error is not None:
            raise last_error
        raise ValueError("\u672a\u63d0\u4f9b\u53ef\u7528\u7684\u7a97\u53e3\u6807\u9898\u5339\u914d\u89c4\u5219\u3002")

    def focus_window(self, wrapper: BaseWrapper) -> None:
        handle = self._handle(wrapper)
        if handle is not None:
            try:
                win32gui.ShowWindow(handle, win32con.SW_RESTORE)
            except Exception:
                pass
            try:
                win32gui.BringWindowToTop(handle)
            except Exception:
                pass
            try:
                win32gui.SetForegroundWindow(handle)
            except Exception:
                pass
        try:
            wrapper.restore()
        except Exception:
            pass
        try:
            wrapper.set_focus()
        except Exception:
            wrapper.set_focus()
        self.highlight_window(wrapper, duration_seconds=0.18)

    def iter_controls(
        self,
        root: BaseWrapper,
        max_depth: int = 4,
        max_nodes: int = 200,
        max_seconds: float = 3.0,
        skip_child_classes: Iterable[str] | None = None,
    ) -> Iterable[BaseWrapper]:
        skip_classes = set(self._SKIP_CHILDREN_CLASSES if skip_child_classes is None else skip_child_classes)
        queue: deque[tuple[BaseWrapper, int]] = deque([(root, 0)])
        emitted = 0
        deadline = time.monotonic() + max_seconds
        while queue and emitted < max_nodes:
            if time.monotonic() >= deadline:
                return
            node, depth = queue.popleft()
            if depth >= max_depth:
                continue
            if self._class_name(node) in skip_classes:
                continue
            try:
                children = list(node.children())
            except Exception:
                continue
            for child in children:
                emitted += 1
                yield child
                if emitted >= max_nodes:
                    return
                queue.append((child, depth + 1))

    def find_first_text_match(
        self,
        root: BaseWrapper,
        texts: Iterable[str],
        control_types: Iterable[str] | None = None,
        max_depth: int = 5,
        max_nodes: int = 240,
        skip_child_classes: Iterable[str] | None = None,
    ) -> BaseWrapper | None:
        lowered_texts = [text.casefold() for text in texts if text]
        allowed_types = {item for item in (control_types or []) if item}
        for effective_skip_classes in self._search_skip_variants(skip_child_classes):
            for control in self.iter_controls(
                root,
                max_depth=max_depth,
                max_nodes=max_nodes,
                skip_child_classes=effective_skip_classes,
            ):
                control_type = self._control_type(control)
                if allowed_types and control_type not in allowed_types:
                    continue
                display_text = self.read_text(control).casefold()
                if display_text and any(token in display_text for token in lowered_texts):
                    return control
        return None

    def click(self, control: BaseWrapper) -> None:
        self.highlight_control(control, duration_seconds=0.18)
        try:
            control.click_input()
            return
        except Exception:
            pass
        try:
            control.invoke()
            return
        except Exception:
            pass
        control.click()

    def click_text(
        self,
        root: BaseWrapper,
        texts: Iterable[str],
        control_types: Iterable[str] | None = None,
        max_depth: int = 5,
        max_nodes: int = 240,
        skip_child_classes: Iterable[str] | None = None,
    ) -> str:
        control = self.find_first_text_match(
            root,
            texts=texts,
            control_types=control_types,
            max_depth=max_depth,
            max_nodes=max_nodes,
            skip_child_classes=skip_child_classes,
        )
        if control is None:
            joined = ", ".join(texts)
            raise ValueError(f"\u672a\u627e\u5230\u5339\u914d\u7684\u63a7\u4ef6\u6587\u6848: {joined}")
        self.click(control)
        return self.read_text(control)

    def fill_labeled_input(
        self,
        root: BaseWrapper,
        labels: Iterable[str],
        value: str,
        *,
        label_control_types: Iterable[str] | None = None,
        input_control_types: Iterable[str] | None = None,
        max_depth: int = 6,
        max_nodes: int = 320,
        skip_child_classes: Iterable[str] | None = None,
    ) -> str:
        if not str(value).strip():
            return ""
        label = self.find_first_text_match(
            root,
            texts=labels,
            control_types=label_control_types or ["Text", "Document", "Edit", "Button", "Hyperlink"],
            max_depth=max_depth,
            max_nodes=max_nodes,
            skip_child_classes=skip_child_classes,
        )
        if label is None:
            joined = ", ".join(labels)
            raise ValueError(f"未找到标签文本: {joined}")
        input_control = self._find_input_near_label(
            label,
            input_control_types=input_control_types or ["Edit", "Document", "ComboBox"],
        )
        if input_control is None:
            joined = ", ".join(labels)
            raise ValueError(f"找到标签但未找到可输入控件: {joined}")
        self.set_text(input_control, value)
        return self.read_text(label)

    def set_text(self, control: BaseWrapper, value: str) -> None:
        normalized = str(value)
        if self._value_already_matches(control, normalized):
            return
        self.highlight_control(control, duration_seconds=0.18)
        try:
            control.set_focus()
        except Exception:
            pass
        for method_name in ("set_edit_text", "set_text", "select"):
            try:
                method = getattr(control, method_name)
            except Exception:
                continue
            try:
                method(normalized)
                return
            except Exception:
                continue
        self.click(control)
        try:
            control.type_keys("^a{BACKSPACE}", set_foreground=True)
            control.type_keys(normalized, with_spaces=True, set_foreground=True)
            return
        except Exception:
            pass
        send_keys("^a{BACKSPACE}")
        send_keys(normalized, with_spaces=True)

    def count_controls(
        self,
        root: BaseWrapper,
        control_types: Iterable[str],
        max_depth: int = 5,
        max_nodes: int = 240,
        skip_child_classes: Iterable[str] | None = None,
    ) -> int:
        allowed_types = {item for item in control_types if item}
        for effective_skip_classes in self._search_skip_variants(skip_child_classes):
            count = 0
            for control in self.iter_controls(
                root,
                max_depth=max_depth,
                max_nodes=max_nodes,
                skip_child_classes=effective_skip_classes,
            ):
                if self._control_type(control) in allowed_types:
                    count += 1
            if count > 0 or not effective_skip_classes:
                return count
        return 0

    def capture_window(self, wrapper: BaseWrapper, target_path: Path) -> Path | None:
        if find_spec("PIL") is None:
            return None
        try:
            image = wrapper.capture_as_image()
            target_path.parent.mkdir(parents=True, exist_ok=True)
            image.save(target_path)
            return target_path
        except Exception:
            return None

    def send_keys(self, text: str) -> None:
        send_keys(text)

    def highlight_window(self, wrapper: BaseWrapper, duration_seconds: float = 0.4) -> None:
        rect = self._control_rect(wrapper)
        if rect is not None:
            self.highlight_rect(rect, duration_seconds=duration_seconds)

    def highlight_control(self, control: BaseWrapper, duration_seconds: float = 0.4) -> None:
        rect = self._control_rect(control)
        if rect is not None:
            self.highlight_rect(rect, duration_seconds=duration_seconds)

    def highlight_rect(
        self,
        rect: tuple[int, int, int, int],
        *,
        duration_seconds: float = 0.4,
        flashes: int = 2,
    ) -> None:
        left, top, right, bottom = rect
        if right - left < 2 or bottom - top < 2:
            return
        now = time.monotonic()
        if self._last_highlighted_rect == rect and (now - self._last_highlighted_at) < 0.6:
            return
        self._last_highlighted_rect = rect
        self._last_highlighted_at = now
        user32 = None
        hdc = 0
        try:
            user32 = ctypes.windll.user32
            hdc = user32.GetWindowDC(0)
            if not hdc:
                return
            focus_rect = wintypes.RECT(left, top, right, bottom)
            effective_flashes = max(min(flashes, 1), 1)
            on_seconds = max(duration_seconds / max(effective_flashes * 2, 1), 0.03)
            off_seconds = min(on_seconds, 0.05)
            for index in range(effective_flashes):
                user32.DrawFocusRect(hdc, ctypes.byref(focus_rect))
                time.sleep(on_seconds)
                user32.DrawFocusRect(hdc, ctypes.byref(focus_rect))
                if index + 1 < effective_flashes:
                    time.sleep(off_seconds)
        except Exception:
            return
        finally:
            try:
                user32.ReleaseDC(0, hdc)
            except Exception:
                pass

    def read_text(self, control: BaseWrapper) -> str:
        text = ""
        try:
            text = control.window_text() or ""
        except Exception:
            text = ""
        if text:
            return text.strip()
        try:
            name = getattr(control.element_info, "name", "")
        except Exception:
            name = ""
        return str(name).strip()

    def _control_type(self, control: BaseWrapper) -> str:
        try:
            return str(getattr(control.element_info, "control_type", "") or "")
        except Exception:
            return ""

    def _class_name(self, control: BaseWrapper) -> str:
        try:
            return str(getattr(control.element_info, "class_name", "") or "")
        except Exception:
            return ""

    @staticmethod
    def _handle(control: BaseWrapper) -> int | None:
        for attr in ("handle",):
            try:
                value = getattr(control, attr)
            except Exception:
                value = None
            if isinstance(value, int) and value > 0:
                return value
        try:
            element_info = getattr(control, "element_info", None)
            value = getattr(element_info, "handle", None)
        except Exception:
            value = None
        return value if isinstance(value, int) and value > 0 else None

    @staticmethod
    def _control_rect(control: BaseWrapper) -> tuple[int, int, int, int] | None:
        try:
            rect = control.rectangle()
        except Exception:
            return None
        return rect.left, rect.top, rect.right, rect.bottom

    def _find_input_near_label(
        self,
        label_control: BaseWrapper,
        *,
        input_control_types: Iterable[str],
    ) -> BaseWrapper | None:
        allowed_types = {item for item in input_control_types if item}
        try:
            parent = label_control.parent()
        except Exception:
            parent = None
        if parent is not None:
            candidate = self._find_input_among_siblings(label_control, parent, allowed_types)
            if candidate is not None:
                return candidate
        return None

    def _find_input_among_siblings(
        self,
        label_control: BaseWrapper,
        parent: BaseWrapper,
        allowed_types: set[str],
    ) -> BaseWrapper | None:
        try:
            siblings = list(parent.children())
        except Exception:
            return None
        label_index = -1
        label_identity = self._element_identity(label_control)
        for index, sibling in enumerate(siblings):
            if self._element_identity(sibling) == label_identity:
                label_index = index
                break
        if label_index < 0:
            return None
        for sibling in siblings[label_index + 1 :]:
            direct_match = self._match_input_candidate(sibling, allowed_types)
            if direct_match is not None:
                return direct_match
        for sibling in siblings:
            direct_match = self._match_input_candidate(sibling, allowed_types)
            if direct_match is not None and self.read_text(sibling) != self.read_text(label_control):
                return direct_match
        return None

    def _match_input_candidate(
        self,
        control: BaseWrapper,
        allowed_types: set[str],
    ) -> BaseWrapper | None:
        if self._control_type(control) in allowed_types:
            return control
        for candidate in self.iter_controls(
            control,
            max_depth=4,
            max_nodes=40,
            max_seconds=1.0,
            skip_child_classes=[],
        ):
            if self._control_type(candidate) in allowed_types:
                return candidate
        return None

    def _element_identity(self, control: BaseWrapper) -> tuple[str, str, str]:
        try:
            automation_id = str(getattr(control.element_info, "automation_id", "") or "")
        except Exception:
            automation_id = ""
        return (
            self.read_text(control),
            self._control_type(control),
            automation_id,
        )

    def _value_already_matches(self, control: BaseWrapper, expected: str) -> bool:
        normalized = str(expected).strip().casefold()
        if not normalized:
            return False
        for candidate in self._candidate_display_values(control):
            if candidate.casefold() == normalized:
                return True
        return False

    def _candidate_display_values(self, control: BaseWrapper) -> list[str]:
        values: list[str] = []

        def push(raw: str) -> None:
            text = str(raw or "").strip()
            if text and text not in values:
                values.append(text)

        push(self.read_text(control))
        try:
            for child in control.children():
                push(self.read_text(child))
        except Exception:
            pass
        try:
            parent = control.parent()
        except Exception:
            parent = None
        if parent is not None:
            try:
                for sibling in parent.children():
                    push(self.read_text(sibling))
            except Exception:
                pass
        return values

    @staticmethod
    def _search_skip_variants(skip_child_classes: Iterable[str] | None) -> tuple[Iterable[str] | None, ...]:
        normalized = list(skip_child_classes or [])
        if not normalized:
            return (skip_child_classes,)
        return (skip_child_classes, [])

    @staticmethod
    def _is_process_running(image_name: str) -> bool:
        try:
            completed = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {image_name}"],
                check=False,
                capture_output=True,
                text=True,
            )
        except Exception:
            return False
        return image_name.lower() in (completed.stdout or "").lower()
