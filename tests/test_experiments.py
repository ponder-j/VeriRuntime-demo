import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from veriruntime.dsl import DSLValidationError
from veriruntime.model import Verdict, digest
from veriruntime.proofs.dsl import load_proof
from veriruntime.proofs.worker import strip_comments
from veriruntime.tools.ultimate import UltimateAdapter
from veriruntime.tools.framac import FramaCAdapter

ROOT = Path(__file__).resolve().parents[1]


def test_upper_proof_identity_and_binding_are_fixed():
    proof = load_proof(ROOT / 'examples/proofs/check.json')
    assert proof.language == 'Rocq' and proof.statement_symbol == 'VRGoal.Expected.obligation'
    assert replace(proof, id='another-label').semantic_key == proof.semantic_key
    assert replace(proof, proof_symbol='VRGoal.Correct.another').semantic_key != proof.semantic_key
    with pytest.raises(ValueError):
        replace(proof, proof_symbol='VRGoal.Correct.x. Admitted.')
    with pytest.raises(ValueError):
        replace(proof, language='C')


def test_proof_dsl_rejects_weakening_or_execution_commands(tmp_path):
    doc = json.loads((ROOT / 'examples/proofs/check.json').read_text())
    for mutate in (lambda d: d.update(run='rocq compile'),
                   lambda d: d['requirements'].update(axioms=['arbitrary']),
                   lambda d: d['goal']['proof'].update(files=['../Correct.v']),
                   lambda d: d['goal']['proof'].update(symbol='Other.discharge')):
        changed = json.loads(json.dumps(doc))
        mutate(changed)
        (tmp_path / 'task.json').write_text(json.dumps(changed))
        with pytest.raises((DSLValidationError, ValueError)):
            load_proof(tmp_path / 'task.json')


def test_nested_proof_comments_do_not_hide_policy_commands():
    assert strip_comments('(* outer (* inner *) *) Admitted.') == ' Admitted.'
    with pytest.raises(ValueError):
        strip_comments('(* unclosed')


def test_interrupted_proof_report_cannot_be_accepted(tmp_path):
    from veriruntime.proofs.service import _read_result
    path = tmp_path / 'proof-result.json'
    attempt = SimpleNamespace(status=SimpleNamespace(value='TIMEOUT'), verdict=Verdict.UNKNOWN,
                              diagnostic_code='timeout')
    path.write_text('{"status":')
    assert _read_result(path, attempt)['reason'] == 'incomplete_proof_report'
    path.write_text(json.dumps({'status': 'VERIFIED', 'reason': 'kernel_checked', 'code_verdict': 'SAFE'}))
    report = _read_result(path, attempt)
    assert report['status'] == 'UNKNOWN' and report['reason'] == 'proof_execution_not_completed'
    assert report['code_verdict'] is None


def test_new_verifiers_fail_closed(logical):
    ultimate = UltimateAdapter()
    assert ultimate.parse_result('Result:\nTRUE\n', '', 0).verdict == Verdict.SAFE
    assert ultimate.parse_result('Result:\nFALSE\n', '', 0).verdict == Verdict.UNSAFE
    assert ultimate.parse_result('echo TRUE', '', 0).verdict == Verdict.UNKNOWN
    assert ultimate.parse_result('Result:\nTRUE\nResult:\nFALSE\n', '', 0).verdict == Verdict.UNKNOWN
    assert ultimate.parse_result('Result:\nTRUE\n', '', 9).verdict == Verdict.UNKNOWN
    frame = {'contract': 'framac-eva-report-v1', 'exit_code': 0, 'complete': True,
             'has_assertion': True, 'properties': [{'valid': True}]}
    assert FramaCAdapter().parse_result(json.dumps(frame), '', 0).verdict == Verdict.SAFE
    for field, value in (('complete', False), ('has_assertion', False), ('properties', []),
                         ('properties', [{'valid': False}])):
        assert FramaCAdapter().parse_result(json.dumps({**frame, field: value}), '', 0).verdict == Verdict.UNKNOWN


@pytest.mark.integration
def test_real_rocq_binding_and_admission_rejection(tmp_path):
    from veriruntime.proofs.service import check_proof
    from veriruntime.tools.docker import DockerAdapter
    from veriruntime.tools.rocq import RocqAdapter
    adapter = DockerAdapter(RocqAdapter()) if os.environ.get('VRUN_BACKEND') == 'docker' else RocqAdapter()
    if not adapter.profile().available:
        pytest.skip('Optional Rocq image not installed')
    for case, expected, reason in (('Correct', 'VERIFIED', 'kernel_checked'),
                                   ('Incorrect', 'REJECTED', 'proof_type_mismatch'),
                                   ('Admitted', 'REJECTED', 'untrusted_axioms_or_extensions')):
        doc = json.loads((ROOT / 'examples/proofs/check.json').read_text())
        doc['goal']['proof'].update(files=[case + '.v'], symbol='VRGoal.' + case + '.discharge')
        for name in ('Expected.v', case + '.v'):
            (tmp_path / name).write_bytes((ROOT / 'examples/proofs' / name).read_bytes())
        (tmp_path / 'task.json').write_text(json.dumps(doc))
        result = asyncio.run(check_proof(load_proof(tmp_path / 'task.json'), tmp_path / 'executions'))
        assert (result['status'], result['reason']) == (expected, reason)
        assert result['code_verdict'] is None and 'verdict' not in result['attempt']
