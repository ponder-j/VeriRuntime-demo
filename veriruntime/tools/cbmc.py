import json
from pathlib import Path

from veriruntime.model import ExecutionStatus, Verdict
from .base import ParsedResult, ToolAdapter, materialize


class CBMCAdapter(ToolAdapter):
    name = family = "cbmc"
    required_flags = ("--json-ui", "--unwinding-assertions", "--c11", "--function", "--64")
    config = {"adapter": 1, "unwind": 64, "unwinding_assertions": True, "default_checks": True}
    estimated_memory_mb = 256
    prior_runtime_sec = 0.5

    def build_command(self, task, workspace: Path):
        sources = materialize(task.program, workspace)
        return (self.profile().path, *sources, "--function", task.entry,
                f"--{task.semantics.c_standard}",
                "--64" if task.semantics.data_model == "LP64" else "--32",
                "--unwind", str(self.config["unwind"]), "--unwinding-assertions", "--json-ui")

    def parse_result(self, stdout, stderr, exit_code):
        if exit_code not in (0, 10):
            return ParsedResult(Verdict.UNKNOWN, ExecutionStatus.ERROR, "Unexpected CBMC exit code")
        try:
            records = json.loads(stdout)
            if not isinstance(records, list) or not all(isinstance(r, dict) for r in records):
                raise ValueError("Expected CBMC JSON record array")
            statuses = [r["cProverStatus"] for r in records if "cProverStatus" in r]
            properties = [p for r in records for p in r.get("result", [])]
            if any(r.get("messageType") == "ERROR" for r in records):
                return ParsedResult(Verdict.UNKNOWN, ExecutionStatus.ERROR, "CBMC reported an error")
            failed = [p for p in properties if p.get("status") == "FAILURE"]
            assertions = [p for p in failed if p.get("sourceLocation", {}).get("propertyClass") == "assertion"]
            if failed and len(assertions) != len(failed):
                return ParsedResult(Verdict.UNKNOWN, message="Non-assertion failure or incomplete unwinding")
            if statuses == ["failure"] and assertions and exit_code == 10:
                return ParsedResult(Verdict.UNSAFE, message="Assertion counterexample")
            if (statuses == ["success"] and exit_code == 0 and
                    all(p.get("status") == "SUCCESS" for p in properties)):
                return ParsedResult(Verdict.SAFE, message="Complete assertion proof with unwinding checks")
            return ParsedResult(Verdict.UNKNOWN, message="No complete CBMC conclusion")
        except (ValueError, TypeError, KeyError, AttributeError):
            return ParsedResult(Verdict.UNKNOWN, ExecutionStatus.ERROR, "Malformed CBMC JSON output")
