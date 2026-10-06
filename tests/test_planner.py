import asyncio
from copy import deepcopy
from dataclasses import replace
import json
import hashlib
from pathlib import Path
import sys

from jsonschema import Draft202012Validator
import pytest

from conftest import ProcessFixtureAdapter
from veriruntime.dsl import load_task, load_workflow
from veriruntime.model import ProgramFile, ProgramSnapshot, Requirements, VerificationWorkflow, to_data
from veriruntime.planner import CodexPlanner, ExperimentRunner, PlannerError
from veriruntime.planner.codex import CodexRun, default_sol_model
from veriruntime.planner.experiment import _response_schema
from veriruntime.runtime import Cancellation
from veriruntime.service import VerificationService
from veriruntime.tools import ToolRegistry

ROOT = Path(__file__).resolve().parents[1]


class FixturePlanner:
    model = 'fixture'

    def __init__(self, edit=None):
        self.calls, self.edit = [], edit

    async def propose(self, prompt, schema, directory, cancellation=None):
        context = json.loads(prompt.splitlines()[-1])
        self.calls.append(context)
        goals = [{'id': f'G{i+1}', **{key: deepcopy(value) for key, value in item.items()
            if key in ('task','property','semantics','requirements','budget')}}
            for i,item in enumerate(context['inputs'])]
        proposal = {'workflow_document': {'version':'0.1', 'workflow':
            {'id':'fixture-workflow','goals':goals,'dependencies':[]}}, 'rationale':'Fixture', 'limitations':[]}
        if self.edit:
            self.edit(proposal, len(self.calls))
        record=CodexRun('COMPLETED',self.model,(),'',0,0,'','','',{})
        return proposal, record


def service(path):
    adapter=ProcessFixtureAdapter('test-family', "print('SAFE')")
    return VerificationService(path, registry=ToolRegistry([adapter]))


def seed():
    return VerificationWorkflow.single(load_task(ROOT/'examples/tasks/safe_assert.json'))


def test_default_model_honors_sol_and_falls_back(tmp_path, monkeypatch):
    monkeypatch.setenv('CODEX_HOME',str(tmp_path))
    (tmp_path/'config.toml').write_text('model = "gpt-6-sol"')
    assert default_sol_model() == 'gpt-6-sol'
    (tmp_path/'config.toml').write_text('model = "another-model"')
    assert default_sol_model() == 'gpt-6.1-sol'


def test_transport_schema_has_strict_typed_objects():
    schema=_response_schema(['../inputs/input-0/main.c'])
    Draft202012Validator.check_schema(schema)
    def check(value):
        if isinstance(value,dict):
            if 'enum' in value:
                assert 'type' in value
            if value.get('type') == 'object':
                assert set(value['required']) == set(value['properties'])
                assert value['additionalProperties'] is False
            for child in value.values(): check(child)
        elif isinstance(value,list):
            for child in value: check(child)
    check(schema)


def test_plan_only_launches_no_verifiers_and_exports_reloadable_dsl(tmp_path):
    planner=FixturePlanner()
    svc=service(tmp_path)
    result=asyncio.run(ExperimentRunner(svc,planner).run(seed(),'Check existing assertions',plan_only=True))
    assert result.status == 'PLANNED' and result.verifier_executions == 0
    assert svc.store.history() == [] and len(planner.calls) == 1
    path=Path(result.directory)/'round-001/workflow.json'
    workflow=load_workflow(path)
    assert to_data(workflow.goals[0].program) == result.rounds[0]['logical_workflow']['goals'][0]['program']
    # The exported DSL and actual runtime use the same captured input identity.
    execution=asyncio.run(svc.verify(workflow))
    assert execution.goals[0].report.result.semantic_key == workflow.goals[0].semantic_key


