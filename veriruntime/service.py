"""Public fixed-goal/workflow API, independent of upstream semantic planners."""
from __future__ import annotations

from dataclasses import dataclass, replace
import json
from pathlib import Path
import time
import uuid

from veriruntime.model import (Diagnostic, LogicalPlan, TerminationReason, VerificationResult,
                               VerificationWorkflow, Verdict)
from veriruntime.model import to_data
from veriruntime.artifacts import ArtifactStore
from veriruntime.cache import SemanticCache
from veriruntime.optimizer import CostAwareOptimizer, OptimizationResult, RuntimeContext
from veriruntime.plan import CacheLookupPlan
from veriruntime.runtime import Runtime, RuntimeReport
from veriruntime.store import ExecutionStore
from veriruntime.tools import default_registry


@dataclass(frozen=True)
class GoalExecution:
    optimization: OptimizationResult
    report: RuntimeReport


@dataclass(frozen=True)
class WorkflowExecution:
    execution_id: str
    workflow: VerificationWorkflow
    goals: tuple[GoalExecution, ...]


class VerificationService:
    def __init__(self, data_dir=".veriruntime", registry=None, optimizer=None, cache_enabled=True, execution_backend=None):
        self.data_dir = Path(data_dir).resolve()
        self.registry = registry or default_registry()
        self.store = ExecutionStore(self.data_dir)
        self.optimizer = optimizer or CostAwareOptimizer()
        self.cache_enabled = cache_enabled
        self.artifacts = ArtifactStore(self.data_dir, self.store)
        self.cache = SemanticCache(self.store, self.artifacts, self.registry)
        self.execution_backend = execution_backend

    def optimize_goal(self, task):
        return self.optimizer.optimize(LogicalPlan(task), RuntimeContext(self.registry, self.store,
                                       self.cache if self.cache_enabled else None))

    def explain_workflow(self, workflow):
        return tuple(self.optimize_goal(task) for task in workflow.goals)

    def _record_attempt(self, attempt, task):
        workspace = Path(attempt.workspace)
        refs = [self.artifacts.put_file(path, kind, attempt.id).id for kind, path in (
            ("STDOUT", attempt.stdout_path), ("STDERR", attempt.stderr_path),
            ("COMMAND", workspace / "command.json"), ("ATTEMPT", workspace / "attempt.json"),
            ("EXECUTION_SPEC", workspace / "execution-spec.json"),
            ("BACKEND_OUTCOME", workspace / "backend-outcome.json")) if Path(path).exists()]
        for kind, path in self.registry.get(attempt.tool).collect_artifacts(workspace):
            refs.append(self.artifacts.put_file(path, kind, attempt.id).id)
        attempt = replace(attempt, artifact_ids=tuple(refs))
        self.store.record_attempt(attempt, task)
        return attempt

    def _finish_goal(self, task, optimization, report):
        if report.result.verdict == Verdict.UNKNOWN and not report.attempts:
            codes = {reason for candidate in optimization.candidate_scores for reason in candidate.reasons}
            diagnostics = tuple(Diagnostic(code, task.id) for code in sorted(codes))
            report = replace(report, result=replace(report.result,
                diagnostics=report.result.diagnostics + diagnostics,
                failure_reasons=tuple(sorted(set(report.result.failure_reasons) | codes))))
        evidence = self.store.definitive_evidence(task.semantic_key)
        if {a["verdict"] for a in evidence} == {"SAFE", "UNSAFE"}:
            diagnostic = Diagnostic("conflicting_verdict", task.id,
                                    detail="Opposing completed evidence in this goal's execution history")
            report = replace(report, result=replace(report.result, verdict=Verdict.CONFLICT, confirmations=0,
                requirement_satisfied=False, termination_reason=TerminationReason.CONFLICT,
                diagnostics=report.result.diagnostics + (diagnostic,),
                failure_reasons=tuple(sorted(set(report.result.failure_reasons + (diagnostic.code,)))),
                artifacts=tuple(dict.fromkeys(report.result.artifacts + tuple(ref for a in evidence for ref in a.get("artifact_ids", []))))))
            self.cache.clear([task.semantic_key])
        refs = list(report.result.artifacts)
        for kind, value in (("INPUT_SNAPSHOT", task), ("LOGICAL_PLAN", LogicalPlan(task)),
                            ("PHYSICAL_PLAN", optimization.physical_plan), ("OPTIMIZER", optimization),
                            ("LOG", report.events)):
            refs.append(self.artifacts.put_bytes(json.dumps(to_data(value), sort_keys=True).encode(), kind).id)
        refs.extend(ref for attempt in report.attempts for ref in attempt.artifact_ids)
        refs = tuple(dict.fromkeys(refs))
        report = replace(report, result=replace(report.result, artifacts=refs,
                                               optimizer_summary=optimization.explanation))
        root = self.data_dir / "executions" / report.result.execution_id
        root.mkdir(parents=True, exist_ok=True)
        for filename, value in (("logical.json", LogicalPlan(task)), ("physical.json", optimization.physical_plan),
                                ("optimization.json", optimization), ("result.json", report.result)):
            (root / filename).write_text(json.dumps(to_data(value), indent=2))
        (root / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in report.events))
        result_ref = self.artifacts.put_bytes(json.dumps(to_data(report.result), sort_keys=True).encode(), "RESULT").id
        self.store.finish_execution(report, (*refs, result_ref))
        if self.cache_enabled:
            self.cache.store_result(task, report, (*refs, result_ref))
        return GoalExecution(optimization, report)

    async def verify_goal(self, task, cancellation=None, optimization=None, workflow_execution_id=None):
        optimization = optimization or self.optimize_goal(task)
        execution_id = uuid.uuid4().hex
        self.store.start_execution(execution_id, task, optimization, workflow_execution_id)
        start = time.monotonic()
        entry = self.cache.lookup(task) if self.cache_enabled and isinstance(optimization.physical_plan, CacheLookupPlan) else None
        if entry and not (cancellation and cancellation.reason):
            from .runtime.process import utc_now
            result = VerificationResult(execution_id, task.semantic_key, entry.verdict, entry.confirmations,
                True, TerminationReason.REQUIREMENTS_MET, (), time.monotonic() - start, True,
                entry.source_execution_id, goal_id=task.id, artifacts=entry.artifact_ids)
            report = RuntimeReport(result, (), ({"kind": "cache_hit", "time": utc_now(),
                "source_execution_id": entry.source_execution_id, "semantic_key": task.semantic_key},))
        else:
            runtime = Runtime(self.registry, self.data_dir, on_attempt=self._record_attempt,
                              execution_backend=self.execution_backend)
            report = await runtime.execute_goal(LogicalPlan(task), optimization.physical_plan, cancellation,
                                                 execution_id=execution_id)
        return self._finish_goal(task, optimization, report)

    async def verify(self, workflow_or_task, cancellation=None):
        workflow = (workflow_or_task if isinstance(workflow_or_task, VerificationWorkflow)
                    else VerificationWorkflow.single(workflow_or_task))
        execution_id = uuid.uuid4().hex
        self.store.start_workflow(execution_id, workflow)
        remaining = {task.id: task for task in workflow.goals}
        results, executions = {}, []
        # This thin workflow scheduler respects explicit control edges. It performs
        # no theorem composition, assumption propagation or semantic replanning.
        while remaining:
            ready = [task for task in remaining.values() if all(d.predecessor in results
                for d in workflow.dependencies if d.successor == task.id)]
            for task in ready:
                dependencies = [d for d in workflow.dependencies if d.successor == task.id]
                blocked = [d.predecessor for d in dependencies if
                    results[d.predecessor].verdict != d.required_verdict or
                    not results[d.predecessor].requirement_satisfied]
                if blocked:
                    diagnostic = Diagnostic("dependency_not_satisfied", task.id, detail=", ".join(blocked))
                    result = VerificationResult(uuid.uuid4().hex, task.semantic_key, Verdict.UNKNOWN, 0,
                        False, TerminationReason.EXHAUSTED, (), 0, goal_id=task.id, status="BLOCKED",
                        diagnostics=(diagnostic,), failure_reasons=(diagnostic.code,))
                    optimization = self.optimize_goal(task)
                    self.store.start_execution(result.execution_id, task, optimization, execution_id)
                    execution = self._finish_goal(task, optimization, RuntimeReport(result, (), ()))
                else:
                    execution = await self.verify_goal(task, cancellation, workflow_execution_id=execution_id)
                executions.append(execution)
                results[task.id] = execution.report.result
                remaining.pop(task.id)
        self.store.finish_workflow(execution_id, executions)
        return WorkflowExecution(execution_id, workflow, tuple(executions))
