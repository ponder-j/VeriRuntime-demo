"""Plan interpreter and budget-aware, evidence-aware scheduler."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
from pathlib import Path
import time
import uuid

from veriruntime.model import (Diagnostic, ExecutionStatus, TerminationReason, VerificationResult, Verdict, to_data)
from veriruntime.plan import CacheLookupPlan, ParallelPlan, RunPlan, SequencePlan
from .process import Cancellation, MemoryMonitor, execute, utc_now


def reconcile(attempts, minimum: int) -> tuple[Verdict, int, bool]:
    evidence = {Verdict.SAFE: set(), Verdict.UNSAFE: set()}
    for attempt in attempts:
        if attempt.status == ExecutionStatus.COMPLETED and attempt.verdict.definitive:
            evidence[attempt.verdict].add(attempt.family)
    if evidence[Verdict.SAFE] and evidence[Verdict.UNSAFE]:
        return Verdict.CONFLICT, 0, False
    for verdict, families in evidence.items():
        if len(families) >= minimum:
            return verdict, len(families), True
    return Verdict.UNKNOWN, max(map(len, evidence.values())), False


@dataclass(frozen=True)
class RuntimeReport:
    result: VerificationResult
    attempts: tuple
    events: tuple[dict, ...]


class Runtime:
    def __init__(self, registry, data_dir: str | Path = ".veriruntime", on_attempt=None):
        self.registry = registry
        self.data_dir = Path(data_dir).resolve()
        self.on_attempt = on_attempt

    async def run(self, logical, plan, cancellation: Cancellation | None = None,
                  execution_id: str | None = None) -> RuntimeReport:
        return await self.execute_goal(logical, plan, cancellation, execution_id)

    async def execute_goal(self, logical, plan, cancellation: Cancellation | None = None,
                  execution_id: str | None = None) -> RuntimeReport:
        task = logical.task
        execution_id = execution_id or uuid.uuid4().hex
        root = self.data_dir / "executions" / execution_id
        root.mkdir(parents=True, exist_ok=False)
        (root / "logical.json").write_text(json.dumps(to_data(logical), indent=2))
        (root / "physical.json").write_text(json.dumps(to_data(plan), indent=2))
        start = time.monotonic()
        deadline = start + task.budget.wall_time_sec
        stop = Cancellation()
        memory = MemoryMonitor(task.budget.memory_mb)
        semaphore = asyncio.Semaphore(task.budget.max_parallel)
        from veriruntime.observability import EventLog
        journal = EventLog(root / "events.jsonl")
        attempts, events = [], journal.events
        event = journal.emit

        def done():
            verdict, _, satisfied = reconcile(attempts, task.requirements.min_confirmations)
            return satisfied or verdict == Verdict.CONFLICT or stop.reason is not None

        async def visit(node):
            if done() or time.monotonic() >= deadline or (cancellation and cancellation.reason):
                return
            if isinstance(node, RunPlan):
                async with semaphore:
                    if done() or time.monotonic() >= deadline or (cancellation and cancellation.reason):
                        return
                    adapter = self.registry.get(node.tool)
                    if not adapter.supports(task):
                        event("tool_skipped", tool=node.tool, reason="incompatible_or_unavailable")
                        return
                    event("tool_start", tool=node.tool, remaining_wall_sec=deadline - time.monotonic())
                    slice_deadline = min(deadline, time.monotonic() + node.time_slice_sec) if node.time_slice_sec else deadline
                    attempt = await execute(adapter, task, root / uuid.uuid4().hex, execution_id,
                                            slice_deadline, stop, memory, cancellation)
                    if self.on_attempt:
                        attempt = self.on_attempt(attempt, task) or attempt
                    attempts.append(attempt)
                    event("tool_end", tool=node.tool, attempt_id=attempt.id, verdict=attempt.verdict.value,
                          status=attempt.status.value, wall_time_sec=attempt.wall_time_sec)
            elif isinstance(node, SequencePlan):
                for child in node.children:
                    await visit(child)
                    if done():
                        break
                    event("fallback", reason="requirements_not_satisfied")
            elif isinstance(node, ParallelPlan):
                # Nested plan operators are allowed; a global semaphore bounds all subprocesses.
                local = asyncio.Semaphore(node.max_parallel)
                async def child_run(child):
                    async with local:
                        await visit(child)
                children = [asyncio.create_task(child_run(child)) for child in node.children]
                pending = set(children)
                try:
                    while pending:
                        completed, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                        # Reconcile every completion in this batch before deciding cancellation.
                        for child in completed:
                            child.result()
                        verdict, _, satisfied = reconcile(attempts, task.requirements.min_confirmations)
                        if verdict == Verdict.CONFLICT:
                            stop.cancel(TerminationReason.CONFLICT)
                        elif satisfied:
                            stop.cancel(TerminationReason.REQUIREMENTS_MET)
                        if stop.reason:
                            event("cancel_remaining", reason=stop.reason.value)
                except asyncio.CancelledError:
                    stop.cancel(TerminationReason.USER_CANCEL)
                finally:
                    await asyncio.gather(*children, return_exceptions=False)
            elif isinstance(node, CacheLookupPlan):
                # Cache validation belongs to the service; stale evidence executes the fallback.
                event("cache_fallback", reason="no_validated_cache_entry")
                await visit(node.fallback)
            else:
                raise TypeError(f"Unknown physical node: {type(node).__name__}")

        event("execution_start", semantic_key=task.semantic_key)
        await visit(plan)
        verdict, confirmations, satisfied = reconcile(attempts, task.requirements.min_confirmations)
        reason = (TerminationReason.CONFLICT if verdict == Verdict.CONFLICT else
                  TerminationReason.REQUIREMENTS_MET if satisfied else
                  cancellation.reason if cancellation and cancellation.reason else
                  stop.reason if stop.reason else
                  TerminationReason.WALL_BUDGET if time.monotonic() >= deadline else
                  TerminationReason.NO_CANDIDATES if not attempts else TerminationReason.EXHAUSTED)
        result = VerificationResult(execution_id, task.semantic_key, verdict, confirmations, satisfied,
                                    reason, tuple(a.id for a in attempts), time.monotonic() - start,
                                    goal_id=task.id,
                                    diagnostics=tuple(Diagnostic(a.diagnostic_code, task.id, a.tool, a.id, a.message)
                                                      for a in attempts if a.diagnostic_code),
                                    failure_reasons=tuple(sorted({a.diagnostic_code for a in attempts if a.diagnostic_code})))
        from dataclasses import replace
        if verdict in (Verdict.CONFLICT, Verdict.UNKNOWN):
            code = ("conflicting_verdict" if verdict == Verdict.CONFLICT else
                    "cancellation" if reason == TerminationReason.USER_CANCEL else
                    "timeout" if reason == TerminationReason.WALL_BUDGET and not attempts else
                    "no_compatible_tool" if not attempts else "insufficient_confirmations")
            result = replace(result, diagnostics=result.diagnostics + (Diagnostic(code, task.id),),
                             failure_reasons=tuple(sorted(set(result.failure_reasons + (code,)))))
        event("execution_end", verdict=verdict.value, confirmations=confirmations)
        (root / "result.json").write_text(json.dumps(to_data(result), indent=2))
        return RuntimeReport(result, tuple(attempts), tuple(events))
