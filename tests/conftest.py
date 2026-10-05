from pathlib import Path
import sys

import pytest

from veriruntime.dsl import load_task
from veriruntime.model import ToolProfile, Verdict
from veriruntime.tools import ParsedResult, ToolAdapter, ToolRegistry


class ProcessFixtureAdapter(ToolAdapter):
    """Unit tests only. Runtime knows nothing about fixture names or behavior."""
    def __init__(self, name, code, family=None):
        self.name = name
        self.family = family or name
        self.code = code
        self._profile = ToolProfile(name, self.family, True, "fixture-v1", sys.executable,
                                    estimated_memory_mb=64, config_id="fixture")

    def environment(self):
        import os
        return dict(os.environ)

    def build_command(self, task, workspace):
        return (sys.executable, "-c", self.code)

    def parse_result(self, stdout, stderr, exit_code):
        from veriruntime.model import ExecutionStatus
        if exit_code != 0:
            return ParsedResult(Verdict.UNKNOWN, ExecutionStatus.ERROR)
        return ParsedResult(Verdict(stdout.strip()) if stdout.strip() in Verdict._value2member_map_ else Verdict.UNKNOWN)


@pytest.fixture
def logical():
    from veriruntime.model import LogicalPlan
    return LogicalPlan(load_task(Path(__file__).resolve().parents[1] / "examples/tasks/safe_assert.json"))


@pytest.fixture
def fixture_registry():
    return lambda *adapters: ToolRegistry(adapters)
