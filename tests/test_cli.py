import json
from pathlib import Path

from veriruntime.cli import main

ROOT = Path(__file__).resolve().parents[1]
TASK = str(ROOT / 'examples/tasks/safe_assert.json')


def test_explain_two_levels_and_no_execution(tmp_path, capsys):
    assert main(['explain', TASK, '--data-dir', str(tmp_path)]) == 0
    output = capsys.readouterr().out
    assert 'Logical Workflow' in output and 'Physical Execution' in output
    assert 'Physical Plan' in output and 'CACHE MISS' in output
    assert not (tmp_path / 'executions').exists()


def test_parse_and_errors(capsys):
    assert main(['parse', TASK]) == 0
    result = json.loads(capsys.readouterr().out)
    assert len(result['logical_workflow']['goals']) == 1
    assert main(['validate', '/missing/task.json']) == 2
    assert 'vrun:' in capsys.readouterr().err


def test_history_and_unknown_execution(tmp_path, capsys):
    assert main(['history', '--json', '--data-dir', str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out) == []
    assert main(['show', 'absent', '--data-dir', str(tmp_path)]) == 2
    assert 'Unknown execution' in capsys.readouterr().err
