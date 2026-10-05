"""Shared semantic and evidence records, independent of backend implementations."""
from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
import hashlib
import json
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

    @property
    def size_bytes(self) -> int:
        return sum(len(f.content.encode("utf-8")) for f in self.files)

    def identity(self) -> dict:
        return {"files": [{"logical_path": f.logical_path, "sha256": f.sha256}
                          for f in self.files], "sources": self.sources}


@dataclass(frozen=True)
class VerificationProperty:
    kind: str = "assertion_safety"


@dataclass(frozen=True)
class Semantics:
    c_standard: str = "c11"
    data_model: str = "LP64"


@dataclass(frozen=True)
class Requirements:
    min_confirmations: int = 1


@dataclass(frozen=True)
class Budget:
    wall_time_sec: float = 30
    memory_mb: int = 2048
    max_parallel: int = 2


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

    @property
    def semantic_key(self) -> str:
        # Labels and resource budgets affect execution, not the proposition/trust.
        return digest({"contract": "veriruntime-semantic-v1", "language": self.language,
                       "entry": self.entry, "program": self.program.identity(),
                       "property": self.property, "semantics": self.semantics,
                       "requirements": self.requirements})


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
