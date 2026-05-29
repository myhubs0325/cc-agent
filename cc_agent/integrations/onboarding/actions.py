from __future__ import annotations

import json
import logging
import os
import platform
import re
import shutil
import socket
import subprocess
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path
from string import ascii_uppercase
from typing import Any, Iterable

import httpx

from cc_agent.automation.windows.ui_driver import WindowsUiDriver
from cc_agent.domain.enums import RunStatus
from cc_agent.domain.models import StepResult, TaskStep
from cc_agent.integrations.adspower import AdsPowerAdapter
from cc_agent.integrations.base import BaseAdapter
from cc_agent.integrations.dbit_octopus import DbitOctopusAdapter
from cc_agent.onboarding import (
    AdsPowerEnvironmentRecord,
    CloneAccountDetail,
    NewUserSetupContext,
    OnboardingDocumentParser,
    OnboardingProfile,
    OnboardingTaskFactory,
)


logger = logging.getLogger(__name__)


class OnboardingAdapter(BaseAdapter):
    name = "onboarding"

    def __init__(
        self,
        config: dict[str, Any],
        document_parser: OnboardingDocumentParser,
        task_factory: OnboardingTaskFactory,
        adspower_adapter: AdsPowerAdapter,
        dbit_adapter: DbitOctopusAdapter,
        dbit_config: dict[str, Any],
    ) -> None:
        self._config = config
        self._document_parser = document_parser
        self._task_factory = task_factory
        self._adspower_adapter = adspower_adapter
        self._dbit_adapter = dbit_adapter
        self._windows_ui_driver = WindowsUiDriver()
        executable_path = str(dbit_config.get("executable_path", "")).strip()
        self._configured_dbit_executable_path = Path(executable_path).expanduser() if executable_path else None
        self._default_dbit_install_root = self._normalize_install_root(
            Path(str(config.get("default_dbit_install_root", r"D:\DBit-octopus")).strip() or r"D:\DBit-octopus")
        )
        self._default_dbit_executable_path = self._default_dbit_install_root / "DBit-octopus.exe"
        self._default_adspower_install_root = self._normalize_install_root(
            Path(str(config.get("default_adspower_install_root", r"D:\ADSPower")).strip() or r"D:\ADSPower")
        )
        self._default_adspower_executable_candidates = self._adspower_executable_candidates(self._default_adspower_install_root)
        self._default_adspower_executable_path = self._default_adspower_executable_candidates[0]
        self._installer_wait_seconds = float(config.get("installer_wait_seconds", 15))
        self._installer_window_timeout_seconds = float(config.get("installer_window_timeout_seconds", 12))
        self._startup_flow_name = str(config.get("startup_flow_name", "startup"))
        self._setup_workspace_flow_name = str(config.get("setup_workspace_flow_name", "onboarding_workspace"))
        self._proxy_probe_timeout_seconds = float(config.get("proxy_probe_timeout_seconds", 4))
        self._adspower_ready_timeout_seconds = min(
            max(float(config.get("adspower_ready_timeout_seconds", 10.0)), 3.0),
            15.0,
        )
        self._adspower_download_url = str(config.get("adspower_download_url", "")).strip()
        self._adspower_register_url = str(config.get("adspower_register_url", "")).strip()
        self._adspower_pricing_url = str(config.get("adspower_pricing_url", "")).strip()
        self._proxy_purchase_url = str(config.get("proxy_purchase_url", "")).strip()

    @property
    def capabilities(self) -> list[str]:
        return [
            "validate_setup_inputs",
            "parse_onboarding_source",
            "install_dbit_octopus",
            "prepare_non_vpn_environment",
            "verify_adspower_ready",
            "verify_proxy_ready",
            "create_adspower_environments",
            "export_dbit_setup_blueprint",
            "open_dbit_configuration_workspace",
            "apply_dbit_setup_blueprint",
            "finish_manual_handoff",
        ]

    def execute(self, step: TaskStep) -> StepResult:
        handlers = {
            "validate_setup_inputs": self._validate_setup_inputs,
            "parse_onboarding_source": self._parse_onboarding_source,
            "install_dbit_octopus": self._install_dbit_octopus,
            "prepare_non_vpn_environment": self._prepare_non_vpn_environment,
            "verify_adspower_ready": self._verify_adspower_ready,
            "verify_proxy_ready": self._verify_proxy_ready,
            "create_adspower_environments": self._create_adspower_environments,
            "export_dbit_setup_blueprint": self._export_dbit_setup_blueprint,
            "open_dbit_configuration_workspace": self._open_dbit_configuration_workspace,
            "apply_dbit_setup_blueprint": self._apply_dbit_setup_blueprint,
            "finish_manual_handoff": self._finish_manual_handoff,
        }
        handler = handlers.get(step.action)
        if handler is None:
            return StepResult(
                step_id=step.id,
                status=RunStatus.FAILED,
                message=f"未支持的 onboarding 动作: {step.action}",
            )
        return handler(step)

    def _validate_setup_inputs(self, step: TaskStep) -> StepResult:
        context_path = self._context_path(step)
        context = self._load_context(context_path)
        installer = Path(context.installer_path).expanduser()
        source = Path(context.source_path).expanduser()
        if not installer.exists():
            raise ValueError(f"安装包不存在: {installer}")
        if not source.exists():
            raise ValueError(f"资料来源不存在: {source}")
        context.last_success_step = step.action
        context.wait_reason = None
        self._save_context(context_path, context)
        return self._success(step, "安装包和资料来源校验通过。")

    def _parse_onboarding_source(self, step: TaskStep) -> StepResult:
        context_path = self._context_path(step)
        context = self._load_context(context_path)
        parsed = self._document_parser.parse(context.source_path)
        context.parsed_profile = parsed.profile
        context.parse_warnings = parsed.warnings
        context.source_kind = parsed.source_kind
        context.last_success_step = step.action
        context.wait_reason = None
        self._save_context(context_path, context)
        warning_note = f"，发现 {len(parsed.warnings)} 条提醒" if parsed.warnings else ""
        return self._success(step, f"资料解析完成{warning_note}。", {"warnings": parsed.warnings})

    def _install_dbit_octopus(self, step: TaskStep) -> StepResult:
        context_path = self._context_path(step)
        context = self._load_context(context_path)
        dbit_executable_path = self._resolved_dbit_executable_path(context.parsed_profile)
        install_root = self._resolved_dbit_install_root(context.parsed_profile)
        installation_ready, install_meta = self._is_dbit_installation_ready(
            dbit_executable_path,
            install_root,
            probe_runtime=True,
        )
        if installation_ready:
            context.last_success_step = step.action
            context.wait_reason = None
            self._save_context(context_path, context)
            return self._success(step, "已检测到 DbitOCT，可跳过安装。")
        if install_root.exists() and any(reason != "executable_missing" for reason in install_meta.get("incomplete_reasons", [])):
            if dbit_executable_path.exists():
                self._stop_processes_by_image_name(dbit_executable_path.name)
            self._remove_dbit_installation_root(install_root)
        if not self._install_root_is_available(install_root):
            return self._waiting(
                step,
                context_path=context_path,
                context=context,
                wait_reason="dbit_installation_disk_required",
                resume_from=step.action,
                message=(
                    f"当前电脑未检测到可用的非系统盘，默认目标目录 {install_root} 不可用。"
                    " DBitOCT 不支持安装到系统盘（C盘），请准备 D/E/F 等非系统盘后再继续。"
                ),
                extra_data={"default_install_root": str(install_root)},
            )
        install_root.mkdir(parents=True, exist_ok=True)
        if not str(context.parsed_profile.install_path or "").strip():
            context.parsed_profile.install_path = str(install_root)

        installer_path = Path(context.installer_path).expanduser()
        if not installer_path.exists():
            raise ValueError(f"找不到安装包: {installer_path}")
        if self._is_process_running(installer_path.name) and not dbit_executable_path.exists():
            self._stop_processes_by_image_name(installer_path.name)
            self._stop_processes_by_image_name(f"{installer_path.stem}.tmp")
        if not self._is_process_running(installer_path.name):
            self._launch_installer(installer_path, install_root=install_root, install_target="dbit")

        deadline = time.monotonic() + max(self._installer_wait_seconds, 90.0)
        while time.monotonic() < deadline:
            if dbit_executable_path.exists():
                installation_ready, install_meta = self._is_dbit_installation_ready(
                    dbit_executable_path,
                    install_root,
                    probe_runtime=not self._is_process_running(installer_path.name),
                )
                if installation_ready:
                    self._stop_processes_by_image_name(installer_path.name)
                    self._stop_processes_by_image_name(f"{installer_path.stem}.tmp")
                    context.last_success_step = step.action
                    context.wait_reason = None
                    self._save_context(context_path, context)
                    return self._success(step, "DbitOCT 安装检测通过。")
            time.sleep(1)

        return self._waiting(
            step,
            context_path=context_path,
            context=context,
            wait_reason="dbit_installation_required",
            resume_from=step.action,
            message="DbitOCT 尚未安装完成，或检测到安装目录不完整，请删除残留后重新安装，然后点击继续执行。",
            extra_data=install_meta,
        )

    def _prepare_non_vpn_environment(self, step: TaskStep) -> StepResult:
        context_path = self._context_path(step)
        context = self._load_context(context_path)
        install_root = self._resolved_dbit_install_root(context.parsed_profile)
        dbit_executable_path = self._resolved_dbit_executable_path(context.parsed_profile)
        if not self._install_root_is_available(install_root):
            return self._waiting(
                step,
                context_path=context_path,
                context=context,
                wait_reason="dbit_installation_disk_required",
                resume_from=step.action,
                message=(
                    f"当前电脑未检测到可用的非系统盘，默认目标目录 {install_root} 不可用。"
                    " DBitOCT 不支持安装到系统盘（C盘），请准备 D/E/F 等非系统盘后再继续。"
                ),
                extra_data={"default_install_root": str(install_root)},
            )
        context.parsed_profile.install_path = str(install_root)
        install_root.mkdir(parents=True, exist_ok=True)
        installation_ready, install_meta = self._is_dbit_installation_ready(
            dbit_executable_path,
            install_root,
            probe_runtime=False,
        )
        if not installation_ready:
            return self._waiting(
                step,
                context_path=context_path,
                context=context,
                wait_reason="dbit_installation_required",
                resume_from="install_dbit_octopus",
                message="检测到 DbitOCT 安装目录不完整，需先清理残留并重新安装，再继续初始化流程。",
                extra_data=install_meta,
            )

        dbit_result = self._dbit_adapter.execute(
            TaskStep(
                adapter="dbit_octopus",
                action="run_ui_flow",
                description="执行 DbitOCT startup 流程",
                params={
                    "flow_name": self._startup_flow_name,
                    "username": context.parsed_profile.dbit_username or "",
                    "password": context.parsed_profile.dbit_password or "",
                },
            )
        )
        if dbit_result.status == RunStatus.SUCCEEDED:
            context.last_success_step = step.action
            context.wait_reason = None
            self._save_context(context_path, context)
            return self._success(step, "非 VPN 的基础准备已完成。", artifacts=dbit_result.artifacts)
        if dbit_result.status == RunStatus.WAITING_INPUT:
            if bool(dbit_result.data.get("control_panel_authorized") or dbit_result.data.get("control_panel_ready")):
                context.last_success_step = step.action
                context.wait_reason = None
                self._save_context(context_path, context)
                return self._success(
                    step,
                    "非 VPN 的基础准备已在后台接管模式下完成。",
                    data=dbit_result.data,
                    artifacts=dbit_result.artifacts,
                )
            return self._waiting(
                step,
                context_path=context_path,
                context=context,
                wait_reason="dbit_manual_input_required",
                resume_from=step.action,
                message=dbit_result.message,
                artifacts=dbit_result.artifacts,
            )
        return StepResult(
            step_id=step.id,
            status=dbit_result.status,
            message=dbit_result.message,
            artifacts=dbit_result.artifacts,
            data=dbit_result.data,
        )

    def _verify_adspower_ready(self, step: TaskStep) -> StepResult:
        return self._verify_adspower_ready_v2(step)

    def _verify_adspower_ready_v2(self, step: TaskStep) -> StepResult:
        return self._verify_adspower_ready_v3(step)
        context_path = self._context_path(step)
        context = self._load_context(context_path)
        profile = context.parsed_profile

        service_result: StepResult
        try:
            service_result = self._adspower_adapter.execute(
                TaskStep(
                    adapter="adspower",
                    action="ensure_service_ready",
                    description="检查 AdsPower 本地服务",
                    params={},
                )
            )
        except Exception:
            service_result = StepResult(
                step_id=step.id,
                status=RunStatus.FAILED,
                message="AdsPower 服务未就绪。",
            )

        if (
            service_result.status == RunStatus.SUCCEEDED
            and profile.adspower_username
            and profile.adspower_password
            and profile.adspower_plan_ready is not False
        ):
            self._close_adspower_guidance_windows()
            context.adspower_purchase_status = "ready"
            context.last_success_step = step.action
            context.wait_reason = None
            self._save_context(context_path, context)
            return self._success(step, "AdsPower 本地服务已就绪。")

        if not self._adspower_executable_exists():
            install_root = self._resolved_adspower_install_root()
            detected_system = self._detected_adspower_windows_label()
            if not self._install_root_is_available(install_root):
                return self._waiting(
                    step,
                    context_path=context_path,
                    context=context,
                    wait_reason="adspower_setup_required",
                    resume_from=step.action,
                    message=(
                        f"当前电脑未检测到可用的非系统盘，默认目标目录 {install_root} 不可用。"
                        " AdsPower 不会安装到系统盘（C盘），请准备 D/E/F 等非系统盘后再继续。"
                    ),
                    extra_data={
                        "default_install_root": str(install_root),
                        "detected_system": detected_system,
                    },
                )
            install_root.mkdir(parents=True, exist_ok=True)
            downloaded_installer = None
            download_error = ""
            try:
                downloaded_installer = self._download_adspower_installer_if_needed()
            except Exception as exc:
                download_error = str(exc)
            launched_installer = False
            if downloaded_installer is not None and not self._is_process_running(downloaded_installer.name):
                self._launch_installer(downloaded_installer, install_root=install_root, install_target="adspower")
                launched_installer = True
            installer_running = downloaded_installer is not None and self._is_process_running(downloaded_installer.name)
            if installer_running:
                self._start_adspower_installer_assistant()
            installation_detected = False
            if launched_installer or installer_running:
                deadline = time.monotonic() + self._installer_wait_seconds
                while time.monotonic() < deadline:
                    if self._adspower_executable_exists():
                        installation_detected = True
                        break
                    if self._adspower_executable_exists():
                        context.adspower_purchase_status = "ready"
                        context.last_success_step = step.action
                        context.wait_reason = None
                        self._save_context(context_path, context)
                        return self._success(
                            step,
                            f"AdsPower 已自动安装到 {install_root} 并检测通过。",
                            data={
                                "default_install_root": str(install_root),
                                "downloaded_installer_path": str(downloaded_installer),
                                "detected_system": detected_system,
                            },
                        )
                    time.sleep(1)
            if installation_detected:
                if profile.adspower_plan_ready is False and profile.adspower_username and profile.adspower_password:
                    opened = self._open_guidance_once(
                        context,
                        context_flag="adspower_pricing_guidance_opened",
                        url=self._adspower_pricing_url,
                    )
                    context.adspower_purchase_status = "required"
                    return self._waiting(
                        step,
                        context_path=context_path,
                        context=context,
                        wait_reason="adspower_purchase_required",
                        resume_from=step.action,
                        message=(
                            "AdsPower 已自动安装，并已打开套餐开通页。请完成套餐开通后点击继续。"
                            if opened
                            else "AdsPower 已自动安装，但资料标记为未开通套餐。请完成套餐开通后点击继续。"
                        ),
                    )
                if not profile.adspower_username or not profile.adspower_password:
                    self._launch_adspower_if_available()
                    opened = self._open_guidance_once(
                        context,
                        context_flag="adspower_registration_guidance_opened",
                        url=self._adspower_register_url,
                    )
                    context.adspower_purchase_status = "login_required"
                    return self._waiting(
                        step,
                        context_path=context_path,
                        context=context,
                        wait_reason="adspower_login_required",
                        resume_from=step.action,
                        message=(
                            "AdsPower 已自动安装，并已拉起登录页和注册引导。没有账号就先注册，有账号就直接登录，完成后点击继续。"
                            if opened
                            else "AdsPower 已自动安装，并已拉起登录页。没有账号就先注册，有账号就直接登录，完成后点击继续。"
                        ),
                        extra_data={
                            "default_install_root": str(install_root),
                            "downloaded_installer_path": str(downloaded_installer) if downloaded_installer is not None else "",
                            "detected_system": detected_system,
                        },
                    )
                self._launch_adspower_if_available()
                context.adspower_purchase_status = "required"
                return self._waiting(
                    step,
                    context_path=context_path,
                    context=context,
                    wait_reason="adspower_setup_required",
                    resume_from=step.action,
                    message="AdsPower 已自动安装并已启动。请完成登录和初始化后点击继续。",
                    extra_data={
                        "default_install_root": str(install_root),
                        "downloaded_installer_path": str(downloaded_installer) if downloaded_installer is not None else "",
                        "detected_system": detected_system,
                    },
                )
            opened = self._open_guidance_once(
                context,
                context_flag="adspower_download_guidance_opened",
                url=self._adspower_download_url,
            )
            context.adspower_purchase_status = "required"
            return self._waiting(
                step,
                context_path=context_path,
                context=context,
                wait_reason="adspower_setup_required",
                resume_from=step.action,
                message=(
                    f"AdsPower 尚未安装，默认安装目录已准备为 {install_root}。"
                    f"{' 已自动下载并启动安装程序。' if launched_installer else ''}"
                    f"{f' 自动下载安装时遇到网络问题：{download_error}。' if download_error else ''}"
                    f"{' 已尝试打开官方下载页。' if opened else ''}"
                    " 安装完成后点击继续。"
                ),
                extra_data={
                    "default_install_root": str(install_root),
                    "downloaded_installer_path": str(downloaded_installer) if downloaded_installer is not None else "",
                    "download_error": download_error,
                    "detected_system": detected_system,
                },
            )

        if profile.adspower_plan_ready is False and profile.adspower_username and profile.adspower_password:
            opened = self._open_guidance_once(
                context,
                context_flag="adspower_pricing_guidance_opened",
                url=self._adspower_pricing_url,
            )
            context.adspower_purchase_status = "required"
            return self._waiting(
                step,
                context_path=context_path,
                context=context,
                wait_reason="adspower_purchase_required",
                resume_from=step.action,
                message=(
                    "资料标记 AdsPower 套餐尚未开通，已尝试打开官方套餐页。请完成开通后点击继续。"
                    if opened
                    else "资料标记 AdsPower 套餐尚未开通，请完成开通后点击继续。"
                ),
            )

        if not profile.adspower_username or not profile.adspower_password:
            self._launch_adspower_if_available()
            opened = self._open_guidance_once(
                context,
                context_flag="adspower_registration_guidance_opened",
                url=self._adspower_register_url,
            )
            context.adspower_purchase_status = "login_required"
            return self._waiting(
                step,
                context_path=context_path,
                context=context,
                wait_reason="adspower_login_required",
                resume_from=step.action,
                message=(
                    "缺少 AdsPower 登录信息，已尝试打开 AdsPower 注册指引页面。请注册或补齐登录信息后继续。"
                    if opened
                    else "缺少 AdsPower 登录信息，请注册或补齐登录信息后继续。"
                ),
            )

        self._launch_adspower_if_available()
        opened = self._open_guidance_once(
            context,
            context_flag="adspower_registration_guidance_opened",
            url=self._adspower_register_url,
        )
        setup_message = (
            "AdsPower 本地程序已尝试启动，且已打开注册/登录指引。请确认已登录并完成初始化后点击继续。"
            if opened
            else "AdsPower 本地程序已尝试启动，但服务仍不可用。请确认已登录并完成初始化后点击继续。"
        )

        context.adspower_purchase_status = "required"
        return self._waiting(
            step,
            context_path=context_path,
            context=context,
            wait_reason="adspower_setup_required",
            resume_from=step.action,
            message=setup_message,
        )

    def _verify_proxy_ready(self, step: TaskStep) -> StepResult:
        context_path = self._context_path(step)
        context = self._load_context(context_path)
        profile = context.parsed_profile
        available_proxy_count = self._available_proxy_count(profile)
        required_proxy_count = self._required_proxy_count(profile)

        if self._has_partial_static_proxy_sync_credentials(profile):
            return self._waiting(
                step,
                context_path=context_path,
                context=context,
                wait_reason="proxy_sync_credentials_missing",
                resume_from=step.action,
                message="已提供部分静态代理同步信息，但缺少代理用户ID或代理密钥，请补齐后继续。",
            )

        if self._should_use_static_proxy_sync(profile):
            clone_details = self._clone_details(profile)
            saved_proxies = self._query_ipfoxy_saved_proxies()
            assignments = self._allocate_ipfoxy_proxy_assignments(saved_proxies, clone_details)
            if len(assignments) < len(clone_details):
                self._launch_adspower_if_available()
                context.static_proxy_sync_guidance_opened = True
                self._save_context(context_path, context)
                return self._waiting(
                    step,
                    context_path=context_path,
                    context=context,
                    wait_reason="static_proxy_sync_required",
                    resume_from=step.action,
                    message=(
                        "静态代理模式下当前可用的 IPFoxy 集成代理数量不足。"
                        " 一个 IP 只能同时承载 1 个 TikTok 环境和 1 个 Facebook 环境，"
                        " 已经被 2 个环境占用的代理不能继续复用。"
                    ),
                    extra_data={
                        "required_proxy_count": required_proxy_count,
                        "clone_count": len(clone_details),
                        "available_saved_proxy_count": len(saved_proxies),
                    },
                )
            context.proxy_purchase_status = "static_sync"
            context.last_success_step = step.action
            context.wait_reason = None
            self._save_context(context_path, context)
            return self._success(
                step,
                f"已识别为静态代理集成模式，当前 IPFoxy 代理数量满足需求，需要 {required_proxy_count} 个 IP。",
                data={
                    "proxy_mode": "static_sync",
                    "required_proxy_count": required_proxy_count,
                    "clone_count": len(clone_details),
                },
            )

        if available_proxy_count >= required_proxy_count and available_proxy_count > 0:
            probe_error = self._probe_profile_proxies(profile)
            if probe_error is not None:
                context.proxy_purchase_status = "required"
                return self._waiting(
                    step,
                    context_path=context_path,
                    context=context,
                    wait_reason="proxy_connectivity_failed",
                    resume_from=step.action,
                    message=probe_error,
                )

            context.proxy_purchase_status = "ready"
            context.last_success_step = step.action
            context.wait_reason = None
            self._save_context(context_path, context)
            return self._success(step, f"代理资源校验通过，当前可用 {available_proxy_count} 条。")

        if available_proxy_count <= 0 and not self._has_static_proxy_sync_credentials(profile):
            opened = self._open_guidance_once(
                context,
                context_flag="proxy_purchase_guidance_opened",
                url=self._proxy_purchase_url,
            )
            context.proxy_purchase_status = "required"
            message = (
                "当前未提供可直接使用的代理信息。"
                + (
                    " 已尝试打开代理购买页或说明页，默认推荐 IPFoxy，但也可以手动选择其他平台。"
                    if opened
                    else " 请先购买或补全代理资源，默认推荐 IPFoxy，但也可以手动选择其他平台。"
                )
            )
            return self._waiting(
                step,
                context_path=context_path,
                context=context,
                wait_reason="proxy_purchase_required",
                resume_from=step.action,
                message=message,
            )

        if available_proxy_count < required_proxy_count:
            opened = self._open_guidance_once(
                context,
                context_flag="proxy_purchase_guidance_opened",
                url=self._proxy_purchase_url,
            )
            context.proxy_purchase_status = "required"
            message = (
                f"当前可用代理数为 {available_proxy_count}，低于所需的 {required_proxy_count}。"
                + (" 已尝试打开代理购买页或说明页，请补齐代理资源后点击继续。" if opened else " 请补齐代理资源后点击继续。")
            )
            return self._waiting(
                step,
                context_path=context_path,
                context=context,
                wait_reason="proxy_purchase_required",
                resume_from=step.action,
                message=message,
            )

        probe_error = self._probe_profile_proxies(profile)
        if probe_error is not None:
            context.proxy_purchase_status = "required"
            return self._waiting(
                step,
                context_path=context_path,
                context=context,
                wait_reason="proxy_connectivity_failed",
                resume_from=step.action,
                message=probe_error,
            )

        context.proxy_purchase_status = "ready"
        context.last_success_step = step.action
        context.wait_reason = None
        self._save_context(context_path, context)
        return self._success(step, f"代理资源校验通过，当前可用 {available_proxy_count} 条。")

    def _create_adspower_environments(self, step: TaskStep) -> StepResult:
        context_path = self._context_path(step)
        context = self._load_context(context_path)
        profile = context.parsed_profile
        if self._should_use_static_proxy_sync(profile):
            return self._create_adspower_environments_with_static_proxy(step, context_path, context, profile)

        environment_records: list[AdsPowerEnvironmentRecord] = []
        for index, clone_detail in enumerate(self._clone_details(profile), start=1):
            environment_name = str(
                clone_detail.adspower_environment_name
                or clone_detail.clone_name
                or profile.environment_name
                or f"{clone_detail.platform or profile.platform or 'Environment'}-{index:02d}"
            ).strip()
            proxy_host = str(clone_detail.proxy_host or profile.proxy_host or "").strip()
            proxy_port = str(clone_detail.proxy_port or profile.proxy_port or "").strip()
            if not proxy_host or not proxy_port:
                return self._waiting(
                    step,
                    context_path=context_path,
                    context=context,
                    wait_reason="proxy_details_missing",
                    resume_from=step.action,
                    message=f"分身 [{clone_detail.clone_name or environment_name}] 缺少代理主机或端口，无法创建 AdsPower 环境。",
                )

            result = self._adspower_adapter.execute(
                TaskStep(
                    adapter="adspower",
                    action="ensure_profile",
                    description=f"确保 AdsPower 环境存在: {environment_name}",
                    params={
                        "environment_name": environment_name,
                        "platform": clone_detail.platform or profile.platform or "",
                        "platform_username": clone_detail.platform_account or profile.platform_username or "",
                        "platform_password": clone_detail.platform_password or profile.platform_password or "",
                        "proxy_host": proxy_host,
                        "proxy_port": proxy_port,
                        "proxy_username": clone_detail.proxy_username or profile.proxy_username or "",
                        "proxy_password": clone_detail.proxy_password or profile.proxy_password or "",
                    },
                )
            )
            if result.status != RunStatus.SUCCEEDED:
                return result

            profile_id = str(result.data.get("profile_id", "")).strip() or None
            profile_no = str(result.data.get("profile_no", "")).strip() or None
            environment_records.append(
                AdsPowerEnvironmentRecord(
                    clone_name=clone_detail.clone_name or environment_name,
                    environment_name=environment_name,
                    platform=clone_detail.platform or profile.platform or None,
                    profile_id=profile_id,
                    profile_no=profile_no,
                    proxy_host=proxy_host,
                    proxy_port=proxy_port,
                    proxy_username=clone_detail.proxy_username or profile.proxy_username or None,
                    proxy_password=clone_detail.proxy_password or profile.proxy_password or None,
                    status="ready",
                )
            )

        context.environment_records = environment_records
        if len(environment_records) == 1:
            context.environment_id = environment_records[0].profile_id
        self._refresh_adspower_environment_management_view()
        self._surface_adspower_environment_feedback(environment_records)
        context.last_success_step = step.action
        context.wait_reason = None
        self._save_context(context_path, context)
        return self._success(
            step,
            f"AdsPower 环境已创建或补全，共 {len(environment_records)} 个。",
            data={"environment_records": [record.model_dump(mode="json") for record in environment_records]},
        )

    def _create_adspower_environments_with_static_proxy(
        self,
        step: TaskStep,
        context_path: str,
        context: NewUserSetupContext,
        profile: OnboardingProfile,
    ) -> StepResult:
        clone_details = self._clone_details(profile)
        saved_proxies = self._query_ipfoxy_saved_proxies()
        assigned_proxies = self._allocate_ipfoxy_proxy_assignments(saved_proxies, clone_details)
        if len(assigned_proxies) < len(clone_details):
            self._launch_adspower_if_available()
            context.static_proxy_sync_guidance_opened = True
            self._save_context(context_path, context)
            return self._waiting(
                step,
                context_path=context_path,
                context=context,
                wait_reason="static_proxy_sync_required",
                resume_from=step.action,
                message=(
                    "静态代理模式下未检测到足够的 IPFoxy 集成代理。"
                    " 一个 IP 只能同时承载 1 个 TikTok 环境和 1 个 Facebook 环境，"
                    " 已经达到 2 个环境上限的代理不能继续复用。"
                ),
                extra_data={
                    "required_proxy_count": self._required_proxy_count(profile),
                    "clone_count": len(clone_details),
                    "available_saved_proxy_count": len(saved_proxies),
                },
            )

        environment_records: list[AdsPowerEnvironmentRecord] = []
        for index, clone_detail in enumerate(clone_details, start=1):
            environment_name = str(
                clone_detail.adspower_environment_name
                or clone_detail.clone_name
                or profile.environment_name
                or f"{clone_detail.platform or profile.platform or 'Environment'}-{index:02d}"
            ).strip()
            proxy_record = assigned_proxies[index - 1]
            proxy_id = str(
                proxy_record.get("proxy_id")
                or proxy_record.get("id")
                or proxy_record.get("Proxy_id")
                or ""
            ).strip()
            if not proxy_id:
                return self._waiting(
                    step,
                    context_path=context_path,
                    context=context,
                    wait_reason="static_proxy_sync_required",
                    resume_from=step.action,
                    message="IPFoxy 代理列表缺少可绑定的代理ID，请重新同步代理后继续。",
                )

            result = self._adspower_adapter.execute(
                TaskStep(
                    adapter="adspower",
                    action="ensure_profile",
                    description=f"确保 AdsPower 环境存在: {environment_name}",
                    params={
                        "environment_name": environment_name,
                        "platform": clone_detail.platform or profile.platform or "",
                        "platform_username": clone_detail.platform_account or profile.platform_username or "",
                        "platform_password": clone_detail.platform_password or profile.platform_password or "",
                        "proxy_id": proxy_id,
                        "proxy_soft": "ipfoxy",
                        "proxy_type": str(proxy_record.get("type", "")).strip() or "socks5",
                    },
                )
            )
            if result.status != RunStatus.SUCCEEDED:
                return result

            profile_id = str(result.data.get("profile_id", "")).strip() or None
            profile_no = str(result.data.get("profile_no", "")).strip() or None
            environment_records.append(
                AdsPowerEnvironmentRecord(
                    clone_name=clone_detail.clone_name or environment_name,
                    environment_name=environment_name,
                    platform=clone_detail.platform or profile.platform or None,
                    profile_id=profile_id,
                    profile_no=profile_no,
                    proxy_host=str(proxy_record.get("host", "")).strip() or None,
                    proxy_port=str(proxy_record.get("port", "")).strip() or None,
                    proxy_username=str(proxy_record.get("proxy_user", "")).strip() or None,
                    proxy_password=str(proxy_record.get("proxy_password", "")).strip() or None,
                    status="ready",
                )
            )

        context.environment_records = environment_records
        if len(environment_records) == 1:
            context.environment_id = environment_records[0].profile_id
        self._refresh_adspower_environment_management_view()
        self._surface_adspower_environment_feedback(environment_records)
        context.last_success_step = step.action
        context.wait_reason = None
        self._save_context(context_path, context)
        return self._success(
            step,
            f"AdsPower 集成代理环境已创建或补全，共 {len(environment_records)} 个。",
            data={"environment_records": [record.model_dump(mode='json') for record in environment_records]},
        )

        profiles_by_name = self._query_adspower_profiles_by_name()
        environment_records: list[AdsPowerEnvironmentRecord] = []
        missing_names: list[str] = []

        for index, clone_detail in enumerate(self._clone_details(profile), start=1):
            environment_name = str(
                clone_detail.adspower_environment_name
                or clone_detail.clone_name
                or profile.environment_name
                or f"{clone_detail.platform or profile.platform or 'Environment'}-{index:02d}"
            ).strip()
            existing = profiles_by_name.get(environment_name.casefold())
            if existing is None:
                missing_names.append(environment_name)
                environment_records.append(
                    AdsPowerEnvironmentRecord(
                        clone_name=clone_detail.clone_name or environment_name,
                        environment_name=environment_name,
                        platform=clone_detail.platform or profile.platform or None,
                        status="static_sync_pending",
                    )
                )
                continue
            environment_records.append(
                AdsPowerEnvironmentRecord(
                    clone_name=clone_detail.clone_name or environment_name,
                    environment_name=environment_name,
                    platform=clone_detail.platform or profile.platform or None,
                    profile_id=str(existing.get("user_id", "")).strip() or None,
                    profile_no=str(existing.get("serial_number", "")).strip() or None,
                    status="ready",
                )
            )

        context.environment_records = environment_records
        if not missing_names:
            if len(environment_records) == 1:
                context.environment_id = environment_records[0].profile_id
            context.last_success_step = step.action
            context.wait_reason = None
            self._save_context(context_path, context)
            return self._success(
                step,
                f"静态代理模式下已识别到 {len(environment_records)} 个 AdsPower 环境。",
                data={"environment_records": [record.model_dump(mode="json") for record in environment_records]},
            )

        self._launch_adspower_if_available()
        context.static_proxy_sync_guidance_opened = True
        self._save_context(context_path, context)
        return self._waiting(
            step,
            context_path=context_path,
            context=context,
            wait_reason="static_proxy_sync_required",
            resume_from=step.action,
            message=(
                "静态代理模式：请在 AdsPower 的“代理管理”里填写代理用户ID和代理密钥并同步代理，"
                f"然后按模板中的环境名称手动创建或绑定这些环境：{', '.join(missing_names)}。"
                " 完成后点击继续，程序会再次检测这些环境是否已经存在。"
            ),
            extra_data={"missing_environment_names": missing_names},
        )

    def _export_dbit_setup_blueprint(self, step: TaskStep) -> StepResult:
        context_path = self._context_path(step)
        context = self._load_context(context_path)
        blueprint_path = Path(context_path).with_name("dbit_setup_blueprint.json")
        blueprint = self._build_dbit_blueprint(context)
        blueprint_path.write_text(json.dumps(blueprint, ensure_ascii=False, indent=2), encoding="utf-8")
        context.dbit_blueprint_path = str(blueprint_path)
        context.last_success_step = step.action
        context.wait_reason = None
        self._save_context(context_path, context)
        warning_count = len(blueprint.get("warnings", []))
        warning_note = f"，包含 {warning_count} 条提醒" if warning_count else ""
        return self._success(
            step,
            f"Dbit 配置蓝图已生成{warning_note}。",
            data={"dbit_blueprint_path": str(blueprint_path), "warnings": blueprint.get("warnings", [])},
            artifacts=[str(blueprint_path)],
        )

    def _open_dbit_configuration_workspace(self, step: TaskStep) -> StepResult:
        context_path = self._context_path(step)
        context = self._load_context(context_path)
        dbit_result = self._dbit_adapter.execute(
            TaskStep(
                adapter="dbit_octopus",
                action="run_ui_flow",
                description="打开 Dbit 配置工作区",
                params={
                    "flow_name": self._setup_workspace_flow_name,
                    "username": context.parsed_profile.dbit_username or "",
                    "password": context.parsed_profile.dbit_password or "",
                },
            )
        )
        if dbit_result.status == RunStatus.SUCCEEDED:
            frontstage_feedback = self._surface_dbit_blueprint_feedback(
                context,
                clone_index=0,
                automation_mode=str(dbit_result.data.get("automation_mode", "") or "control_panel").strip(),
                show_system_config=True,
            )
            context.last_success_step = step.action
            context.wait_reason = None
            self._save_context(context_path, context)
            data = dict(dbit_result.data)
            if frontstage_feedback:
                data["frontstage_feedback"] = frontstage_feedback
            return self._success(step, "Dbit 配置工作区已就绪。", data=data or None, artifacts=dbit_result.artifacts)
        if dbit_result.status == RunStatus.WAITING_INPUT:
            if bool(dbit_result.data.get("control_panel_authorized") or dbit_result.data.get("control_panel_ready")):
                frontstage_feedback = self._surface_dbit_blueprint_feedback(
                    context,
                    clone_index=0,
                    automation_mode="control_panel",
                    show_system_config=True,
                )
                context.last_success_step = step.action
                context.wait_reason = None
                self._save_context(context_path, context)
                data = dict(dbit_result.data)
                if frontstage_feedback:
                    data["frontstage_feedback"] = frontstage_feedback
                return self._success(
                    step,
                    "Dbit 配置工作区已在后台接管模式下就绪。",
                    data=data,
                    artifacts=dbit_result.artifacts,
                )
            logger.info("DBit workspace is not fully visible yet; continuing onboarding without blocking: %s", dbit_result.message)
            context.last_success_step = step.action
            context.wait_reason = None
            self._save_context(context_path, context)
            return self._success(
                step,
                "Dbit 配置工作区已启动，前台页未稳定前改由后续写入步骤继续推进。",
                data={"workspace_deferred": True, **dict(dbit_result.data)},
                artifacts=dbit_result.artifacts,
            )
        return StepResult(
            step_id=step.id,
            status=dbit_result.status,
            message=dbit_result.message,
            artifacts=dbit_result.artifacts,
            data=dbit_result.data,
        )

    def _apply_dbit_setup_blueprint_legacy(self, step: TaskStep) -> StepResult:
        context_path = self._context_path(step)
        context = self._load_context(context_path)
        blueprint_path = str(context.dbit_blueprint_path or "").strip()
        if not blueprint_path:
            raise ValueError("当前没有 Dbit 配置蓝图，无法写入。")

        clone_total = len(context.environment_records) or 1
        clone_index = min(context.next_clone_index, max(clone_total - 1, 0))
        dbit_result = self._dbit_adapter.execute(
            TaskStep(
                adapter="dbit_octopus",
                action="apply_blueprint",
                description="写入 Dbit 配置蓝图",
                params={
                    "blueprint_path": blueprint_path,
                    "clone_index": clone_index,
                    "apply_system_config": not context.system_config_applied,
                },
            )
        )

        context.system_config_applied = context.system_config_applied or bool(
            dbit_result.data.get("system_config_applied", False)
        )
        if dbit_result.status == RunStatus.WAITING_INPUT:
            self._save_context(context_path, context)
            return self._waiting(
                step,
                context_path=context_path,
                context=context,
                wait_reason="dbit_blueprint_manual_completion_required",
                resume_from=step.action,
                message=dbit_result.message,
                artifacts=dbit_result.artifacts,
                extra_data=dbit_result.data,
            )
        if dbit_result.status != RunStatus.SUCCEEDED:
            return dbit_result

        if clone_index + 1 < clone_total:
            context.next_clone_index = clone_index + 1
            self._save_context(context_path, context)
            return self._waiting(
                step,
                context_path=context_path,
                context=context,
                wait_reason="dbit_switch_next_clone",
                resume_from=step.action,
                message=(
                    f"已写入第 {clone_index + 1} 个分身配置。"
                    f" 请在 Dbit 中切换到第 {clone_index + 2} 个分身后点击继续。"
                ),
                artifacts=dbit_result.artifacts,
                extra_data=dbit_result.data,
            )

        context.next_clone_index = clone_index + 1
        context.last_success_step = step.action
        context.wait_reason = None
        self._save_context(context_path, context)
        return self._success(
            step,
            f"Dbit 蓝图写入完成，共处理 {clone_total} 个分身。",
            data=dbit_result.data,
            artifacts=dbit_result.artifacts,
        )

    def _apply_dbit_setup_blueprint(self, step: TaskStep) -> StepResult:
        context_path = self._context_path(step)
        context = self._load_context(context_path)
        blueprint_path = str(context.dbit_blueprint_path or "").strip()
        if not blueprint_path:
            raise ValueError("当前没有 Dbit 配置蓝图，无法写入。")

        clone_total = len(context.environment_records) or 1
        clone_index = min(context.next_clone_index, max(clone_total - 1, 0))
        last_result: StepResult | None = None
        final_frontstage_feedback: dict[str, Any] | None = None

        while clone_index < clone_total:
            apply_system_config = not context.system_config_applied
            dbit_result = self._dbit_adapter.execute(
                TaskStep(
                    adapter="dbit_octopus",
                    action="apply_blueprint",
                    description="写入 Dbit 配置蓝图",
                    params={
                        "blueprint_path": blueprint_path,
                        "clone_index": clone_index,
                        "apply_system_config": apply_system_config,
                        "prefer_control_panel": True,
                    },
                )
            )

            context.system_config_applied = context.system_config_applied or bool(
                dbit_result.data.get("system_config_applied", False)
            )
            if dbit_result.status == RunStatus.WAITING_INPUT:
                context.next_clone_index = clone_index
                self._save_context(context_path, context)
                return self._waiting(
                    step,
                    context_path=context_path,
                    context=context,
                    wait_reason="dbit_blueprint_manual_completion_required",
                    resume_from=step.action,
                    message=dbit_result.message,
                    artifacts=dbit_result.artifacts,
                    extra_data=dbit_result.data,
                )
            if dbit_result.status != RunStatus.SUCCEEDED:
                return dbit_result

            last_result = dbit_result
            frontstage_feedback = self._surface_dbit_blueprint_feedback(
                context,
                clone_index=clone_index,
                automation_mode=str(dbit_result.data.get("automation_mode", "") or "").strip(),
                show_system_config=apply_system_config,
            )
            if frontstage_feedback:
                dbit_result.data["frontstage_feedback"] = frontstage_feedback
                final_frontstage_feedback = frontstage_feedback
            next_clone_index = clone_index + 1
            if next_clone_index >= clone_total:
                context.next_clone_index = next_clone_index
                context.last_success_step = step.action
                context.wait_reason = None
                self._save_context(context_path, context)
                final_data = dict(dbit_result.data)
                if final_frontstage_feedback:
                    final_data["frontstage_feedback"] = final_frontstage_feedback
                return self._success(
                    step,
                    f"Dbit 蓝图写入完成，共处理 {clone_total} 个分身。",
                    data=final_data,
                    artifacts=dbit_result.artifacts,
                )

            current_platform = self._clone_platform_for_index(context, clone_index)
            next_platform = self._clone_platform_for_index(context, next_clone_index)
            automation_mode = str(dbit_result.data.get("automation_mode", "") or "").strip()
            if not self._can_auto_advance_dbit_clone(current_platform, next_platform, automation_mode):
                context.next_clone_index = next_clone_index
                self._save_context(context_path, context)
                return self._waiting(
                    step,
                    context_path=context_path,
                    context=context,
                    wait_reason="dbit_switch_next_clone",
                    resume_from=step.action,
                    message=(
                        f"已写入第 {clone_index + 1} 个分身配置。"
                        f" 请在 Dbit 中切换到第 {next_clone_index + 1} 个分身后点击继续。"
                    ),
                    artifacts=dbit_result.artifacts,
                    extra_data=dbit_result.data,
                )

            clone_index = next_clone_index
            context.next_clone_index = clone_index
            self._save_context(context_path, context)

        if last_result is None:
            raise ValueError("Dbit 蓝图写入未产生任何结果。")
        final_data = dict(last_result.data)
        if final_frontstage_feedback:
            final_data["frontstage_feedback"] = final_frontstage_feedback
        return self._success(
            step,
            f"Dbit 蓝图写入完成，共处理 {clone_total} 个分身。",
            data=final_data,
            artifacts=last_result.artifacts,
        )

    def _clone_platform_for_index(self, context: NewUserSetupContext, clone_index: int) -> str:
        clone_details = self._clone_details(context.parsed_profile)
        if clone_index < len(clone_details):
            platform = _normalize_dbit_platform(clone_details[clone_index].platform)
            if platform:
                return platform
        if clone_index < len(context.environment_records):
            platform = _normalize_dbit_platform(context.environment_records[clone_index].platform)
            if platform:
                return platform
        return _normalize_dbit_platform(context.parsed_profile.platform)

    @staticmethod
    def _can_auto_advance_dbit_clone(current_platform: str, next_platform: str, automation_mode: str = "") -> bool:
        if automation_mode == "control_panel":
            return True
        return bool(current_platform and next_platform and current_platform != next_platform)

    def _finish_manual_handoff(self, step: TaskStep) -> StepResult:
        context_path = self._context_path(step)
        context = self._load_context(context_path)
        context.setup_finished_before_manual_start = True
        context.last_success_step = step.action
        context.wait_reason = None
        self._save_context(context_path, context)
        environment_count = len(context.environment_records)
        blueprint_note = f" 配置蓝图: {context.dbit_blueprint_path}。" if context.dbit_blueprint_path else ""
        clone_count = max(context.next_clone_index, len(self._clone_details(context.parsed_profile)))
        return self._success(
            step,
            f"已完成安装向导当前阶段：AdsPower 环境 {environment_count} 个已准备，Dbit 已处理 {clone_count} 个分身配置，流程未自动启动分身。"
            f"{blueprint_note}",
        )

    def _surface_adspower_environment_feedback(
        self,
        environment_records: list[AdsPowerEnvironmentRecord],
    ) -> None:
        if not environment_records:
            return
        try:
            window = self._connect_adspower_window(timeout_seconds=2.0)
        except Exception:
            window = None
        if window is None:
            return
        if self._looks_like_adspower_login_window(window):
            self._highlight_adspower_login_region(window)
            return
        for tokens in (
            ["环境", "浏览器环境", "Profiles", "Profile"],
            ["环境管理", "浏览器环境", "Profiles", "Profile"],
        ):
            try:
                self._windows_ui_driver.click_text(
                    window,
                    texts=tokens,
                    control_types=["Button", "TabItem", "ListItem", "Hyperlink", "Text", "Document"],
                    max_depth=10,
                    max_nodes=480,
                    skip_child_classes=[],
                )
                time.sleep(0.3)
                break
            except Exception:
                continue
        target_names = [
            str(record.environment_name or "").strip()
            for record in environment_records
            if str(record.environment_name or "").strip()
        ]
        for name in target_names[:2]:
            try:
                self._windows_ui_driver.click_text(
                    window,
                    texts=[name],
                    control_types=["Text", "Document", "ListItem", "Button"],
                    max_depth=12,
                    max_nodes=800,
                    skip_child_classes=[],
                )
                return
            except Exception:
                continue

    def _surface_dbit_blueprint_feedback(
        self,
        context: NewUserSetupContext,
        *,
        clone_index: int,
        automation_mode: str,
        show_system_config: bool = False,
    ) -> dict[str, Any]:
        if automation_mode != "control_panel":
            return {}
        try:
            platform = self._clone_platform_for_index(context, clone_index)
        except Exception:
            return {}
        try:
            result = self._dbit_adapter.execute(
                TaskStep(
                    adapter="dbit_octopus",
                    action="surface_frontstage_feedback",
                    description="同步 Dbit 前台页面反馈",
                    params={
                        "username": context.parsed_profile.dbit_username or "",
                        "password": context.parsed_profile.dbit_password or "",
                        "show_system_config": show_system_config,
                        "platform": platform,
                    },
                )
            )
        except Exception:
            return {"status": "failed", "message": "surface_frontstage_feedback raised exception"}
        return {
            "status": result.status.value,
            "message": result.message,
            "data": result.data,
            "artifacts": result.artifacts,
        }

    def _looks_like_adspower_login_window(self, window: Any) -> bool:
        login_tokens = [
            "登录",
            "登入",
            "Sign in",
            "Login",
            "注册",
            "记住密码",
            "Google",
        ]
        for token in login_tokens:
            try:
                match = self._windows_ui_driver.find_first_text_match(
                    window,
                    texts=[token],
                    control_types=["Text", "Document", "Button", "Hyperlink"],
                    max_depth=8,
                    max_nodes=360,
                    skip_child_classes=[],
                )
            except Exception:
                match = None
            if match is not None:
                return True
        account_control, password_control = self._find_adspower_login_field_controls(window, "account")
        return account_control is not None and password_control is not None

    def _highlight_adspower_login_region(self, window: Any) -> None:
        try:
            candidates = self._collect_adspower_input_candidates(window)
        except Exception:
            candidates = []
        for candidate in candidates[:2]:
            try:
                self._windows_ui_driver.highlight_control(candidate["control"], duration_seconds=0.55)
            except Exception:
                continue

    @staticmethod
    def _context_path(step: TaskStep) -> str:
        path = str(step.params.get("context_path", "")).strip()
        if not path:
            raise ValueError("新用户安装任务缺少 context_path。")
        return path

    @staticmethod
    def _load_context(context_path: str) -> NewUserSetupContext:
        path = Path(context_path)
        if not path.exists():
            raise ValueError(f"找不到安装上下文文件: {path}")
        return NewUserSetupContext.model_validate_json(path.read_text(encoding="utf-8"))

    @staticmethod
    def _save_context(context_path: str, context: NewUserSetupContext) -> None:
        Path(context_path).write_text(context.model_dump_json(indent=2), encoding="utf-8")

    def _waiting(
        self,
        step: TaskStep,
        *,
        context_path: str,
        context: NewUserSetupContext,
        wait_reason: str,
        resume_from: str,
        message: str,
        artifacts: list[str] | None = None,
        extra_data: dict[str, Any] | None = None,
    ) -> StepResult:
        context.wait_reason = wait_reason
        self._save_context(context_path, context)
        resume_task = self._task_factory.build_resume_task(context_path, resume_from)
        data = {
            "wait_reason": wait_reason,
            "resume_task": resume_task.model_dump(mode="json"),
        }
        if extra_data:
            data.update(extra_data)
        return StepResult(
            step_id=step.id,
            status=RunStatus.WAITING_INPUT,
            message=message,
            data=data,
            artifacts=artifacts or [],
        )

    @staticmethod
    def _success(
        step: TaskStep,
        message: str,
        data: dict[str, Any] | None = None,
        artifacts: list[str] | None = None,
    ) -> StepResult:
        return StepResult(
            step_id=step.id,
            status=RunStatus.SUCCEEDED,
            message=message,
            data=data or {},
            artifacts=artifacts or [],
        )

    def _failed(
        self,
        step: TaskStep,
        *,
        context_path: str,
        context: NewUserSetupContext,
        message: str,
        data: dict[str, Any] | None = None,
        artifacts: list[str] | None = None,
    ) -> StepResult:
        context.wait_reason = None
        self._save_context(context_path, context)
        return StepResult(
            step_id=step.id,
            status=RunStatus.FAILED,
            message=message,
            data=data or {},
            artifacts=artifacts or [],
        )

    @staticmethod
    def _is_process_running(image_name: str) -> bool:
        completed = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {image_name}"],
            check=False,
            capture_output=True,
            text=True,
        )
        return image_name.lower() in (completed.stdout or "").lower()

    @staticmethod
    def _stop_processes_by_image_name(image_name: str) -> None:
        if not str(image_name).strip():
            return
        subprocess.run(
            ["taskkill", "/F", "/IM", image_name],
            check=False,
            capture_output=True,
            text=True,
        )

    @staticmethod
    def _build_installer_command(
        installer_path: Path,
        install_root: Path | None = None,
        install_target: str = "",
    ) -> list[str]:
        if installer_path.suffix.lower() == ".msi":
            command = ["msiexec", "/i", str(installer_path)]
            if install_root is not None:
                command.append(f"TARGETDIR={install_root}")
            return command
        command = [str(installer_path)]
        if install_root is not None:
            if install_target == "dbit":
                command.extend(["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-", f"/DIR={install_root}"])
            elif install_target == "adspower":
                command.extend(["/S", f"/D={install_root}"])
        return command

    def _launch_installer(
        self,
        installer_path: Path,
        install_root: Path | None = None,
        install_target: str = "",
    ) -> None:
        if install_root is not None:
            install_root.mkdir(parents=True, exist_ok=True)
        command = self._build_installer_command(installer_path, install_root=install_root, install_target=install_target)
        if installer_path.suffix.lower() == ".msi":
            subprocess.Popen(command)
            return
        subprocess.Popen(command, cwd=str((install_root or installer_path.parent)))
        if install_target == "adspower":
            self._start_adspower_installer_assistant(timeout_seconds=min(self._installer_window_timeout_seconds, 6.0))

    @staticmethod
    def _adspower_executable_candidates(install_root: Path) -> list[Path]:
        return [
            install_root / "AdsPower Global.exe",
            install_root / "AdsPower Global" / "AdsPower Global.exe",
        ]

    def _complete_adspower_installer_if_visible(self, timeout_seconds: float | None = None) -> bool:
        title_patterns = [
            r".*AdsPower.*安装.*",
            r".*AdsPower.*Setup.*",
            r".*AdsPower.*",
        ]
        button_texts = [
            ["我同意(I)", "我同意", "同意", "I Agree"],
            ["安装", "立即安装", "Install"],
            ["下一步", "继续", "Next"],
            ["完成", "Finish", "关闭", "Close"],
            ["确认", "确定", "OK"],
        ]
        deadline = time.monotonic() + (timeout_seconds if timeout_seconds is not None else self._installer_window_timeout_seconds)
        clicked_any = False
        while time.monotonic() < deadline:
            window = None
            for backend in ("win32", "uia"):
                try:
                    window = self._windows_ui_driver.connect_any(
                        title_patterns,
                        timeout_seconds=1.0,
                        backend=backend,
                    )
                    break
                except Exception:
                    continue
            if window is None:
                if clicked_any:
                    return True
                time.sleep(0.5)
                continue
            try:
                self._windows_ui_driver.focus_window(window)
            except Exception:
                pass
            clicked_this_round = False
            for texts in button_texts:
                try:
                    self._windows_ui_driver.click_text(
                        window,
                        texts=texts,
                        control_types=["Button", "Text", "Hyperlink"],
                        max_depth=8,
                        max_nodes=320,
                        skip_child_classes=[],
                    )
                    clicked_any = True
                    clicked_this_round = True
                    time.sleep(0.8)
                    break
                except Exception:
                    continue
            if clicked_this_round:
                continue
            try:
                self._windows_ui_driver.send_keys("{PGDN}")
                time.sleep(0.2)
            except Exception:
                pass
            try:
                self._windows_ui_driver.send_keys("%I")
                clicked_any = True
                time.sleep(0.8)
                continue
            except Exception:
                pass
            if clicked_any:
                return True
            time.sleep(0.5)
        return clicked_any

    def _start_adspower_installer_assistant(self, timeout_seconds: float | None = None) -> None:
        threading.Thread(
            target=self._complete_adspower_installer_if_visible,
            kwargs={"timeout_seconds": timeout_seconds},
            daemon=True,
        ).start()

    def _start_windows_security_alert_assistant(
        self,
        *,
        process_tokens: list[str],
        timeout_seconds: float,
    ) -> None:
        threading.Thread(
            target=self._accept_windows_security_alert_if_visible,
            kwargs={"process_tokens": process_tokens, "timeout_seconds": timeout_seconds},
            daemon=True,
        ).start()

    def _accept_windows_security_alert_if_visible(
        self,
        *,
        process_tokens: list[str],
        timeout_seconds: float,
    ) -> bool:
        title_patterns = [
            r".*Windows Security Alert.*",
            r".*Windows Defender Firewall.*",
            r".*安全警报.*",
            r".*防火墙.*",
        ]
        button_texts = [
            ["允许访问", "允许", "Allow access", "Allow"],
            ["确定", "OK"],
        ]
        deadline = time.monotonic() + max(timeout_seconds, 3.0)
        clicked_any = False
        lowered_tokens = [token.casefold() for token in process_tokens if token]
        while time.monotonic() < deadline:
            window = None
            for backend in ("uia", "win32"):
                try:
                    window = self._windows_ui_driver.connect_any(
                        title_patterns,
                        timeout_seconds=0.8,
                        backend=backend,
                    )
                    break
                except Exception:
                    continue
            if window is None:
                time.sleep(0.4)
                continue
            if not self._window_contains_any_token(window, lowered_tokens):
                time.sleep(0.4)
                continue
            logger.info("Detected Windows security alert for %s; attempting automatic allow.", ", ".join(process_tokens))
            try:
                self._windows_ui_driver.focus_window(window)
            except Exception:
                pass
            for texts in button_texts:
                try:
                    self._windows_ui_driver.click_text(
                        window,
                        texts=texts,
                        control_types=["Button", "Text", "Hyperlink"],
                        max_depth=8,
                        max_nodes=240,
                        skip_child_classes=[],
                    )
                    clicked_any = True
                    logger.info("Windows security alert accepted via button: %s", "/".join(texts))
                    time.sleep(0.8)
                    break
                except Exception:
                    continue
            if clicked_any:
                return True
        return clicked_any

    def _window_contains_any_token(self, window: Any, lowered_tokens: list[str]) -> bool:
        if not lowered_tokens:
            return False
        for control in self._windows_ui_driver.iter_controls(
            window,
            max_depth=8,
            max_nodes=240,
            skip_child_classes=[],
        ):
            text = self._windows_ui_driver.read_text(control).casefold()
            if text and any(token in text for token in lowered_tokens):
                return True
        return False

    def _ensure_windows_firewall_allows_program(self, executable_path: Path, rule_group: str) -> None:
        if os.name != "nt" or not executable_path.exists():
            return
        normalized_program = str(executable_path.resolve())
        rule_name = f"CCLocalAgent {rule_group}"
        check = subprocess.run(
            ["netsh", "advfirewall", "firewall", "show", "rule", f"name={rule_name}"],
            check=False,
            capture_output=True,
            text=True,
        )
        if check.returncode == 0 and normalized_program.casefold() in (check.stdout or "").casefold():
            return
        add_rule = subprocess.run(
            [
                "netsh",
                "advfirewall",
                "firewall",
                "add",
                "rule",
                f"name={rule_name}",
                "dir=in",
                "action=allow",
                f"program={normalized_program}",
                "enable=yes",
                "profile=any",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if add_rule.returncode == 0:
            logger.info("Firewall allow rule ensured for %s", normalized_program)
        else:
            logger.warning(
                "Failed to ensure firewall allow rule for %s: %s",
                normalized_program,
                (add_rule.stderr or add_rule.stdout or "").strip(),
            )

    def _launch_adspower_if_available(self) -> None:
        executable = self._resolved_adspower_executable_path()
        if not executable.exists():
            return
        if self._is_process_running(executable.name):
            return
        self._ensure_windows_firewall_allows_program(executable, "AdsPower Global")
        self._start_windows_security_alert_assistant(
            process_tokens=["AdsPower", "AdsPower Global", executable.name],
            timeout_seconds=min(self._installer_window_timeout_seconds, 12.0),
        )
        logger.info("Launching AdsPower executable: %s", executable)
        subprocess.Popen([str(executable)], cwd=str(executable.parent))

    def _adspower_executable_exists(self) -> bool:
        return self._resolved_adspower_executable_path().exists()

    def _refresh_adspower_environment_management_view(self) -> bool:
        self._close_adspower_guidance_windows()
        self._launch_adspower_if_available()
        window = self._connect_adspower_window(timeout_seconds=3.0)
        if window is None and self._restore_adspower_window_rect() is None:
            return False
        time.sleep(0.3)
        self._windows_ui_driver.send_keys("{F5}")
        time.sleep(1.0)
        return True

    @staticmethod
    def _close_adspower_guidance_windows() -> int:
        try:
            import ctypes
            from ctypes import wintypes
        except Exception:
            return 0

        user32 = ctypes.windll.user32
        WM_CLOSE = 0x0010
        closed = 0

        def is_guidance_title(title: str) -> bool:
            lowered = str(title or "").strip().casefold()
            if not lowered or "adspower" not in lowered:
                return False
            if lowered.startswith("adspower browser |"):
                return False
            return any(token in lowered for token in ("register", "guide", "指南", "注册"))

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def enum_proc(hwnd, _lparam):
            nonlocal closed
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            title = buffer.value.strip()
            if is_guidance_title(title):
                user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
                closed += 1
            return True

        user32.EnumWindows(enum_proc, 0)
        return closed

    @staticmethod
    def _open_url(url: str) -> bool:
        if not url:
            return False
        try:
            return bool(webbrowser.open(url))
        except Exception:
            return False

    def _open_guidance_once(
        self,
        context: NewUserSetupContext,
        *,
        context_flag: str,
        url: str,
    ) -> bool:
        if not url:
            return False
        if bool(getattr(context, context_flag, False)):
            return True
        opened = self._open_url(url)
        if opened:
            setattr(context, context_flag, True)
        return opened

    @staticmethod
    def _normalize_install_root(preferred: Path) -> Path:
        candidate = preferred.expanduser()
        anchor = Path(candidate.anchor) if candidate.anchor else None
        if anchor is None or anchor.exists():
            return candidate

        relative_parts = candidate.parts[1:] if len(candidate.parts) > 1 else ()
        for drive in _available_non_system_windows_roots():
            if drive == anchor:
                continue
            return drive.joinpath(*relative_parts) if relative_parts else drive
        return candidate

    @staticmethod
    def _install_root_is_available(path: Path) -> bool:
        anchor = Path(path.anchor) if path.anchor else None
        return anchor is None or anchor.exists()

    def _resolved_dbit_install_root(self, profile: OnboardingProfile | None = None) -> Path:
        install_path = str(getattr(profile, "install_path", "") or "").strip()
        if install_path:
            return Path(install_path).expanduser()
        if self._configured_dbit_executable_path is not None and self._configured_dbit_executable_path.exists():
            return self._configured_dbit_executable_path.parent
        if self._default_dbit_executable_path.exists():
            return self._default_dbit_install_root
        return self._default_dbit_install_root

    def _resolved_dbit_executable_path(self, profile: OnboardingProfile | None = None) -> Path:
        install_path = str(getattr(profile, "install_path", "") or "").strip()
        install_root_candidate = Path(install_path).expanduser() / "DBit-octopus.exe" if install_path else None
        if install_root_candidate is not None and install_root_candidate.exists():
            return install_root_candidate
        if self._configured_dbit_executable_path is not None and self._configured_dbit_executable_path.exists():
            return self._configured_dbit_executable_path
        if self._default_dbit_executable_path.exists():
            return self._default_dbit_executable_path
        return install_root_candidate or self._configured_dbit_executable_path or self._default_dbit_executable_path

    def _is_dbit_installation_ready(
        self,
        executable_path: Path,
        install_root: Path,
        *,
        probe_runtime: bool,
    ) -> tuple[bool, dict[str, Any]]:
        reasons, meta = self._dbit_installation_incomplete_reasons(executable_path, install_root)
        if reasons:
            return False, {"incomplete_reasons": reasons, **meta}
        if probe_runtime and not self._probe_dbit_runtime_ready(executable_path, install_root):
            return False, {"incomplete_reasons": ["runtime_probe_failed"], **meta}
        return True, meta

    @staticmethod
    def _dbit_installation_incomplete_reasons(
        executable_path: Path,
        install_root: Path,
    ) -> tuple[list[str], dict[str, Any]]:
        executable = executable_path.expanduser()
        root = install_root.expanduser()
        reasons: list[str] = []
        meta: dict[str, Any] = {
            "dbit_executable_path": str(executable),
            "dbit_install_root": str(root),
        }
        if not root.exists():
            reasons.append("install_root_missing")
            return reasons, meta
        if not executable.exists():
            reasons.append("executable_missing")
        required_files = [
            root / "DBit-octopus.dll",
            root / "DBit-octopus.deps.json",
            root / "DBit-octopus.runtimeconfig.json",
        ]
        missing_required = [str(path) for path in required_files if not path.exists()]
        if missing_required:
            reasons.append("required_files_missing")
            meta["missing_required_files"] = missing_required
        installer_tmp_files = sorted(str(path) for path in root.glob("is-*.tmp"))
        if installer_tmp_files:
            reasons.append("installer_tmp_files_present")
            meta["installer_tmp_files"] = installer_tmp_files
        uninstaller_data = root / "unins000.dat"
        if uninstaller_data.exists():
            try:
                size = int(uninstaller_data.stat().st_size)
            except OSError:
                size = -1
            meta["unins000_dat_size"] = size
            if size == 0:
                reasons.append("uninstaller_data_empty")
        return reasons, meta

    def _probe_dbit_runtime_ready(self, executable_path: Path, install_root: Path) -> bool:
        executable = executable_path.expanduser()
        root = install_root.expanduser()
        image_name = executable.name
        if not executable.exists():
            return False
        if self._is_process_running(image_name):
            return True
        try:
            process = subprocess.Popen([str(executable)], cwd=str(root))
        except Exception:
            return False
        deadline = time.monotonic() + max(float(self._config.get("dbit_runtime_probe_seconds", 8)), 2.0)
        data_root = root / "data"
        while time.monotonic() < deadline:
            if self._is_process_running(image_name):
                return True
            if data_root.exists():
                return True
            if process.poll() is not None:
                break
            time.sleep(0.5)
        return False

    @staticmethod
    def _remove_dbit_installation_root(install_root: Path) -> None:
        target = install_root.expanduser()
        if not target.exists():
            return
        try:
            resolved = target.resolve()
        except Exception:
            resolved = target
        anchor = Path(resolved.anchor) if resolved.anchor else None
        if anchor is not None and resolved == anchor:
            raise ValueError(f"拒绝删除磁盘根目录: {resolved}")
        if "dbit" not in resolved.name.casefold() and not (resolved / "DBit-octopus.exe").exists():
            raise ValueError(f"拒绝删除非 DBit 安装目录: {resolved}")

        def _handle_remove_error(func, path: str, _exc_info: Any) -> None:
            os.chmod(path, 0o777)
            func(path)

        shutil.rmtree(resolved, onerror=_handle_remove_error)

    def _resolved_adspower_executable_path(self) -> Path:
        resolver = getattr(self._adspower_adapter, "_resolved_executable_path", None)
        if callable(resolver):
            resolved = resolver()
            if isinstance(resolved, Path):
                return resolved.expanduser()
        configured_path = str(getattr(self._adspower_adapter, "_configured_executable_path", "") or "").strip()
        configured = Path(configured_path).expanduser() if configured_path else None
        if configured is not None and configured.exists():
            return configured
        executable_path = str(getattr(self._adspower_adapter, "_executable_path", "") or "").strip()
        executable = Path(executable_path).expanduser() if executable_path else None
        if executable is not None and executable.exists():
            return executable
        for candidate in self._default_adspower_executable_candidates:
            if candidate.exists():
                return candidate
        return configured or executable or self._default_adspower_executable_path

    def _resolved_adspower_install_root(self) -> Path:
        executable_path = self._resolved_adspower_executable_path()
        if executable_path.exists():
            parent = executable_path.parent
            return parent.parent if parent.name.casefold() == "adspower global" else parent
        return self._default_adspower_install_root

    def _download_adspower_installer_if_needed(self) -> Path | None:
        download_url = str(self._adspower_download_url or "").strip()
        lowered = download_url.casefold()
        if "adspower.com/download" not in lowered and "version.adspower.net/" not in lowered:
            return None
        installers_dir = self._resolved_adspower_install_root() / "installers"
        installers_dir.mkdir(parents=True, exist_ok=True)
        architecture = self._preferred_adspower_windows_architecture()
        existing = sorted(installers_dir.glob(f"AdsPower-Global-*-{architecture}.exe"))
        if existing:
            return existing[-1]
        resolved_url = self._resolve_adspower_installer_download_url(download_url, architecture=architecture)
        if not resolved_url:
            return None
        target_path = installers_dir / Path(resolved_url.split("?")[0]).name
        if target_path.exists():
            return target_path
        self._download_file_with_urllib(resolved_url, target_path)
        return target_path

    @classmethod
    def _resolve_adspower_installer_download_url(cls, download_url: str, *, architecture: str = "") -> str:
        normalized_architecture = (architecture or cls._preferred_adspower_windows_architecture()).casefold()
        if normalized_architecture not in {"x64", "x86"}:
            normalized_architecture = "x64"
        variant = "win64-global" if normalized_architecture == "x64" else "win32-global"
        if download_url.casefold().endswith(".exe"):
            lowered = download_url.casefold()
            if "version.adspower.net/software/" not in lowered:
                return download_url
            normalized_url = re.sub(
                r"/software/(?:win32|win64)-global/",
                f"/software/{variant}/",
                download_url,
                flags=re.I,
            )
            return re.sub(
                r"-(?:x64|x86)\.exe(?=$|[?#])",
                f"-{normalized_architecture}.exe",
                normalized_url,
                flags=re.I,
            )
        with httpx.Client(timeout=20, follow_redirects=True, trust_env=False) as client:
            response = client.get(download_url)
        response.raise_for_status()
        matches = re.findall(rf"https://version\.adspower\.net/software/{variant}/[^\"']+\.exe", response.text, re.I)
        if not matches:
            matches = re.findall(rf"//version\.adspower\.net/software/{variant}/[^\"']+\.exe", response.text, re.I)
            matches = [f"https:{item}" for item in matches]
        if not matches:
            return ""

        def version_key(url: str) -> tuple[int, ...]:
            match = re.search(rf"AdsPower-Global-(\d+(?:\.\d+)+)-{normalized_architecture}\.exe", url, re.I)
            if not match:
                return (0,)
            return tuple(int(part) for part in match.group(1).split("."))

        return max(matches, key=version_key)

    @staticmethod
    def _windows_architecture_markers() -> list[str]:
        return [
            str(os.environ.get("PROCESSOR_ARCHITEW6432", "") or ""),
            str(os.environ.get("PROCESSOR_ARCHITECTURE", "") or ""),
            str(platform.machine() or ""),
            str(platform.architecture()[0] or ""),
        ]

    @classmethod
    def _preferred_adspower_windows_architecture(cls) -> str:
        for marker in cls._windows_architecture_markers():
            normalized = marker.casefold()
            if any(token in normalized for token in ("amd64", "x86_64", "arm64", "aarch64", "ia64", "64bit")):
                return "x64"
            if any(token in normalized for token in ("x86", "i386", "i686", "32bit")):
                return "x86"
        return "x64"

    @classmethod
    def _detected_adspower_windows_label(cls) -> str:
        return "Windows 64位" if cls._preferred_adspower_windows_architecture() == "x64" else "Windows 32位"

    @staticmethod
    def _download_file_with_urllib(download_url: str, target_path: Path) -> None:
        request = urllib.request.Request(download_url, headers={"User-Agent": "Mozilla/5.0"})
        partial_path = target_path.with_suffix(f"{target_path.suffix}.part")
        try:
            with urllib.request.urlopen(request, timeout=60) as response, partial_path.open("wb") as handle:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    handle.write(chunk)
            partial_path.replace(target_path)
        except Exception:
            partial_path.unlink(missing_ok=True)
            raise

    def _verify_adspower_ready_v3(self, step: TaskStep) -> StepResult:
        return self._verify_adspower_ready_fast(step)

        context_path = self._context_path(step)
        context = self._load_context(context_path)
        profile = context.parsed_profile

        service_result = self._probe_adspower_service_v3(step)
        if (
            service_result.status == RunStatus.SUCCEEDED
            and profile.adspower_username
            and profile.adspower_password
            and profile.adspower_plan_ready is not False
        ):
            self._close_adspower_guidance_windows()
            context.adspower_purchase_status = "ready"
            context.last_success_step = step.action
            context.wait_reason = None
            self._save_context(context_path, context)
            return self._success(step, "AdsPower 本地服务已就绪。")

        install_result = self._ensure_adspower_installed_v3(step, context_path, context)
        if isinstance(install_result, StepResult):
            return install_result

        if profile.adspower_plan_ready is False and profile.adspower_username and profile.adspower_password:
            opened = self._open_guidance_once(
                context,
                context_flag="adspower_pricing_guidance_opened",
                url=self._adspower_pricing_url,
            )
            context.adspower_purchase_status = "required"
            return self._waiting(
                step,
                context_path=context_path,
                context=context,
                wait_reason="adspower_purchase_required",
                resume_from=step.action,
                message=(
                    "资料标记 AdsPower 套餐尚未开通，已尝试打开官方套餐页。请开通后点击继续。"
                    if opened
                    else "资料标记 AdsPower 套餐尚未开通，请开通后点击继续。"
                ),
            )

        if not profile.adspower_username or not profile.adspower_password:
            self._launch_adspower_if_available()
            opened = self._open_guidance_once(
                context,
                context_flag="adspower_registration_guidance_opened",
                url=self._adspower_register_url,
            )
            context.adspower_purchase_status = "login_required"
            return self._waiting(
                step,
                context_path=context_path,
                context=context,
                wait_reason="adspower_login_required",
                resume_from=step.action,
                message=(
                    "缺少 AdsPower 登录信息，已拉起 AdsPower 登录页。没有账号就先注册，有账号就直接登录，完成后点击继续。"
                    if opened
                    else "缺少 AdsPower 登录信息，请注册或补齐登录信息后继续。"
                ),
            )

        self._launch_adspower_if_available()
        login_attempted = self._attempt_adspower_login(profile)
        if self._wait_for_adspower_service_ready(timeout_seconds=20):
            self._close_adspower_guidance_windows()
            context.adspower_purchase_status = "ready"
            context.last_success_step = step.action
            context.wait_reason = None
            self._save_context(context_path, context)
            return self._success(
                step,
                "AdsPower 已按表格中的账号信息自动登录并就绪。",
                data=install_result,
            )

        context.adspower_purchase_status = "login_required"
        return self._waiting(
            step,
            context_path=context_path,
            context=context,
            wait_reason="adspower_login_required",
            resume_from=step.action,
            message=(
                "AdsPower 已拉起登录页，并已按表格中的账号信息尝试自动登录。请确认当前登录状态后点击继续。"
                if login_attempted
                else "AdsPower 已拉起登录页，但自动登录未完成。请按表格中的账号信息登录后点击继续。"
            ),
            extra_data=install_result,
        )

    def _verify_adspower_ready_fast(self, step: TaskStep) -> StepResult:
        context_path = self._context_path(step)
        context = self._load_context(context_path)
        profile = context.parsed_profile

        ready, probe_message = self._probe_adspower_service_state()
        logger.info("AdsPower readiness probe at step 3: ready=%s message=%s", ready, probe_message)
        if ready and profile.adspower_username and profile.adspower_password and profile.adspower_plan_ready is not False:
            self._close_adspower_guidance_windows()
            context.adspower_purchase_status = "ready"
            context.last_success_step = step.action
            context.wait_reason = None
            self._save_context(context_path, context)
            return self._success(step, "AdsPower 本地服务已就绪。", data={"probe_message": probe_message})

        install_result = self._ensure_adspower_installed_v3(step, context_path, context)
        if isinstance(install_result, StepResult):
            return install_result

        if profile.adspower_plan_ready is False and profile.adspower_username and profile.adspower_password:
            logger.error("AdsPower readiness failed: plan is marked as not ready in source data.")
            context.adspower_purchase_status = "required"
            return self._failed(
                step,
                context_path=context_path,
                context=context,
                message="AdsPower plan is not ready. Automatic flow will stop here.",
                data=install_result | {"probe_message": probe_message},
            )
            opened = self._open_guidance_once(
                context,
                context_flag="adspower_pricing_guidance_opened",
                url=self._adspower_pricing_url,
            )
            context.adspower_purchase_status = "required"
            return self._waiting(
                step,
                context_path=context_path,
                context=context,
                wait_reason="adspower_purchase_required",
                resume_from=step.action,
                message=(
                    "资料标记 AdsPower 套餐尚未开通，已尝试打开官方套餐页。请开通后继续。"
                    if opened
                    else "资料标记 AdsPower 套餐尚未开通，请开通后继续。"
                ),
            )

        if not profile.adspower_username or not profile.adspower_password:
            logger.error("AdsPower readiness failed: missing login credentials.")
            context.adspower_purchase_status = "login_required"
            return self._failed(
                step,
                context_path=context_path,
                context=context,
                message="AdsPower login credentials are missing. Automatic flow will stop here.",
                data=install_result | {"probe_message": probe_message},
            )
            self._launch_adspower_if_available()
            opened = self._open_guidance_once(
                context,
                context_flag="adspower_registration_guidance_opened",
                url=self._adspower_register_url,
            )
            context.adspower_purchase_status = "login_required"
            return self._waiting(
                step,
                context_path=context_path,
                context=context,
                wait_reason="adspower_login_required",
                resume_from=step.action,
                message=(
                    "缺少 AdsPower 登录信息，已拉起登录页。没有账号就先注册，有账号就直接登录，完成后继续。"
                    if opened
                    else "缺少 AdsPower 登录信息，请注册或补齐登录信息后继续。"
                ),
            )

        self._restart_adspower_if_service_unavailable(probe_message=probe_message)
        overall_timeout = self._adspower_ready_timeout_seconds
        logger.info("AdsPower executable launched; waiting up to %.1f seconds for runtime readiness.", overall_timeout)
        runtime_signal = self._wait_for_adspower_runtime_signal(
            profile,
            timeout_seconds=overall_timeout,
        )
        logger.info(
            "AdsPower runtime signal: state=%s probe=%s login_attempted=%s",
            runtime_signal.get("state"),
            runtime_signal.get("probe_message", ""),
            runtime_signal.get("login_attempted", False),
        )
        if runtime_signal["state"] == "ready":
            self._close_adspower_guidance_windows()
            context.adspower_purchase_status = "ready"
            context.last_success_step = step.action
            context.wait_reason = None
            self._save_context(context_path, context)
            logger.info("AdsPower runtime became ready at step 3.")
            return self._success(
                step,
                "AdsPower 已在 10 秒级启动窗口内就绪。",
                data=install_result | runtime_signal,
            )

        if runtime_signal.get("state") == "window_only":
            logger.warning("AdsPower reached window_only state; performing one cold restart retry.")
            self._restart_adspower_if_service_unavailable(probe_message=str(runtime_signal.get("probe_message", "") or ""))
            retry_signal = self._wait_for_adspower_runtime_signal(
                profile,
                timeout_seconds=overall_timeout,
            )
            logger.info(
                "AdsPower runtime retry signal: state=%s probe=%s login_attempted=%s",
                retry_signal.get("state"),
                retry_signal.get("probe_message", ""),
                retry_signal.get("login_attempted", False),
            )
            if retry_signal.get("state") == "ready":
                self._close_adspower_guidance_windows()
                context.adspower_purchase_status = "ready"
                context.last_success_step = step.action
                context.wait_reason = None
                self._save_context(context_path, context)
                logger.info("AdsPower runtime became ready after cold restart retry.")
                return self._success(
                    step,
                    "AdsPower has been launched and is ready after retry.",
                    data=install_result | retry_signal,
                )
            runtime_signal = retry_signal

        failure_message = (
            "AdsPower login window was detected and auto-login was attempted, but the local API is still not ready."
            if runtime_signal.get("state") == "login_window" and runtime_signal.get("login_attempted")
            else (
                "AdsPower login window was detected, but auto-login did not complete and the local API is still not ready."
                if runtime_signal.get("state") == "login_window"
                else f"AdsPower did not become ready within {int(overall_timeout)} seconds."
            )
        )
        context.adspower_purchase_status = "login_required"
        if runtime_signal["state"] == "login_window":
            message = (
                "AdsPower 登录页已出现，系统已尝试自动登录。请确认当前登录状态后继续。"
                if runtime_signal.get("login_attempted")
                else "AdsPower 登录页已出现，但自动登录未完成。请按资料中的账号信息登录后继续。"
            )
        else:
            message = (
                f"AdsPower 在 {int(self._adspower_ready_timeout_seconds)} 秒内既未完成本地服务就绪，"
                "也未稳定进入可继续的登录状态。请检查程序启动状态后继续。"
            )
        return self._waiting(
            step,
            context_path=context_path,
            context=context,
            wait_reason="adspower_login_required",
            resume_from=step.action,
            message=message,
            extra_data=install_result | runtime_signal,
        )

    def _probe_adspower_service_state(self) -> tuple[bool, str]:
        client = getattr(self._adspower_adapter, "_client", None)
        probe_ready = getattr(client, "probe_ready", None)
        if not callable(probe_ready):
            return False, "probe_ready unavailable"
        try:
            ready, message = probe_ready()
        except Exception as exc:
            return False, str(exc)
        return bool(ready), str(message or "")

    def _wait_for_adspower_runtime_signal(
        self,
        profile: OnboardingProfile,
        *,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + max(timeout_seconds, 1.0)
        last_probe_message = ""
        login_attempted = False
        window_seen = False
        login_window_seen = False
        logger.info("Waiting for AdsPower runtime signal. timeout=%.1fs", timeout_seconds)
        while time.monotonic() < deadline:
            ready, probe_message = self._probe_adspower_service_state()
            if probe_message != last_probe_message:
                logger.info("AdsPower API probe: %s", probe_message)
                last_probe_message = probe_message
            if ready:
                return {"state": "ready", "probe_message": probe_message, "login_attempted": login_attempted}
            window = self._connect_adspower_window(timeout_seconds=0.8)
            if window is not None:
                if not window_seen:
                    logger.info("AdsPower window detected while waiting for service readiness.")
                window_seen = True
                looks_like_login = self._looks_like_adspower_login_window(window)
                logger.info("AdsPower window login detection result: %s", looks_like_login)
                if looks_like_login and not login_attempted and profile.adspower_username and profile.adspower_password:
                    login_window_seen = True
                    logger.info("AdsPower login window detected; attempting automatic login.")
                    login_attempted = self._attempt_adspower_login(profile)
                    logger.info("AdsPower automatic login attempt result: %s", login_attempted)
                    if login_attempted:
                        logger.info("Login submitted; waiting 5 seconds for verification to complete.")
                        time.sleep(5.0)
            time.sleep(0.4)
        return {
            "state": "login_window" if login_window_seen else ("window_only" if window_seen else "timeout"),
            "probe_message": last_probe_message,
            "login_attempted": login_attempted,
        }

    def _probe_adspower_service_v3(self, step: TaskStep) -> StepResult:
        try:
            return self._adspower_adapter.execute(
                TaskStep(
                    adapter="adspower",
                    action="ensure_service_ready",
                    description="检查 AdsPower 本地服务",
                    params={},
                )
            )
        except Exception:
            return StepResult(
                step_id=step.id,
                status=RunStatus.FAILED,
                message="AdsPower 本地服务尚未就绪。",
            )

    def _ensure_adspower_installed_v3(
        self,
        step: TaskStep,
        context_path: Path,
        context: NewUserSetupContext,
    ) -> StepResult | dict[str, Any]:
        install_root = self._resolved_adspower_install_root()
        detected_system = self._detected_adspower_windows_label()
        logger.info("Ensuring AdsPower installation. root=%s system=%s", install_root, detected_system)
        if self._adspower_executable_exists():
            return {
                "default_install_root": str(install_root),
                "downloaded_installer_path": "",
                "detected_system": detected_system,
            }
        if not self._install_root_is_available(install_root):
            logger.error("AdsPower installation failed: install root is not available: %s", install_root)
            return self._failed(
                step,
                context_path=str(context_path),
                context=context,
                message=f"AdsPower install root is not available: {install_root}",
                data={
                    "default_install_root": str(install_root),
                    "detected_system": detected_system,
                },
            )
            return self._waiting(
                step,
                context_path=context_path,
                context=context,
                wait_reason="adspower_setup_required",
                resume_from=step.action,
                message=(
                    f"当前电脑未检测到可用的非系统盘，默认目标目录 {install_root} 不可用。"
                    " AdsPower 不会安装到系统盘（C盘），请准备 D/E/F 等非系统盘后再继续。"
                ),
                extra_data={
                    "default_install_root": str(install_root),
                    "detected_system": detected_system,
                },
            )

        install_root.mkdir(parents=True, exist_ok=True)
        downloaded_installer = None
        download_error = ""
        try:
            downloaded_installer = self._download_adspower_installer_if_needed()
            if downloaded_installer is not None:
                logger.info("AdsPower installer resolved: %s", downloaded_installer)
        except Exception as exc:
            download_error = str(exc)
            logger.warning("AdsPower installer download failed: %s", exc)

        launched_installer = False
        if downloaded_installer is not None and not self._is_process_running(downloaded_installer.name):
            self._launch_installer(downloaded_installer, install_root=install_root, install_target="adspower")
            launched_installer = True
            logger.info("AdsPower installer launched: %s", downloaded_installer)
        installer_running = downloaded_installer is not None and self._is_process_running(downloaded_installer.name)
        if installer_running:
            self._start_adspower_installer_assistant()
            logger.info("AdsPower installer assistant attached to running installer.")

        if not self._adspower_executable_exists() and (launched_installer or installer_running):
            deadline = time.monotonic() + self._installer_wait_seconds
            while time.monotonic() < deadline:
                if self._adspower_executable_exists():
                    logger.info("AdsPower executable detected after installer run.")
                    break
                logger.info("Waiting for AdsPower executable to appear...")
                time.sleep(1)

        if self._adspower_executable_exists():
            return {
                "default_install_root": str(install_root),
                "downloaded_installer_path": str(downloaded_installer) if downloaded_installer is not None else "",
                "download_error": download_error,
                "detected_system": detected_system,
            }

        logger.error(
            "AdsPower installation did not complete automatically. root=%s installer=%s download_error=%s",
            install_root,
            downloaded_installer,
            download_error,
        )
        return self._failed(
            step,
            context_path=str(context_path),
            context=context,
            message="AdsPower was not installed successfully during automatic handling.",
            data={
                "default_install_root": str(install_root),
                "downloaded_installer_path": str(downloaded_installer) if downloaded_installer is not None else "",
                "download_error": download_error,
                "detected_system": detected_system,
            },
        )

        opened = self._open_guidance_once(
            context,
            context_flag="adspower_download_guidance_opened",
            url=self._adspower_download_url,
        )
        context.adspower_purchase_status = "required"
        return self._waiting(
            step,
            context_path=context_path,
            context=context,
            wait_reason="adspower_setup_required",
            resume_from=step.action,
            message=(
                f"AdsPower 尚未安装，默认安装目录已准备为 {install_root}。"
                f"{' 已自动下载并启动安装程序。' if launched_installer else ''}"
                f"{f' 自动下载安装时遇到网络问题：{download_error}。' if download_error else ''}"
                f"{' 已尝试打开官方下载页。' if opened else ''}"
                " 安装完成后点击继续。"
            ),
            extra_data={
                "default_install_root": str(install_root),
                "downloaded_installer_path": str(downloaded_installer) if downloaded_installer is not None else "",
                "download_error": download_error,
                "detected_system": detected_system,
            },
        )

    def _wait_for_adspower_service_ready(self, timeout_seconds: float = 20.0) -> bool:
        client = getattr(self._adspower_adapter, "_client", None)
        probe_ready = getattr(client, "probe_ready", None)
        if not callable(probe_ready):
            return False
        deadline = time.monotonic() + max(timeout_seconds, 1.0)
        while time.monotonic() < deadline:
            ready, _message = probe_ready()
            if ready:
                return True
            time.sleep(1)
        return False

    def _restart_adspower_if_service_unavailable(self, *, probe_message: str = "") -> None:
        executable = self._resolved_adspower_executable_path()
        if not executable.exists():
            return
        process_count = self._process_count_by_image_name(executable.name)
        if process_count > 0:
            logger.warning(
                "AdsPower service is unavailable (%s). Restarting process tree. current_process_count=%s",
                probe_message or "no probe message",
                process_count,
            )
            self._stop_processes_by_image_name(executable.name)
            time.sleep(1.5)
        self._launch_adspower_if_available()

    @staticmethod
    def _process_count_by_image_name(image_name: str) -> int:
        if not str(image_name).strip():
            return 0
        completed = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {image_name}"],
            check=False,
            capture_output=True,
            text=True,
        )
        stdout = completed.stdout or ""
        return sum(1 for line in stdout.splitlines() if image_name.casefold() in line.casefold())

    def _attempt_adspower_login(self, profile: OnboardingProfile) -> bool:
        account = str(profile.adspower_username or "").strip()
        password = str(profile.adspower_password or "").strip()
        if not account or not password:
            return False
        login_mode = self._detect_adspower_login_mode(account)
        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline:
            window = self._connect_adspower_window(timeout_seconds=1.0)
            if window is None:
                time.sleep(0.4)
                continue
            self._select_adspower_login_mode(window, login_mode)
            filled = self._fill_adspower_login_form_by_controls(window, account, password, login_mode)
            logger.info("AdsPower login form fill result: %s", filled)
            if filled:
                submitted = self._submit_adspower_login_by_controls(window)
                logger.info("AdsPower login submit result: %s", submitted)
                return submitted
            time.sleep(0.4)
        rect = self._restore_adspower_window_rect()
        if rect is None:
            return False
        try:
            from pywinauto import mouse
        except Exception:
            return False

        left, top, right, bottom = rect
        width = max(right - left, 1)
        height = max(bottom - top, 1)
        try:
            mode_point = self._adspower_login_mode_point(rect, login_mode)
            if mode_point is not None:
                try:
                    mouse.click(coords=mode_point)
                    time.sleep(0.2)
                except Exception:
                    pass
            account_point = (left + int(width * 0.68), top + int(height * 0.23))
            mouse.click(coords=account_point)
            time.sleep(0.4)
            self._windows_ui_driver.send_keys("^a{BACKSPACE}")
            self._windows_ui_driver.send_keys(account)
            time.sleep(0.2)
            self._windows_ui_driver.send_keys("{TAB}")
            time.sleep(0.2)
            self._windows_ui_driver.send_keys("^a{BACKSPACE}")
            self._windows_ui_driver.send_keys(password)
            time.sleep(0.2)
            self._windows_ui_driver.send_keys("{ENTER}")
            return True
        except Exception as exc:
            logger.warning("AdsPower fallback mouse login failed: %s", exc)
            return False

    @staticmethod
    def _detect_adspower_login_mode(account: str) -> str:
        normalized = str(account or "").strip()
        if not normalized:
            return "account"
        if "@" in normalized:
            return "email"
        if re.search(r"[A-Za-z]", normalized):
            return "account"
        digits = re.sub(r"\D", "", normalized)
        if 6 <= len(digits) <= 15:
            return "phone"
        return "account"

    def _connect_adspower_window(self, timeout_seconds: float = 3.0) -> Any | None:
        try:
            from pywinauto import Desktop
        except Exception:
            return None

        hwnd = self._find_adspower_window_handle()
        if hwnd:
            for backend in ("uia", "win32"):
                try:
                    window = Desktop(backend=backend).window(handle=hwnd).wrapper_object()
                    if not self._is_adspower_window_wrapper(window):
                        continue
                    self._windows_ui_driver.focus_window(window)
                    return window
                except Exception:
                    continue

        title_patterns = [r".*AdsPower Browser.*", r".*AdsPower.*"]
        for backend in ("uia", "win32"):
            try:
                window = self._windows_ui_driver.connect_any(
                    title_patterns,
                    timeout_seconds=timeout_seconds,
                    backend=backend,
                )
                if not self._is_adspower_window_wrapper(window):
                    continue
                self._windows_ui_driver.focus_window(window)
                return window
            except Exception:
                continue
        return None

    def _select_adspower_login_mode(self, window: Any, login_mode: str) -> bool:
        if login_mode == "account":
            return False
        for texts in self._adspower_login_mode_text_groups(login_mode):
            try:
                self._windows_ui_driver.click_text(
                    window,
                    texts=texts,
                    control_types=["TabItem", "Button", "Hyperlink", "Text", "Document"],
                    max_depth=10,
                    max_nodes=480,
                    skip_child_classes=[],
                )
                time.sleep(0.3)
                return True
            except Exception:
                continue
        return False

    def _fill_adspower_login_form_by_controls(
        self,
        window: Any,
        account: str,
        password: str,
        login_mode: str,
    ) -> bool:
        account_filled = False
        for labels in self._adspower_account_label_groups(login_mode):
            try:
                self._windows_ui_driver.fill_labeled_input(
                    window,
                    labels=labels,
                    value=account,
                    max_depth=10,
                    max_nodes=480,
                    skip_child_classes=[],
                )
                account_filled = True
                break
            except Exception:
                continue

        password_filled = False
        if account_filled:
            for labels in self._adspower_password_label_groups():
                try:
                    self._windows_ui_driver.fill_labeled_input(
                        window,
                        labels=labels,
                        value=password,
                        max_depth=10,
                        max_nodes=480,
                        skip_child_classes=[],
                    )
                    password_filled = True
                    break
                except Exception:
                    continue
        if account_filled and password_filled:
            return True

        account_control, password_control = self._find_adspower_login_field_controls(window, login_mode)
        if account_control is not None and not account_filled:
            account_filled = self._set_adspower_input_value(account_control, account)
            time.sleep(0.1)
        if password_control is not None and not password_filled:
            password_filled = self._set_adspower_input_value(password_control, password)
            time.sleep(0.1)
        if account_filled and password_filled:
            return True

        if account_control is not None and account_filled and not password_filled:
            try:
                self._windows_ui_driver.send_keys("{TAB}")
                time.sleep(0.1)
                password_filled = self._paste_text_into_focused_control(password)
            except Exception:
                password_filled = False
        return account_filled and password_filled

    def _find_adspower_login_field_controls(self, window: Any, login_mode: str) -> tuple[Any | None, Any | None]:
        candidates = self._collect_adspower_input_candidates(window)
        if not candidates:
            return None, None

        password_control = self._select_best_adspower_field_control(candidates, role="password", login_mode=login_mode)
        account_control = self._select_best_adspower_field_control(
            candidates,
            role="account",
            login_mode=login_mode,
            exclude=password_control,
        )

        ordered_candidates = sorted(
            candidates,
            key=lambda item: (
                item["rect"][1],
                item["rect"][0],
            ),
        )
        if account_control is None:
            account_entry = ordered_candidates[0] if ordered_candidates else None
            account_control = account_entry["control"] if account_entry is not None else None
        if password_control is None:
            account_top = None
            if account_control is not None:
                account_rect = self._adspower_control_rect(account_control)
                account_top = account_rect[1] if account_rect is not None else None
            for entry in ordered_candidates:
                control = entry["control"]
                if account_control is not None and self._adspower_controls_match(control, account_control):
                    continue
                if account_top is not None and entry["rect"][1] < account_top:
                    continue
                password_control = control
                break
        return account_control, password_control

    def _select_best_adspower_field_control(
        self,
        candidates: list[dict[str, Any]],
        *,
        role: str,
        login_mode: str,
        exclude: Any | None = None,
    ) -> Any | None:
        best_control: Any | None = None
        best_score = 0
        for candidate in candidates:
            control = candidate["control"]
            if exclude is not None and self._adspower_controls_match(control, exclude):
                continue
            score = self._score_adspower_field_candidate(candidate, role=role, login_mode=login_mode)
            if score > best_score:
                best_score = score
                best_control = control
        return best_control

    def _score_adspower_field_candidate(self, candidate: dict[str, Any], *, role: str, login_mode: str) -> int:
        rect = candidate["rect"]
        hints = " ".join(candidate["hints"]).casefold()
        control_type = str(candidate["control_type"] or "")
        top = rect[1]
        height = max(rect[3] - rect[1], 1)
        score = 0

        if control_type == "Edit":
            score += 8
        elif control_type == "ComboBox":
            score += 6
        elif control_type == "Document":
            score += 5
        elif control_type == "Pane":
            score += 3

        if 20 <= height <= 72:
            score += 3

        password_tokens = self._adspower_text_tokens(self._adspower_password_label_groups())
        account_tokens = self._adspower_text_tokens(self._adspower_account_label_groups(login_mode))
        generic_account_tokens = self._adspower_text_tokens([["账号", "账户", "Account", "Username", "User name", "User ID"]])

        if role == "password":
            if any(token.casefold() in hints for token in password_tokens):
                score += 14
            if any(token.casefold() in hints for token in account_tokens):
                score -= 6
            if top > 0:
                score += 2
        else:
            if any(token.casefold() in hints for token in account_tokens):
                score += 14
            elif any(token.casefold() in hints for token in generic_account_tokens):
                score += 8
            if any(token.casefold() in hints for token in password_tokens):
                score -= 12
            if top > 0:
                score += 4 if top < 500 else 1
        return score

    def _collect_adspower_input_candidates(self, window: Any) -> list[dict[str, Any]]:
        window_rect = self._adspower_control_rect(window)
        candidates: list[dict[str, Any]] = []
        seen: set[tuple[str, tuple[int, int, int, int]]] = set()
        for control in self._windows_ui_driver.iter_controls(
            window,
            max_depth=12,
            max_nodes=800,
            max_seconds=4.0,
            skip_child_classes=[],
        ):
            control_type = self._adspower_control_type(control)
            if control_type not in {"Edit", "Document", "Pane", "ComboBox"}:
                continue
            rect = self._adspower_control_rect(control)
            if rect is None or not self._looks_like_adspower_text_input(rect, window_rect):
                continue
            identity = (control_type, rect)
            if identity in seen:
                continue
            seen.add(identity)
            candidates.append(
                {
                    "control": control,
                    "rect": rect,
                    "control_type": control_type,
                    "hints": self._adspower_control_text_hints(control),
                }
            )
        return candidates

    def _looks_like_adspower_text_input(
        self,
        rect: tuple[int, int, int, int],
        window_rect: tuple[int, int, int, int] | None,
    ) -> bool:
        left, top, right, bottom = rect
        width = max(right - left, 0)
        height = max(bottom - top, 0)
        if width < 100 or height < 18:
            return False
        if window_rect is None:
            return height <= 120
        win_left, win_top, win_right, win_bottom = window_rect
        win_width = max(win_right - win_left, 1)
        win_height = max(win_bottom - win_top, 1)
        if height > max(int(win_height * 0.18), 96):
            return False
        if width > int(win_width * 0.92):
            return False
        if top < win_top + int(win_height * 0.08):
            return False
        if bottom > win_top + int(win_height * 0.82):
            return False
        return True

    def _adspower_control_text_hints(self, control: Any) -> list[str]:
        hints: list[str] = []

        def push(raw: str) -> None:
            text = str(raw or "").strip()
            if text and text not in hints:
                hints.append(text)

        push(self._windows_ui_driver.read_text(control))
        try:
            element_info = getattr(control, "element_info", None)
            if element_info is not None:
                push(getattr(element_info, "automation_id", ""))
                push(getattr(element_info, "name", ""))
                push(getattr(element_info, "class_name", ""))
        except Exception:
            pass
        for related in self._adspower_neighbor_controls(control):
            push(self._windows_ui_driver.read_text(related))
            try:
                push(getattr(related.element_info, "automation_id", ""))
            except Exception:
                pass
        return hints

    def _adspower_neighbor_controls(self, control: Any) -> list[Any]:
        neighbors: list[Any] = []
        try:
            parent = control.parent()
        except Exception:
            parent = None
        if parent is None:
            return neighbors
        try:
            siblings = list(parent.children())
        except Exception:
            siblings = []
        try:
            children = list(control.children())
        except Exception:
            children = []
        for item in siblings + children + [parent]:
            if self._adspower_controls_match(item, control):
                continue
            neighbors.append(item)
        return neighbors[:10]

    def _set_adspower_input_value(self, control: Any, value: str) -> bool:
        if not str(value).strip():
            return False
        for method_name in ("set_edit_text", "set_text"):
            try:
                method = getattr(control, method_name)
            except Exception:
                continue
            try:
                method(value)
                logger.info("AdsPower input value set via %s.", method_name)
                return True
            except Exception:
                continue
        if self._type_keys_on_control(control, "^a{BACKSPACE}" + value):
            logger.info("AdsPower input value set via control.type_keys.")
            return True
        try:
            self._windows_ui_driver.click(control)
            time.sleep(0.1)
        except Exception:
            rect = self._adspower_control_rect(control)
            if rect is not None:
                try:
                    from pywinauto import mouse

                    left, top, right, bottom = rect
                    mouse.click(coords=(left + int((right - left) / 2), top + int((bottom - top) / 2)))
                    time.sleep(0.1)
                except Exception:
                    pass
        if self._paste_text_into_focused_control(value):
            return True
        try:
            self._windows_ui_driver.set_text(control, value)
            logger.info("AdsPower input value set via ui_driver.set_text.")
            return True
        except Exception:
            pass
        try:
            self._windows_ui_driver.send_keys("^a{BACKSPACE}")
            self._windows_ui_driver.send_keys(value)
            logger.info("AdsPower input value set via global send_keys.")
            return True
        except Exception:
            return False

    def _type_keys_on_control(self, control: Any, keys: str) -> bool:
        try:
            control.type_keys(keys, with_spaces=True, set_foreground=False)
            return True
        except Exception:
            return False

    def _paste_text_into_focused_control(self, value: str) -> bool:
        if not self._set_windows_clipboard_text(value):
            return False
        try:
            self._windows_ui_driver.send_keys("^a{BACKSPACE}")
            time.sleep(0.05)
        except Exception:
            pass
        try:
            self._windows_ui_driver.send_keys("^v")
            time.sleep(0.05)
            return True
        except Exception:
            return False

    @staticmethod
    def _set_windows_clipboard_text(value: str) -> bool:
        try:
            import ctypes
        except Exception:
            return False

        CF_UNICODETEXT = 13
        GMEM_MOVEABLE = 0x0002
        kernel32 = ctypes.windll.kernel32
        user32 = ctypes.windll.user32
        normalized = str(value or "").replace("\r\n", "\n").replace("\n", "\r\n")
        buffer = ctypes.create_unicode_buffer(normalized)
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, ctypes.sizeof(buffer))
        if not handle:
            return False
        locked = kernel32.GlobalLock(handle)
        if not locked:
            kernel32.GlobalFree(handle)
            return False
        try:
            ctypes.memmove(locked, ctypes.addressof(buffer), ctypes.sizeof(buffer))
        finally:
            kernel32.GlobalUnlock(handle)

        if not user32.OpenClipboard(None):
            kernel32.GlobalFree(handle)
            return False
        try:
            user32.EmptyClipboard()
            if not user32.SetClipboardData(CF_UNICODETEXT, handle):
                kernel32.GlobalFree(handle)
                return False
            handle = None
            return True
        finally:
            user32.CloseClipboard()

    def _adspower_control_rect(self, control: Any) -> tuple[int, int, int, int] | None:
        try:
            rect = control.rectangle()
        except Exception:
            return None
        return rect.left, rect.top, rect.right, rect.bottom

    @staticmethod
    def _adspower_control_type(control: Any) -> str:
        try:
            return str(getattr(control.element_info, "control_type", "") or "")
        except Exception:
            return ""

    @staticmethod
    def _adspower_controls_match(left: Any, right: Any) -> bool:
        if left is right:
            return True
        try:
            left_rect = left.rectangle()
            right_rect = right.rectangle()
            if (
                left_rect.left,
                left_rect.top,
                left_rect.right,
                left_rect.bottom,
            ) != (
                right_rect.left,
                right_rect.top,
                right_rect.right,
                right_rect.bottom,
            ):
                return False
            left_type = str(getattr(left.element_info, "control_type", "") or "")
            right_type = str(getattr(right.element_info, "control_type", "") or "")
            return left_type == right_type
        except Exception:
            return False

    def _submit_adspower_login_by_controls(self, window: Any) -> bool:
        for texts in self._adspower_submit_button_text_groups():
            submit_control = self._find_adspower_submit_control_by_text(window, texts=texts)
            if submit_control is not None and self._activate_adspower_control(submit_control):
                logger.info("AdsPower submit activated by labeled control: %s", "/".join(texts))
                return True

        submit_control = self._find_adspower_submit_control(window)
        if submit_control is not None and self._activate_adspower_control(submit_control):
            logger.info("AdsPower submit activated by scored control.")
            return True

        _account_control, password_control = self._find_adspower_login_field_controls(window, "account")
        if password_control is not None:
            if self._type_keys_on_control(password_control, "{ENTER}"):
                logger.info("AdsPower submit activated by password control Enter.")
                time.sleep(0.15)
                return True
            self._focus_adspower_control(password_control)
            for key_sequence in ("{ENTER}", "{TAB}{SPACE}", "{TAB}{ENTER}", "{TAB}{TAB}{SPACE}", "{TAB}{TAB}{ENTER}"):
                try:
                    self._windows_ui_driver.send_keys(key_sequence)
                    logger.info("AdsPower submit attempted by global key sequence: %s", key_sequence)
                    time.sleep(0.15)
                    return True
                except Exception:
                    continue

        if self._type_keys_on_control(window, "{ENTER}"):
            logger.info("AdsPower submit activated by window Enter.")
            time.sleep(0.15)
            return True

        submit_point = self._adspower_submit_point(window, password_control=password_control)
        if submit_point is not None:
            try:
                from pywinauto import mouse

                mouse.click(coords=submit_point)
                logger.info("AdsPower submit activated by fallback mouse click.")
                time.sleep(0.15)
                return True
            except Exception:
                pass
        try:
            self._windows_ui_driver.send_keys("{ENTER}")
            logger.info("AdsPower submit attempted by final global Enter.")
            time.sleep(0.15)
            return True
        except Exception:
            return False

    def _find_adspower_submit_control_by_text(self, window: Any, *, texts: Iterable[str]) -> Any | None:
        for control_types in (["Button", "Hyperlink"], ["Pane"]):
            try:
                control = self._windows_ui_driver.find_first_text_match(
                    window,
                    texts=texts,
                    control_types=control_types,
                    max_depth=10,
                    max_nodes=480,
                    skip_child_classes=[],
                )
            except Exception:
                control = None
            if control is not None:
                return control
        return None

    def _find_adspower_submit_control(self, window: Any) -> Any | None:
        candidates: list[dict[str, Any]] = []
        window_rect = self._adspower_control_rect(window)
        _account_control, password_control = self._find_adspower_login_field_controls(window, "account")
        password_rect = self._adspower_control_rect(password_control) if password_control is not None else None

        for control in self._windows_ui_driver.iter_controls(
            window,
            max_depth=12,
            max_nodes=900,
            max_seconds=4.0,
            skip_child_classes=[],
        ):
            control_type = self._adspower_control_type(control)
            if control_type not in {"Button", "Hyperlink", "Text", "Document", "Pane"}:
                continue
            rect = self._adspower_control_rect(control)
            if rect is None or not self._looks_like_adspower_submit_region(rect, window_rect, password_rect):
                continue
            hints = self._adspower_control_text_hints(control)
            score = self._score_adspower_submit_candidate(
                rect=rect,
                hints=hints,
                control_type=control_type,
                password_rect=password_rect,
                window_rect=window_rect,
            )
            if score <= 0:
                continue
            candidates.append(
                {
                    "control": control,
                    "score": score,
                    "rect": rect,
                }
            )

        if not candidates:
            return None
        candidates.sort(key=lambda item: (item["score"], -item["rect"][1]), reverse=True)
        return candidates[0]["control"]

    def _score_adspower_submit_candidate(
        self,
        *,
        rect: tuple[int, int, int, int],
        hints: list[str],
        control_type: str,
        password_rect: tuple[int, int, int, int] | None,
        window_rect: tuple[int, int, int, int] | None,
    ) -> int:
        left, top, right, bottom = rect
        width = max(right - left, 1)
        height = max(bottom - top, 1)
        hint_text = " ".join(hints).casefold()
        score = 0

        if control_type == "Button":
            score += 12
        elif control_type == "Hyperlink":
            score += 6
        elif control_type == "Text":
            score += 4
        elif control_type == "Document":
            score += 3
        elif control_type == "Pane":
            score += 2

        login_tokens = self._adspower_text_tokens(self._adspower_submit_button_text_groups())
        if any(token.casefold() in hint_text for token in login_tokens):
            score += 18
        if any(token in hint_text for token in ("submit", "continue", "next", "go")):
            score += 6
        if any(token in hint_text for token in ("register", "sign up", "forgot", "忘记", "注册")):
            score -= 12

        if 24 <= height <= 72:
            score += 4
        if width >= 100:
            score += 2

        if password_rect is not None:
            password_center_x = password_rect[0] + int((password_rect[2] - password_rect[0]) / 2)
            candidate_center_x = left + int(width / 2)
            horizontal_delta = abs(candidate_center_x - password_center_x)
            if horizontal_delta <= 80:
                score += 5
            elif horizontal_delta <= 160:
                score += 2
            vertical_gap = top - password_rect[3]
            if 0 <= vertical_gap <= 140:
                score += 8
            elif vertical_gap < 0:
                score -= 8
        if window_rect is not None:
            win_left, win_top, win_right, win_bottom = window_rect
            win_width = max(win_right - win_left, 1)
            win_height = max(win_bottom - win_top, 1)
            if left >= win_left + int(win_width * 0.18) and right <= win_right - int(win_width * 0.18):
                score += 3
            if top >= win_top + int(win_height * 0.28):
                score += 2
        return score

    def _looks_like_adspower_submit_region(
        self,
        rect: tuple[int, int, int, int],
        window_rect: tuple[int, int, int, int] | None,
        password_rect: tuple[int, int, int, int] | None,
    ) -> bool:
        left, top, right, bottom = rect
        width = max(right - left, 0)
        height = max(bottom - top, 0)
        if width < 56 or height < 18:
            return False
        if password_rect is not None:
            if bottom <= password_rect[1]:
                return False
            if top > password_rect[3] + 220:
                return False
        if window_rect is None:
            return True
        win_left, win_top, win_right, win_bottom = window_rect
        win_width = max(win_right - win_left, 1)
        win_height = max(win_bottom - win_top, 1)
        if left < win_left + int(win_width * 0.08) or right > win_right - int(win_width * 0.08):
            return False
        if top < win_top + int(win_height * 0.18) or bottom > win_bottom - int(win_height * 0.08):
            return False
        return True

    def _activate_adspower_control(self, control: Any) -> bool:
        try:
            self._windows_ui_driver.click(control)
            time.sleep(0.1)
            return True
        except Exception:
            pass
        rect = self._adspower_control_rect(control)
        if rect is None:
            return False
        try:
            from pywinauto import mouse

            left, top, right, bottom = rect
            mouse.click(coords=(left + int((right - left) / 2), top + int((bottom - top) / 2)))
            time.sleep(0.1)
            return True
        except Exception:
            pass
        if self._focus_adspower_control(control):
            for key_sequence in ("{SPACE}", "{ENTER}"):
                try:
                    self._windows_ui_driver.send_keys(key_sequence)
                    time.sleep(0.1)
                    return True
                except Exception:
                    continue
        return False

    def _focus_adspower_control(self, control: Any) -> bool:
        try:
            control.set_focus()
            time.sleep(0.05)
            return True
        except Exception:
            pass
        try:
            self._windows_ui_driver.click(control)
            time.sleep(0.05)
            return True
        except Exception:
            pass
        rect = self._adspower_control_rect(control)
        if rect is None:
            return False
        try:
            from pywinauto import mouse

            left, top, right, bottom = rect
            mouse.click(coords=(left + int((right - left) / 2), top + int((bottom - top) / 2)))
            time.sleep(0.05)
            return True
        except Exception:
            return False

    def _adspower_submit_point(
        self,
        window: Any,
        *,
        password_control: Any | None = None,
    ) -> tuple[int, int] | None:
        password_rect = self._adspower_control_rect(password_control) if password_control is not None else None
        if password_rect is not None:
            left, top, right, bottom = password_rect
            width = max(right - left, 1)
            height = max(bottom - top, 1)
            return left + int(width / 2), bottom + min(max(int(height * 1.3), 28), 72)

        window_rect = self._adspower_control_rect(window)
        if window_rect is None:
            return None
        left, top, right, bottom = window_rect
        width = max(right - left, 1)
        height = max(bottom - top, 1)
        return left + int(width * 0.68), top + int(height * 0.44)

    @staticmethod
    def _adspower_login_mode_text_groups(login_mode: str) -> list[list[str]]:
        if login_mode == "account":
            return []
        if login_mode == "phone":
            return [
                ["手机号登录", "手机登录", "手机号", "Phone", "Mobile"],
            ]
        return [
            ["邮箱登录", "邮箱", "Email", "E-mail"],
        ]

    @staticmethod
    def _adspower_account_label_groups(login_mode: str) -> list[list[str]]:
        generic_labels = ["账号", "账户", "Account", "Username", "User name", "User ID"]
        if login_mode == "account":
            return [
                generic_labels,
                ["用户名", "登录账号", "账号/邮箱/手机号", "Email / Phone", "Phone / Email"],
                ["邮箱", "邮箱地址", "Email", "E-mail", "Email Address"],
                ["手机号", "手机号码", "Phone", "Phone Number", "Mobile", "Mobile Number"],
            ]
        if login_mode == "phone":
            return [
                ["手机号", "手机号码", "Phone", "Phone Number", "Mobile", "Mobile Number"],
                generic_labels,
            ]
        return [
            ["邮箱", "邮箱地址", "Email", "E-mail", "Email Address"],
            generic_labels,
        ]

    @staticmethod
    def _adspower_password_label_groups() -> list[list[str]]:
        return [
            ["密码", "登录密码", "Password", "Passcode"],
        ]

    @staticmethod
    def _adspower_submit_button_text_groups() -> list[list[str]]:
        return [
            ["登录", "立即登录", "Login", "Sign in", "Sign In"],
        ]

    @staticmethod
    def _adspower_text_tokens(text_groups: Iterable[Iterable[str]]) -> list[str]:
        tokens: list[str] = []
        for group in text_groups:
            for item in group:
                text = str(item or "").strip()
                if text and text not in tokens:
                    tokens.append(text)
        return tokens

    @staticmethod
    def _adspower_login_mode_point(rect: tuple[int, int, int, int], login_mode: str) -> tuple[int, int] | None:
        left, top, right, bottom = rect
        width = max(right - left, 1)
        height = max(bottom - top, 1)
        if login_mode == "phone":
            return left + int(width * 0.60), top + int(height * 0.17)
        if login_mode == "email":
            return left + int(width * 0.73), top + int(height * 0.17)
        return None

    @staticmethod
    def _find_adspower_window_handle() -> int:
        try:
            import ctypes
            from ctypes import wintypes
        except Exception:
            return 0

        user32 = ctypes.windll.user32
        candidates: list[tuple[int, int]] = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def enum_proc(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            title = buffer.value.strip()
            class_name = OnboardingAdapter._window_class_name(hwnd)
            process_path = OnboardingAdapter._window_process_path(hwnd)
            score = OnboardingAdapter._score_adspower_window(title, class_name, process_path)
            if score >= 0:
                candidates.append((score, hwnd))
            return True

        user32.EnumWindows(enum_proc, 0)
        if not candidates:
            return 0
        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates[0][1]

    @staticmethod
    def _is_adspower_window_wrapper(window: Any) -> bool:
        hwnd = 0
        try:
            hwnd = int(getattr(window, 'handle', 0) or 0)
        except Exception:
            hwnd = 0
        if hwnd <= 0:
            try:
                hwnd = int(getattr(window.element_info, 'handle', 0) or 0)
            except Exception:
                hwnd = 0
        if hwnd <= 0:
            return False
        title = ''
        try:
            title = str(window.window_text() or '').strip()
        except Exception:
            title = ''
        class_name = OnboardingAdapter._window_class_name(hwnd)
        process_path = OnboardingAdapter._window_process_path(hwnd)
        return OnboardingAdapter._score_adspower_window(title, class_name, process_path) >= 0

    @staticmethod
    def _score_adspower_window(title: str, class_name: str, process_path: str) -> int:
        lowered = str(title or '').strip().casefold()
        lowered_class = str(class_name or '').strip().casefold()
        lowered_path = str(process_path or '').strip().casefold()
        if not lowered or 'adspower' not in lowered:
            return -1
        if any(token in lowered for token in ('setup', '???')):
            return -1
        if lowered_class in {'cabinetwclass', 'explorewclass'}:
            return -1
        if lowered_path:
            if lowered_path.endswith("\\explorer.exe"):
                return -1
            if "adspower global.exe" not in lowered_path and "\\adspower\\" not in lowered_path:
                return -1

        score = 0
        if 'adspower browser' in lowered:
            score += 8
        if 'chrome_widgetwin_' in lowered_class:
            score += 4
        if 'adspower global.exe' in lowered_path:
            score += 6
        if any(token in lowered for token in ('login', '???', 'sign in', 'signin')):
            score += 3
        if lowered.startswith('adspower browser |'):
            score += 1
        if any(token in lowered for token in ('register', 'guide', '???', '???')):
            score -= 6
        return score

    @staticmethod
    def _window_class_name(hwnd: int) -> str:
        try:
            import ctypes
        except Exception:
            return ''
        user32 = ctypes.windll.user32
        class_buffer = ctypes.create_unicode_buffer(256)
        if not user32.GetClassNameW(hwnd, class_buffer, 255):
            return ''
        return class_buffer.value.strip()

    @staticmethod
    def _window_process_path(hwnd: int) -> str:
        try:
            import ctypes
            from ctypes import wintypes
        except Exception:
            return ''

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        process = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if not process:
            return ''
        try:
            buffer = ctypes.create_unicode_buffer(4096)
            size = wintypes.DWORD(len(buffer))
            query = kernel32.QueryFullProcessImageNameW
            query.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
            query.restype = wintypes.BOOL
            if not query(process, 0, buffer, ctypes.byref(size)):
                return ''
            return buffer.value.strip()
        finally:
            kernel32.CloseHandle(process)

    @staticmethod
    def _restore_adspower_window_rect() -> tuple[int, int, int, int] | None:
        try:
            import ctypes
            from ctypes import wintypes
            from pywinauto import Desktop
        except Exception:
            return None

        user32 = ctypes.windll.user32
        matched_hwnd = OnboardingAdapter._find_adspower_window_handle()
        if matched_hwnd == 0:
            return None
        user32.ShowWindow(matched_hwnd, 9)
        user32.SetForegroundWindow(matched_hwnd)
        try:
            window = Desktop(backend="uia").window(handle=matched_hwnd).wrapper_object()
            for control_type in ("Document", "Pane"):
                candidates = window.descendants(control_type=control_type)
                if not candidates:
                    continue
                document = max(
                    candidates,
                    key=lambda item: max(item.rectangle().right - item.rectangle().left, 0)
                    * max(item.rectangle().bottom - item.rectangle().top, 0),
                )
                rect = document.rectangle()
                return rect.left, rect.top, rect.right, rect.bottom
        except Exception:
            pass
        rect = wintypes.RECT()
        user32.GetWindowRect(matched_hwnd, ctypes.byref(rect))
        return rect.left, rect.top, rect.right, rect.bottom

    @staticmethod
    def _available_proxy_count(profile: OnboardingProfile) -> int:
        proxies: set[tuple[str, str, str]] = set()
        for detail in profile.clone_details:
            host = str(detail.proxy_host or "").strip()
            port = str(detail.proxy_port or "").strip()
            username = str(detail.proxy_username or "").strip()
            if host and port:
                proxies.add((host, port, username))
        if proxies:
            return len(proxies)
        if profile.proxy_host and profile.proxy_port:
            return 1
        return 0

    @staticmethod
    def _has_static_proxy_sync_credentials(profile: OnboardingProfile) -> bool:
        return bool(str(profile.proxy_user_id or "").strip() and str(profile.proxy_sync_key or "").strip())

    @staticmethod
    def _has_partial_static_proxy_sync_credentials(profile: OnboardingProfile) -> bool:
        has_user_id = bool(str(profile.proxy_user_id or "").strip())
        has_sync_key = bool(str(profile.proxy_sync_key or "").strip())
        return has_user_id != has_sync_key

    def _should_use_static_proxy_sync(self, profile: OnboardingProfile) -> bool:
        if not self._has_static_proxy_sync_credentials(profile):
            return False
        if _is_dynamic_proxy_mode(profile.proxy_mode):
            return False
        if _is_static_proxy_mode(profile.proxy_mode):
            return True
        return not self._has_dynamic_proxy_details(profile)

    @staticmethod
    def _has_dynamic_proxy_details(profile: OnboardingProfile) -> bool:
        if str(profile.proxy_host or "").strip() and str(profile.proxy_port or "").strip():
            return True
        return any(
            str(detail.proxy_host or "").strip() and str(detail.proxy_port or "").strip()
            for detail in profile.clone_details
        )

    @staticmethod
    def _required_proxy_count(profile: OnboardingProfile) -> int:
        tiktok_count = profile.tiktok_clone_count
        facebook_count = profile.facebook_clone_count
        if tiktok_count and facebook_count:
            return max(tiktok_count, facebook_count)
        if tiktok_count or facebook_count:
            return max(tiktok_count, facebook_count)
        platforms = {detail.platform.lower() for detail in profile.clone_details if detail.platform}
        if {"tiktok", "facebook"} <= platforms:
            tiktok_details = sum(1 for detail in profile.clone_details if detail.platform.lower() == "tiktok")
            facebook_details = sum(1 for detail in profile.clone_details if detail.platform.lower() == "facebook")
            return max(tiktok_details, facebook_details)
        return max(len(profile.clone_details), 1 if profile.proxy_host and profile.proxy_port else 0)

    def _probe_profile_proxies(self, profile: OnboardingProfile) -> str | None:
        checked: set[tuple[str, str, str]] = set()
        for clone_detail in self._clone_details(profile):
            host = str(clone_detail.proxy_host or profile.proxy_host or "").strip()
            port = str(clone_detail.proxy_port or profile.proxy_port or "").strip()
            username = str(clone_detail.proxy_username or profile.proxy_username or "").strip()
            if not host or not port:
                continue
            key = (host, port, username)
            if key in checked:
                continue
            checked.add(key)
            try:
                self._probe_proxy(host, int(port))
            except Exception as exc:
                return f"代理 {host}:{port} 连通性检测失败: {exc}"
        return None

    def _probe_proxy(self, host: str, port: int) -> None:
        with socket.create_connection((host, port), timeout=self._proxy_probe_timeout_seconds):
            return

    @staticmethod
    def _clone_details(profile: OnboardingProfile) -> list[CloneAccountDetail]:
        if profile.clone_details:
            return profile.clone_details
        return [
            CloneAccountDetail(
                sequence="1",
                platform=profile.platform or "",
                clone_name=profile.environment_name or "Default-01",
                adspower_environment_name=profile.environment_name,
                platform_account=profile.platform_username,
                platform_password=profile.platform_password,
                proxy_host=profile.proxy_host,
                proxy_port=profile.proxy_port,
                proxy_username=profile.proxy_username,
                proxy_password=profile.proxy_password,
            )
        ]

    def _query_adspower_profiles_by_name(self) -> dict[str, dict[str, Any]]:
        try:
            result = self._adspower_adapter.execute(
                TaskStep(
                    adapter="adspower",
                    action="query_profiles",
                    description="查询 AdsPower 环境",
                    params={"page": 1, "page_size": 200},
                )
            )
        except Exception:
            return {}
        if result.status != RunStatus.SUCCEEDED:
            return {}
        profiles = result.data.get("profiles", [])
        if not isinstance(profiles, list):
            return {}
        records: dict[str, dict[str, Any]] = {}
        for item in profiles:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            if not name:
                continue
            if "user_id" not in item and "id" in item:
                item["user_id"] = item.get("id")
            if "serial_number" not in item and "profile_no" in item:
                item["serial_number"] = item.get("profile_no")
            records[name.casefold()] = item
        return records

    def _query_ipfoxy_saved_proxies(self) -> list[dict[str, Any]]:
        try:
            result = self._adspower_adapter.execute(
                TaskStep(
                    adapter="adspower",
                    action="query_proxies",
                    description="查询 AdsPower 代理列表",
                    params={"page": 1, "limit": 100},
                )
            )
        except Exception:
            return []
        if result.status != RunStatus.SUCCEEDED:
            return []
        proxies = result.data.get("proxies", [])
        if not isinstance(proxies, list):
            return []
        ipfoxy_proxies = [
            item
            for item in proxies
            if isinstance(item, dict)
            and {
                str(item.get("ipchecker", "")).strip().casefold(),
                str(item.get("proxy_partner", "")).strip().casefold(),
            }
            & {"ipfoxy"}
        ]
        return sorted(ipfoxy_proxies, key=_saved_proxy_sort_key, reverse=True)

    @staticmethod
    def _allocate_ipfoxy_proxy_assignments(
        saved_proxies: list[dict[str, Any]],
        clone_details: list[CloneAccountDetail],
    ) -> list[dict[str, Any]]:
        if not clone_details or not saved_proxies:
            return []

        normalized_platforms = [_normalize_dbit_platform(detail.platform) or "generic" for detail in clone_details]
        platform_counts: dict[str, int] = {}
        for platform in normalized_platforms:
            platform_counts[platform] = platform_counts.get(platform, 0) + 1

        pending_indices = sorted(
            range(len(clone_details)),
            key=lambda index: (-platform_counts.get(normalized_platforms[index], 0), index),
        )
        proxy_states = [
            {
                "record": proxy_record,
                "remaining": _saved_proxy_remaining_capacity(proxy_record),
                "platforms": set(),
                "order": order,
            }
            for order, proxy_record in enumerate(saved_proxies)
        ]
        assignments: list[dict[str, Any] | None] = [None] * len(clone_details)

        def backtrack(position: int) -> bool:
            if position >= len(pending_indices):
                return True
            clone_index = pending_indices[position]
            platform = normalized_platforms[clone_index]
            candidate_states = sorted(proxy_states, key=lambda state: (state["remaining"], state["order"]))
            for state in candidate_states:
                remaining = int(state["remaining"])
                assigned_platforms = state["platforms"]
                if remaining <= 0:
                    continue
                if platform in {"tiktok", "facebook"} and platform in assigned_platforms:
                    continue
                state["remaining"] = remaining - 1
                added_platform = False
                if platform in {"tiktok", "facebook"} and platform not in assigned_platforms:
                    assigned_platforms.add(platform)
                    added_platform = True
                assignments[clone_index] = state["record"]
                if backtrack(position + 1):
                    return True
                assignments[clone_index] = None
                state["remaining"] = remaining
                if added_platform:
                    assigned_platforms.remove(platform)
            return False

        if not backtrack(0):
            return [item for item in assignments if item is not None]
        return [item for item in assignments if item is not None]

    @staticmethod
    def _build_dbit_blueprint(context: NewUserSetupContext) -> dict[str, Any]:
        profile = context.parsed_profile
        clone_configs: list[dict[str, Any]] = []
        clone_details = OnboardingAdapter._clone_details(profile)
        business_scenarios = list(profile.business_scenarios or [])
        for clone_index, (detail, environment) in enumerate(
            zip(
                clone_details,
                context.environment_records,
                strict=False,
            )
        ):
            scenario = OnboardingAdapter._select_business_scenario_for_clone(
                business_scenarios,
                clone_index=clone_index,
                platform=detail.platform or environment.platform,
            )
            scenario_keywords = list(scenario.keywords) if scenario and scenario.keywords else profile.search_keywords
            scenario_target_audience = (
                scenario.target_audience if scenario and scenario.target_audience else profile.target_audience
            )
            scenario_background = (
                scenario.business_intro if scenario and scenario.business_intro else profile.business_background
            )
            scenario_selling_points = (
                "; ".join(scenario.selling_points)
                if scenario and scenario.selling_points
                else profile.business_selling_points
            )
            scenario_excluded_audience = scenario.excluded_audience if scenario else None
            scenario_comment_reply_template = scenario.comment_reply_template if scenario else None
            scenario_auto_post_copy = scenario.auto_post_copy if scenario else None
            scenario_asset_folder = scenario.asset_folder if scenario else None
            clone_configs.append(
                {
                    "clone_name": detail.clone_name or environment.clone_name,
                    "platform": detail.platform or environment.platform,
                    "platform_account": detail.platform_account,
                    "platform_password": detail.platform_password,
                    "adspower_environment_name": environment.environment_name,
                    "adspower_profile_id": environment.profile_id,
                    "proxy_mode": profile.proxy_mode,
                    "proxy_user_id": profile.proxy_user_id,
                    "proxy_sync_key": profile.proxy_sync_key,
                    "proxy_host": environment.proxy_host,
                    "proxy_port": environment.proxy_port,
                    "search_keywords": scenario_keywords,
                    "target_audience": scenario_target_audience,
                    "business_background": scenario_background,
                    "business_selling_points": scenario_selling_points,
                    "excluded_audience": scenario_excluded_audience,
                    "comment_reply_template": scenario_comment_reply_template,
                    "auto_post_copy": scenario_auto_post_copy,
                    "asset_folder": scenario_asset_folder or profile.asset_folder,
                }
            )

        warnings = list(context.parse_warnings)
        if not profile.ai_model_api_key:
            warnings.append("AI 模型 API Key 为空，当前蓝图仅标记为待补，不阻断流程。")
        if not context.environment_records:
            warnings.append("尚未创建 AdsPower 环境，Dbit 分身配置无法完整写入。")

        return {
            "dbit": {
                "username": profile.dbit_username,
                "password": profile.dbit_password,
                "license_code": profile.dbit_license_code,
                "ai_model_api_key": profile.ai_model_api_key,
                "ai_model_api_key_status": "ready" if profile.ai_model_api_key else "pending",
            },
            "customer": {
                "company_name": profile.company_name or profile.customer_name,
                "contact_name": profile.contact_name,
                "contact_handle": profile.contact_handle,
                "target_market": profile.target_market,
            },
            "proxy": {
                "mode": profile.proxy_mode,
                "user_id": profile.proxy_user_id,
                "sync_key": profile.proxy_sync_key,
            },
            "business": {
                "summary": profile.business_summary,
                "target_audience": profile.target_audience,
                "background": profile.business_background,
                "selling_points": profile.business_selling_points,
                "keywords": profile.search_keywords,
            },
            "environments": [record.model_dump(mode="json") for record in context.environment_records],
            "clone_configs": clone_configs,
            "warnings": warnings,
            "manual_constraints": [
                "禁止自动点击最终支付确认。",
                "禁止自动启动分身。",
                "敏感表单提交前必须人工复核。",
            ],
        }

    @staticmethod
    def _select_business_scenario_for_clone(
        scenarios: list[Any],
        *,
        clone_index: int,
        platform: str | None,
    ) -> Any | None:
        if not scenarios:
            return None
        normalized_platform = _normalize_dbit_platform(platform)
        matching = [
            scenario
            for scenario in scenarios
            if _normalize_dbit_platform(getattr(scenario, "platform", None)) in {"", normalized_platform}
        ]
        if not matching:
            matching = scenarios
        if clone_index < len(matching):
            return matching[clone_index]
        return matching[min(len(matching) - 1, 0)]


def _normalize_dbit_platform(value: str | None) -> str:
    normalized = str(value or "").strip().casefold()
    if normalized in {"facebook", "face book", "face-book", "fb"}:
        return "facebook"
    if normalized in {"tiktok", "tik tok", "tiki tok"}:
        return "tiktok"
    return normalized


def _available_non_system_windows_roots() -> list[Path]:
    roots: list[Path] = []
    seen: set[str] = set()
    for letter in ascii_uppercase:
        if letter == "C":
            continue
        root = Path(f"{letter}:\\")
        if root.exists() and str(root).casefold() not in seen:
            roots.append(root)
            seen.add(str(root).casefold())
    cwd_anchor = Path.cwd().anchor
    if cwd_anchor:
        root = Path(cwd_anchor)
        if root.exists() and root.drive.casefold() != "c:" and str(root).casefold() not in seen:
            roots.append(root)
    return roots


def _saved_proxy_sort_key(proxy_record: dict[str, Any]) -> tuple[int, str]:
    for key in ("proxy_id", "id", "Proxy_id"):
        raw = str(proxy_record.get(key, "")).strip()
        if raw.isdigit():
            return int(raw), raw
    return 0, str(proxy_record.get("proxy_id", "") or "")


def _saved_proxy_remaining_capacity(proxy_record: dict[str, Any]) -> int:
    try:
        profile_count = int(str(proxy_record.get("profile_count", "0") or "0").strip() or "0")
    except Exception:
        profile_count = 0
    related_profile_no = proxy_record.get("related_profile_no")
    related_count = 0
    if isinstance(related_profile_no, list):
        related_count = sum(1 for item in related_profile_no if str(item).strip())
    current_usage = max(profile_count, related_count)
    return max(0, 2 - current_usage)


def _is_unused_saved_proxy(proxy_record: dict[str, Any]) -> bool:
    try:
        profile_count = int(str(proxy_record.get("profile_count", "0") or "0").strip() or "0")
    except Exception:
        profile_count = 0
    related_profile_no = proxy_record.get("related_profile_no")
    has_related_profiles = isinstance(related_profile_no, list) and any(str(item).strip() for item in related_profile_no)
    return profile_count <= 0 and not has_related_profiles


def _is_static_proxy_mode(value: str | None) -> bool:
    normalized = str(value or "").strip().casefold()
    return normalized in {"static", "static_sync", "静态", "静态代理"}


def _is_dynamic_proxy_mode(value: str | None) -> bool:
    normalized = str(value or "").strip().casefold()
    return normalized in {"dynamic", "dynamic_proxy", "动态", "动态代理"}
