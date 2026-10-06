"""Attempt orchestration and budget policy, independent of process implementation."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import time

from veriruntime.execution import ExecutionOutcome, ExecutionSpec, LocalExecutionBackend
# Compatibility timestamp export; process operations remain inside the backend.
from veriruntime.execution.local import utc_now
from veriruntime.model import ExecutionAttempt, ExecutionStatus, TerminationReason, Verdict, to_data


class Cancellation:
    def __init__(self):
        self.reason: TerminationReason | None = None

    def cancel(self, reason=TerminationReason.USER_CANCEL):
        if self.reason is None:
            self.reason = reason


class MemoryMonitor:
    """Aggregate sampled-memory policy using opaque backend handles.

    Unavailable metrics are not zero usage or a hard isolation guarantee.
    Docker cgroup enforcement remains in the existing optional transport.
    """
    def __init__(self, memory_mb):
        self.limit = memory_mb * 1024 * 1024
        self.active = {}

    def register(self, backend, handle):
        self.active[handle.id] = (backend, handle)

    def unregister(self, handle):
        self.active.pop(handle.id, None)

    async def exceeded(self):
        metrics = await asyncio.gather(*(backend.collect_metrics(handle)
            for backend, handle in tuple(self.active.values())))
        return sum(item.rss_bytes or 0 for item in metrics) > self.limit


async def execute(adapter, task, workspace: Path, execution_id: str, deadline: float,
                  cancellation: Cancellation, memory: MemoryMonitor,
                  external_cancel: Cancellation | None = None, backend=None) -> ExecutionAttempt:
    backend = backend or LocalExecutionBackend()
    workspace = workspace.resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    attempt_id = workspace.name
    profile = adapter.profile()
    start_time, start = utc_now(), time.monotonic()
    stdout_path, stderr_path = workspace / 'stdout.txt', workspace / 'stderr.txt'
    argv, handle, waiting, outcome = (), None, None, None
    verdict, status = Verdict.UNKNOWN, ExecutionStatus.START_FAILED
    reason, message, diagnostic_code = TerminationReason.START_FAILURE, '', ''
    env = adapter.attempt_environment(workspace)
    try:
        argv = adapter.build_command(task, workspace)
        # Preserve M9's reservation race fix, including delayed daemon creates.
        preparation = asyncio.create_task(asyncio.to_thread(adapter.prepare, task, workspace))
        try:
            await asyncio.shield(preparation)
        except asyncio.CancelledError:
            await preparation
            raise
        (workspace / 'command.json').write_text(json.dumps({'argv': argv, 'version': profile.version,
            'config_id': profile.config_id, 'semantic_key': task.semantic_key,
            'environment': {k: env.get(k) for k in ('LC_ALL', 'DYLD_LIBRARY_PATH', 'PATH', 'JAVA',
                'HOME', 'TMPDIR', 'XDG_CACHE_HOME', 'XDG_CONFIG_HOME')}}, indent=2))
        cancel_reason = cancellation.reason or (external_cancel.reason if external_cancel else None)
        if cancel_reason:
            status, reason = ExecutionStatus.CANCELLED, cancel_reason
        elif time.monotonic() >= deadline:
            status, reason = ExecutionStatus.TIMEOUT, TerminationReason.WALL_BUDGET
        else:
            spec = ExecutionSpec(tuple(argv), str(workspace), env, deadline - time.monotonic(),
                cpu_request=1.0, memory_request_mb=max(16, task.budget.memory_mb // task.budget.max_parallel),
                metadata={'execution_id': execution_id, 'attempt_id': attempt_id, 'tool': profile.name,
                          'semantic_key': task.semantic_key, 'goal_memory_budget_mb': task.budget.memory_mb,
                          'tool_version': profile.version, 'tool_config_id': profile.config_id,
                          'tool_path': profile.path},
                stdout_path=str(stdout_path), stderr_path=str(stderr_path))
            (workspace / 'execution-spec.json').write_text(json.dumps(spec.record(), indent=2))
            starting = asyncio.create_task(backend.start(spec))
            try:
                handle = await asyncio.shield(starting)
            except asyncio.CancelledError:
                # Obtain ownership before cleanup if startup finishes after cancel.
                handle = await starting
                raise
            memory.register(backend, handle)
            waiting = asyncio.create_task(backend.wait(handle))
            while not waiting.done():
                cancel_reason = cancellation.reason or (external_cancel.reason if external_cancel else None)
                if cancel_reason:
                    await backend.cancel(handle, cancel_reason)
                    break
                if time.monotonic() >= deadline:
                    await backend.cancel(handle, TerminationReason.WALL_BUDGET)
                    break
                if await memory.exceeded():
                    cancellation.cancel(TerminationReason.MEMORY_BUDGET)
                    await backend.cancel(handle, TerminationReason.MEMORY_BUDGET)
                    break
                await asyncio.sleep(min(0.02, max(0, deadline - time.monotonic())))
            outcome = await waiting
            status, reason = outcome.status, outcome.termination_reason
    except asyncio.CancelledError:
        status, reason = ExecutionStatus.CANCELLED, TerminationReason.USER_CANCEL
        if handle is not None:
            await backend.cancel(handle, reason)
    except Exception as exc:
        message = str(exc)
        if handle is not None:
            status, reason = ExecutionStatus.ERROR, TerminationReason.PROCESS_ERROR
    finally:
        if handle is not None:
            try:
                await backend.cleanup(handle)
                if waiting is not None:
                    outcome = await waiting
                else:
                    outcome = await backend.wait(handle)
            except Exception as exc:
                status, reason, message = ExecutionStatus.ERROR, TerminationReason.PROCESS_ERROR, str(exc)
                diagnostic_code = 'backend_cleanup_failed'
            finally:
                memory.unregister(handle)
        try:
            await asyncio.to_thread(adapter.cleanup, workspace)
        except Exception as exc:
            status, reason, message = ExecutionStatus.ERROR, TerminationReason.PROCESS_ERROR, str(exc)
            diagnostic_code = 'transport_cleanup_failed'
        for path, content in ((stdout_path, outcome.stdout if outcome else ''),
                              (stderr_path, outcome.stderr if outcome else '')):
            if not path.exists():
                path.write_text(content)
    if outcome is None:
        outcome = ExecutionOutcome(handle.id if handle else attempt_id, backend.name, None,
            stdout_path.read_text(errors='replace'), stderr_path.read_text(errors='replace'),
            start_time, utc_now(), time.monotonic() - start, status, reason,
            diagnostics={'error': message, 'execution_started': handle is not None})
    (workspace / 'backend-outcome.json').write_text(json.dumps(to_data(outcome), indent=2))
    if status == ExecutionStatus.COMPLETED:
        try:
            parsed = adapter.parse_attempt_result(outcome.stdout, outcome.stderr, outcome.exit_code, workspace)
            verdict, status, message, diagnostic_code = parsed.verdict, parsed.status, parsed.message, parsed.diagnostic_code
            if status != ExecutionStatus.COMPLETED:
                verdict = Verdict.UNKNOWN
                reason = (TerminationReason.MEMORY_BUDGET if status == ExecutionStatus.OOM
                          else TerminationReason.PROCESS_ERROR)
        except Exception as exc:
            status, reason, message = ExecutionStatus.ERROR, TerminationReason.PROCESS_ERROR, f'Adapter parse failed: {exc}'
    if not diagnostic_code and verdict == Verdict.UNKNOWN:
        diagnostic_code = {ExecutionStatus.TIMEOUT: 'timeout', ExecutionStatus.OOM: 'out_of_memory',
                           ExecutionStatus.START_FAILED: 'start_failed', ExecutionStatus.ERROR: 'verifier_error',
                           ExecutionStatus.CANCELLED: 'cancellation'}.get(status, 'inconclusive_verifier')
    attempt = ExecutionAttempt(attempt_id, execution_id, task.semantic_key, profile.name, profile.family,
        profile.version, profile.config_id, tuple(argv), start_time, utc_now(), time.monotonic() - start,
        verdict, status, reason, outcome.exit_code, str(workspace), str(stdout_path), str(stderr_path), message,
        diagnostic_code=diagnostic_code, execution_backend=backend.name, backend_metrics=to_data(outcome.metrics))
    (workspace / 'attempt.json').write_text(json.dumps(to_data(attempt), indent=2))
    return attempt
