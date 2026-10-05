"""Validated declarative JSON -> immutable logical IR."""
from __future__ import annotations

import hashlib
from importlib.resources import files
import json
from pathlib import Path
import re

from jsonschema import Draft202012Validator

from veriruntime.model import (Budget, LogicalPlan, ProgramFile, ProgramSnapshot,
                               Requirements, Semantics, VerificationProperty, VerificationTask)


class DSLValidationError(ValueError):
    pass


def _unique_object(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise DSLValidationError(f"Duplicate JSON key: {key}")
        obj[key] = value
    return obj


def schema() -> dict:
    return json.loads(files(__package__).joinpath("verification-task.schema.json").read_text())


def snapshot(sources: list[str], base: Path) -> ProgramSnapshot:
    paths = tuple((base / p).resolve() for p in sources)
    if len(set(paths)) != len(paths):
        raise DSLValidationError("Duplicate source translation units")
    captured: dict[Path, str] = {}

    def capture(path: Path) -> None:
        if path in captured:
            return
        try:
            content = path.read_bytes().decode("utf-8")
        except (OSError, UnicodeError) as exc:
            raise DSLValidationError(f"Cannot snapshot source {path}: {exc}") from exc
        captured[path] = content
        # Literal local includes are recursive immutable inputs. Macro includes
        # cannot be resolved without a build configuration, so fail closed.
        stripped = re.sub(r'/\*.*?\*/|//[^\n]*', '', content, flags=re.S)
        for include in re.findall(r'^\s*#\s*include\s+([^\n]+)', stripped, re.M):
            include = include.strip()
            local = re.fullmatch(r'"([^"\n]+)"\s*', include)
            if local:
                dependency = path.parent / local.group(1)
                if Path(local.group(1)).is_absolute() or dependency.resolve() != dependency.absolute():
                    # Parent traversal is fine; symlinks/absolute include spellings
                    # cannot be replayed faithfully in an isolated workspace.
                    import os
                    normalized = Path(os.path.abspath(dependency))
                    if Path(local.group(1)).is_absolute() or normalized != dependency.resolve():
                        raise DSLValidationError(f"Absolute or symlink include unsupported: {include}")
                capture(dependency.resolve())
            elif not re.fullmatch(r'<[A-Za-z0-9_./-]+>\s*', include):
                raise DSLValidationError(f"Unsupported nonliteral include in {path}: {include}")

    for path in paths:
        capture(path)
    import os
    root = Path(os.path.commonpath([str(base), *(str(p.parent) for p in captured)]))
    entries = tuple(sorted((ProgramFile(p.relative_to(root).as_posix(), text,
                                         hashlib.sha256(text.encode("utf-8")).hexdigest())
                            for p, text in captured.items()), key=lambda f: f.logical_path))
    return ProgramSnapshot(entries, tuple(p.relative_to(root).as_posix() for p in paths))


def load_task(path: str | Path) -> VerificationTask:
    path = Path(path).resolve()
    try:
        doc = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DSLValidationError(str(exc)) from exc
    errors = sorted(Draft202012Validator(schema()).iter_errors(doc), key=lambda e: str(e.path))
    if errors:
        raise DSLValidationError("; ".join(f"{'/'.join(map(str, e.path)) or '$'}: {e.message}"
                                          for e in errors))
    task = doc["task"]
    return VerificationTask(task["id"], task["language"], task["entry"],
                            snapshot(task["sources"], path.parent),
                            VerificationProperty(**doc["property"]),
                            Semantics(**doc["semantics"]), Requirements(**doc["requirements"]),
                            Budget(**doc["budget"]), doc["version"])


def parse(path: str | Path) -> LogicalPlan:
    return LogicalPlan(load_task(path))
