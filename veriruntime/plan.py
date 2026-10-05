"""Physical execution AST; only the optimizer constructs public execution plans."""
from __future__ import annotations
from dataclasses import dataclass
from typing import TypeAlias


@dataclass(frozen=True)
class RunPlan:
    tool: str
    time_slice_sec: float | None = None
    operator: str = "Run"


@dataclass(frozen=True)
class SequencePlan:
    children: tuple[PhysicalPlan, ...]
    operator: str = "Sequence"


@dataclass(frozen=True)
class ParallelPlan:
    children: tuple[PhysicalPlan, ...]
    max_parallel: int
    operator: str = "Parallel"


@dataclass(frozen=True)
class CacheLookupPlan:
    semantic_key: str
    source_execution_id: str
    fallback: PhysicalPlan
    operator: str = "CacheLookup"


PhysicalPlan: TypeAlias = RunPlan | SequencePlan | ParallelPlan | CacheLookupPlan
