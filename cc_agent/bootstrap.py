from __future__ import annotations

import logging
import os
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ValidationError

from cc_agent.domain.models import AppConfig, LlmConfig, SafetyConfig
from cc_agent.integrations.adspower import AdsPowerAdapter
from cc_agent.integrations.clash_verge import ClashVergeAdapter
from cc_agent.integrations.dbit_octopus import DbitOctopusAdapter
from cc_agent.integrations.onboarding import OnboardingAdapter
from cc_agent.integrations.registry import IntegrationRegistry
from cc_agent.integrations.wps import WpsAdapter
from cc_agent.llm.client import MockPlannerClient, OpenAICompatiblePlannerClient, PlannerClient
from cc_agent.onboarding import OnboardingDocumentParser, OnboardingTaskFactory
from cc_agent.planner import CapabilityRouter, IntentParser, PlanBuilder
from cc_agent.runtime.artifact_store import ArtifactStore
from cc_agent.runtime.executor import Executor
from cc_agent.runtime.state_store import RunStateStore
from cc_agent.runtime.step_runner import StepRunner
from cc_agent.safety.guard import SafetyGuard
from cc_agent.storage.db import Database
from cc_agent.storage.repositories import RunRepository
from cc_agent.utils.logging import configure_logging
from cc_agent.utils.paths import AppPaths

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ApplicationContext:
    project_root: Path
    data_root: Path
    app_config: AppConfig
    llm_config: LlmConfig
    safety_config: SafetyConfig
    paths: AppPaths
    registry: IntegrationRegistry
    run_repository: RunRepository
    onboarding_document_parser: OnboardingDocumentParser
    onboarding_task_factory: OnboardingTaskFactory
    plan_builder: PlanBuilder
    safety_guard: SafetyGuard
    executor: Executor


def build_context(project_root: Path, data_root: Path | None = None) -> ApplicationContext:
    configure_logging()

    resolved_data_root = data_root or project_root
    config_root = _ensure_runtime_config(project_root, resolved_data_root)
    default_config_root = project_root / "config"

    app_config = _load_config_model(AppConfig, "app", config_root / "app.yaml", default_config_root / "app.yaml")
    llm_config = _load_config_model(
        LlmConfig,
        "llm",
        config_root / "llm.yaml",
        default_config_root / "llm.yaml",
        transform=_resolve_env_markers,
    )
    safety_config = _load_config_model(
        SafetyConfig,
        "safety",
        config_root / "safety.yaml",
        default_config_root / "safety.yaml",
    )

    paths = AppPaths.from_config(project_root, resolved_data_root, config_root, app_config)
    paths.ensure()

    database = Database(paths.database_path)
    recovered_database = database.initialize()
    if recovered_database is not None:
        logger.warning("Recovered runtime database from %s after startup sqlite failure.", recovered_database)

    run_repository = RunRepository(database)
    try:
        incomplete_runs = run_repository.fail_incomplete_runs()
    except sqlite3.DatabaseError:
        recovered_database = database.backup_corrupt_database()
        logger.exception("Run repository initialization failed; recreating runtime database.")
        database.initialize()
        run_repository = RunRepository(database)
        incomplete_runs = run_repository.fail_incomplete_runs()
        if recovered_database is not None:
            logger.warning("Recovered runtime database from %s after repository startup failure.", recovered_database)

    if incomplete_runs:
        logger.info("Marked %s incomplete runs as failed during startup.", incomplete_runs)

    onboarding_document_parser = OnboardingDocumentParser()
    onboarding_task_factory = OnboardingTaskFactory(paths.artifacts_root / "onboarding")

    registry = IntegrationRegistry()
    clash_adapter = ClashVergeAdapter(app_config.integrations.get("clash_verge", {}))
    adspower_adapter = AdsPowerAdapter(app_config.integrations.get("adspower", {}))
    dbit_adapter = DbitOctopusAdapter(
        app_config.integrations.get("dbit_octopus", {}),
        paths.config_dir / "flows" / "dbit_octopus",
        paths.screenshots_dir,
    )
    onboarding_adapter = OnboardingAdapter(
        app_config.integrations.get("onboarding", {}),
        onboarding_document_parser,
        onboarding_task_factory,
        adspower_adapter,
        dbit_adapter,
        app_config.integrations.get("dbit_octopus", {}),
    )
    registry.register(clash_adapter)
    registry.register(adspower_adapter)
    registry.register(dbit_adapter)
    registry.register(onboarding_adapter)
    registry.register(WpsAdapter(app_config.integrations.get("wps", {}), paths.exports_dir))

    planner_client = _create_planner_client(llm_config)
    router = CapabilityRouter()
    intent_parser = IntentParser(router)
    plan_builder = PlanBuilder(
        intent_parser,
        router,
        planner_client,
        onboarding_document_parser=onboarding_document_parser,
        onboarding_task_factory=onboarding_task_factory,
    )
    safety_guard = SafetyGuard(safety_config)
    executor = Executor(
        step_runner=StepRunner(registry),
        run_repository=run_repository,
        artifact_store=ArtifactStore(paths.logs_dir),
        state_store=RunStateStore(),
    )

    return ApplicationContext(
        project_root=project_root,
        data_root=resolved_data_root,
        app_config=app_config,
        llm_config=llm_config,
        safety_config=safety_config,
        paths=paths,
        registry=registry,
        run_repository=run_repository,
        onboarding_document_parser=onboarding_document_parser,
        onboarding_task_factory=onboarding_task_factory,
        plan_builder=plan_builder,
        safety_guard=safety_guard,
        executor=executor,
    )


