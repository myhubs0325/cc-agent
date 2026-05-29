from __future__ import annotations

import time
import winreg
from ctypes import WINFUNCTYPE, Structure, byref, c_size_t, create_unicode_buffer, sizeof, windll, wintypes
from pathlib import Path
from typing import Any
import subprocess

import yaml

from cc_agent.automation.windows.ui_driver import WindowsUiDriver
from cc_agent.domain.enums import RunStatus
from cc_agent.domain.models import StepResult, TaskStep
from cc_agent.integrations.base import BaseAdapter

TH32CS_SNAPPROCESS = 0x00000002
PROCESS_TERMINATE = 0x0001
SYNCHRONIZE = 0x00100000
INVALID_HANDLE_VALUE = c_size_t(-1).value


class PROCESSENTRY32W(Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


class ClashVergeAdapter(BaseAdapter):
    name = "clash_verge"
    _RELATED_PROCESS_NAMES = ("clash-verge.exe", "verge-mihomo.exe", "verge-mihomo-alpha.exe")

    def __init__(self, config: dict[str, Any], ui_driver: WindowsUiDriver | None = None) -> None:
        self._config = config
        self._ui_driver = ui_driver or WindowsUiDriver()

    @property
    def capabilities(self) -> list[str]:
        return ["prepare_global_proxy"]

    def execute(self, step: TaskStep) -> StepResult:
        if step.action != "prepare_global_proxy":
            return StepResult(
                step_id=step.id,
                status=RunStatus.SUCCEEDED,
                message="Clash Verge \u9002\u914d\u5668\u5df2\u63a5\u6536\u8be5\u8bf7\u6c42\u3002",
                data={"command": step.params.get("command", "")},
            )
        return self._prepare_global_proxy(step)

    def _prepare_global_proxy(self, step: TaskStep) -> StepResult:
        executable_path = Path(str(step.params.get("executable_path") or self._config.get("executable_path", "")).strip())
        verge_config_path = Path(str(step.params.get("verge_config_path") or self._config.get("verge_config_path", "")).strip())
        core_config_path = Path(str(step.params.get("core_config_path") or self._config.get("core_config_path", "")).strip())
        startup_wait_seconds = float(step.params.get("startup_wait_seconds") or self._config.get("startup_wait_seconds", 8))
        expected_proxy_server = str(
            step.params.get("system_proxy_server") or self._config.get("system_proxy_server", "127.0.0.1:7897")
        ).strip()

        if not executable_path.exists():
            raise ValueError(f"\u627e\u4e0d\u5230 Clash Verge \u53ef\u6267\u884c\u6587\u4ef6: {executable_path}")
        if not verge_config_path.exists():
            raise ValueError(f"\u627e\u4e0d\u5230 Clash Verge \u914d\u7f6e\u6587\u4ef6: {verge_config_path}")
        if not core_config_path.exists():
            raise ValueError(f"\u627e\u4e0d\u5230 Clash Verge \u6838\u5fc3\u914d\u7f6e\u6587\u4ef6: {core_config_path}")

        verge_changed = self._merge_yaml(
            verge_config_path,
            {"enable_tun_mode": True, "enable_system_proxy": True},
        )
        core_changed = self._merge_yaml(core_config_path, {"mode": "global", "tun": {"enable": True}})

        window_title = str(self._config.get("window_title", "Clash Verge")).strip()
        if verge_changed or core_changed:
            self._restart_process(executable_path, startup_wait_seconds=startup_wait_seconds)
        elif not self._window_exists(window_title):
            self._restart_process(executable_path, startup_wait_seconds=startup_wait_seconds)
        else:
            time.sleep(1.0)

        if not self._wait_for_process_running(executable_path.name, timeout_seconds=startup_wait_seconds):
            raise ValueError(f"Clash Verge 进程未在预期时间内启动: {executable_path.name}")
        if not self._ensure_window_visible(window_title, timeout_seconds=startup_wait_seconds):
            raise ValueError(f"Clash Verge 进程已启动，但主窗口未在预期时间内显示: {window_title}")

        proxy_ready = self._wait_for_proxy(expected_proxy_server, timeout_seconds=startup_wait_seconds)
        if not proxy_ready:
            proxy_enabled, proxy_server = self._read_proxy_settings()
            raise ValueError(
                "Clash Verge \u914d\u7f6e\u5df2\u5199\u5165\uff0c\u4f46 Windows \u7cfb\u7edf\u4ee3\u7406\u6ca1\u6709\u5728\u9884\u671f\u65f6\u95f4\u5185\u6307\u5411 "
                f"{expected_proxy_server}\u3002\u5f53\u524d\u68c0\u6d4b\u5230 ProxyEnable={int(proxy_enabled)}\uff0c"
                f"ProxyServer={proxy_server or '<empty>'}\u3002"
            )

        return StepResult(
            step_id=step.id,
            status=RunStatus.SUCCEEDED,
            message="Clash Verge \u5168\u5c40\u4ee3\u7406\u73af\u5883\u5df2\u5c31\u7eea\u3002",
            data={
                "executable_path": str(executable_path),
                "verge_config_path": str(verge_config_path),
                "core_config_path": str(core_config_path),
                "updated_verge_config": verge_changed,
                "updated_core_config": core_changed,
                "system_proxy_server": expected_proxy_server,
            },
        )

    def _merge_yaml(self, path: Path, expected_values: dict[str, Any]) -> bool:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        changed = self._merge_mapping(payload, expected_values)
        if changed:
            path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
        return changed

    def _merge_mapping(self, payload: dict[str, Any], expected_values: dict[str, Any]) -> bool:
        changed = False
        for key, value in expected_values.items():
            if isinstance(value, dict):
                current = payload.get(key)
                if not isinstance(current, dict):
                    payload[key] = {}
                    current = payload[key]
                    changed = True
                if self._merge_mapping(current, value):
                    changed = True
                continue
            if payload.get(key) != value:
                payload[key] = value
                changed = True
        return changed

    def _window_exists(self, window_title: str) -> bool:
        return any(self._is_window_visible(hwnd) for hwnd in self._find_window_handles(window_title))

    def _ensure_window_visible(self, window_title: str, timeout_seconds: float) -> bool:
        deadline = time.monotonic() + max(timeout_seconds, 1.0)
        while time.monotonic() < deadline:
            window_handles = self._find_window_handles(window_title)
            if window_handles:
                for hwnd in window_handles:
                    self._show_window(hwnd)
                if any(self._is_window_visible(hwnd) for hwnd in window_handles):
                    return True
            time.sleep(0.5)
        return False

    def _find_window_handles(self, window_title: str) -> list[int]:
        handles: list[int] = []
        normalized_title = window_title.casefold()
        enum_windows_proc = WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def callback(hwnd: int, _lparam: int) -> bool:
            title = self._get_window_text(hwnd)
            if normalized_title in title.casefold():
                handles.append(int(hwnd))
            return True

        windll.user32.EnumWindows(enum_windows_proc(callback), 0)
        return handles

    def _get_window_text(self, hwnd: int) -> str:
        title_length = windll.user32.GetWindowTextLengthW(hwnd)
        if title_length <= 0:
            return ""
        buffer = create_unicode_buffer(title_length + 1)
        windll.user32.GetWindowTextW(hwnd, buffer, title_length + 1)
        return buffer.value.strip()

    def _show_window(self, hwnd: int) -> None:
        if windll.user32.IsIconic(hwnd):
            windll.user32.ShowWindow(hwnd, 9)
        else:
            windll.user32.ShowWindow(hwnd, 5)
        windll.user32.BringWindowToTop(hwnd)
        windll.user32.SetForegroundWindow(hwnd)

    def _is_window_visible(self, hwnd: int) -> bool:
        return bool(windll.user32.IsWindowVisible(hwnd))

    def _restart_process(self, executable_path: Path, startup_wait_seconds: float) -> None:
        for process_name in self._RELATED_PROCESS_NAMES:
            self._terminate_processes(process_name, timeout_seconds=10.0)
        time.sleep(1.0)
        self._ui_driver.start_process(str(executable_path), startup_wait_seconds=startup_wait_seconds)

    def _wait_for_process_running(self, process_name: str, timeout_seconds: float) -> bool:
        deadline = time.monotonic() + max(timeout_seconds, 1.0)
        while time.monotonic() < deadline:
            if self._is_process_running(process_name):
                return True
            time.sleep(0.5)
        return False

    def _is_process_running(self, process_name: str) -> bool:
        return bool(self._find_process_ids_by_name(process_name))

    def _find_process_ids_by_name(self, process_name: str) -> list[int]:
        snapshot = windll.kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if snapshot == INVALID_HANDLE_VALUE:
            return []
        entry = PROCESSENTRY32W()
        entry.dwSize = sizeof(PROCESSENTRY32W)
        process_ids: list[int] = []
        try:
            has_entry = bool(windll.kernel32.Process32FirstW(snapshot, byref(entry)))
            while has_entry:
                if str(entry.szExeFile).casefold() == process_name.casefold():
                    process_ids.append(int(entry.th32ProcessID))
                has_entry = bool(windll.kernel32.Process32NextW(snapshot, byref(entry)))
        finally:
            windll.kernel32.CloseHandle(snapshot)
        return process_ids

    def _terminate_processes(self, process_name: str, timeout_seconds: float) -> None:
        timeout_millis = int(max(timeout_seconds, 0.0) * 1000)
        for process_id in self._find_process_ids_by_name(process_name):
            handle = windll.kernel32.OpenProcess(PROCESS_TERMINATE | SYNCHRONIZE, False, process_id)
            if not handle:
                continue
            try:
                windll.kernel32.TerminateProcess(handle, 1)
                if timeout_millis > 0:
                    windll.kernel32.WaitForSingleObject(handle, timeout_millis)
            finally:
                windll.kernel32.CloseHandle(handle)

    def _wait_for_proxy(self, expected_proxy_server: str, timeout_seconds: float) -> bool:
        deadline = time.monotonic() + max(timeout_seconds, 1.0)
        while time.monotonic() < deadline:
            proxy_enabled, proxy_server = self._read_proxy_settings()
            if proxy_server == expected_proxy_server:
                if not proxy_enabled:
                    self._enable_system_proxy(expected_proxy_server)
                    proxy_enabled, proxy_server = self._read_proxy_settings()
                if proxy_enabled and proxy_server == expected_proxy_server:
                    return True
            time.sleep(1.0)
        return False

    def _enable_system_proxy(self, proxy_server: str) -> None:
        settings_path = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            settings_path,
            0,
            winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE,
        ) as key:
            current_proxy_server = str(winreg.QueryValueEx(key, "ProxyServer")[0]).strip()
            if current_proxy_server != proxy_server:
                winreg.SetValueEx(key, "ProxyServer", 0, winreg.REG_SZ, proxy_server)
            winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 1)
        self._refresh_internet_options()

    def _refresh_internet_options(self) -> None:
        internet_set_option = windll.wininet.InternetSetOptionW
        internet_set_option.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD]
        internet_set_option.restype = wintypes.BOOL
        internet_option_settings_changed = 39
        internet_option_refresh = 37
        internet_set_option(None, internet_option_settings_changed, None, 0)
        internet_set_option(None, internet_option_refresh, None, 0)

    def _read_proxy_settings(self) -> tuple[bool, str]:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Internet Settings") as key:
            proxy_enable = int(winreg.QueryValueEx(key, "ProxyEnable")[0])
            proxy_server = str(winreg.QueryValueEx(key, "ProxyServer")[0])
        return proxy_enable == 1, proxy_server.strip()
