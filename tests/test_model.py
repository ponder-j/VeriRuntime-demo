from dataclasses import FrozenInstanceError
import pytest
from veriruntime.model import Budget, ExecutionStatus, Verdict, canonical_json


def test_contract_records_are_immutable():
    with pytest.raises(FrozenInstanceError):
        Budget().max_parallel = 100


def test_verdict_is_separate_from_lifecycle():
    assert not Verdict.UNKNOWN.definitive
    assert Verdict.SAFE.definitive
    assert canonical_json({"status": ExecutionStatus.TIMEOUT, "verdict": Verdict.UNKNOWN}) == (
        '{"status":"TIMEOUT","verdict":"UNKNOWN"}')
