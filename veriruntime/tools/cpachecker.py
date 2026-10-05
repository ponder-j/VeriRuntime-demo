"""Optional native CPAchecker value analysis with validated Princess counterexamples."""
from pathlib import Path
import re
import shutil
import sys

from veriruntime.model import ExecutionStatus, Verdict
from .base import ParsedResult, ToolAdapter, materialize


class CPAcheckerAdapter(ToolAdapter):
    name = family = "cpachecker"
    required_flags = ("--config", "--spec", "--entry-function", "--output-path", "--option")
    config = {"adapter": 1, "analysis": "valueAnalysis", "counterexample_solver": "PRINCESS",
              "unknown_functions": "reject", "assert_model": 1, "float": "unsupported"}
    estimated_memory_mb = 1024
    prior_runtime_sec = 4.0

    def launch_prefix(self, path):
        bash = sorted((self.root / ".veriruntime/toolchains/bash").glob("**/bin/bash"))
        return (str(bash[-1]), str(path)) if bash else (str(path),)

    def version_text(self, output):
        match = re.search(r'^CPAchecker [^\n]+', output, re.M)
        return match.group(0) if match else output.strip()

    def supports(self, task):
        # This configuration has no floating-point theory. Reject rather than
        # approximate the proposition or silently change the supplied semantics.
        return (super().supports(task) and shutil.which("clang") is not None and
                not any(re.search(r'\b(float|double|__vr_assert_)\b', f.content) for f in task.program.files))

    def build_command(self, task, workspace: Path):
        sources = materialize(task.program, workspace)
        cpa_root = Path(self.profile().path).parent.parent
        model_dir = workspace / "adapter-headers"
        model_dir.mkdir()
        suffix = task.semantic_key[:16]
        function = "__vr_assert_" + suffix
        label = "VR_ASSERT_FAILURE_" + suffix
        if any(function in f.content or label in f.content for f in task.program.files):
            raise ValueError("Input collides with assertion model identifiers")
        header = f'''#ifndef VR_ASSERT_HELPER_{suffix}
#define VR_ASSERT_HELPER_{suffix}
static void {function}(void) {{ {label}: while (1) {{}} }}
#endif
#undef assert
#ifdef NDEBUG
#define assert(e) ((void)0)
#else
#define assert(e) ((e) ? (void)0 : {function}())
#endif
'''
        (model_dir / "assert.h").write_text(header)
        specification = workspace / "assertion.spc"
        specification.write_text(f"OBSERVER AUTOMATON VeriRuntimeAssertion\nINITIAL STATE Init;\nSTATE USEFIRST Init :\n  MATCH LABEL [{label}] -> ERROR;\nEND AUTOMATON\n")
        checker_config = workspace / "counterexample.properties"
        checker_config.write_text(f"#include {cpa_root}/config/cex-checks/predicateAnalysis-as-cex-check.properties\nsolver.solver=PRINCESS\ncpa.predicate.encodeFloatAs=UNSUPPORTED\n")
        compiled = tuple(str(workspace / f"translation-{i}.i") for i in range(len(sources)))
        argv = (*self.launch_prefix(self.profile().path), "--valueAnalysis", "--heap", "768M",
                "--option", "cpa.value.ignoreCallsToUnknownFunctions=false", "--option", "solver.solver=PRINCESS",
                "--option", "cpa.predicate.encodeFloatAs=UNSUPPORTED", "--option", f"counterexample.checker.config={checker_config}",
                "--entry-function", task.entry, "--spec", str(specification), "--output-path", str(workspace / "tool-output"),
                "--64" if task.semantics.data_model == "LP64" else "--32", *compiled)
        compile_commands = [[shutil.which("clang"), "-E", "-P", f"-std={task.semantics.c_standard}",
            "-m64" if task.semantics.data_model == "LP64" else "-m32", "-I", str(model_dir), source] for source in sources]
        import json
        manifest = workspace / "adapter-command.json"
        manifest.write_text(json.dumps({"preprocessor_commands": compile_commands,
                                       "compiled_sources": compiled, "verifier_argv": argv}, indent=2))
        return (sys.executable, str(Path(__file__).with_name("cpa_driver.py")), str(manifest))

    def parse_result(self, stdout, stderr, exit_code):
        output = stdout + "\n" + stderr
        if exit_code != 0 or re.search(r'^(?:Error|ERROR):', output, re.M):
            return ParsedResult(Verdict.UNKNOWN, ExecutionStatus.ERROR, "CPAchecker or preprocessing error", "verifier_error")
        if "may no longer be sound" in output or "Ignoring function calls is unsound" in output:
            return ParsedResult(Verdict.UNKNOWN, message="Rejected unsound configuration warning", diagnostic_code="verifier_error")
        verdicts = re.findall(r'^Verification result: (TRUE|FALSE|UNKNOWN)(?:[.,]|\s)', output, re.M)
        if verdicts == ["TRUE"]:
            return ParsedResult(Verdict.SAFE, message="CPAchecker assertion reachability proof")
        if verdicts == ["FALSE"] and re.search(r'^Error path found and confirmed by counterexample check with CPACHECKER\.', output, re.M):
            return ParsedResult(Verdict.UNSAFE, message="CPAchecker counterexample confirmed with Princess")
        return ParsedResult(Verdict.UNKNOWN, message="Incomplete CPAchecker analysis", diagnostic_code="inconclusive_verifier")

    def collect_artifacts(self, workspace):
        artifacts = [("ADAPTER_COMMAND", workspace / "adapter-command.json"),
                     ("PROPERTY_SPEC", workspace / "assertion.spc"),
                     ("TOOL_CONFIG", workspace / "counterexample.properties"),
                     ("ADAPTER_HEADER", workspace / "adapter-headers/assert.h")]
        artifacts += [("PREPROCESSED_SOURCE", p) for p in workspace.glob("translation-*.i")]
        for path in (workspace / "tool-output").glob("*"):
            if path.is_file() and not path.name.endswith(".lck"):
                kind = "COUNTEREXAMPLE" if path.name.startswith("Counterexample") else "TOOL_OUTPUT"
                artifacts.append((kind, path))
        return tuple((kind, path) for kind, path in artifacts if path.exists())
