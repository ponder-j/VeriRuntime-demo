import hashlib
from importlib.resources import files
import json
from pathlib import Path

from veriruntime.dsl import DSLValidationError, _read, _validate
from veriruntime.model import Budget, ProgramFile, ProgramSnapshot
from .model import ProofTask


def load_proof(path):
    path, doc = _read(path)
    definition = json.loads(files('veriruntime.dsl').joinpath('verification-proof.schema.json').read_text())
    _validate(doc, definition)
    goal = doc['goal']
    paths = [goal['statement']['file'], *goal['proof']['files']]
    if len(set(paths)) != len(paths) or 'Binding.v' in paths:
        raise DSLValidationError('Proof sources require distinct modules; Binding is reserved')
    snapshot = []
    for name in paths:
        source = path.parent / name
        if source.is_symlink():
            raise DSLValidationError('Proof source symlinks are unsupported')
        try:
            content = source.read_bytes().decode('utf-8')
        except (OSError, UnicodeError) as exc:
            raise DSLValidationError(f'Cannot read proof source {name}: {exc}') from exc
        snapshot.append(ProgramFile(name, content, hashlib.sha256(content.encode()).hexdigest()))
    namespace = goal['namespace']
    for symbol, modules in ((goal['statement']['symbol'], [Path(paths[0]).stem]),
                            (goal['proof']['symbol'], [Path(p).stem for p in paths[1:]])):
        if not any(symbol.startswith(namespace + '.' + module + '.') for module in modules):
            raise DSLValidationError('Symbols must refer to the declared statement/proof modules')
    return ProofTask(goal['id'], paths[0], goal['statement']['symbol'], tuple(paths[1:]),
        goal['proof']['symbol'], namespace, ProgramSnapshot(tuple(snapshot), tuple(paths)), Budget(**doc['budget']))
