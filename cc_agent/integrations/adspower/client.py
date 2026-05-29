from __future__ import annotations

import os
import time
from typing import Any

import httpx


class AdsPowerClient:
    _HEALTHCHECK_USER_ID = "healthcheck"

    def __init__(
        self,
        base_url: str,
        timeout_seconds: float = 30.0,
        api_key_env: str = "ADSPOWER_API_KEY",
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._api_key_env = api_key_env
        self._min_request_interval_seconds = 0.35
        self._last_request_started_at = 0.0
        self._rate_limit_retry_delays = (0.8, 1.5, 2.5)

    def create_profile(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/api/v2/browser-profile/create", json=payload)

    def update_profile(self, profile_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = dict(payload)
        body["user_id"] = profile_id
        return self._request("POST", "/api/v1/user/update", json=body)

    def query_profiles(
        self,
        *,
        group_id: str | None = None,
        user_id: str | None = None,
        serial_number: str | None = None,
        page: int = 1,
        page_size: int = 100,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"page": page, "page_size": page_size}
        if group_id:
            params["group_id"] = group_id
        if user_id:
            params["user_id"] = user_id
        if serial_number:
            params["serial_number"] = serial_number
        return self._request("GET", "/api/v1/user/list", params=params)

    def query_proxies(
        self,
        *,
        page: int = 1,
        limit: int = 50,
        proxy_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"page": page, "limit": limit}
        if proxy_ids:
            body["Proxy_id"] = proxy_ids
        return self._request("POST", "/api/v2/proxy-list/list", json=body)

    def start_browser(self, profile_id: str = "", profile_no: str = "") -> dict[str, Any]:
        params = self._build_profile_params(profile_id, profile_no)
        return self._request("GET", "/api/v1/browser/start", params=params)

    def stop_browser(self, profile_id: str = "", profile_no: str = "") -> dict[str, Any]:
        params = self._build_profile_params(profile_id, profile_no)
        return self._request("GET", "/api/v1/browser/stop", params=params)

    def is_ready(self) -> bool:
        return self.probe_ready()[0]

    def probe_ready(self) -> tuple[bool, str]:
        try:
            with httpx.Client(timeout=min(self._timeout_seconds, 5.0), trust_env=False) as client:
                response = client.get(
                    f"{self._base_url}/api/v1/browser/active",
                    headers=self._headers(),
                    params={"user_id": self._HEALTHCHECK_USER_ID},
                )
                status_code = response.status_code
                if status_code >= 400:
                    return False, f"HTTP {status_code}"
                payload = response.json()
            code = payload.get("code", -1)
            if code in (0, "0"):
                return True, "ready"
            message = str(payload.get("msg", "")).strip() or f"API code {code}"
            return False, message
        except Exception as exc:
            return False, str(exc)

    def _headers(self) -> dict[str, str]:
        api_key = os.getenv(self._api_key_env, "").strip()
        if not api_key:
            return {}
        return {"Authorization": f"Bearer {api_key}"}

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        for attempt, retry_delay in enumerate((0.0, *self._rate_limit_retry_delays)):
            if retry_delay > 0:
                time.sleep(retry_delay)
            self._throttle()
            with httpx.Client(timeout=self._timeout_seconds, trust_env=False) as client:
                response = client.request(
                    method=method,
                    url=f"{self._base_url}{path}",
                    headers=self._headers(),
                    params=params,
                    json=json,
                )
                response.raise_for_status()
                payload = response.json()
            try:
                self._raise_for_api_error(payload)
                return payload
            except ValueError as exc:
                if attempt >= len(self._rate_limit_retry_delays) or not self._is_rate_limit_error(str(exc)):
                    raise
        raise RuntimeError("Unreachable")

    def _throttle(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_request_started_at
        if elapsed < self._min_request_interval_seconds:
            time.sleep(self._min_request_interval_seconds - elapsed)
        self._last_request_started_at = time.monotonic()

    @staticmethod
    def _is_rate_limit_error(message: str) -> bool:
        normalized = message.casefold()
        return "too many request" in normalized or "rate limit" in normalized

    @staticmethod
    def _build_profile_params(profile_id: str, profile_no: str) -> dict[str, str]:
        if profile_id:
            return {"user_id": profile_id}
        if profile_no:
            return {"serial_number": profile_no}
        raise ValueError("Either profile_id or profile_no must be provided.")

    @staticmethod
    def _raise_for_api_error(payload: dict[str, Any]) -> None:
        code = payload.get("code", 0)
        if code in (0, "0", None):
            return
        msg = str(payload.get("msg", "")).strip() or "Unknown AdsPower error"
        raise ValueError(f"AdsPower API error {code}: {msg}")
