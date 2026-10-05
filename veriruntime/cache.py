"""Exact per-goal result cache. Evidence, not the chosen plan, establishes validity."""
from dataclasses import dataclass
import json

from veriruntime.model import Verdict


@dataclass(frozen=True)
class CacheEntry:
    semantic_key: str
    verdict: Verdict
    confirmations: int
    source_execution_id: str
    artifact_ids: tuple[str, ...]
    evidence: tuple[dict, ...]
    created_time: str


class SemanticCache:
    contract = "definitive-cache-v2"

    def __init__(self, store, artifacts):
        self.store, self.artifacts = store, artifacts

    def lookup(self, task):
        with self.store.connection() as db:
            row = db.execute("SELECT payload FROM cache_entries WHERE semantic_key=?", (task.semantic_key,)).fetchone()
        if not row:
            return None
        try:
            entry = json.loads(row[0])
            if entry.get("contract") != self.contract or entry["semantic_key"] != task.semantic_key:
                return None
            verdict = Verdict(entry["verdict"])
            if not verdict.definitive:
                return None
            historical = self.store.definitive_evidence(task.semantic_key)
            if any(a["verdict"] != verdict.value for a in historical):
                return None
            source = self.store.show(entry["source_execution_id"])
            result = source["result"]
            if not result or result["verdict"] != verdict.value or not result["requirement_satisfied"]:
                return None
            evidence = [a for a in source["attempts"] if a["status"] == "COMPLETED" and a["verdict"] == verdict.value]
            if any(a["status"] == "COMPLETED" and a["verdict"] in ("SAFE", "UNSAFE") and a["verdict"] != verdict.value
                   for a in source["attempts"]):
                return None
            families = {a["family"] for a in evidence if a["semantic_key"] == task.semantic_key and a["version"] and a["config_id"]}
            if len(families) < task.requirements.min_confirmations or len(families) != entry["confirmations"]:
                return None
            refs = entry["artifact_ids"]
            if not refs or set(refs) != set(source["artifacts"]):
                return None
            if not all((artifact := self.store.artifact(ref)) and self.artifacts.valid(artifact) for ref in refs):
                return None
            # SQLite metadata must agree with the immutable artifact records.
            # In particular, changing a family/count in metadata cannot manufacture
            # independent confirmations without corresponding raw attempt evidence.
            from pathlib import Path
            records = [self.store.artifact(ref) for ref in refs]
            for kind, expected in (("LOGICAL_PLAN", source["logical_plan"]), ("RESULT", result)):
                if not any(r["kind"] == kind and json.loads(Path(r["path"]).read_text()) == expected for r in records):
                    return None
            for attempt in evidence:
                expected = {k: v for k, v in attempt.items() if k != "artifact_ids"}
                raw = [r for r in records if r["kind"] == "ATTEMPT" and r["attempt_id"] == attempt["id"]]
                if not any({k: v for k, v in json.loads(Path(r["path"]).read_text()).items() if k != "artifact_ids"} == expected for r in raw):
                    return None
            return CacheEntry(task.semantic_key, verdict, len(families), entry["source_execution_id"],
                              tuple(refs), tuple(evidence), entry["created_time"])
        except (ValueError, KeyError, TypeError, OSError):
            return None

    def store_result(self, task, report, artifact_ids):
        result = report.result
        if not result.verdict.definitive or not result.requirement_satisfied or result.cache_hit:
            return False
        evidence = [a for a in report.attempts if a.status.value == "COMPLETED" and a.verdict == result.verdict]
        families = {a.family for a in evidence}
        if len(families) < task.requirements.min_confirmations:
            return False
        from .runtime.process import utc_now
        payload = {"contract": self.contract, "semantic_key": task.semantic_key, "verdict": result.verdict.value,
            "confirmations": len(families), "source_execution_id": result.execution_id,
            "artifact_ids": list(artifact_ids), "created_time": utc_now(),
            "evidence": [{"attempt_id": a.id, "tool": a.tool, "family": a.family, "version": a.version,
                          "config_id": a.config_id, "verdict": a.verdict.value, "artifact_ids": a.artifact_ids}
                         for a in evidence]}
        with self.store.connection() as db:
            db.execute("INSERT OR REPLACE INTO cache_entries VALUES(?,?)", (task.semantic_key, json.dumps(payload)))
        return True

    def clear(self, semantic_keys):
        # Never remove execution history, artifacts or toolchains.
        with self.store.connection() as db:
            return sum(db.execute("DELETE FROM cache_entries WHERE semantic_key=?", (key,)).rowcount
                       for key in set(semantic_keys))
