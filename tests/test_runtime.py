import asyncio
from dataclasses import replace
from pathlib import Path
import sys
import time

import psutil
import pytest

from conftest import ProcessFixtureAdapter as Fixture
from veriruntime.model import Budget, ExecutionStatus, Requirements, TerminationReason, Verdict
from veriruntime.plan import ParallelPlan, RunPlan, SequencePlan
from veriruntime.runtime import Cancellation, Runtime
from veriruntime.tools import default_registry


def test_timeout_and_start_failure(logical, fixture_registry, tmp_path):
    logical = replace(logical, task=replace(logical.task, budget=Budget(0.1, 512, 2)))
    fixture = Fixture("slow", "import time;time.sleep(10)")
    report = asyncio.run(Runtime(fixture_registry(fixture), tmp_path).run(logical, RunPlan("slow")))
    assert report.result.verdict == Verdict.UNKNOWN
    assert report.attempts[0].status == ExecutionStatus.TIMEOUT
    fixture.build_command = lambda *args: ("/nonexistent/verifier",)
    report = asyncio.run(Runtime(fixture_registry(fixture), tmp_path).run(logical, RunPlan("slow")))
    assert report.attempts[0].status == ExecutionStatus.START_FAILED


def test_unknown_sequence_fallback(logical, fixture_registry, tmp_path):
    reg = fixture_registry(Fixture("first", "print('UNKNOWN')"), Fixture("next", "print('SAFE')"))
    plan = SequencePlan((RunPlan("first"), RunPlan("next")))
    report = asyncio.run(Runtime(reg, tmp_path).run(logical, plan))
    assert report.result.verdict == Verdict.SAFE
    assert len(report.attempts) == 2


def test_real_parallel_and_confirmation_requirement(logical, fixture_registry, tmp_path):
    logical = replace(logical, task=replace(logical.task, requirements=Requirements(2)))
    reg = fixture_registry(*(Fixture(name, "import time;time.sleep(0.2);print('SAFE')") for name in ('a', 'b')))
    plan = ParallelPlan((RunPlan("a"), RunPlan("b")), 2)
    report = asyncio.run(Runtime(reg, tmp_path).run(logical, plan))
    assert report.result.verdict == Verdict.SAFE
    assert report.result.confirmations == 2
    assert report.result.wall_time_sec < 0.38
    assert all(a.status == ExecutionStatus.COMPLETED for a in report.attempts)


def test_early_cancellation(logical, fixture_registry, tmp_path):
    reg = fixture_registry(Fixture("fast", "print('SAFE')"), Fixture("slow", "import time;time.sleep(5);print('SAFE')"))
    report = asyncio.run(Runtime(reg, tmp_path).run(logical, ParallelPlan((RunPlan("fast"), RunPlan("slow")), 2)))
    assert report.result.verdict == Verdict.SAFE
    slow = next(a for a in report.attempts if a.tool == "slow")
    assert slow.status == ExecutionStatus.CANCELLED
    assert slow.termination_reason == TerminationReason.REQUIREMENTS_MET


def test_user_cancel(logical, fixture_registry, tmp_path):
    async def scenario():
        token = Cancellation()
        reg = fixture_registry(Fixture("slow", "import time;time.sleep(5)"))
        future = asyncio.create_task(Runtime(reg, tmp_path).run(logical, RunPlan("slow"), token))
        await asyncio.sleep(0.08)
        token.cancel()
        return await future
    report = asyncio.run(scenario())
    assert report.attempts[0].status == ExecutionStatus.CANCELLED
    assert report.result.termination_reason == TerminationReason.USER_CANCEL


def test_child_process_group_cleanup(logical, fixture_registry, tmp_path):
    logical = replace(logical, task=replace(logical.task, budget=Budget(0.2, 512, 1)))
    code = ("import subprocess,sys,time; "
            "p=subprocess.Popen([sys.executable,'-c','import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(20)']); "
            "print(p.pid,flush=True);time.sleep(20)")
    report = asyncio.run(Runtime(fixture_registry(Fixture("tree", code)), tmp_path).run(logical, RunPlan("tree")))
    pid = int(Path(report.attempts[0].stdout_path).read_text().strip())
    for _ in range(30):
        if not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE:
            break
        time.sleep(0.01)
    else:
        pytest.fail("Verifier child survived process-group cleanup")


def test_conflict_and_duplicate_family(logical, fixture_registry, tmp_path):
    logical = replace(logical, task=replace(logical.task, requirements=Requirements(2)))
    reg = fixture_registry(Fixture("a", "print('SAFE')"), Fixture("b", "print('UNSAFE')"))
    report = asyncio.run(Runtime(reg, tmp_path).run(logical, ParallelPlan((RunPlan("a"), RunPlan("b")), 2)))
    assert report.result.verdict == Verdict.CONFLICT
    reg = fixture_registry(Fixture("a", "print('SAFE')", "same"), Fixture("b", "print('SAFE')", "same"))
    report = asyncio.run(Runtime(reg, tmp_path).run(logical, SequencePlan((RunPlan("a"), RunPlan("b")))))
    assert report.result.verdict == Verdict.UNKNOWN
    assert report.result.confirmations == 1


def test_abnormal_exit_and_memory_budget(logical, fixture_registry, tmp_path):
    reg = fixture_registry(Fixture("bad", "import sys;sys.exit(3)"))
    report = asyncio.run(Runtime(reg, tmp_path).run(logical, RunPlan("bad")))
    assert report.attempts[0].status == ExecutionStatus.ERROR
    logical = replace(logical, task=replace(logical.task, budget=Budget(3, 24, 1)))
    reg = fixture_registry(Fixture("large", "import time;data=bytearray(80*1024*1024);time.sleep(3)"))
    report = asyncio.run(Runtime(reg, tmp_path).run(logical, RunPlan("large")))
    assert report.attempts[0].status == ExecutionStatus.OOM


@pytest.mark.integration
def test_runtime_real_crosscheck(logical, tmp_path):
    reg = default_registry(Path(__file__).resolve().parents[1])
    if len(reg.compatible_tools(logical.task)) < 2:
        pytest.skip("Two real verifiers not installed")
    logical = replace(logical, task=replace(logical.task, requirements=Requirements(2)))
    plan = ParallelPlan(tuple(RunPlan(p.name) for p in reg.compatible_tools(logical.task)), 2)
    report = asyncio.run(Runtime(reg, tmp_path).run(logical, plan))
    assert report.result.verdict == Verdict.SAFE
    assert report.result.confirmations >= 2
