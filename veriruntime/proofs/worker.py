"""Compile only supplied proofs, bind their types, then recheck all compiled libraries."""
import json
from pathlib import Path
import re
import subprocess
import sys

from .model import decode_proof


def strip_comments(text):
    # Rocq comments nest. This is only a preliminary policy gate; independent
    # kernel and assumption checks establish acceptance after compilation.
    depth, i, output = 0, 0, []
    while i < len(text):
        if text.startswith('(*', i):
            depth += 1
            i += 2
        elif depth and text.startswith('*)', i):
            depth -= 1
            i += 2
        else:
            if not depth:
                output.append(text[i])
            i += 1
    if depth:
        raise ValueError('Unclosed proof comment')
    return ''.join(output)


def main():
    manifest_path = Path(sys.argv[1])
    workspace = manifest_path.parent
    manifest = json.loads(manifest_path.read_text())
    task = decode_proof(manifest['task'])
    compiler = manifest['compiler']
    build = workspace / 'proof-build'
    build.mkdir()
    commands = []
    result = {'status': 'REJECTED', 'reason': '', 'proof_symbol': task.proof_symbol,
              'statement_symbol': task.statement_symbol, 'semantic_key': task.semantic_key,
              'code_verdict': None, 'assumptions_policy': 'closed'}

    def reject(reason, detail=''):
        result.update(reason=reason, detail=detail)
        (workspace / 'proof-result.json').write_text(json.dumps(result, indent=2))
        (workspace / 'proof-steps.json').write_text(json.dumps(commands, indent=2))
        print('VRUN_PROOF_REJECTED:' + reason, flush=True)
        return 1

    def run(args, name):
        command = [compiler, *args]
        completed = subprocess.run(command, cwd=build, capture_output=True, text=True,
                                   stdin=subprocess.DEVNULL)
        (build / (name + '.log')).write_text(completed.stdout + '\n' + completed.stderr)
        commands.append({'argv': command, 'exit_code': completed.returncode, 'log': name + '.log'})
        return completed

    for source in task.program.files:
        text = strip_comments(source.content)
        if re.search(r'\b(Admitted|Axiom|Axioms|Parameter|Parameters|Conjecture|Drop|Load)\b|'
                     r'Declare\s+ML\s+Module|Unset\s+(Guard|Universe|Positivity)\s+Checking|'
                     r'bypass_check', text):
            return reject('untrusted_axioms_or_extensions', source.logical_path)
        (build / source.logical_path).write_bytes(source.content.encode())
    namespace = task.namespace
    sources = [task.statement_file, *task.proof_files]
    dependencies = run(['dep', '-sort', '-Q', str(build), namespace, *sources], 'dependencies')
    order = dependencies.stdout.split()
    if dependencies.returncode or set(order) != set(sources) or len(order) != len(sources):
        return reject('proof_dependency_error', dependencies.stdout + dependencies.stderr)
    for index, source in enumerate(order):
        compiled = run(['compile', '-q', '-Q', str(build), namespace, source], f'compile-{index}')
        if compiled.returncode:
            return reject('proof_compilation_error', compiled.stderr[-2000:])
    # This creates a typed binding, not a proof. The proof must already exist in
    # upstream code and inhabit this exact supplied statement.
    binding = ('From ' + namespace + ' Require Import ' + ' '.join(Path(p).stem for p in sources) + '.\n'
        + 'Definition required_statement : Prop := ' + task.statement_symbol + '.\n'
        + 'Definition bound_proof : required_statement := ' + task.proof_symbol + '.\n'
        + 'Set Printing All Assumptions.\nPrint Assumptions bound_proof.\n')
    (build / 'Binding.v').write_text(binding)
    bound = run(['compile', '-q', '-Q', str(build), namespace, 'Binding.v'], 'binding')
    if bound.returncode:
        return reject('proof_type_mismatch', bound.stderr[-2000:])
    audit = run(['repl', '-q', '-batch', '-Q', str(build), namespace, '-l', 'Binding.v'], 'assumptions')
    if audit.returncode or audit.stdout.strip() != 'Closed under the global context':
        return reject('untrusted_assumptions', audit.stdout + audit.stderr)
    checked = run(['check', '-o', '-Q', str(build), namespace, namespace + '.Binding'], 'kernel')
    if checked.returncode:
        return reject('kernel_recheck_failed', checked.stderr[-2000:])
    result.update(status='VERIFIED', reason='kernel_checked', kernel_rechecked=True)
    (workspace / 'proof-result.json').write_text(json.dumps(result, indent=2))
    (workspace / 'proof-steps.json').write_text(json.dumps(commands, indent=2))
    print('VRUN_PROOF_KERNEL_CHECKED', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
