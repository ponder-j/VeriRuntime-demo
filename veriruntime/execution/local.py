"""POSIX execution backend. CPU/memory requests are metadata, not isolation."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import os
from pathlib import Path
import signal
import subprocess
import time
import uuid

from veriruntime.model import ExecutionStatus, TerminationReason
from .backend import ExecutionBackend, ExecutionHandle, ExecutionMetrics, ExecutionOutcome, ExecutionSpec


def utc_now():
    return datetime.now(timezone.utc).isoformat()


async def cleanup_group(process):
    """Terminate the session, escalate even after parent exit, and reap parent."""
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
    send(signal.SIGKILL)
    await process.wait()


class _LocalHandle(ExecutionHandle):
    def __init__(self, spec, process, streams, start_time, start):
        super().__init__(uuid.uuid4().hex, 'local-posix')
        # Mutable implementation state stays behind the opaque base contract.
        object.__setattr__(self, '_state', {
            'spec': spec, 'process': process, 'streams': streams, 'start_time': start_time,
            'start': start, 'reason': None, 'completion': None, 'peak': 0, 'metrics': ExecutionMetrics(),
            'cancel_lock': asyncio.Lock(), 'closed': False,
            'communication': asyncio.create_task(process.communicate(spec.stdin_data))})


class LocalExecutionBackend(ExecutionBackend):
    name = 'local-posix'
    resource_guarantees = {
        'wall_timeout': 'enforced', 'cancellation': 'POSIX process group with TERM/KILL escalation',
        'cpu_request': 'metadata only', 'memory_request': 'metadata only',
        'metrics': 'sampled local process tree; not cgroup or remote worker usage',
        'escaped_process_groups': 'outside portable cleanup guarantee'}

    async def start(self, spec):
        if os.name != 'posix':
            raise OSError('LocalExecutionBackend requires POSIX; use the existing Linux runtime on Windows')
        streams = []
        start_time, start = utc_now(), time.monotonic()
        try:
            outputs = []
            for path in (spec.stdout_path, spec.stderr_path):
                if path:
                    stream = Path(path).open('wb')
                    streams.append(stream)
                    outputs.append(stream)
                else:
                    outputs.append(asyncio.subprocess.PIPE)
            spawning = asyncio.create_task(asyncio.create_subprocess_exec(*spec.argv, cwd=spec.cwd,
                env=dict(spec.environment), stdin=(asyncio.subprocess.PIPE if spec.stdin_data is not None
                    else asyncio.subprocess.DEVNULL), stdout=outputs[0], stderr=outputs[1], start_new_session=True))
            try:
                process = await asyncio.shield(spawning)
            except asyncio.CancelledError:
                process = await spawning
                if process.stdin:
                    process.stdin.close()
                await cleanup_group(process)
                raise
            handle = _LocalHandle(spec, process, streams, start_time, start)
            # The backend owns the deadline from startup, even before a caller waits.
            handle._state['completion'] = asyncio.create_task(self._finish(handle))
            return handle
        except BaseException:
            for stream in streams:
                stream.close()
            raise

    async def collect_metrics(self, handle):
        state = handle._state
        # Minimal verifier images need only the stdlib synchronous runner. The
        # control runtime has psutil; metrics remain explicit when unavailable.
        try:
            import psutil
        except ImportError:
            return ExecutionMetrics()
        rss, cpu = 0, 0.0
        try:
            parent = psutil.Process(state['process'].pid)
            processes = [parent, *parent.children(recursive=True)]
        except psutil.Error:
            processes = []
        for process in processes:
            try:
                rss += process.memory_info().rss
                times = process.cpu_times()
                cpu += times.user + times.system
            except psutil.Error:
                pass
        state['peak'] = max(state['peak'], rss)
        previous_cpu = state['metrics'].cpu_seconds or 0.0
        state['metrics'] = ExecutionMetrics(rss, state['peak'], max(previous_cpu, cpu), 'local_process_tree')
        return state['metrics']

    async def cancel(self, handle, reason=TerminationReason.USER_CANCEL):
        state = handle._state
        async with state['cancel_lock']:
            if state['communication'].done() or state['closed']:
                return
            if state['reason'] is None:
                state['reason'] = reason
            await cleanup_group(state['process'])

    async def wait(self, handle):
        state = handle._state
        if state['completion'] is None:
            state['completion'] = asyncio.create_task(self._finish(handle))
        return await asyncio.shield(state['completion'])

    async def _finish(self, handle):
        state, error = handle._state, ''
        spec, process = state['spec'], state['process']
        captured = (None, None)
        try:
            remaining = None if spec.wall_time_limit is None else max(
                0, spec.wall_time_limit - (time.monotonic() - state['start']))
            captured = await asyncio.wait_for(asyncio.shield(state['communication']), remaining)
        except asyncio.TimeoutError:
            await self.cancel(handle, TerminationReason.WALL_BUDGET)
        except (OSError, BrokenPipeError) as exc:
            error = str(exc)
        finally:
            await self.collect_metrics(handle)
            await cleanup_group(process)
            if not state['communication'].done():
                try:
                    await asyncio.wait_for(asyncio.shield(state['communication']), .2)
                except asyncio.TimeoutError:
                    # A process escaping the owned group may retain a pipe.
                    state['communication'].cancel()
            communications = await asyncio.gather(state['communication'], return_exceptions=True)
            if isinstance(communications[0], tuple):
                captured = communications[0]
            for stream in state['streams']:
                stream.close()
            state['closed'] = True
        reason = state['reason'] or (TerminationReason.PROCESS_ERROR if error else TerminationReason.NORMAL)
        status = (ExecutionStatus.TIMEOUT if reason == TerminationReason.WALL_BUDGET else
                  ExecutionStatus.OOM if reason == TerminationReason.MEMORY_BUDGET else
                  ExecutionStatus.ERROR if error else
                  ExecutionStatus.CANCELLED if state['reason'] else ExecutionStatus.COMPLETED)
        def output(path, raw):
            return Path(path).read_text(errors='replace') if path else (raw or b'').decode(errors='replace')
        return ExecutionOutcome(handle.id, self.name, process.returncode,
            output(spec.stdout_path, captured[0]), output(spec.stderr_path, captured[1]),
            state['start_time'], utc_now(), time.monotonic() - state['start'], status, reason,
            state['metrics'], {'resource_guarantees': self.resource_guarantees, 'error': error})

    async def cleanup(self, handle):
        await self.cancel(handle, TerminationReason.PROCESS_ERROR)
        await self.wait(handle)

    def run_sync(self, spec, *, stdout=None, stderr=None, capture_output=True, inherit_group=False):
        """Probe/compiler helper using the same local implementation boundary.

        Nested drivers inherit the already supervised outer process group. Their
        timeout/cancellation belongs to that outer execution. Independent probes
        own a new group and clean it on normal exit or timeout.
        """
        if os.name != 'posix':
            raise OSError('LocalExecutionBackend requires POSIX')
        if inherit_group and spec.wall_time_limit is not None:
            raise ValueError('Nested driver deadlines belong to the owning execution')
        start_time, start = utc_now(), time.monotonic()
        target_out = subprocess.PIPE if capture_output else stdout
        target_err = subprocess.PIPE if capture_output else stderr
        process = subprocess.Popen(spec.argv, cwd=spec.cwd, env=dict(spec.environment),
            stdin=subprocess.PIPE if spec.stdin_data is not None else subprocess.DEVNULL,
            stdout=target_out, stderr=target_err, start_new_session=not inherit_group)
        status, reason = ExecutionStatus.COMPLETED, TerminationReason.NORMAL
        def signal_group(sig):
            try:
                os.killpg(process.pid, sig)
            except ProcessLookupError:
                pass
        try:
            raw_out, raw_err = process.communicate(spec.stdin_data, timeout=spec.wall_time_limit)
        except subprocess.TimeoutExpired:
            status, reason = ExecutionStatus.TIMEOUT, TerminationReason.WALL_BUDGET
            signal_group(signal.SIGTERM)
            try:
                process.wait(timeout=0.2)
            except subprocess.TimeoutExpired:
                pass
            signal_group(signal.SIGKILL)
            raw_out, raw_err = process.communicate()
        finally:
            if not inherit_group:
                signal_group(signal.SIGKILL)
            process.wait()
        return ExecutionOutcome(uuid.uuid4().hex, self.name, process.returncode,
            (raw_out or b'').decode(errors='replace'), (raw_err or b'').decode(errors='replace'),
            start_time, utc_now(), time.monotonic() - start, status, reason,
            diagnostics={'resource_guarantees': self.resource_guarantees,
                         'process_group': 'inherited' if inherit_group else 'owned', 'metrics': 'not sampled'})
