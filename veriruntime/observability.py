"""Structured internal events and a separate user-facing renderer."""
from __future__ import annotations
import json
from pathlib import Path

from .model import to_data
from .plan import CacheLookupPlan, ParallelPlan, RunPlan, SequencePlan
from .runtime.process import utc_now


class EventLog:
    def __init__(self, path: Path | None = None):
        self.path = path
        self.events: list[dict] = []

    def emit(self, kind, **data):
        event = {"time": utc_now(), "kind": kind, **data}
        self.events.append(event)
        if self.path:
            with self.path.open("a") as output:
                output.write(json.dumps(event) + "\n")
        return event


def plan_lines(plan, indent=""):
    if isinstance(plan, RunPlan):
        return [f"{indent}Run({plan.tool}, time_slice={plan.time_slice_sec}s)"]
    if isinstance(plan, CacheLookupPlan):
        return [f"{indent}CacheLookup({plan.semantic_key[:16]}…, source={plan.source_execution_id})",
                f"{indent}  Fallback if evidence is invalid:", *plan_lines(plan.fallback, indent + "    ")]
    label = "Parallel" if isinstance(plan, ParallelPlan) else "Sequence"
    lines = [f"{indent}{label}" + (f"(max_parallel={plan.max_parallel})" if isinstance(plan, ParallelPlan) else "")]
    for child in plan.children:
        lines.extend(plan_lines(child, indent + "  "))
    return lines


class CLIRenderer:
    def emit(self, value):
        print(value)

    def json(self, value):
        self.emit(json.dumps(to_data(value), indent=2, sort_keys=True))

    def explain(self, workflow, optimizations, profiles):
        self.emit("Logical Workflow")
        self.emit(f"  Workflow {workflow.workflow_id} (provided by DSL / upstream semantic planner)")
        for goal in workflow.goals:
            self.emit(f"  {goal.id}: Verify({goal.property.kind}, entry={goal.entry}, {goal.semantics.c_standard}/{goal.semantics.data_model})")
            self.emit(f"    sources: {', '.join(goal.program.sources)}; confirmations >= {goal.requirements.min_confirmations}")
            self.emit(f"    semantic goal key: {goal.semantic_key}")
        self.emit("  Dependencies: " + ("none" if not workflow.dependencies else ""))
        for dep in workflow.dependencies:
            self.emit(f"    {dep.predecessor} --[{dep.required_verdict.value} and required confirmations]--> {dep.successor}")
        self.emit("Physical Execution (per-goal optimizer)")
        versions = {p.name: p for p in profiles}
        for goal, opt in zip(workflow.goals, optimizations):
            self.emit(f"  Goal {goal.id}: CACHE {opt.explanation['cache']}")
            self.emit("    Tool Candidates:")
            for candidate in opt.candidate_scores:
                profile = versions[candidate.tool]
                count = candidate.history.run_count if candidate.history else 0
                self.emit(f"      {candidate.tool}: compatible={candidate.compatible}; available={profile.available}; version={profile.version or '-'}")
                self.emit(f"        history runs={count}; success_rate={candidate.success_rate:.3f}; expected={candidate.expected_runtime_sec:.3f}s; score={candidate.score:.3f}")
                if candidate.reasons:
                    self.emit(f"        filtered: {', '.join(candidate.reasons)}")
            self.emit(f"    Optimizer: {opt.explanation['reason']}")
            self.emit(f"    Selected: {', '.join(opt.explanation['selected_tools']) or 'cached evidence / no candidate'}")
            self.emit(f"    Trust feasible: {opt.explanation['trust_feasible']}")
            self.emit("    Physical Plan:")
            for line in plan_lines(opt.physical_plan, "      "):
                self.emit(line)

    def execution(self, execution, cache_enabled=True):
        for goal in execution.goals:
            result = goal.report.result
            self.emit("CACHE HIT" if result.cache_hit else "CACHE MISS" if cache_enabled else "CACHE DISABLED")
            self.emit(f"Goal {result.goal_id}: {result.verdict.value}; confirmations={result.confirmations}; requirement_satisfied={result.requirement_satisfied}")
            self.emit(f"  execution: {result.execution_id}; status: {result.status}; wall time: {result.wall_time_sec:.3f}s")
            self.emit(f"  verifier executions: {len(goal.report.attempts)}")
            for attempt in goal.report.attempts:
                self.emit(f"  {attempt.tool}: {attempt.status.value} / {attempt.verdict.value} ({attempt.wall_time_sec:.3f}s)")
                self.emit(f"    version: {attempt.version}; termination: {attempt.termination_reason.value}")
            self.emit(f"  Artifacts: {len(result.artifacts)} references; inspect with vrun show {result.execution_id}")
            if result.source_execution_id:
                self.emit(f"  Provenance source: {result.source_execution_id}")
            if result.failure_reasons:
                self.emit(f"  Diagnostics: {', '.join(result.failure_reasons)}")
