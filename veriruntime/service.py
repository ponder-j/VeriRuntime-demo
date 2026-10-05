"""Public fixed-goal/workflow API, ready for a future upstream semantic planner."""
from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
import uuid

from veriruntime.model import (Diagnostic, LogicalPlan, TerminationReason, VerificationResult,
                               VerificationWorkflow, Verdict)
from veriruntime.optimizer import CostAwareOptimizer, OptimizationResult, RuntimeContext
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
    def __init__(self, data_dir=".veriruntime", registry=None, optimizer=None, cache_enabled=True):
        self.data_dir = Path(data_dir).resolve()
        self.registry = registry or default_registry()
        self.store = ExecutionStore(self.data_dir)
        self.optimizer = optimizer or CostAwareOptimizer()
        self.cache_enabled = cache_enabled

    def optimize_goal(self, task):
        return self.optimizer.optimize(LogicalPlan(task), RuntimeContext(self.registry, self.store))

    def explain_workflow(self, workflow):
        return tuple(self.optimize_goal(task) for task in workflow.goals)

    async def verify_goal(self, task, cancellation=None, optimization=None):
        optimization = optimization or self.optimize_goal(task)
        runtime = Runtime(self.registry, self.data_dir, on_attempt=self.store.record_attempt)
        report = await runtime.execute_goal(LogicalPlan(task), optimization.physical_plan, cancellation)
        result = replace(report.result, optimizer_summary=optimization.explanation)
        return GoalExecution(optimization, replace(report, result=result))

    async def verify(self, workflow_or_task, cancellation=None):
        workflow = (workflow_or_task if isinstance(workflow_or_task, VerificationWorkflow)
                    else VerificationWorkflow.single(workflow_or_task))
        execution_id = uuid.uuid4().hex
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
                    execution = GoalExecution(self.optimize_goal(task), RuntimeReport(result, (), ()))
                else:
                    execution = await self.verify_goal(task, cancellation)
                executions.append(execution)
                results[task.id] = execution.report.result
                remaining.pop(task.id)
        return WorkflowExecution(execution_id, workflow, tuple(executions))
