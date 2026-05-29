from __future__ import annotations

import logging
from typing import Any, Callable
from uuid import uuid4

from cc_agent.domain.enums import RunStatus
from cc_agent.domain.models import ExecutionResult, TaskSpec
from cc_agent.runtime.artifact_store import ArtifactStore
from cc_agent.runtime.state_store import RunStateStore
from cc_agent.runtime.step_runner import StepRunner
from cc_agent.storage.repositories import RunRepository


logger = logging.getLogger(__name__)


class Executor:
    def __init__(
        self,
        step_runner: StepRunner,
        run_repository: RunRepository,
        artifact_store: ArtifactStore,
        state_store: RunStateStore,
    ) -> None:
        self._step_runner = step_runner
        self._run_repository = run_repository
        self._artifact_store = artifact_store
        self._state_store = state_store

    def execute(
        self,
        task: TaskSpec,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> ExecutionResult:
        return self._execute_task(task, source_checkpoint_id=None, progress_callback=progress_callback)

    def resume(
        self,
        checkpoint_id: str,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> ExecutionResult:
        record = self._run_repository.get_checkpoint(checkpoint_id)
        if record is None:
            raise ValueError(f"找不到待恢复的 checkpoint: {checkpoint_id}")
        task = TaskSpec.model_validate_json(record.task_json)
        return self._execute_task(task, source_checkpoint_id=checkpoint_id, progress_callback=progress_callback)

    def resume_latest(
        self,
        target: str | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> ExecutionResult:
        record = self._run_repository.get_latest_checkpoint(target=target)
        if record is None:
            raise ValueError("当前没有可继续执行的等待任务。")
        return self.resume(record.id, progress_callback=progress_callback)

    def _execute_task(
        self,
        task: TaskSpec,
        source_checkpoint_id: str | None,
        progress_callback: Callable[[dict[str, Any]], None] | None,
    ) -> ExecutionResult:
        run_id = str(uuid4())
        self._run_repository.create_run(run_id, task, RunStatus.RUNNING)
        self._state_store.set_state(run_id, RunStatus.RUNNING)

        step_results = []
        final_status = RunStatus.SUCCEEDED
        summary = task.summary
        collected_artifacts: list[str] = []
        metadata: dict[str, Any] = {}
        should_delete_source_checkpoint = source_checkpoint_id is not None

        for index, step in enumerate(task.steps):
            logger.info(
                "Run %s step %s/%s started: %s.%s -> %s",
                run_id,
                index + 1,
                len(task.steps),
                step.adapter,
                step.action,
                step.description,
            )
            if progress_callback is not None:
                progress_callback(
                    {
                        "event": "step_started",
                        "run_id": run_id,
                        "target": task.target,
                        "task_summary": task.summary,
                        "step_index": index + 1,
                        "total_steps": len(task.steps),
                        "step_id": step.id,
                        "step_description": step.description,
                        "adapter": step.adapter,
                        "action": step.action,
                    }
                )
            result = self._step_runner.run(step)
            step_results.append(result)
            collected_artifacts.extend(result.artifacts)
            logger.info(
                "Run %s step %s/%s finished: status=%s message=%s",
                run_id,
                index + 1,
                len(task.steps),
                result.status.value,
                result.message,
            )
            if progress_callback is not None:
                progress_callback(
                    {
                        "event": "step_finished",
                        "run_id": run_id,
                        "target": task.target,
                        "step_index": index + 1,
                        "total_steps": len(task.steps),
                        "step_id": step.id,
                        "step_description": step.description,
                        "adapter": step.adapter,
                        "action": step.action,
                        "status": result.status.value,
                        "message": result.message,
                    }
                )
            if result.status == RunStatus.SUCCEEDED:
                continue
            if result.status == RunStatus.WAITING_INPUT:
                final_status = RunStatus.WAITING_INPUT
                summary = result.message or f"任务在等待人工输入：{step.description}"
                checkpoint_task = self._build_resume_task(task, result.data, index)
                if checkpoint_task is not None:
                    checkpoint_id = str(uuid4())
                    wait_reason = str(result.data.get("wait_reason", "manual_input_required")).strip()
                    self._run_repository.save_checkpoint(
                        checkpoint_id=checkpoint_id,
                        run_id=run_id,
                        target=task.target,
                        summary=summary,
                        wait_reason=wait_reason,
                        task=checkpoint_task,
                    )
                    metadata["checkpoint_id"] = checkpoint_id
                    metadata["wait_reason"] = wait_reason
                    logger.warning("Run %s paused for input: wait_reason=%s checkpoint=%s", run_id, wait_reason, checkpoint_id)
                if source_checkpoint_id is not None:
                    self._run_repository.delete_checkpoint(source_checkpoint_id)
                    should_delete_source_checkpoint = False
                break
            final_status = RunStatus.FAILED
            detail = result.message or step.description
            summary = f"执行在以下步骤中断：{detail}"
            logger.error("Run %s failed at step %s: %s", run_id, step.id, detail)
            break

        execution_result = ExecutionResult(
            run_id=run_id,
            status=final_status,
            summary=summary,
            step_results=step_results,
            artifacts=collected_artifacts,
            metadata=metadata,
        )
        artifact_path = self._artifact_store.persist(execution_result)
        execution_result.artifacts.append(str(artifact_path))
        logger.info("Run %s artifact persisted: %s", run_id, artifact_path)

        self._run_repository.update_run(run_id, final_status, summary)
        self._state_store.set_state(run_id, final_status)
        logger.info("Run %s completed: status=%s summary=%s", run_id, final_status.value, summary)
        if should_delete_source_checkpoint and source_checkpoint_id is not None and final_status != RunStatus.WAITING_INPUT:
            self._run_repository.delete_checkpoint(source_checkpoint_id)
        return execution_result

    @staticmethod
    def _build_resume_task(
        task: TaskSpec,
        step_data: dict[str, Any],
        current_index: int,
    ) -> TaskSpec | None:
        resume_task_data = step_data.get("resume_task")
        if isinstance(resume_task_data, dict):
            return TaskSpec.model_validate(resume_task_data)
        remaining_steps = task.steps[current_index + 1 :]
        if not remaining_steps:
            return None
        return TaskSpec(
            user_command=task.user_command,
            target=task.target,
            summary=task.summary,
            steps=remaining_steps,
        )
