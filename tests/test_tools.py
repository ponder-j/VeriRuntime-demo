from pathlib import Path
import subprocess

import pytest
from veriruntime.dsl import load_task
from veriruntime.model import ExecutionStatus, Verdict
from veriruntime.tools import ToolRegistry, default_registry
from veriruntime.tools.cbmc import CBMCAdapter
from veriruntime.tools.esbmc import ESBMCAdapter

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize("name,adapter", [("cbmc", CBMCAdapter), ("esbmc", ESBMCAdapter)])
@pytest.mark.parametrize("case,verdict,code", [("safe", Verdict.SAFE, 0), ("unsafe", Verdict.UNSAFE, 1)])
def test_observed_real_output(name, adapter, case, verdict, code):
    if name == "cbmc" and code == 1:
        code = 10
    p = adapter().parse_result((FIXTURES / name / f"{case}.stdout").read_text(),
                              (FIXTURES / name / f"{case}.stderr").read_text(), code)
    assert p.verdict == verdict
    assert p.status == ExecutionStatus.COMPLETED


def test_fail_closed_parsers():
    assert CBMCAdapter().parse_result("SUCCESS", "", 0).status == ExecutionStatus.ERROR
    assert ESBMCAdapter().parse_result("some SUCCESS string", "", 0).verdict == Verdict.UNKNOWN
    output = "  FAILED [main.unwinding.1] unwinding assertion\nVERIFICATION FAILED\n"
    assert ESBMCAdapter().parse_result(output, "", 1).verdict == Verdict.UNKNOWN
    assert CBMCAdapter().parse_result("[]", "", 137).verdict == Verdict.UNKNOWN


def test_registry_capabilities():
    from dataclasses import replace
    from veriruntime.model import VerificationProperty
    reg = default_registry(ROOT)
    task = load_task(ROOT / "examples/tasks/safe_assert.json")
    assert all(p.available for p in reg.compatible_tools(task))
    assert reg.compatible_tools(replace(task, property=VerificationProperty("memory_safety"))) == ()
    with pytest.raises(ValueError, match="Duplicate"):
        reg.register(CBMCAdapter(ROOT))
    assert ToolRegistry().compatible_tools(task) == ()


@pytest.mark.integration
@pytest.mark.parametrize("tool", ["cbmc", "esbmc"])
@pytest.mark.parametrize("case,verdict", [("safe", Verdict.SAFE), ("unsafe", Verdict.UNSAFE)])
def test_real_verifier(tool, case, verdict, tmp_path):
    adapter = default_registry(ROOT).get(tool)
    if not adapter.profile().available:
        pytest.skip(f"{tool} not installed: {adapter.profile().diagnostic}")
    task = load_task(ROOT / f"examples/tasks/{case}_assert.json")
    argv = adapter.build_command(task, tmp_path)
    result = subprocess.run(argv, env=adapter.environment(), capture_output=True, text=True, timeout=30)
    assert adapter.parse_result(result.stdout, result.stderr, result.returncode).verdict == verdict