def _create_planner_client(config: LlmConfig) -> PlannerClient:
    if config.provider.lower() == "mock":
        return MockPlannerClient()
    return OpenAICompatiblePlannerClient(config)


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _load_config_model(
    model_class: type[BaseModel],
    config_name: str,
    runtime_path: Path,
    default_path: Path,
    *,
    transform=None,
) -> BaseModel:
    payload = _load_runtime_yaml(config_name, runtime_path, default_path)
    if transform is not None:
        payload = transform(payload)

    try:
        return model_class.model_validate(payload)
    except ValidationError as exc:
        payload = _restore_default_config(config_name, runtime_path, default_path, exc)
        if transform is not None:
            payload = transform(payload)
        return model_class.model_validate(payload)


def _load_runtime_yaml(config_name: str, runtime_path: Path, default_path: Path) -> dict[str, Any]:
    try:
        return _load_yaml(runtime_path)
    except (OSError, yaml.YAMLError) as exc:
        return _restore_default_config(config_name, runtime_path, default_path, exc)


def _restore_default_config(
    config_name: str,
    runtime_path: Path,
    default_path: Path,
    error: Exception,
) -> dict[str, Any]:
    if runtime_path == default_path or not default_path.exists():
        raise error

    backup_path = _backup_problem_file(runtime_path)
    runtime_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(default_path, runtime_path)
    logger.warning(
        "Recovered %s config at %s using default config. Backup: %s. Cause: %s",
        config_name,
        runtime_path,
        backup_path,
        error,
    )
    return _load_yaml(runtime_path)


def _backup_problem_file(path: Path) -> Path | None:
    if not path.exists():
        return None

    timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")
    backup_path = path.with_name(f"{path.name}.broken.{timestamp}")
    path.rename(backup_path)
    return backup_path


def _ensure_runtime_config(project_root: Path, data_root: Path) -> Path:
    default_config_root = project_root / "config"
    runtime_config_root = data_root / "config"
    if runtime_config_root.exists():
        return runtime_config_root
    if default_config_root.exists():
        runtime_config_root.parent.mkdir(parents=True, exist_ok=True)
        import shutil

        shutil.copytree(default_config_root, runtime_config_root)
        return runtime_config_root
    runtime_config_root.mkdir(parents=True, exist_ok=True)
    return runtime_config_root


def _resolve_env_markers(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _resolve_env_markers(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_env_markers(item) for item in value]
    if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
        return os.getenv(value[2:-1], "")
    return value
