from importlib.resources import files
import json

from jsonschema import Draft202012Validator


def test_packaged_schemas_are_valid_and_available():
    root=files('veriruntime.dsl')
    for name in ('verification-task.schema.json','verification-workflow.schema.json'):
        Draft202012Validator.check_schema(json.loads(root.joinpath(name).read_text()))
