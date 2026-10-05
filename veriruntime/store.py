"""SQLite execution history. Store policy is independent of tool-specific logic."""
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import statistics

from veriruntime.model import RuntimeStats, to_data


def size_bucket(task):
    size = task.program.size_bytes
    return "tiny" if size < 4096 else "small" if size < 65536 else "large"


class ExecutionStore:
    def __init__(self, data_dir=".veriruntime"):
        self.root = Path(data_dir).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "runtime.sqlite3"
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS attempts (
                    id TEXT PRIMARY KEY, execution_id TEXT NOT NULL,
                    tool TEXT NOT NULL, version TEXT NOT NULL, config_id TEXT NOT NULL,
                    property_kind TEXT NOT NULL, size_bucket TEXT NOT NULL,
                    end_time TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS attempts_stats ON attempts(tool, property_kind, size_bucket, version, config_id);
                CREATE TABLE IF NOT EXISTS runtime_stats (
                    tool TEXT, property_kind TEXT, size_bucket TEXT, version TEXT, config_id TEXT,
                    payload TEXT NOT NULL, PRIMARY KEY(tool, property_kind, size_bucket, version, config_id));
                CREATE TABLE IF NOT EXISTS tasks (semantic_key TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS executions (
                    id TEXT PRIMARY KEY, semantic_key TEXT NOT NULL, goal_id TEXT NOT NULL,
                    created_time TEXT NOT NULL, logical_plan TEXT NOT NULL, physical_plan TEXT NOT NULL,
                    optimization TEXT NOT NULL, result TEXT, events TEXT, artifacts TEXT,
                    workflow_execution_id TEXT);
                CREATE INDEX IF NOT EXISTS executions_key ON executions(semantic_key, created_time);
                CREATE TABLE IF NOT EXISTS artifacts (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS cache_entries (semantic_key TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS workflows (id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL,
                    created_time TEXT NOT NULL, payload TEXT NOT NULL, goal_executions TEXT);
            """)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def record_attempt(self, attempt, task):
        with self.connection() as db:
            db.execute("INSERT OR REPLACE INTO attempts VALUES(?,?,?,?,?,?,?,?,?)",
                (attempt.id, attempt.execution_id, attempt.tool, attempt.version, attempt.config_id,
                 task.property.kind, size_bucket(task), attempt.end_time, json.dumps(to_data(attempt))))
        self.stats_for(attempt.tool, task, attempt.version, attempt.config_id)

    def stats_for(self, tool, task, version, config_id):
        params = (tool, task.property.kind, size_bucket(task), version, config_id)
        with self.connection() as db:
            rows = db.execute("SELECT payload FROM attempts WHERE tool=? AND property_kind=? AND size_bucket=? AND version=? AND config_id=? ORDER BY end_time", params).fetchall()
            if not rows:
                return None
            attempts = [json.loads(row[0]) for row in rows]
            completed = [a for a in attempts if a["status"] == "COMPLETED"]
            safe = sum(a["verdict"] == "SAFE" for a in completed)
            unsafe = sum(a["verdict"] == "UNSAFE" for a in completed)
            measured = [a["wall_time_sec"] for a in attempts if a["status"] != "CANCELLED"]
            measured = measured or [a["wall_time_sec"] for a in attempts]
            stats = RuntimeStats(tool, task.property.kind, size_bucket(task), len(attempts), safe + unsafe,
                safe, unsafe, sum(a["verdict"] == "UNKNOWN" for a in attempts),
                sum(a["status"] == "TIMEOUT" for a in attempts), statistics.mean(measured),
                statistics.median(measured), measured[-1])
            db.execute("INSERT OR REPLACE INTO runtime_stats VALUES(?,?,?,?,?,?)", (*params, json.dumps(to_data(stats))))
            return stats

    def record_artifact(self, artifact):
        with self.connection() as db:
            db.execute("INSERT OR IGNORE INTO artifacts VALUES(?,?)", (artifact.id, json.dumps(to_data(artifact))))

    def artifact(self, artifact_id):
        with self.connection() as db:
            row = db.execute("SELECT payload FROM artifacts WHERE id=?", (artifact_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def start_execution(self, execution_id, task, optimization, workflow_execution_id=None):
        from .model import LogicalPlan
        from .runtime.process import utc_now
        with self.connection() as db:
            db.execute("INSERT OR IGNORE INTO tasks VALUES(?,?)", (task.semantic_key, json.dumps(to_data(task))))
            db.execute("INSERT INTO executions VALUES(?,?,?,?,?,?,?,NULL,NULL,NULL,?)", (
                execution_id, task.semantic_key, task.id, utc_now(), json.dumps(to_data(LogicalPlan(task))),
                json.dumps(to_data(optimization.physical_plan)), json.dumps(to_data(optimization)), workflow_execution_id))

    def finish_execution(self, report, artifacts):
        with self.connection() as db:
            db.execute("UPDATE executions SET result=?, events=?, artifacts=? WHERE id=?", (
                json.dumps(to_data(report.result)), json.dumps(to_data(report.events)),
                json.dumps(list(artifacts)), report.result.execution_id))

    def show(self, execution_id):
        with self.connection() as db:
            row = db.execute("SELECT * FROM executions WHERE id=?", (execution_id,)).fetchone()
            if not row:
                raise KeyError(f"Unknown execution: {execution_id}")
            attempts = db.execute("SELECT payload FROM attempts WHERE execution_id=? ORDER BY end_time", (execution_id,)).fetchall()
        record = dict(row)
        for field in ("logical_plan", "physical_plan", "optimization", "result", "events", "artifacts"):
            record[field] = json.loads(record[field]) if record[field] else None
        record["attempts"] = [json.loads(a[0]) for a in attempts]
        record["artifact_records"] = [self.artifact(a) for a in record["artifacts"] or []]
        return record

    def history(self, limit=20):
        with self.connection() as db:
            rows = db.execute("SELECT id,goal_id,created_time,result FROM executions ORDER BY created_time DESC LIMIT ?", (limit,)).fetchall()
        return [{**dict(row), "result": json.loads(row["result"]) if row["result"] else None} for row in rows]

    def definitive_evidence(self, semantic_key):
        with self.connection() as db:
            rows = db.execute("SELECT payload FROM attempts WHERE json_extract(payload,'$.semantic_key')=? AND json_extract(payload,'$.status')='COMPLETED' AND json_extract(payload,'$.verdict') IN ('SAFE','UNSAFE')", (semantic_key,)).fetchall()
        return tuple(json.loads(row[0]) for row in rows)

    def start_workflow(self, execution_id, workflow):
        from .runtime.process import utc_now
        with self.connection() as db:
            db.execute("INSERT INTO workflows VALUES(?,?,?,?,NULL)", (
                execution_id, workflow.workflow_id, utc_now(), json.dumps(to_data(workflow))))

    def finish_workflow(self, execution_id, goals):
        with self.connection() as db:
            db.execute("UPDATE workflows SET goal_executions=? WHERE id=?", (
                json.dumps([g.report.result.execution_id for g in goals]), execution_id))