@pytest.mark.parametrize('change', ['property','semantics','entry','confirmations','budget','source','coverage','cycle'])
def test_rejects_untrusted_or_weakened_proposals_before_execution(change,tmp_path):
    def edit(proposal,number):
        workflow=proposal['workflow_document']['workflow']
        goal=workflow['goals'][0]
        if change == 'property': goal['property']['kind']='memory_safety'
        if change == 'semantics': goal['semantics']['data_model']='ILP32'
        if change == 'entry': goal['task']['entry']='other'
        if change == 'confirmations': goal['requirements']['min_confirmations']=1
        if change == 'budget': goal['budget']['wall_time_sec']=31
        if change == 'source': goal['task']['sources']=['/tmp/unrelated.c']
        if change == 'coverage': workflow['goals']=[]
        if change == 'cycle': workflow['dependencies']=[{'from':'G1','to':'G1','required_verdict':'SAFE'}]
    original=seed()
    original=replace(original,goals=(replace(original.goals[0],requirements=Requirements(2)),))
    svc=service(tmp_path)
    result=asyncio.run(ExperimentRunner(svc,FixturePlanner(edit)).run(original,'Check assertions'))
    assert result.status == 'ERROR' and result.verifier_executions == 0
    assert result.diagnostics[0]['code'] == 'invalid_workflow'
    assert svc.store.history() == []


def test_explicit_replanning_receives_feedback_and_changes_dependency(tmp_path):
    def edit(proposal,number):
        proposal['workflow_document']['workflow']['dependencies']=[
            {'from':'G1','to':'G2','required_verdict':'UNSAFE' if number == 1 else 'SAFE'}]
    planner=FixturePlanner(edit)
    original=load_workflow(ROOT/'examples/tasks/assertion_workflow.json')
    result=asyncio.run(ExperimentRunner(service(tmp_path),planner).run(original,'Check goals in order'))
    assert result.status == 'COMPLETED' and len(result.rounds) == 2
    assert result.rounds[0]['execution']['goals'][1]['report']['result']['status'] == 'BLOCKED'
    feedback=planner.calls[1]['previous_feedback']
    assert feedback['goals'][1]['failure_reasons'] == ['dependency_not_satisfied']
    assert result.rounds[1]['execution']['goals'][0]['report']['result']['cache_hit']
    assert result.verifier_executions == 2
    assert original.dependencies[0].required_verdict.value == 'SAFE'


def test_unchanged_unknown_stops_without_duplicate_execution(tmp_path):
    adapter=ProcessFixtureAdapter('test-family',"print('UNKNOWN')")
    svc=VerificationService(tmp_path,registry=ToolRegistry([adapter]))
    planner=FixturePlanner()
    result=asyncio.run(ExperimentRunner(svc,planner,max_rounds=3).run(seed(),'Check assertions'))
    assert result.status == 'INCOMPLETE' and result.stop_reason == 'unchanged_workflow'
    assert len(planner.calls) == 2 and result.verifier_executions == 1
    assert planner.calls[1]['previous_feedback']['goals'][0]['verdict'] == 'UNKNOWN'


def test_codex_process_protocol_and_usage(tmp_path):
    executable=tmp_path/'codex-fixture'
    executable.write_text(f'#!{sys.executable}\n'+'''import json,sys
from pathlib import Path
prompt=sys.stdin.read()
Path(sys.argv[sys.argv.index('--output-last-message')+1]).write_text(json.dumps({'message':prompt}))
print(json.dumps({'type':'turn.completed','usage':{'input_tokens':12,'output_tokens':3}}))
''')
    executable.chmod(0o755)
    schema={'type':'object','additionalProperties':False,'required':['message'],'properties':{'message':{'type':'string'}}}
    document,record=asyncio.run(CodexPlanner(executable=str(executable)).propose('hello',schema,tmp_path/'round'))
    assert document == {'message':'hello'} and record.usage['input_tokens'] == 12
    assert record.command[record.command.index('--sandbox')+1] == 'read-only'
    assert '--ephemeral' in record.command and record.status == 'COMPLETED'
    assert (tmp_path/'round/codex-run.json').exists()


def test_codex_timeout_including_blocked_stdin_and_cancellation(tmp_path):
    executable=tmp_path/'codex-fixture'
    executable.write_text(f'#!{sys.executable}\nimport time\ntime.sleep(30)\n')
    executable.chmod(0o755)
    planner=CodexPlanner(executable=str(executable),timeout_sec=0.1)
    with pytest.raises(PlannerError) as failure:
        asyncio.run(planner.propose('x'*200000,{'type':'object'},tmp_path/'timeout'))
    assert failure.value.code == 'planner_timeout'
    assert failure.value.record.wall_time_sec < 3
    token=Cancellation(); token.cancel()
    with pytest.raises(PlannerError) as failure:
        asyncio.run(planner.propose('hello',{'type':'object'},tmp_path/'cancelled',token))
    assert failure.value.code == 'planner_cancelled' and failure.value.record.exit_code is None


