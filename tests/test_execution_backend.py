"""Lifecycle and substitution tests for the infrastructure execution boundary."""
import ast
import asyncio
import json
import os
from pathlib import Path
import sys
import time

import psutil
import pytest

from conftest import ProcessFixtureAdapter as Fixture
from veriruntime.execution import (ExecutionBackend, ExecutionHandle, ExecutionMetrics,
                                  ExecutionOutcome, ExecutionSpec, LocalExecutionBackend)
from veriruntime.model import ExecutionStatus, TerminationReason, Verdict
from veriruntime.runtime.process import Cancellation, MemoryMonitor, execute
from veriruntime.service import VerificationService


class TokenBackend(ExecutionBackend):
    """Test-only transport with opaque IDs and no process/PID implementation."""
    name = 'test-token'
    def __init__(self, status=ExecutionStatus.COMPLETED, cleanup_error=False):
        self.specs, self.cleaned = [], []
        self.status, self.cleanup_error = status, cleanup_error

    async def start(self, spec):
        self.specs.append(spec)
        return ExecutionHandle(str(len(self.specs) - 1), self.name)

    async def wait(self, handle):
        spec = self.specs[int(handle.id)]
        Path(spec.stdout_path).write_text('SAFE\n')
        Path(spec.stderr_path).write_text('')
        return ExecutionOutcome(handle.id, self.name, 0, 'SAFE\n', '', 'start', 'end', 0.01,
            self.status, TerminationReason.NORMAL if self.status == ExecutionStatus.COMPLETED
            else TerminationReason.WALL_BUDGET, diagnostics={'transport': 'test-only'})

    async def cancel(self, handle, reason=TerminationReason.USER_CANCEL):
        pass

    async def collect_metrics(self, handle):
        return ExecutionMetrics()

    async def cleanup(self, handle):
        self.cleaned.append(handle.id)
        if self.cleanup_error:
            raise OSError('test infrastructure cleanup failed')


@pytest.mark.parametrize('status,expected', [(ExecutionStatus.COMPLETED, Verdict.SAFE),
                                            (ExecutionStatus.TIMEOUT, Verdict.UNKNOWN)])
def test_service_uses_opaque_backend_and_preserves_fixed_goal(logical, fixture_registry, tmp_path, status, expected):
    backend = TokenBackend(status)
    adapter = Fixture('one', '')
    adapter.build_command = lambda *args: ('/not-a-local-process', 'literal;$()')
    service = VerificationService(tmp_path, registry=fixture_registry(adapter), execution_backend=backend)
    result = asyncio.run(service.verify_goal(logical.task))
    spec = backend.specs[0]
    assert spec.argv == ('/not-a-local-process', 'literal;$()')
    assert spec.cpu_request == 1 and spec.memory_request_mb == logical.task.budget.memory_mb // 2
    assert spec.metadata['semantic_key'] == logical.task.semantic_key
    assert result.report.result.verdict == expected and result.report.attempts[0].execution_backend == backend.name
    assert backend.cleaned == ['0']
    attempt = result.report.attempts[0]
    receipt = json.loads((Path(attempt.workspace) / 'backend-outcome.json').read_text())
    assert receipt['backend'] == backend.name and receipt['status'] == status.value
    assert any(service.store.artifact(ref)['kind'] == 'EXECUTION_SPEC' for ref in attempt.artifact_ids)
    if expected == Verdict.UNKNOWN:
        assert service.cache.lookup(logical.task) is None


def test_cleanup_failure_cannot_supply_definitive_evidence(logical, fixture_registry, tmp_path):
    backend = TokenBackend(cleanup_error=True)
    result = asyncio.run(VerificationService(tmp_path, registry=fixture_registry(Fixture('one', '')),
        execution_backend=backend).verify_goal(logical.task))
    attempt = result.report.attempts[0]
    assert attempt.status == ExecutionStatus.ERROR and attempt.verdict == Verdict.UNKNOWN
    assert attempt.diagnostic_code == 'backend_cleanup_failed'


