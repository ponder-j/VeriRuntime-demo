from dataclasses import replace
import json
from pathlib import Path

import pytest
from veriruntime.dsl import DSLValidationError, load_task
from veriruntime.model import Requirements, Semantics, VerificationProperty

EXAMPLES = Path(__file__).resolve().parents[1] / "examples/tasks"


@pytest.fixture
def task_file(tmp_path):
    doc = json.loads((EXAMPLES / "safe_assert.json").read_text())
    doc["task"]["sources"] = ["main.c"]
    (tmp_path / "main.c").write_text("#include <assert.h>\nint main(void){assert(1);return 0;}\n")
    path = tmp_path / "task.json"
    path.write_text(json.dumps(doc))
    return path


def test_same_input_and_immutable_snapshot(task_file):
    task = load_task(task_file)
    assert task.semantic_key == load_task(task_file).semantic_key
    (task_file.parent / "main.c").write_text("int main(void){return 1;}")
    assert task.semantic_key != load_task(task_file).semantic_key
    assert "assert(1)" in task.program.files[0].content


@pytest.mark.parametrize("field,value", [
    ("property", VerificationProperty("memory_safety")),
    ("semantics", Semantics(data_model="ILP32")),
    ("requirements", Requirements(2)), ("entry", "other")])
def test_semantic_changes_change_key(task_file, field, value):
    task = load_task(task_file)
    assert task.semantic_key != replace(task, **{field: value}).semantic_key


def test_budget_and_label_do_not_change_proposition(task_file):
    task = load_task(task_file)
    assert task.semantic_key == replace(task, id="label", budget=replace(task.budget, max_parallel=1)).semantic_key


@pytest.mark.parametrize("change", [
    lambda d: d.update(tool="cbmc"),
    lambda d: d["task"].update(strategy="parallel"),
    lambda d: d["property"].update(kind="typo"),
    lambda d: d["budget"].update(wall_time_sec=0),
    lambda d: d["requirements"].update(min_confirmations=True),
    lambda d: d["task"].update(sources=["missing.c"]),
    lambda d: d["task"].update(entry="main; echo injected"),
])
def test_invalid_requests(task_file, change):
    doc = json.loads(task_file.read_text())
    change(doc)
    task_file.write_text(json.dumps(doc))
    with pytest.raises(DSLValidationError):
        load_task(task_file)


def test_local_headers_and_multiple_sources(task_file):
    base = task_file.parent
    (base / "main.c").write_text('#include "local.h"\nint main(void){return helper();}')
    (base / "local.h").write_text("int helper(void);")
    (base / "helper.c").write_text("int helper(void){return 0;}")
    doc = json.loads(task_file.read_text())
    doc["task"]["sources"].append("helper.c")
    task_file.write_text(json.dumps(doc))
    task = load_task(task_file)
    assert len(task.program.sources) == 2
    assert len(task.program.files) == 3
    (base / "local.h").write_text("int helper(void);\n#define NEW 1")
    assert task.semantic_key != load_task(task_file).semantic_key


def test_macro_include_fails_closed(task_file):
    (task_file.parent / "main.c").write_text('#define HEADER "live.h"\n#include HEADER\n')
    with pytest.raises(DSLValidationError, match="nonliteral"):
        load_task(task_file)


def test_duplicate_json_keys_rejected(task_file):
    task_file.write_text('{"version":"0.1","version":"0.2"}')
    with pytest.raises(DSLValidationError, match="Duplicate"):
        load_task(task_file)


@pytest.mark.parametrize('code', ['int main(void){return __TIME__[0];}', '#include_next "live.h"', '%:include "live.h"'])
def test_nonreplayable_inputs_rejected(task_file, code):
    (task_file.parent/'main.c').write_text(code)
    with pytest.raises(DSLValidationError):
        load_task(task_file)


def test_spliced_include_is_snapshotted(task_file):
    (task_file.parent/'local.h').write_text('int helper(void);')
    (task_file.parent/'main.c').write_text('#in\\\nclude "local.h"\nint main(void){return 0;}')
    assert len(load_task(task_file).program.files) == 2
