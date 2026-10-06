"""Backend-local CLI knowledge. No scheduling or cache policy lives here."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import shutil
from veriruntime.execution import ExecutionSpec, LocalExecutionBackend

from veriruntime.model import (ExecutionStatus, ProgramSnapshot, ToolProfile,
                               VerificationTask, Verdict, digest)


@dataclass(frozen=True)
class ParsedResult:
    verdict: Verdict
    status: ExecutionStatus = ExecutionStatus.COMPLETED
    message: str = ""
    diagnostic_code: str = ""


def materialize(program: ProgramSnapshot, workspace: Path) -> tuple[str, ...]:
    root = (workspace / "input").resolve()
    root.mkdir(parents=True, exist_ok=True)
    for file in program.files:
        path = (root / file.logical_path).resolve()
        if not path.is_relative_to(root):
            raise ValueError("Snapshot path escapes workspace")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(file.content.encode("utf-8"))
    return tuple(str(root / p) for p in program.sources)


class ToolAdapter:
    name = ""
    family = ""
    required_flags: tuple[str, ...] = ()
    config: dict = {}
    estimated_memory_mb = 512
    prior_runtime_sec = 1.0
    version_flags = ('--version',)
    help_flags = ('--help',)

    def __init__(self, root: Path | str = "."):
        self.root = Path(root).resolve()
        self._profile: ToolProfile | None = None

    def candidates(self) -> list[Path]:
        override = os.environ.get(f"VRUN_{self.name.upper()}")
        if override:
            return [Path(override).expanduser()]
        found = shutil.which(self.name)
        paths = [Path(found)] if found else []
        local = self.root / ".veriruntime/toolchains" / self.name
        paths += sorted(local.glob(f"**/bin/{self.name}"))
        paths += [Path.home() / ".local/bin" / self.name]
        return paths

    def environment(self) -> dict[str, str]:
        env = dict(os.environ)
        env["LC_ALL"] = "C"
        # An undeclared host include path would violate snapshot identity.
        for key in ("CPATH", "C_INCLUDE_PATH", "CPLUS_INCLUDE_PATH", "LD_PRELOAD",
                    "JAVA_TOOL_OPTIONS", "_JAVA_OPTIONS", "JDK_JAVA_OPTIONS"):
            env.pop(key, None)
        if os.uname().sysname == "Darwin":
            local_libs = sorted((self.root / ".veriruntime/toolchains/deps").glob("*/*/lib"))
            host_libs = sorted(Path("/opt/homebrew/opt").glob("*/lib"))
            if local_libs:
                env["DYLD_LIBRARY_PATH"] = ":".join(map(str, local_libs + host_libs))
        return env

    def attempt_environment(self, workspace: Path) -> dict[str, str]:
        env = self.environment()
        for name, folder in (("HOME", "home"), ("TMPDIR", "tmp"),
                             ("XDG_CACHE_HOME", "cache"), ("XDG_CONFIG_HOME", "config")):
            path = workspace / '.environment' / folder
            path.mkdir(parents=True, exist_ok=True)
            env[name] = str(path)
        return env

    def cleanup(self, workspace: Path) -> None:
        """Transport cleanup after process group termination; native tools need none."""

    def prepare(self, task, workspace: Path) -> None:
        """Reserve transport resources before launching a supervised process."""

    def parse_attempt_result(self, stdout, stderr, exit_code, workspace):
        return self.parse_result(stdout, stderr, exit_code)

    def detect(self) -> ToolProfile:
        diagnostics = []
        for path in self.candidates():
            if not path.is_file():
                continue
            try:
                prefix = self.launch_prefix(path)
                probe = LocalExecutionBackend()
                version_run = probe.run_sync(ExecutionSpec((*prefix, *self.version_flags),
                    str(self.root), self.environment(), wall_time_limit=15))
                version = self.version_text(version_run.stdout + version_run.stderr)
                if version_run.exit_code != 0 or version_run.status != ExecutionStatus.COMPLETED or not version:
                    diagnostics.append(f"{path}: version probe failed: {version[:400]}")
                    continue
                help_run = probe.run_sync(ExecutionSpec((*prefix, *self.help_flags),
                    str(self.root), self.environment(), wall_time_limit=15))
                help_text = help_run.stdout + help_run.stderr
                missing = [f for f in self.required_flags if f not in help_text]
                if help_run.exit_code != 0 or help_run.status != ExecutionStatus.COMPLETED or missing:
                    diagnostics.append(f"{path}: unsupported CLI flags {missing}")
                    continue
                self._profile = ToolProfile(self.name, self.family, True, version,
                    str(path.resolve()), estimated_memory_mb=self.estimated_memory_mb,
                    prior_runtime_sec=self.prior_runtime_sec, config_id=digest(self.config))
                return self._profile
            except OSError as exc:
                diagnostics.append(f"{path}: {exc}")
        self._profile = ToolProfile(self.name, self.family, False, "", None,
            config_id=digest(self.config), diagnostic="; ".join(diagnostics) or "Executable not found")
        return self._profile

    def launch_prefix(self, path):
        return (str(path),)

    def version_text(self, output):
        return output.strip()

    def profile(self) -> ToolProfile:
        return self._profile or self.detect()

    def version(self) -> str:
        return self.profile().version

    def supports(self, task: VerificationTask) -> bool:
        p = self.profile()
        return (p.available and task.language in p.languages and
                task.property.kind in p.properties and task.semantics.c_standard in p.c_standards
                and task.semantics.data_model in p.data_models and self.accepts_program(task))

    def accepts_program(self, task) -> bool:
        return True

    def build_command(self, task: VerificationTask, workspace: Path) -> tuple[str, ...]:
        raise NotImplementedError

    def parse_result(self, stdout: str, stderr: str, exit_code: int) -> ParsedResult:
        raise NotImplementedError

    def collect_artifacts(self, workspace: Path) -> tuple[tuple[str, Path], ...]:
        return ()