def test_local_outcome_wait_is_repeatable_and_cleanup_idempotent(tmp_path):
    async def scenario():
        backend = LocalExecutionBackend()
        spec = ExecutionSpec((sys.executable, '-c',
            "import os,sys,time; print(os.environ['VR_TEST_VALUE']); print(sys.argv[1]); "
            "print('stderr',file=sys.stderr); time.sleep(.06); sys.exit(7)", 'literal;$()'),
            str(tmp_path), {**os.environ, 'VR_TEST_VALUE': 'isolated', 'HOST_TOKEN': 'not-in-evidence'},
            wall_time_limit=2, cpu_request=2, memory_request_mb=512)
        handle = await backend.start(spec)
        await asyncio.sleep(.03)
        assert (await backend.collect_metrics(handle)).rss_bytes > 0
        first = await backend.wait(handle)
        assert await backend.wait(handle) == first
        await backend.cleanup(handle)
        await backend.cleanup(handle)
        assert first.status == ExecutionStatus.COMPLETED and first.exit_code == 7
        assert first.stdout == 'isolated\nliteral;$()\n' and first.stderr == 'stderr\n'
        assert first.metrics.peak_sampled_rss_bytes > 0
        assert first.diagnostics['resource_guarantees']['cpu_request'] == 'metadata only'
        assert 'HOST_TOKEN' not in spec.record()['environment']
        assert spec.record()['cpu_request'] == 2
    asyncio.run(scenario())


def test_sync_probe_timeout_cleans_descendant_after_parent_exit(tmp_path):
    code = ("import subprocess,sys; p=subprocess.Popen([sys.executable,'-c',"
            "'import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(20)']); "
            "print(p.pid,flush=True)")
    outcome = LocalExecutionBackend().run_sync(ExecutionSpec((sys.executable, '-c', code),
        str(tmp_path), dict(os.environ), wall_time_limit=.15))
    assert outcome.status == ExecutionStatus.TIMEOUT
    pid = int(outcome.stdout.strip())
    for _ in range(30):
        if not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE:
            break
        time.sleep(.01)
    else:
        pytest.fail('Probe descendant survived backend timeout')


def test_local_timeout_retains_captured_output(tmp_path):
    async def scenario():
        backend = LocalExecutionBackend()
        handle = await backend.start(ExecutionSpec((sys.executable, '-c',
            "import sys,time; print('before',flush=True); print('diagnostic',file=sys.stderr,flush=True); time.sleep(20)"),
            str(tmp_path), dict(os.environ), wall_time_limit=.15))
        await asyncio.sleep(.4)  # Deadline ownership must not depend on invoking wait.
        result = await backend.wait(handle)
        await backend.cleanup(handle)
        assert result.status == ExecutionStatus.TIMEOUT
        assert result.wall_time_sec < .4
        assert result.stdout == 'before\n' and result.stderr == 'diagnostic\n'
    asyncio.run(scenario())


def test_direct_backend_start_cancellation_does_not_orphan_process(tmp_path, monkeypatch):
    original = asyncio.create_subprocess_exec
    created = []
    async def delayed_spawn(*args, **kwargs):
        await asyncio.sleep(.1)
        process = await original(*args, **kwargs)
        created.append(process)
        return process
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', delayed_spawn)
    async def scenario():
        backend = LocalExecutionBackend()
        future = asyncio.create_task(backend.start(ExecutionSpec((sys.executable, '-c',
            'import time;time.sleep(20)'), str(tmp_path), dict(os.environ))))
        await asyncio.sleep(.03)
        future.cancel()
        with pytest.raises(asyncio.CancelledError):
            await future
        assert created and created[0].returncode is not None
    asyncio.run(scenario())


def test_cancel_during_backend_start_waits_for_owned_handle(logical, tmp_path):
    class DelayedLocal(LocalExecutionBackend):
        def __init__(self):
            self.started = self.cleaned = False
        async def start(self, spec):
            await asyncio.sleep(.12)
            handle = await super().start(spec)
            self.started = True
            return handle
        async def cleanup(self, handle):
            assert self.started
            await super().cleanup(handle)
            self.cleaned = True
    backend = DelayedLocal()
    async def scenario():
        future = asyncio.create_task(execute(Fixture('slow', 'import time;time.sleep(20)'), logical.task,
            tmp_path / 'attempt', 'execution', time.monotonic() + 5, Cancellation(), MemoryMonitor(512),
            backend=backend))
        await asyncio.sleep(.04)
        future.cancel()
        return await future
    attempt = asyncio.run(scenario())
    assert backend.cleaned and attempt.status == ExecutionStatus.CANCELLED


def test_raw_process_apis_are_confined_to_backend_implementation():
    root = Path(__file__).resolve().parents[1] / 'veriruntime'
    for source in root.rglob('*.py'):
        if source.is_relative_to(root / 'execution'):
            continue
        for node in ast.walk(ast.parse(source.read_text())):
            if isinstance(node, ast.Import):
                assert not any(alias.name == 'subprocess' for alias in node.names), source
            if isinstance(node, ast.ImportFrom):
                assert node.module != 'subprocess', source
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in ('create_subprocess_exec', 'create_subprocess_shell', 'killpg'), source
