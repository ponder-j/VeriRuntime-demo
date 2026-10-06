"""A bounded, recorded Codex CLI invocation with structured final output."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import re
import shutil
import time
import tomllib

from jsonschema import Draft202012Validator, ValidationError

from veriruntime.model import ExecutionStatus, TerminationReason, to_data
from veriruntime.execution import ExecutionSpec, LocalExecutionBackend
from veriruntime.runtime.process import utc_now


def _user_config():
    config = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "config.toml"
    try:
        return tomllib.loads(config.read_text())
    except (OSError, ValueError):
        return {}


def default_sol_model():
    model = _user_config().get("model", "")
    if isinstance(model, str) and model.endswith("-sol"):
        return model
    return "gpt-6.1-sol"


class PlannerError(ValueError):
    def __init__(self, code, detail, record=None):
        self.code, self.record = code, record
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True)
class CodexRun:
    status: str
    model: str
    command: tuple[str, ...]
    start_time: str
    wall_time_sec: float
    exit_code: int | None
    stdout_path: str
    stderr_path: str
    answer_path: str
    usage: dict
    error: str = ""


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


class CodexPlanner:
    def __init__(self, model=None, executable="codex", timeout_sec=180):
        if isinstance(timeout_sec, bool) or not math.isfinite(timeout_sec) or not 0 < timeout_sec <= 3600:
            raise ValueError("Planner timeout must be between 0 and 3600 seconds")
        self.model = model or default_sol_model()
        self.executable, self.timeout_sec = executable, timeout_sec

    async def propose(self, prompt, schema, directory, cancellation=None):
        directory = Path(directory).resolve()
        directory.mkdir(parents=True, exist_ok=True)
        schema_path, answer = directory / "response.schema.json", directory / "answer.json"
        answer.unlink(missing_ok=True)
        stdout_path, stderr_path = directory / "codex.events.jsonl", directory / "codex.stderr.txt"
        (directory / "prompt.txt").write_text(prompt, encoding="utf-8")
        schema_path.write_text(json.dumps(schema, indent=2), encoding="utf-8")
        Draft202012Validator.check_schema(schema)
        executable = shutil.which(self.executable)
        if not executable:
            raise PlannerError("codex_not_found", f"Executable unavailable: {self.executable}")
        flags = []
        for feature in ("shell_tool", "multi_agent", "apps", "plugins", "remote_plugin"):
            flags.extend(("--disable", feature))
        for server in _user_config().get("mcp_servers", {}):
            if not re.fullmatch(r'[A-Za-z0-9_-]+', server):
                raise PlannerError("unsupported_codex_config", "MCP server names must support dotted CLI overrides")
            flags.extend(("--config", f'mcp_servers.{server}.enabled=false'))
        command = (executable, "exec", "--model", self.model, "--sandbox", "read-only",
                   "--config", 'approval_policy="never"', "--config", 'web_search="disabled"', *flags,
                   "--ephemeral", "--json", "--color", "never",
                   "--skip-git-repo-check", "--cd", str(directory),
                   "--output-schema", str(schema_path), "--output-last-message", str(answer), "-")
        (directory / "command.json").write_text(json.dumps({"argv": command, "model": self.model}, indent=2))
        start_time, start = utc_now(), time.monotonic()
        backend = LocalExecutionBackend()
        handle, waiting, outcome = None, None, None
        status = "START_FAILED"
        detail = ""
        try:
            if cancellation and cancellation.reason:
                status = "CANCELLED"
            else:
                spec = ExecutionSpec(command, str(directory), dict(os.environ), self.timeout_sec,
                    metadata={'role': 'upstream_semantic_planner', 'model': self.model},
                    stdout_path=str(stdout_path), stderr_path=str(stderr_path), stdin_data=prompt.encode('utf-8'))
                starting = asyncio.create_task(backend.start(spec))
                try:
                    handle = await asyncio.shield(starting)
                except asyncio.CancelledError:
                    handle = await starting
                    raise
                waiting = asyncio.create_task(backend.wait(handle))
                while not waiting.done():
                    if cancellation and cancellation.reason:
                        await backend.cancel(handle, TerminationReason.USER_CANCEL)
                        break
                    await asyncio.sleep(0.02)
                outcome = await waiting
                status = outcome.status.value
                if outcome.status == ExecutionStatus.COMPLETED and outcome.exit_code != 0:
                    status = 'ERROR'
        except asyncio.CancelledError:
            status = "CANCELLED"
            if handle is not None:
                await backend.cancel(handle, TerminationReason.USER_CANCEL)
        except (OSError, BrokenPipeError) as exc:
            status = "ERROR" if handle else "START_FAILED"
            detail = str(exc)
        finally:
            if handle is not None:
                await backend.cleanup(handle)
                outcome = await backend.wait(handle)
                (directory / 'backend-outcome.json').write_text(json.dumps(to_data(outcome), indent=2))
            stdout_path.touch(exist_ok=True)
            stderr_path.touch(exist_ok=True)
        usage = {}
        unexpected_actions = []
        if stdout_path.exists():
            with stdout_path.open(errors="replace") as stream:
                for line in stream:
                    try:
                        event = json.loads(line)
                        if event.get("type") == "turn.completed":
                            usage = event.get("usage", {})
                        if event.get("type") == "error":
                            detail = str(event.get("message", ""))[:2000]
                        if event.get("type") == "turn.failed":
                            detail = str(event.get("error", {}).get("message", ""))[:2000]
                            if status == "COMPLETED":
                                status = "ERROR"
                        item = event.get("item", {})
                        if item.get("type") in ("command_execution", "file_change", "mcp_tool_call", "web_search"):
                            unexpected_actions.append(item.get("type"))
                    except (ValueError, AttributeError):
                        pass
        if status not in ("COMPLETED", "CANCELLED", "TIMEOUT") and not detail:
            with stderr_path.open("rb") as stream:
                stream.seek(max(0, stderr_path.stat().st_size - 2000))
                detail = stream.read().decode("utf-8", errors="replace")
        record = CodexRun(status, self.model, command, start_time, time.monotonic() - start,
                          outcome.exit_code if outcome else None, str(stdout_path), str(stderr_path),
                          str(answer), usage, detail)
        (directory / "codex-run.json").write_text(json.dumps(to_data(record), indent=2))
        if status != "COMPLETED":
            raise PlannerError("planner_" + status.lower(), detail or f"See {stderr_path}", record)
        if unexpected_actions:
            raise PlannerError("unexpected_planner_tool_use", ", ".join(sorted(set(unexpected_actions))), record)
        try:
            if answer.stat().st_size > 1024 * 1024:
                raise ValueError("Planner answer exceeds 1 MiB")
            document = json.loads(answer.read_text(), object_pairs_hook=_unique_object)
            Draft202012Validator(schema).validate(document)
        except (OSError, ValueError, ValidationError) as exc:
            # Includes jsonschema ValidationError; never treat malformed output as evidence.
            raise PlannerError("invalid_planner_output", str(exc), record) from exc
        return document, record
