import asyncio
from dataclasses import replace
import json
from pathlib import Path

import pytest

from conftest import ProcessFixtureAdapter as Fixture
from veriruntime.model import Requirements, Verdict
from veriruntime.plan import CacheLookupPlan
from veriruntime.service import VerificationService


def test_miss_hit_zero_starts_and_provenance(logical, fixture_registry, tmp_path):
    adapter = Fixture('a', "print('SAFE')")
    service = VerificationService(tmp_path, registry=fixture_registry(adapter))
    first = asyncio.run(service.verify_goal(logical.task))
    assert not first.report.result.cache_hit
    adapter.build_command = lambda *args: pytest.fail("Cache hit launched a verifier")
    second = asyncio.run(service.verify_goal(logical.task))
    assert isinstance(second.optimization.physical_plan, CacheLookupPlan)
    assert second.report.result.cache_hit
    assert second.report.attempts == ()
    assert second.report.result.verdict == Verdict.SAFE
    source = service.store.show(first.report.result.execution_id)
    assert source['attempts'][0]['version'] == 'fixture-v1'
    assert source['artifact_records']
    assert all(service.artifacts.valid(a) for a in source['artifact_records'])
    assert second.report.result.source_execution_id == first.report.result.execution_id


@pytest.mark.parametrize('verdict', ['UNKNOWN', 'CONFLICT'])
def test_indefinite_results_not_reusable(logical, fixture_registry, tmp_path, verdict):
    service = VerificationService(tmp_path, registry=fixture_registry(Fixture('a', f"print('{verdict}')")))
    result = asyncio.run(service.verify_goal(logical.task))
    assert result.report.result.verdict == Verdict.UNKNOWN
    assert service.cache.lookup(logical.task) is None


def test_conflicting_tools_never_cached(logical, fixture_registry, tmp_path):
    task = replace(logical.task, requirements=Requirements(2))
    service = VerificationService(tmp_path, registry=fixture_registry(
        Fixture('a', "print('SAFE')"), Fixture('b', "print('UNSAFE')")))
    result = asyncio.run(service.verify_goal(task))
    assert result.report.result.verdict == Verdict.CONFLICT
    assert service.cache.lookup(task) is None


def test_requirement_mismatch_and_corrupt_evidence(logical, fixture_registry, tmp_path):
    service = VerificationService(tmp_path, registry=fixture_registry(Fixture('a', "print('SAFE')")))
    first = asyncio.run(service.verify_goal(logical.task))
    assert service.cache.lookup(replace(logical.task, requirements=Requirements(2))) is None
    source = service.store.show(first.report.result.execution_id)
    artifact = source['artifact_records'][0]
    Path(artifact['path']).chmod(0o644)
    Path(artifact['path']).write_bytes(b'corrupt')
    assert service.cache.lookup(logical.task) is None


def test_cache_disabled_and_clear_is_scoped(logical, fixture_registry, tmp_path):
    service = VerificationService(tmp_path, registry=fixture_registry(Fixture('a', "print('SAFE')")))
    asyncio.run(service.verify_goal(logical.task))
    disabled = VerificationService(tmp_path, registry=service.registry, cache_enabled=False)
    report = asyncio.run(disabled.verify_goal(logical.task))
    assert not report.report.result.cache_hit
    assert len(report.report.attempts) == 1
    assert service.cache.clear([logical.task.semantic_key]) == 1
    assert len(service.store.history()) == 2
    assert service.cache.lookup(logical.task) is None


def test_observed_cross_run_conflict_evicts_cache(logical, fixture_registry, tmp_path):
    first = VerificationService(tmp_path, registry=fixture_registry(Fixture('a', "print('SAFE')")))
    asyncio.run(first.verify_goal(logical.task))
    second = VerificationService(tmp_path, registry=fixture_registry(Fixture('b', "print('UNSAFE')")), cache_enabled=False)
    report = asyncio.run(second.verify_goal(logical.task))
    assert report.report.result.verdict == Verdict.CONFLICT
    assert first.cache.lookup(logical.task) is None
    assert 'conflicting_verdict' in report.report.result.failure_reasons


def test_modified_provenance_metadata_invalidates_hit(logical, fixture_registry, tmp_path):
    service = VerificationService(tmp_path, registry=fixture_registry(Fixture('a', "print('SAFE')")))
    report = asyncio.run(service.verify_goal(logical.task))
    attempt = report.report.attempts[0]
    with service.store.connection() as db:
        payload=json.loads(db.execute('SELECT payload FROM attempts WHERE id=?',(attempt.id,)).fetchone()[0])
        payload['version']='altered'
        db.execute('UPDATE attempts SET payload=? WHERE id=?',(json.dumps(payload),attempt.id))
    assert service.cache.lookup(logical.task) is None


def test_timeout_not_cached(logical, fixture_registry, tmp_path):
    from veriruntime.model import Budget, ExecutionStatus
    task=replace(logical.task,budget=Budget(0.1,512,1))
    service=VerificationService(tmp_path,registry=fixture_registry(Fixture('slow','import time;time.sleep(5)')))
    report=asyncio.run(service.verify_goal(task))
    assert report.report.attempts[0].status == ExecutionStatus.TIMEOUT
    assert service.cache.lookup(task) is None
