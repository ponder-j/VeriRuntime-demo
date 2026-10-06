"""Shared semantic and evidence records, independent of backend implementations."""
from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from enum import Enum
import hashlib
import json
from pathlib import PurePosixPath
import re
from typing import Any


class Verdict(str, Enum):
    SAFE = "SAFE"
    UNSAFE = "UNSAFE"
    UNKNOWN = "UNKNOWN"
    CONFLICT = "CONFLICT"

    @property
    def definitive(self) -> bool:
        return self in (Verdict.SAFE, Verdict.UNSAFE)


class ExecutionStatus(str, Enum):
    COMPLETED = "COMPLETED"
    TIMEOUT = "TIMEOUT"
    CANCELLED = "CANCELLED"
    ERROR = "ERROR"
    OOM = "OOM"
    START_FAILED = "START_FAILED"


class TerminationReason(str, Enum):
    NORMAL = "NORMAL"
    WALL_BUDGET = "WALL_BUDGET"
    REQUIREMENTS_MET = "REQUIREMENTS_MET"
    USER_CANCEL = "USER_CANCEL"
    MEMORY_BUDGET = "MEMORY_BUDGET"
    PROCESS_ERROR = "PROCESS_ERROR"
    START_FAILURE = "START_FAILURE"
    NO_CANDIDATES = "NO_CANDIDATES"
    EXHAUSTED = "EXHAUSTED"
    CONFLICT = "CONFLICT"


