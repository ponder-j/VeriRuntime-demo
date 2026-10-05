from pathlib import Path
import re
import subprocess
import sys

from veriruntime.model import ExecutionStatus, Verdict
from .base import ParsedResult, ToolAdapter, materialize


class ESBMCAdapter(ToolAdapter):
    name = family = "esbmc"
    required_flags = ("--std", "--unwind", "--no-unwinding-assertions", "--multi-property", "--z3")
    config = {"adapter": 1, "unwind": 64, "unwinding_assertions": True,
              "solver": "z3", "multi_property": True, "overflow_check": True, "ub_shift_check": True}
    estimated_memory_mb = 512
    prior_runtime_sec = 0.7

    def build_command(self, task, workspace: Path):
        sources = materialize(task.program, workspace)
        args = [self.profile().path, *sources, "--function", task.entry, "--std", task.semantics.c_standard,
                "--64" if task.semantics.data_model == "LP64" else "--32", "--unwind",
                str(self.config["unwind"]), "--z3", "--multi-property", "--overflow-check", "--ub-shift-check"]
        if sys.platform == "darwin":
            sdk = subprocess.check_output(["xcrun", "--show-sdk-path"], text=True, timeout=10).strip()
            args += ["--sysroot", sdk, "-I", str(Path(sdk) / "usr/include")]
        return tuple(args)

    def parse_result(self, stdout, stderr, exit_code):
        if exit_code not in (0, 1):
            return ParsedResult(Verdict.UNKNOWN, ExecutionStatus.ERROR, "Unexpected ESBMC exit code")
        output = stdout + "\n" + stderr
        conclusions = re.findall(r'^VERIFICATION (SUCCESSFUL|FAILED|UNKNOWN)\s*$', output, re.M)
        # Match the actual property table, never echoed source/trace text.
        rows = re.findall(r'^\s*(PASSED|FAILED|UNKNOWN)\s+\[([^\]]+)\].*$', output, re.M)
        failures = [prop for state, prop in rows if state == "FAILED"]
        if failures and any(".assertion." not in prop for prop in failures):
            return ParsedResult(Verdict.UNKNOWN, message="Non-assertion failure or incomplete unwinding",
                diagnostic_code="insufficient_unwinding" if any("unwind" in p for p in failures) else "verifier_error")
        if conclusions == ["FAILED"] and failures and exit_code == 1:
            return ParsedResult(Verdict.UNSAFE, message="Assertion counterexample")
        if conclusions == ["SUCCESSFUL"] and exit_code == 0 and all(state == "PASSED" for state, _ in rows):
            return ParsedResult(Verdict.SAFE, message="Complete assertion proof with unwinding checks")
        return ParsedResult(Verdict.UNKNOWN, message="No complete ESBMC conclusion")