@pytest.mark.parametrize('answer',['not JSON','{"message":"one","message":"two"}','{}'])
def test_malformed_output_and_missing_executable_are_honest_failures(answer,tmp_path):
    executable=tmp_path/'codex-fixture'
    executable.write_text(f'#!{sys.executable}\n'+'''import sys
from pathlib import Path
sys.stdin.read()
'''+f"Path(sys.argv[sys.argv.index('--output-last-message')+1]).write_text({answer!r})\n")
    executable.chmod(0o755)
    with pytest.raises(PlannerError) as failure:
        asyncio.run(CodexPlanner(executable=str(executable)).propose('hello',
            {'type':'object','required':['message'],'properties':{'message':{'type':'string'}}},tmp_path/'invalid'))
    assert failure.value.code == 'invalid_planner_output'
    with pytest.raises(PlannerError) as failure:
        asyncio.run(CodexPlanner(executable='missing-veriruntime-codex-fixture').propose('hello',{'type':'object'},tmp_path/'missing'))
    assert failure.value.code == 'codex_not_found'


def test_modified_input_snapshot_is_rejected(tmp_path):
    class ModifyingPlanner(FixturePlanner):
        async def propose(self,prompt,schema,directory,cancellation=None):
            answer=await super().propose(prompt,schema,directory,cancellation)
            source=next((directory.parent/'inputs').rglob('*.c'))
            source.write_text('int main(void){return 0;}')
            return answer
    svc=service(tmp_path)
    result=asyncio.run(ExperimentRunner(svc,ModifyingPlanner()).run(seed(),'Check assertions'))
    assert result.status == 'ERROR' and result.verifier_executions == 0
    assert 'snapshot was modified' in result.diagnostics[0]['detail']


def test_crlf_snapshot_is_preserved_byte_for_byte(tmp_path):
    original=seed()
    task=original.goals[0]
    source=task.program.files[0]
    content=source.content.replace('\n','\r\n')
    program=ProgramSnapshot((ProgramFile(source.logical_path,content,hashlib.sha256(content.encode()).hexdigest()),),task.program.sources)
    original=replace(original,goals=(replace(task,program=program),))
    result=asyncio.run(ExperimentRunner(service(tmp_path),FixturePlanner()).run(original,'Check assertions',plan_only=True))
    assert result.status == 'PLANNED'
    captured=next((Path(result.directory)/'inputs').rglob('*.c'))
    assert captured.read_bytes() == content.encode()


def test_cli_missing_codex_returns_structured_failure(tmp_path,monkeypatch,capsys):
    from veriruntime.cli import main
    monkeypatch.setenv('PATH',str(tmp_path))
    code=main(['plan',str(ROOT/'examples/tasks/safe_assert.json'),'--request','Check assertions',
        '--data-dir',str(tmp_path/'data'),'--json'])
    result=json.loads(capsys.readouterr().out)
    assert code == 2 and result['status'] == 'ERROR'
    assert result['diagnostics'][0]['code'] == 'codex_not_found' and result['verifier_executions'] == 0


def test_process_events_cannot_hide_failure_or_tool_execution(tmp_path):
    executable=tmp_path/'codex-fixture'
    schema={'type':'object'}
    for event,code in (({'type':'turn.failed','error':{'message':'Failed turn'}},'planner_error'),
                       ({'type':'item.completed','item':{'type':'command_execution'}},'unexpected_planner_tool_use')):
        executable.write_text(f'#!{sys.executable}\n'+'''import json,sys
from pathlib import Path
sys.stdin.read()
Path(sys.argv[sys.argv.index('--output-last-message')+1]).write_text('{}')
'''+f'print({json.dumps(event)!r})\n')
        executable.chmod(0o755)
        with pytest.raises(PlannerError) as failure:
            asyncio.run(CodexPlanner(executable=str(executable)).propose('hello',schema,tmp_path/code))
        assert failure.value.code == code


def test_stale_answer_is_never_reused(tmp_path):
    executable=tmp_path/'codex-fixture'
    executable.write_text(f'#!{sys.executable}\nimport sys\nsys.stdin.read()\n')
    executable.chmod(0o755)
    directory=tmp_path/'round'; directory.mkdir()
    (directory/'answer.json').write_text('{}')
    with pytest.raises(PlannerError) as failure:
        asyncio.run(CodexPlanner(executable=str(executable)).propose('hello',{'type':'object'},directory))
    assert failure.value.code == 'invalid_planner_output' and not (directory/'answer.json').exists()
