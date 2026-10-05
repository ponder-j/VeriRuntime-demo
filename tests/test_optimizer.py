import asyncio
from dataclasses import replace

from conftest import ProcessFixtureAdapter as Fixture
from veriruntime.model import Budget, Requirements, VerificationProperty
from veriruntime.optimizer import CostAwareOptimizer, RuntimeContext
from veriruntime.plan import ParallelPlan, RunPlan, SequencePlan
from veriruntime.runtime import Runtime
from veriruntime.store import ExecutionStore


def optimize(logical, registry, history=None):
    return CostAwareOptimizer().optimize(logical, RuntimeContext(registry, history))


def test_one_or_multiple_candidates(logical, fixture_registry):
    a, b = Fixture('a', "print('SAFE')"), Fixture('b', "print('SAFE')")
    assert isinstance(optimize(logical, fixture_registry(a)).physical_plan, RunPlan)
    assert isinstance(optimize(logical, fixture_registry(a, b)).physical_plan, ParallelPlan)
    logical = replace(logical, task=replace(logical.task, budget=Budget(10, 128, 1)))
    assert isinstance(optimize(logical, fixture_registry(a, b)).physical_plan, SequencePlan)


def test_requirements_and_resource_constraints(logical, fixture_registry):
    reg = fixture_registry(Fixture('a', "print('SAFE')"), Fixture('b', "print('SAFE')"))
    two = replace(logical, task=replace(logical.task, requirements=Requirements(2)))
    result = optimize(two, reg)
    assert len(result.explanation['selected_tools']) == 2
    assert result.explanation['trust_feasible']
    small = replace(logical, task=replace(logical.task, budget=Budget(0.001, 128, 2)))
    assert isinstance(optimize(small, reg).physical_plan, RunPlan)
    memory = replace(two, task=replace(two.task, budget=Budget(30, 64, 2)))
    assert isinstance(optimize(memory, reg).physical_plan, SequencePlan)
    unsupported = replace(logical, task=replace(logical.task, property=VerificationProperty('memory_safety')))
    result = optimize(unsupported, reg)
    assert result.physical_plan.children == ()
    assert all('unsupported_property' in c.reasons for c in result.candidate_scores)


def test_history_aware_ordering(logical, fixture_registry, tmp_path):
    a = Fixture('a', "import time;time.sleep(0.12);print('SAFE')")
    b = Fixture('b', "print('SAFE')")
    reg = fixture_registry(a, b)
    history = ExecutionStore(tmp_path)
    runtime = Runtime(reg, tmp_path, on_attempt=history.record_attempt)
    for name in ['a','b']:
        asyncio.run(runtime.run(logical, RunPlan(name)))
    result = optimize(logical, reg, history)
    assert result.explanation['selected_tools'][0] == 'b'
    assert all(c.history.run_count == 1 for c in result.candidate_scores)
    assert all(c.history.safe_count == 1 for c in result.candidate_scores)