def to_data(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {f.name: to_data(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, dict):
        return {str(k): to_data(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [to_data(v) for v in value]
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(to_data(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ProgramFile:
    logical_path: str
    content: str
    sha256: str

    def __post_init__(self) -> None:
        if hashlib.sha256(self.content.encode("utf-8")).hexdigest() != self.sha256:
            raise ValueError("ProgramFile content does not match its hash")


@dataclass(frozen=True)
class ProgramSnapshot:
    files: tuple[ProgramFile, ...]
    sources: tuple[str, ...]

    def __post_init__(self):
        paths = [f.logical_path for f in self.files]
        if not self.sources or len(paths) != len(set(paths)) or len(self.sources) != len(set(self.sources)):
            raise ValueError("Snapshot requires unique files and source translation units")
        if any(PurePosixPath(p).is_absolute() or '..' in PurePosixPath(p).parts for p in paths):
            raise ValueError("Snapshot paths must be normalized relative logical paths")
        if not set(self.sources) <= set(paths):
            raise ValueError("Every source must belong to the immutable snapshot")

    @property
    def size_bytes(self) -> int:
        return sum(len(f.content.encode("utf-8")) for f in self.files)

    def identity(self) -> dict:
        return {"files": [{"logical_path": f.logical_path, "sha256": f.sha256}
                          for f in self.files], "sources": self.sources}


@dataclass(frozen=True)
class VerificationProperty:
    kind: str = "assertion_safety"

    def __post_init__(self):
        if self.kind not in ("assertion_safety", "memory_safety"):
            raise ValueError("Unsupported logical property")


@dataclass(frozen=True)
class Semantics:
    c_standard: str = "c11"
    data_model: str = "LP64"

    def __post_init__(self):
        if self.c_standard not in ("c11", "c99") or self.data_model not in ("LP64", "ILP32"):
            raise ValueError("Unsupported C semantics")


@dataclass(frozen=True)
class Requirements:
    min_confirmations: int = 1

    def __post_init__(self):
        if type(self.min_confirmations) is not int or not 1 <= self.min_confirmations <= 16:
            raise ValueError("min_confirmations must be an integer between 1 and 16")


@dataclass(frozen=True)
class Budget:
    wall_time_sec: float = 30
    memory_mb: int = 2048
    max_parallel: int = 2

    def __post_init__(self):
        import math
        if (isinstance(self.wall_time_sec, bool) or not math.isfinite(self.wall_time_sec) or
                not 0 < self.wall_time_sec <= 86400 or type(self.memory_mb) is not int or self.memory_mb < 16 or
                type(self.max_parallel) is not int or not 1 <= self.max_parallel <= 64):
            raise ValueError("Invalid resource budget")


@dataclass(frozen=True)
class SemanticHints:
    prefer_fast_counterexample: bool = False
    expected_characteristics: tuple[str, ...] = ()
    required_capabilities: tuple[str, ...] = ()


@dataclass(frozen=True)
class VerificationTask:
    id: str
    language: str
    entry: str
    program: ProgramSnapshot
    property: VerificationProperty
    semantics: Semantics
    requirements: Requirements
    budget: Budget
    version: str = "0.1"
    hints: SemanticHints = field(default_factory=SemanticHints)

    def __post_init__(self):
        if not self.id or self.language != "C" or self.version != "0.1" or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', self.entry):
            raise ValueError("Invalid fixed verification goal")

    @property
    def semantic_key(self) -> str:
        # Labels and resource budgets affect execution, not the proposition/trust.
        return digest({"contract": "veriruntime-semantic-v1", "language": self.language,
                       "entry": self.entry, "program": self.program.identity(),
                       "property": self.property, "semantics": self.semantics,
                       "requirements": self.requirements})


# A fixed proof obligation. The alias preserves the single-task API.
LogicalGoal = VerificationTask


@dataclass(frozen=True)
class GoalDependency:
    predecessor: str
    successor: str
    required_verdict: Verdict = Verdict.SAFE


@dataclass(frozen=True)
class VerificationWorkflow:
    workflow_id: str
    goals: tuple[LogicalGoal, ...]
    dependencies: tuple[GoalDependency, ...] = ()
    metadata: tuple[tuple[str, str], ...] = ()

    def __post_init__(self):
        ids = [goal.id for goal in self.goals]
        if not ids or len(ids) != len(set(ids)):
            raise ValueError("Workflow goal IDs must be unique and nonempty")
        remaining = set(ids)
        for dep in self.dependencies:
            if dep.predecessor not in remaining or dep.successor not in remaining:
                raise ValueError("Workflow dependency refers to an unknown goal")
            if not dep.required_verdict.definitive:
                raise ValueError("Dependencies require SAFE or UNSAFE")
        edges = {(d.predecessor, d.successor) for d in self.dependencies}
        while remaining:
            ready = {goal for goal in remaining if not any(b == goal and a in remaining for a, b in edges)}
            if not ready:
                raise ValueError("Workflow dependencies must form a DAG")
            remaining -= ready

    @classmethod
    def single(cls, task: VerificationTask):
        return cls(task.id, (task,))


@dataclass(frozen=True)
class LogicalPlan:
    task: VerificationTask
    operation: str = "Verify"


@dataclass(frozen=True)
class ToolProfile:
    name: str
    family: str
    available: bool
    version: str
    path: str | None
    languages: tuple[str, ...] = ("C",)
    properties: tuple[str, ...] = ("assertion_safety",)
    c_standards: tuple[str, ...] = ("c11", "c99")
    data_models: tuple[str, ...] = ("LP64", "ILP32")
    estimated_memory_mb: int = 512
    prior_runtime_sec: float = 1.0
    config_id: str = ""
    diagnostic: str = ""


@dataclass(frozen=True)
class Artifact:
    id: str
    kind: str
    path: str
    size_bytes: int
    sha256: str
    attempt_id: str | None = None


@dataclass(frozen=True)
class ExecutionAttempt:
    id: str
    execution_id: str
    semantic_key: str
    tool: str
    family: str
    version: str
    config_id: str
    command: tuple[str, ...]
    start_time: str
    end_time: str
    wall_time_sec: float
    verdict: Verdict
    status: ExecutionStatus
    termination_reason: TerminationReason
    exit_code: int | None
    workspace: str
    stdout_path: str
    stderr_path: str
    message: str = ""
    artifact_ids: tuple[str, ...] = ()
    diagnostic_code: str = ""
    execution_backend: str = ""
    backend_metrics: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Diagnostic:
    code: str
    goal_id: str
    tool: str | None = None
    attempt_id: str | None = None
    detail: str = ""


@dataclass(frozen=True)
class VerificationResult:
    execution_id: str
    semantic_key: str
    verdict: Verdict
    confirmations: int
    requirement_satisfied: bool
    termination_reason: TerminationReason
    attempt_ids: tuple[str, ...]
    wall_time_sec: float
    cache_hit: bool = False
    source_execution_id: str | None = None
    goal_id: str = ""
    status: str = "COMPLETED"
    artifacts: tuple[str, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()
    optimizer_summary: dict = field(default_factory=dict)
    failure_reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class RuntimeStats:
    tool: str
    property_kind: str
    size_bucket: str
    run_count: int
    definitive_count: int
    safe_count: int
    unsafe_count: int
    unknown_count: int
    timeout_count: int
    mean_runtime_sec: float
    median_runtime_sec: float
    recent_runtime_sec: float
