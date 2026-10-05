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
