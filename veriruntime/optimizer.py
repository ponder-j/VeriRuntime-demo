"""Replaceable per-goal physical optimizer. It never creates logical goals."""
from dataclasses import dataclass, replace
from typing import Protocol

from veriruntime.model import to_data
from veriruntime.plan import CacheLookupPlan, ParallelPlan, PhysicalPlan, RunPlan, SequencePlan


@dataclass(frozen=True)
class RuntimeContext:
    registry: object
    history: object
    cache: object | None = None


@dataclass(frozen=True)
class CandidateScore:
    tool: str
    family: str
    compatible: bool
    reasons: tuple[str, ...]
    history: object | None
    expected_runtime_sec: float
    success_rate: float
    score: float
    selected: bool = False


@dataclass(frozen=True)
class OptimizationResult:
    physical_plan: PhysicalPlan
    explanation: dict
    candidate_scores: tuple[CandidateScore, ...]


class Optimizer(Protocol):
    def optimize(self, logical_plan, runtime_context: RuntimeContext) -> OptimizationResult: ...


class CostAwareOptimizer:
    def optimize(self, logical_plan, runtime_context):
        task = logical_plan.task
        registry, history = runtime_context.registry, runtime_context.history
        scores = []
        for profile in registry.profiles():
            reasons = []
            if not profile.available:
                reasons.append("unavailable")
            if task.language not in profile.languages:
                reasons.append("unsupported_language")
            if task.property.kind not in profile.properties:
                reasons.append("unsupported_property")
            if (task.semantics.c_standard not in profile.c_standards or
                    task.semantics.data_model not in profile.data_models):
                reasons.append("unsupported_semantics")
            if profile.available and not registry.get(profile.name).supports(task) and not reasons:
                reasons.append("unsupported_program_shape")
            if profile.estimated_memory_mb > task.budget.memory_mb:
                reasons.append("memory_budget_below_estimate")
            stats = history.stats_for(profile.name, task, profile.version, profile.config_id) if history else None
            expected = max(0.01, stats.median_runtime_sec if stats else profile.prior_runtime_sec)
            success = (stats.definitive_count + 0.5) / (stats.run_count + 1) if stats else 0.5
            # Hints do not establish facts and may be ignored by this initial model.
            scores.append(CandidateScore(profile.name, profile.family, not reasons, tuple(reasons),
                                         stats, expected, success, success / expected))
        ranked = sorted((c for c in scores if c.compatible), key=lambda c: (-c.score, c.tool))
        # One candidate per verifier family, irrespective of installed configurations.
        candidates, families = [], set()
        for candidate in ranked:
            if candidate.family not in families:
                candidates.append(candidate)
                families.add(candidate.family)
        tiny_budget = bool(candidates and task.budget.wall_time_sec < candidates[0].expected_runtime_sec)
        if tiny_budget and task.requirements.min_confirmations == 1:
            candidates = candidates[:1]
        selected = {c.tool for c in candidates}
        scores = tuple(replace(c, selected=c.tool in selected) for c in scores)
        stages = []
        group, memory = [], 0
        for candidate in candidates:
            estimate = registry.get_profile(candidate.tool).estimated_memory_mb
            if group and (len(group) >= task.budget.max_parallel or memory + estimate > task.budget.memory_mb):
                stages.append(tuple(group))
                group, memory = [], 0
            group.append(candidate.tool)
            memory += estimate
        if group:
            stages.append(tuple(group))
        # Time slices leave subsequent fallback/confirmation stages an opportunity.
        slice_sec = task.budget.wall_time_sec / len(stages) if stages else task.budget.wall_time_sec
        nodes = []
        for group in stages:
            children = tuple(RunPlan(tool, slice_sec) for tool in group)
            nodes.append(children[0] if len(children) == 1 else ParallelPlan(children, len(children)))
        plan = nodes[0] if len(nodes) == 1 else SequencePlan(tuple(nodes))
        explanation = {"optimizer": "cost-aware-v1", "goal_id": task.id, "semantic_key": task.semantic_key,
            "cache": "DISABLED", "selected_tools": [c.tool for c in candidates],
            "reason": "Rank by smoothed definitive rate / median runtime; pack within concurrency and memory estimates; reserve fallback time",
            "min_confirmations": task.requirements.min_confirmations,
            "independent_candidates": len(candidates),
            "trust_feasible": len(candidates) >= task.requirements.min_confirmations,
            "budget": to_data(task.budget), "hints": to_data(task.hints), "hints_applied": False,
            "tiny_budget": tiny_budget, "stages": [list(group) for group in stages]}
        if runtime_context.cache:
            entry = runtime_context.cache.lookup(task)
            explanation["cache"] = "HIT" if entry else "MISS"
            if entry:
                plan = CacheLookupPlan(task.semantic_key, entry.source_execution_id, plan)
                explanation["fallback_selected_tools"] = explanation["selected_tools"]
                explanation["selected_tools"] = []
                explanation["cache_source_execution"] = entry.source_execution_id
        return OptimizationResult(plan, explanation, scores)
