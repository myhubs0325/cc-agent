from __future__ import annotations

import re
from typing import Callable

from cc_agent.onboarding.profile_schema import CloneAccountDetail, OnboardingFieldCandidate, OnboardingProfile


def _normalize_label(label: str) -> str:
    lowered = label.strip().lower()
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", lowered)


def _split_tokens(value: str) -> list[str]:
    return [item.strip() for item in re.split(r"[,;/\n，；、]+", value) if item.strip()]


class OnboardingFieldMapper:
    def __init__(self) -> None:
        self._direct_mappings: dict[str, str] = {
            "客户名称": "customer_name",
            "客户名": "customer_name",
            "公司名称": "company_name",
            "品牌名称": "company_name",
            "联系人": "contact_name",
            "联系电话": "contact_handle",
            "微信": "contact_handle",
            "dbit登录账号": "dbit_username",
            "dbit账号": "dbit_username",
            "dbit登录密码": "dbit_password",
            "dbit密码": "dbit_password",
            "adspower登录账号": "adspower_username",
            "adspower账号": "adspower_username",
            "adspower登录密码": "adspower_password",
            "adspower密码": "adspower_password",
            "平台类型": "platform",
            "平台": "platform",
            "平台账号": "platform_username",
            "平台用户名": "platform_username",
            "账号": "platform_username",
            "平台密码": "platform_password",
            "代理类型": "proxy_mode",
            "代理模式": "proxy_mode",
            "代理用户id": "proxy_user_id",
            "代理userid": "proxy_user_id",
            "用户id": "proxy_user_id",
            "代理密钥": "proxy_sync_key",
            "代理key": "proxy_sync_key",
            "代理ip": "proxy_host",
            "代理host": "proxy_host",
            "代理端口": "proxy_port",
            "代理用户名": "proxy_username",
            "代理账号": "proxy_username",
            "代理密码": "proxy_password",
            "环境名称": "environment_name",
            "分身名称": "environment_name",
            "搜索关键词": "search_keywords",
            "关键字": "search_keywords",
            "客户画像": "target_audience",
            "业务背景": "business_background",
            "业务介绍": "business_background",
            "卖点": "business_selling_points",
            "安装目录": "install_path",
            "软件安装目录": "install_path",
            "软件准备安装到哪里": "install_path",
            "素材目录": "asset_folder",
            "素材文件夹": "asset_folder",
            "备注": "notes",
        }
        self._value_parsers: dict[str, Callable[[str], object]] = {
            "search_keywords": _split_tokens,
            "business_selling_points": lambda value: "; ".join(_split_tokens(value)),
        }

    def map_candidates(self, candidates: list[OnboardingFieldCandidate], source_path: str) -> OnboardingProfile:
        payload: dict[str, object] = {"template_dir": source_path}
        for candidate in candidates:
            mapped_field = self._match_field(candidate.source_label)
            candidate.mapped_field = mapped_field
            if not mapped_field:
                continue
            value = candidate.value.strip()
            if not value:
                continue
            parser = self._value_parsers.get(mapped_field)
            payload[mapped_field] = parser(value) if parser is not None else value

        platform = str(payload.get("platform") or "").strip()
        platform_username = str(payload.get("platform_username") or "").strip()
        proxy_host = str(payload.get("proxy_host") or "").strip()
        proxy_port = str(payload.get("proxy_port") or "").strip()
        environment_name = str(payload.get("environment_name") or "").strip()

        if payload.get("customer_name") and not payload.get("company_name"):
            payload["company_name"] = payload["customer_name"]
        if payload.get("platform_username") and not payload.get("contact_handle"):
            payload["contact_handle"] = payload["platform_username"]

        profile = OnboardingProfile.model_validate(payload)
        if platform or platform_username or proxy_host or proxy_port or environment_name:
            profile.clone_details = [
                CloneAccountDetail(
                    sequence="1",
                    platform=platform or "",
                    clone_name=environment_name or None,
                    adspower_environment_name=environment_name or None,
                    platform_account=platform_username or None,
                    platform_password=profile.platform_password,
                    proxy_host=proxy_host or None,
                    proxy_port=proxy_port or None,
                    proxy_username=profile.proxy_username,
                    proxy_password=profile.proxy_password,
                )
            ]
        return profile

    def _match_field(self, label: str) -> str | None:
        normalized = _normalize_label(label)
        if normalized in self._direct_mappings:
            return self._direct_mappings[normalized]
        for key, field_name in self._direct_mappings.items():
            if len(key) >= 4 and key in normalized:
                return field_name
        return None
