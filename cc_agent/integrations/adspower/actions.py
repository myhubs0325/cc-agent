from __future__ import annotations

import subprocess
import time
from pathlib import Path
from string import ascii_uppercase
from typing import Any

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

from cc_agent.domain.enums import RunStatus
from cc_agent.domain.models import StepResult, TaskStep
from cc_agent.integrations.adspower.client import AdsPowerClient
from cc_agent.integrations.base import BaseAdapter


class AdsPowerAdapter(BaseAdapter):
    name = "adspower"

    def __init__(self, config: dict[str, Any]) -> None:
        base_url = str(config.get("base_url", "http://127.0.0.1:50325"))
        timeout = float(config.get("timeout_seconds", 30))
        api_key_env = str(config.get("api_key_env", "ADSPOWER_API_KEY"))
        self._configured_executable_path = str(config.get("executable_path", "")).strip()
        self._default_install_root = _normalize_non_system_install_root(
            Path(str(config.get("default_install_root", r"D:\ADSPower")).strip() or r"D:\ADSPower")
        )
        self._default_executable_candidates = _adspower_executable_candidates(self._default_install_root)
        self._default_executable_path = self._default_executable_candidates[0]
        self._executable_path = str(self._resolved_executable_path())
        self._startup_wait_seconds = float(config.get("startup_wait_seconds", 10))
        self._service_ready_timeout_seconds = min(
            max(float(config.get("service_ready_timeout_seconds", min(self._startup_wait_seconds, 10.0))), 1.0),
            max(self._startup_wait_seconds, 1.0),
        )
        self._existing_process_probe_timeout_seconds = min(
            max(float(config.get("existing_process_probe_timeout_seconds", 8.0)), 1.0),
            max(self._service_ready_timeout_seconds, 1.0),
        )
        self._probe_interval_seconds = min(max(float(config.get("probe_interval_seconds", 1.0)), 0.2), 2.0)
        self._last_start_attempted_at = 0.0
        self._default_group_id = str(config.get("default_group_id", "0"))
        self._default_proxy_soft = str(config.get("default_proxy_soft", "other"))
        self._default_proxy_type = str(config.get("default_proxy_type", "http"))
        self._default_browser_type = str(config.get("default_browser_type", "chrome"))
        self._default_os = str(config.get("default_os", "win"))
        self._client = AdsPowerClient(
            base_url=base_url,
            timeout_seconds=timeout,
            api_key_env=api_key_env,
        )

    @property
    def capabilities(self) -> list[str]:
        return [
            "ensure_service_ready",
            "ensure_profile",
            "query_profiles",
            "query_proxies",
            "start_profile",
            "stop_profile",
            "open_profile_url",
            "handle_browser_request",
        ]

    def execute(self, step: TaskStep) -> StepResult:
        command = step.params.get("command", "")
        profile_id = str(step.params.get("profile_id", "")).strip()
        profile_no = str(step.params.get("profile_no", "")).strip()
        if step.action == "ensure_service_ready":
            diagnostics = self._ensure_service_ready()
            return StepResult(
                step_id=step.id,
                status=RunStatus.SUCCEEDED,
                message="AdsPower \u672c\u5730\u670d\u52a1\u5df2\u5c31\u7eea\u3002",
                data=diagnostics,
            )
        self._ensure_service_ready()
        if step.action == "query_profiles":
            payload = self._query_profiles(step)
            profiles = self._extract_profiles(payload)
            return StepResult(
                step_id=step.id,
                status=RunStatus.SUCCEEDED,
                message=f"AdsPower \u8d44\u6599\u67e5\u8be2\u5b8c\u6210\uff0c\u547d\u4e2d {len(profiles)} \u6761\u3002",
                data={"profiles": profiles, "raw": payload},
            )
        if step.action == "query_proxies":
            payload = self._query_proxies(step)
            proxies = self._extract_proxies(payload)
            return StepResult(
                step_id=step.id,
                status=RunStatus.SUCCEEDED,
                message=f"AdsPower 代理列表查询完成，命中 {len(proxies)} 条。",
                data={"proxies": proxies, "raw": payload},
            )
        if step.action == "ensure_profile":
            payload = self._ensure_profile(step)
            environment = payload.get("environment", {})
            message = "AdsPower \u73af\u5883\u5df2\u521b\u5efa\u3002" if payload.get("created", False) else "AdsPower \u73af\u5883\u5df2\u8865\u5168\u6216\u590d\u7528\u3002"
            return StepResult(
                step_id=step.id,
                status=RunStatus.SUCCEEDED,
                message=message,
                data=payload | {"profile_id": environment.get("user_id"), "profile_no": environment.get("serial_number")},
            )
        if step.action == "start_profile" and (profile_id or profile_no):
            payload = self._client.start_browser(profile_id=profile_id, profile_no=profile_no)
            return StepResult(
                step_id=step.id,
                status=RunStatus.SUCCEEDED,
                message="AdsPower \u8d44\u6599\u5df2\u542f\u52a8\u3002",
                data=payload,
            )
        if step.action == "stop_profile" and (profile_id or profile_no):
            payload = self._client.stop_browser(profile_id=profile_id, profile_no=profile_no)
            return StepResult(
                step_id=step.id,
                status=RunStatus.SUCCEEDED,
                message="AdsPower \u8d44\u6599\u5df2\u505c\u6b62\u3002",
                data=payload,
            )
        if step.action == "open_profile_url" and (profile_id or profile_no):
            return self._open_profile_url(step, profile_id, profile_no)
        return StepResult(
            step_id=step.id,
            status=RunStatus.SUCCEEDED,
            message="AdsPower \u9002\u914d\u5668\u5df2\u63a5\u6536\u8be5\u8bf7\u6c42\u3002",
            data={"command": command},
        )

    def _ensure_service_ready(self) -> dict[str, Any]:
        ready, probe_message = self._client.probe_ready()
        if ready:
            return {
                "ready": True,
                "wait_strategy": "immediate",
                "probe_message": probe_message,
            }
        executable = self._resolved_executable_path()
        self._executable_path = str(executable)
        if not str(executable):
            raise ValueError("AdsPower \u672c\u5730\u63a5\u53e3\u4e0d\u53ef\u7528\uff0c\u4e14\u672a\u914d\u7f6e AdsPower Global.exe \u8def\u5f84\u3002")
        if not executable.exists():
            raise ValueError(f"\u627e\u4e0d\u5230 AdsPower \u53ef\u6267\u884c\u6587\u4ef6: {executable}")

        process_was_running = self._is_process_running(executable.name)
        launched_now = False
        if not process_was_running:
            subprocess.Popen([str(executable)], cwd=str(executable.parent))
            self._last_start_attempted_at = time.monotonic()
            launched_now = True

        recent_launch = (time.monotonic() - self._last_start_attempted_at) <= max(self._startup_wait_seconds, 1.0)
        wait_strategy = "cold_start" if launched_now or recent_launch else "existing_process_recovery"
        wait_timeout = (
            self._service_ready_timeout_seconds if wait_strategy == "cold_start" else self._existing_process_probe_timeout_seconds
        )

        deadline = time.monotonic() + wait_timeout
        last_probe_message = probe_message
        while time.monotonic() < deadline:
            ready, probe_message = self._client.probe_ready()
            last_probe_message = probe_message
            if ready:
                return {
                    "ready": True,
                    "wait_strategy": wait_strategy,
                    "probe_message": probe_message,
                    "process_was_running": process_was_running,
                    "launched_now": launched_now,
                    "wait_timeout_seconds": wait_timeout,
                }
            time.sleep(self._probe_interval_seconds)

        detail = f"\uff0c\u6700\u540e\u4e00\u6b21\u63a2\u6d4b\u7ed3\u679c\uff1a{last_probe_message}" if last_probe_message else ""
        if wait_strategy == "existing_process_recovery":
            raise ValueError(
                "AdsPower 进程已存在，但本地接口在短恢复等待后仍未就绪，可能停留在登录、验证码或风控页面"
                f"{detail}\u3002"
            )
        raise ValueError(
            "AdsPower 已尝试启动，但本地接口在等待超时后仍未就绪"
            f"{detail}\u3002"
        )

    def _resolved_executable_path(self) -> Path:
        configured = Path(self._configured_executable_path).expanduser() if self._configured_executable_path else None
        if configured is not None and configured.exists():
            return configured
        for candidate in self._default_executable_candidates:
            if candidate.exists():
                return candidate
        return configured or self._default_executable_path

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
        stdout = completed.stdout or ""
        return image_name.lower() in stdout.lower()

    def _query_profiles(self, step: TaskStep) -> dict[str, Any]:
        return self._client.query_profiles(
            group_id=str(step.params.get("group_id", "")).strip() or None,
            user_id=str(step.params.get("profile_id", "")).strip() or None,
            serial_number=str(step.params.get("profile_no", "")).strip() or None,
            page=int(step.params.get("page", 1)),
            page_size=int(step.params.get("page_size", 100)),
        )

    def _query_proxies(self, step: TaskStep) -> dict[str, Any]:
        proxy_ids = step.params.get("proxy_ids")
        normalized_proxy_ids = [str(item).strip() for item in proxy_ids if str(item).strip()] if isinstance(proxy_ids, list) else None
        return self._client.query_proxies(
            page=int(step.params.get("page", 1)),
            limit=int(step.params.get("limit", step.params.get("page_size", 100))),
            proxy_ids=normalized_proxy_ids,
        )

    def _ensure_profile(self, step: TaskStep) -> dict[str, Any]:
        environment_name = str(step.params.get("environment_name", "")).strip()
        if not environment_name:
            raise ValueError("ensure_profile 缺少 environment_name。")

        group_id = str(step.params.get("group_id", "")).strip() or self._default_group_id
        existing = self._find_profile_by_name(environment_name, group_id=group_id)
        payload = self._build_profile_payload(step, group_id=group_id)
        if existing is None:
            response = self._client.create_profile(payload)
            environment = self._extract_environment(response)
            return {"created": True, "environment": environment, "raw": response}

        profile_id = str(existing.get("user_id", "")).strip()
        if not profile_id:
            return {"created": False, "environment": existing, "raw": {"data": existing}}
        update_payload = payload | {"name": environment_name}
        response = self._client.update_profile(profile_id, update_payload)
        environment = self._merge_environment(existing, self._extract_environment(response))
        return {"created": False, "environment": environment, "raw": response}

    def _find_profile_by_name(self, environment_name: str, *, group_id: str) -> dict[str, Any] | None:
        payload = self._client.query_profiles(group_id=group_id, page=1, page_size=100)
        profiles = self._extract_profiles(payload)
        target = environment_name.casefold()
        for profile in profiles:
            if str(profile.get("name", "")).strip().casefold() == target:
                return profile
        return None

    def _build_profile_payload(self, step: TaskStep, *, group_id: str) -> dict[str, Any]:
        environment_name = str(step.params.get("environment_name", "")).strip()
        platform = str(step.params.get("platform", "")).strip()
        platform_username = str(step.params.get("platform_username", "")).strip()
        platform_password = str(step.params.get("platform_password", "")).strip()
        proxy_id = str(step.params.get("proxy_id", "")).strip()
        proxy_soft = str(step.params.get("proxy_soft", "")).strip() or self._default_proxy_soft
        proxy_type = str(step.params.get("proxy_type", "")).strip() or self._default_proxy_type
        proxy_host = str(step.params.get("proxy_host", "")).strip()
        proxy_port = str(step.params.get("proxy_port", "")).strip()
        proxy_username = str(step.params.get("proxy_username", "")).strip()
        proxy_password = str(step.params.get("proxy_password", "")).strip()

        open_urls = step.params.get("open_urls")
        if not isinstance(open_urls, list) or not open_urls:
            platform_url = _default_platform_url(platform)
            open_urls = [platform_url] if platform_url else []

        fingerprint_config = {
            "automatic_timezone": "1",
            "language": ["en-US", "en"],
            "flash": "block",
            "fonts": ["all"],
            "webrtc": "proxy",
            "location": "ask",
            "do_not_track": "default",
        }

        platform_domain = _platform_domain(platform)

        payload = {
            "group_id": group_id,
            "name": environment_name,
            "browser_type": str(step.params.get("browser_type", "")).strip() or self._default_browser_type,
            "os": str(step.params.get("os", "")).strip() or self._default_os,
            "fingerprint_config": fingerprint_config,
            "tabs": open_urls,
            "open_urls": open_urls,
        }
        if proxy_id:
            payload["proxyid"] = proxy_id
            payload["proxy_soft"] = proxy_soft
            payload["proxy_type"] = proxy_type
        else:
            user_proxy_config: dict[str, Any] = {
                "proxy_soft": proxy_soft,
                "proxy_type": proxy_type,
                "proxy_host": proxy_host,
                "proxy_port": proxy_port,
            }
            if proxy_username:
                user_proxy_config["proxy_user"] = proxy_username
            if proxy_password:
                user_proxy_config["proxy_password"] = proxy_password
            payload["user_proxy_config"] = user_proxy_config
        if platform_domain:
            payload["platform"] = platform_domain
            payload["domain_name"] = platform_domain
        if platform_username:
            payload["username"] = platform_username
        if platform_password:
            payload["password"] = platform_password
        return payload

    @staticmethod
    def _extract_profiles(payload: dict[str, Any]) -> list[dict[str, Any]]:
        data = payload.get("data", {})
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            for key in ("list", "items", "data"):
                value = data.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
        return []

    @staticmethod
    def _extract_proxies(payload: dict[str, Any]) -> list[dict[str, Any]]:
        data = payload.get("data", {})
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            for key in ("list", "items", "data"):
                value = data.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
        return []

    @staticmethod
    def _extract_environment(payload: dict[str, Any]) -> dict[str, Any]:
        data = payload.get("data", {})
        if isinstance(data, dict) and "profile_id" in data and "user_id" not in data:
            data["user_id"] = data.get("profile_id")
        if isinstance(data, dict) and "profile_no" in data and "serial_number" not in data:
            data["serial_number"] = data.get("profile_no")
        if isinstance(data, dict) and "id" in data and "user_id" not in data:
            data["user_id"] = data.get("id")
        if isinstance(data, dict):
            return data
        return {}

    @staticmethod
    def _merge_environment(existing: dict[str, Any], updated: dict[str, Any]) -> dict[str, Any]:
        merged = dict(existing)
        merged.update({key: value for key, value in updated.items() if value not in ("", None)})
        if "user_id" not in merged and "id" in merged:
            merged["user_id"] = merged["id"]
        return merged

    def _open_profile_url(self, step: TaskStep, profile_id: str, profile_no: str) -> StepResult:
        url = str(step.params.get("url", "")).strip()
        close_after = bool(step.params.get("close_after", True))
        payload = self._client.start_browser(profile_id=profile_id, profile_no=profile_no)
        data = payload.get("data", {})
        chrome_driver = data.get("webdriver", "")
        debugger_address = data.get("ws", {}).get("selenium", "")
        if not url:
            raise ValueError("\u6253\u5f00\u7f51\u5740\u4efb\u52a1\u7f3a\u5c11 url \u53c2\u6570\u3002")
        if not chrome_driver or not debugger_address:
            raise ValueError("AdsPower \u8fd4\u56de\u7ed3\u679c\u4e2d\u7f3a\u5c11 webdriver \u6216 selenium \u8c03\u8bd5\u5730\u5740\u3002")

        chrome_options = Options()
        chrome_options.add_experimental_option("debuggerAddress", debugger_address)
        service = Service(executable_path=chrome_driver)
        driver = webdriver.Chrome(service=service, options=chrome_options)
        title = ""
        try:
            driver.get(url)
            title = driver.title
        finally:
            if close_after:
                driver.quit()
                self._client.stop_browser(profile_id=profile_id, profile_no=profile_no)

        return StepResult(
            step_id=step.id,
            status=RunStatus.SUCCEEDED,
            message="AdsPower \u5df2\u6253\u5f00\u8d44\u6599\u5e76\u8df3\u8f6c\u5230\u76ee\u6807\u7f51\u9875\u3002",
            data={
                "url": url,
                "title": title,
                "closed_after_run": close_after,
                "profile_id": profile_id,
                "profile_no": profile_no,
            },
        )


def _default_platform_url(platform: str) -> str | None:
    platform_domain = _platform_domain(platform)
    if platform_domain is None:
        return None
    return f"https://www.{platform_domain.rstrip('/')}/" if "." in platform_domain and not platform_domain.startswith("http") else platform_domain


def _platform_domain(platform: str) -> str | None:
    normalized = platform.strip().casefold()
    mapping = {
        "tiktok": "tiktok.com",
        "facebook": "facebook.com",
        "instagram": "instagram.com",
    }
    return mapping.get(normalized)


def _adspower_executable_candidates(install_root: Path) -> list[Path]:
    return [
        install_root / "AdsPower Global.exe",
        install_root / "AdsPower Global" / "AdsPower Global.exe",
    ]


def _normalize_non_system_install_root(preferred: Path) -> Path:
    candidate = preferred.expanduser()
    anchor = Path(candidate.anchor) if candidate.anchor else None
    if anchor is None or anchor.exists():
        return candidate

    relative_parts = candidate.parts[1:] if len(candidate.parts) > 1 else ()
    for letter in ascii_uppercase:
        if letter == "C":
            continue
        root = Path(f"{letter}:\\")
        if root.exists():
            return root.joinpath(*relative_parts) if relative_parts else root
    return candidate
