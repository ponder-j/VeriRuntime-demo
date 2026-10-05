import json
from dataclasses import replace
from pathlib import Path

import pytest

from veriruntime.dsl import DSLValidationError, load_task, load_workflow
from veriruntime.model import SemanticHints, VerificationWorkflow

ROOT = Path(__file__).resolve().parents[1]


def workflow_document():
    single = json.loads((ROOT / "examples/tasks/safe_assert.json").read_text())
    single["task"]["sources"] = [str(ROOT / "examples/c/safe_assert.c")]
    single.pop("version")
    single["task"].pop("id")
    return {"version": "0.1", "workflow": {"id": "dag", "goals": [{"id": "G1", **single},
             {"id": "G2", **single}], "dependencies": [{"from": "G1", "to": "G2", "required_verdict": "SAFE"}]}}


def test_single_task_is_one_goal_workflow():
    path = ROOT / "examples/tasks/safe_assert.json"
    workflow = load_workflow(path)
    assert workflow == VerificationWorkflow.single(load_task(path))
    assert workflow.dependencies == ()


def test_hints_do_not_change_goal_identity():
    task = load_task(ROOT / "examples/tasks/safe_assert.json")
    assert task.semantic_key == replace(task, hints=SemanticHints(True, ("shallow_bug",))).semantic_key


def test_workflow_dag_and_reject_cycles(tmp_path):
    path = tmp_path / "workflow.json"
    doc = workflow_document()
    path.write_text(json.dumps(doc))
    workflow = load_workflow(path)
    assert len(workflow.goals) == 2
    assert workflow.dependencies[0].predecessor == "G1"
    doc["workflow"]["dependencies"].append({"from": "G2", "to": "G1"})
    path.write_text(json.dumps(doc))
    with pytest.raises(DSLValidationError, match="DAG"):
        load_workflow(path)


@pytest.mark.parametrize("change", [
    lambda w: w["dependencies"].append({"from": "absent", "to": "G2"}),
    lambda w: w["goals"][1].update(id="G1"),
    lambda w: w["goals"][0].update(tool="cbmc"),
    lambda w: w["goals"][0].update(hints={"tool": "esbmc"}),
])
def test_invalid_workflows(tmp_path, change):
    doc = workflow_document()
    change(doc["workflow"])
    path = tmp_path / "workflow.json"
    path.write_text(json.dumps(doc))
    with pytest.raises(DSLValidationError):
        load_workflow(path)


def test_workflow_control_dependencies(logical, fixture_registry, tmp_path):
    import asyncio
    from conftest import ProcessFixtureAdapter as Fixture
    from veriruntime.model import GoalDependency, Verdict
    from veriruntime.service import VerificationService
    g1 = replace(logical.task, id="G1")
    g2 = replace(logical.task, id="G2")
    workflow = VerificationWorkflow("dag", (g2, g1), (GoalDependency("G1", "G2"),))
    service = VerificationService(tmp_path, registry=fixture_registry(Fixture("a", "print('SAFE')")))
    report = asyncio.run(service.verify(workflow))
    assert [g.report.result.goal_id for g in report.goals] == ["G1", "G2"]
    assert all(g.report.result.verdict == Verdict.SAFE for g in report.goals)
    service = VerificationService(tmp_path, registry=fixture_registry(Fixture("b", "print('UNSAFE')")))
    report = asyncio.run(service.verify(workflow))
    assert report.goals[1].report.result.status == "BLOCKED"
    assert report.goals[1].report.attempts == ()
    assert report.goals[1].report.result.failure_reasons == ("dependency_not_satisfied",)
