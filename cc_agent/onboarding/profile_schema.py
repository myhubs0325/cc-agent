from __future__ import annotations

from pydantic import BaseModel, Field


class CloneResourcePlan(BaseModel):
    platform: str
    clone_count: int | None = None
    account_count: int | None = None
    proxy_count: int | None = None
    environment_count: int | None = None
    note: str | None = None


class CloneAccountDetail(BaseModel):
    sequence: str
    platform: str
    clone_name: str | None = None
    adspower_environment_name: str | None = None
    platform_account: str | None = None
    platform_password: str | None = None
    account_region: str | None = None
    proxy_host: str | None = None
    proxy_port: str | None = None
    proxy_username: str | None = None
    proxy_password: str | None = None
    login_verified: bool | None = None
    profile_completed: bool | None = None
    note: str | None = None


class BusinessScenario(BaseModel):
    scenario_name: str
    platform: str | None = None
    reuse_clone_count: int | None = None
    keywords: list[str] = Field(default_factory=list)
    target_audience: str | None = None
    excluded_audience: str | None = None
    business_intro: str | None = None
    selling_points: list[str] = Field(default_factory=list)
    comment_reply_template: str | None = None
    auto_post_copy: str | None = None
    asset_folder: str | None = None


class ChecklistItem(BaseModel):
    item: str
    prepared: bool | None = None
    note: str | None = None


class OnboardingProfile(BaseModel):
    template_dir: str
    customer_name: str | None = None
    company_name: str | None = None
    contact_name: str | None = None
    contact_handle: str | None = None
    business_summary: str | None = None
    target_market: str | None = None
    requested_platforms: list[str] = Field(default_factory=list)
    tiktok_clone_count: int = 0
    facebook_clone_count: int = 0
    planned_adspower_environment_count: int = 0
    dbit_username: str | None = None
    dbit_password: str | None = None
    dbit_license_code: str | None = None
    adspower_username: str | None = None
    adspower_password: str | None = None
    adspower_plan_ready: bool | None = None
    ai_model_api_key: str | None = None
    platform: str | None = None
    platform_username: str | None = None
    platform_password: str | None = None
    proxy_mode: str | None = None
    proxy_user_id: str | None = None
    proxy_sync_key: str | None = None
    proxy_host: str | None = None
    proxy_port: str | None = None
    proxy_username: str | None = None
    proxy_password: str | None = None
    environment_name: str | None = None
    search_keywords: list[str] = Field(default_factory=list)
    target_audience: str | None = None
    business_background: str | None = None
    business_selling_points: str | None = None
    install_path: str | None = None
    asset_folder: str | None = None
    notes: str | None = None
    clone_plans: list[CloneResourcePlan] = Field(default_factory=list)
    clone_details: list[CloneAccountDetail] = Field(default_factory=list)
    business_scenarios: list[BusinessScenario] = Field(default_factory=list)
    checklist: list[ChecklistItem] = Field(default_factory=list)


class ParsedOnboardingTemplate(BaseModel):
    profile: OnboardingProfile
    warnings: list[str] = Field(default_factory=list)


class AdsPowerEnvironmentRecord(BaseModel):
    clone_name: str
    environment_name: str
    platform: str | None = None
    profile_id: str | None = None
    profile_no: str | None = None
    proxy_host: str | None = None
    proxy_port: str | None = None
    proxy_username: str | None = None
    proxy_password: str | None = None
    status: str = "pending"


class OnboardingFieldCandidate(BaseModel):
    source_label: str
    value: str
    mapped_field: str | None = None


class ParsedOnboardingSource(BaseModel):
    profile: OnboardingProfile
    warnings: list[str] = Field(default_factory=list)
    source_kind: str
    candidates: list[OnboardingFieldCandidate] = Field(default_factory=list)


class NewUserSetupRequest(BaseModel):
    installer_path: str
    source_path: str
    parsed_source: ParsedOnboardingSource


class NewUserSetupContext(BaseModel):
    installer_path: str
    source_path: str
    source_kind: str
    parsed_profile: OnboardingProfile
    parse_warnings: list[str] = Field(default_factory=list)
    adspower_purchase_status: str = "unknown"
    proxy_purchase_status: str = "unknown"
    environment_id: str | None = None
    environment_records: list[AdsPowerEnvironmentRecord] = Field(default_factory=list)
    run_checkpoint: str | None = None
    last_success_step: str | None = None
    setup_finished_before_manual_start: bool = False
    wait_reason: str | None = None
    dbit_blueprint_path: str | None = None
    system_config_applied: bool = False
    next_clone_index: int = 0
    adspower_download_guidance_opened: bool = False
    adspower_registration_guidance_opened: bool = False
    adspower_pricing_guidance_opened: bool = False
    proxy_purchase_guidance_opened: bool = False
    static_proxy_sync_guidance_opened: bool = False
