import asyncio
from dataclasses import replace
from pathlib import Path
import json

import pytest

from veriruntime.dsl import load_task, load_workflow
from veriruntime.model import Requirements, Verdict
from veriruntime.service import VerificationService
from veriruntime.tools import default_registry

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.integration
@pytest.mark.parametrize('case,expected', [('safe',Verdict.SAFE),('unsafe',Verdict.UNSAFE)])
def test_real_end_to_end_miss_hit_crosscheck(tmp_path, case, expected):
    reg = default_registry(ROOT)
    task = load_task(ROOT / f'examples/tasks/{case}_assert_crosscheck.json')
    if len(reg.compatible_tools(task)) < 2:
        pytest.skip('Need two real verifiers for confirmation requirement')
    service = VerificationService(tmp_path, registry=reg)
    first = asyncio.run(service.verify_goal(task))
    second = asyncio.run(service.verify_goal(task))
    assert first.report.result.verdict == second.report.result.verdict == expected
    assert first.report.result.confirmations >= 2
    assert not first.report.result.cache_hit and len(first.report.attempts) >= 2
    assert second.report.result.cache_hit and second.report.attempts == ()
    assert len(service.store.history()) == 2


@pytest.mark.integration
def test_real_workflow_dependency(tmp_path):
    workflow = load_workflow(ROOT / 'examples/tasks/assertion_workflow.json')
    reg = default_registry(ROOT)
    if not reg.compatible_tools(workflow.goals[0]):
        pytest.skip('No real verifier installed')
    report = asyncio.run(VerificationService(tmp_path, registry=reg).verify(workflow))
    assert [g.report.result.verdict for g in report.goals] == [Verdict.SAFE, Verdict.UNSAFE]
    assert all(g.report.result.requirement_satisfied for g in report.goals)


@pytest.mark.integration
@pytest.mark.parametrize('tool', ['cbmc','esbmc','cpachecker'])
def test_real_multi_source_snapshot(tool, tmp_path):
    import subprocess
    (tmp_path / 'helper.h').write_text('int helper(void);\n')
    (tmp_path / 'helper.c').write_text('#include "helper.h"\nint helper(void){return 7;}\n')
    (tmp_path / 'main.c').write_text('#include <assert.h>\n#include "helper.h"\nint main(void){assert(helper()==7);return 0;}\n')
    doc = json.loads((ROOT / 'examples/tasks/safe_assert.json').read_text())
    doc['task']['sources'] = ['main.c','helper.c']
    (tmp_path / 'task.json').write_text(json.dumps(doc))
    task = load_task(tmp_path / 'task.json')
    adapter = default_registry(ROOT).get(tool)
    if not adapter.profile().available:
        pytest.skip(f'{tool} not installed')
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    command = adapter.build_command(task, workspace)
    result = subprocess.run(command,env=adapter.environment(),capture_output=True,text=True,timeout=30)
    assert adapter.parse_result(result.stdout,result.stderr,result.returncode).verdict == Verdict.SAFE


@pytest.mark.integration
@pytest.mark.parametrize('tool', ['cbmc','esbmc'])
def test_incomplete_unwinding_is_unknown(tool, tmp_path):
    import subprocess
    source = tmp_path / 'long.c'
    source.write_text('#include <assert.h>\nint main(void){int x=0;for(int i=0;i<100;i++)x++;assert(x==100);return 0;}\n')
    doc=json.loads((ROOT/'examples/tasks/safe_assert.json').read_text())
    doc['task']['sources']=['long.c']
    (tmp_path/'task.json').write_text(json.dumps(doc))
    task=load_task(tmp_path/'task.json')
    adapter=default_registry(ROOT).get(tool)
    if not adapter.profile().available:
        pytest.skip(f'{tool} not installed')
    workspace=tmp_path/'workspace';workspace.mkdir()
    result=subprocess.run(adapter.build_command(task,workspace),env=adapter.environment(),capture_output=True,text=True,timeout=30)
    parsed=adapter.parse_result(result.stdout,result.stderr,result.returncode)
    assert parsed.verdict == Verdict.UNKNOWN
    assert parsed.diagnostic_code == 'insufficient_unwinding'
