"""Portable POSIX subprocess lifecycle with process-group cleanup."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import time

import psutil

from veriruntime.model import ExecutionAttempt, ExecutionStatus, TerminationReason, Verdict, to_data


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Cancellation:
    def __init__(self):
        self.reason: TerminationReason | None = None

    def cancel(self, reason=TerminationReason.USER_CANCEL):
        if self.reason is None:
            self.reason = reason


class MemoryMonitor:
    """Aggregate sampled RSS for all active verifier process trees (not isolation)."""
    def __init__(self, memory_mb):
        self.limit = memory_mb * 1024 * 1024
        self.pids: set[int] = set()

    def exceeded(self):
        total = 0
        seen = set()
        for pid in tuple(self.pids):
            try:
                parent = psutil.Process(pid)
                processes = [parent, *parent.children(recursive=True)]
            except psutil.Error:
                continue
            for process in processes:
                try:
                    if process.pid not in seen:
                        total += process.memory_info().rss
                        seen.add(process.pid)
                except psutil.Error:
                    pass
        return total > self.limit


async def cleanup_group(process):
    def send(sig):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            pass
    send(signal.SIGTERM)
    try:
        await asyncio.wait_for(process.wait(), 0.2)
    except asyncio.TimeoutError:
        pass
    # Even when the parent exited, kill any descendants retaining the group.
    send(signal.SIGKILL)
    await process.wait()


async def execute(adapter, task, workspace: Path, execution_id: str, deadline: float,
                  cancellation: Cancellation, memory: MemoryMonitor,
                  external_cancel: Cancellation | None = None) -> ExecutionAttempt:
    workspace = workspace.resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    attempt_id = workspace.name
    profile = adapter.profile()
    start_time, start = utc_now(), time.monotonic()
    stdout_path, stderr_path = workspace / "stdout.txt", workspace / "stderr.txt"
    argv = ()
    process = None
    verdict = Verdict.UNKNOWN
    status = ExecutionStatus.START_FAILED
    reason = TerminationReason.START_FAILURE
    message = ""
    diagnostic_code = ""
    env = adapter.environment()
    try:
        argv = adapter.build_command(task, workspace)
        (workspace / "command.json").write_text(json.dumps({"argv": argv, "version": profile.version,
            "config_id": profile.config_id, "semantic_key": task.semantic_key,
            "environment": {k: env.get(k) for k in ("LC_ALL", "DYLD_LIBRARY_PATH", "PATH", "JAVA")}}, indent=2))
        with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
            cancel_reason = cancellation.reason or (external_cancel.reason if external_cancel else None)
            if cancel_reason:
                status, reason = ExecutionStatus.CANCELLED, cancel_reason
            elif time.monotonic() >= deadline:
                status, reason = ExecutionStatus.TIMEOUT, TerminationReason.WALL_BUDGET
            else:
                process = await asyncio.create_subprocess_exec(*argv, cwd=workspace, env=env,
                    stdin=asyncio.subprocess.DEVNULL, stdout=stdout, stderr=stderr, start_new_session=True)
                memory.pids.add(process.pid)
                while process.returncode is None:
                    cancel_reason = cancellation.reason or (external_cancel.reason if external_cancel else None)
                    if cancel_reason:
                        status = (ExecutionStatus.OOM if cancel_reason == TerminationReason.MEMORY_BUDGET
                                  else ExecutionStatus.CANCELLED)
                        reason = cancel_reason
                        break
                    if time.monotonic() >= deadline:
                        status, reason = ExecutionStatus.TIMEOUT, TerminationReason.WALL_BUDGET
                        break
                    if memory.exceeded():
                        cancellation.cancel(TerminationReason.MEMORY_BUDGET)
                        status, reason = ExecutionStatus.OOM, TerminationReason.MEMORY_BUDGET
                        break
                    await asyncio.sleep(min(0.02, max(0, deadline - time.monotonic())))
                else:
                    status, reason = ExecutionStatus.COMPLETED, TerminationReason.NORMAL
    except asyncio.CancelledError:
        status, reason = ExecutionStatus.CANCELLED, TerminationReason.USER_CANCEL
    except Exception as exc:
        message = str(exc)
    finally:
        if process is not None:
            await cleanup_group(process)
            memory.pids.discard(process.pid)
        stdout_path.touch(exist_ok=True)
        stderr_path.touch(exist_ok=True)
    if status == ExecutionStatus.COMPLETED:
        try:
            parsed = adapter.parse_result(stdout_path.read_text(errors="replace"),
                                          stderr_path.read_text(errors="replace"), process.returncode)
            verdict, status, message = parsed.verdict, parsed.status, parsed.message
            diagnostic_code = parsed.diagnostic_code
            if status != ExecutionStatus.COMPLETED:
                verdict, reason = Verdict.UNKNOWN, TerminationReason.PROCESS_ERROR
        except Exception as exc:
            status, reason, message = ExecutionStatus.ERROR, TerminationReason.PROCESS_ERROR, f"Adapter parse failed: {exc}"
    attempt = ExecutionAttempt(attempt_id, execution_id, task.semantic_key, profile.name, profile.family,
        profile.version, profile.config_id, tuple(argv), start_time, utc_now(), time.monotonic() - start,
        verdict, status, reason, process.returncode if process else None, str(workspace),
        str(stdout_path), str(stderr_path), message)
    if not diagnostic_code and verdict == Verdict.UNKNOWN:
        diagnostic_code = {ExecutionStatus.TIMEOUT: "timeout", ExecutionStatus.OOM: "out_of_memory",
                           ExecutionStatus.START_FAILED: "start_failed", ExecutionStatus.ERROR: "verifier_error",
                           ExecutionStatus.CANCELLED: "cancellation"}.get(status, "inconclusive_verifier")
    from dataclasses import replace
    attempt = replace(attempt, diagnostic_code=diagnostic_code)
    (workspace / "attempt.json").write_text(json.dumps(to_data(attempt), indent=2))
    return attempt
