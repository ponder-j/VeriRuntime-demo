import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path

import pytest

from conftest import ProcessFixtureAdapter as Fixture
from veriruntime.service import VerificationService
from veriruntime.tools.cbmc import CBMCAdapter


def test_attempts_do_not_share_home_or_temp(logical, fixture_registry, tmp_path):
    code = """import os
from pathlib import Path
for name in ('HOME', 'TMPDIR', 'XDG_CACHE_HOME', 'XDG_CONFIG_HOME'):
    sentinel = Path(os.environ[name]) / 'previous-result'
    assert not sentinel.exists()
    sentinel.write_text('SAFE')
print('SAFE')
"""
    service = VerificationService(tmp_path, registry=fixture_registry(Fixture('a', code)), cache_enabled=False)
    runs = [asyncio.run(service.verify_goal(logical.task)) for _ in range(2)]
    assert all(r.report.result.requirement_satisfied for r in runs)
    environments = [json.loads((Path(r.report.attempts[0].workspace) / 'command.json').read_text())['environment'] for r in runs]
    assert environments[0]['HOME'] != environments[1]['HOME']


def test_host_injection_variables_removed(monkeypatch):
    keys = ('CPATH', 'C_INCLUDE_PATH', 'CPLUS_INCLUDE_PATH', 'LD_PRELOAD',
            'JAVA_TOOL_OPTIONS', '_JAVA_OPTIONS', 'JDK_JAVA_OPTIONS')
    for key in keys:
        monkeypatch.setenv(key, '/unexpected/host')
    assert not set(keys) & CBMCAdapter().environment().keys()


@pytest.mark.parametrize('field,value', [('version', 'fixture-v2'), ('config_id', 'new-image'),
                                        ('family', 'different-family'), ('available', False)])
def test_cache_rejects_changed_tool_identity(field, value, logical, fixture_registry, tmp_path):
    adapter = Fixture('a', "print('SAFE')")
    service = VerificationService(tmp_path, registry=fixture_registry(adapter))
    asyncio.run(service.verify_goal(logical.task))
    assert service.cache.lookup(logical.task)
    adapter._profile = replace(adapter._profile, **{field: value})
    assert service.cache.lookup(logical.task) is None


def test_no_tools_cannot_replay_other_environment(logical, fixture_registry, tmp_path):
    first = VerificationService(tmp_path, registry=fixture_registry(Fixture('a', "print('SAFE')")))
    asyncio.run(first.verify_goal(logical.task))
    second = VerificationService(tmp_path, registry=fixture_registry())
    assert second.cache.lookup(logical.task) is None


def test_cancel_during_reservation_waits_before_cleanup(logical, tmp_path):
    import time
    from veriruntime.runtime.process import execute, Cancellation, MemoryMonitor

    class ReservingFixture(Fixture):
        created = removed = False
        def prepare(self, task, workspace):
            time.sleep(0.15)
            self.created = True
        def cleanup(self, workspace):
            assert self.created, 'cleanup ran before reservation completed'
            self.removed = True

    adapter = ReservingFixture('a', "print('SAFE')")
    async def scenario():
        future = asyncio.create_task(execute(adapter, logical.task, tmp_path / 'attempt',
            'execution', time.monotonic() + 5, Cancellation(), MemoryMonitor(512)))
        await asyncio.sleep(0.05)
        future.cancel()
        return await future
    attempt = asyncio.run(scenario())
    assert adapter.removed and attempt.status.value == 'CANCELLED'
