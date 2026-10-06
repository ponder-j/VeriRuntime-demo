from dataclasses import dataclass
import re

from veriruntime.model import Budget, ProgramFile, ProgramSnapshot, digest


@dataclass(frozen=True)
class ProofTask:
    id: str
    statement_file: str
    statement_symbol: str
    proof_files: tuple[str, ...]
    proof_symbol: str
    namespace: str
    program: ProgramSnapshot
    budget: Budget
    language: str = 'Rocq'

    def __post_init__(self):
        if self.language != 'Rocq' or not self.id or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', self.namespace):
            raise ValueError('Invalid fixed proof artifact goal')
        names = (self.statement_file, *self.proof_files)
        if (not self.proof_files or len(set(names)) != len(names) or 'Binding.v' in names or
                any(not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*\.v', name) for name in names) or
                set(names) != {f.logical_path for f in self.program.files} or
                set(names) != set(self.program.sources) or self.budget.max_parallel != 1 or self.budget.memory_mb < 256):
            raise ValueError('Proof artifacts must be a closed flat source set with a sequential budget')
        for symbol, files in ((self.statement_symbol, (self.statement_file,)), (self.proof_symbol, self.proof_files)):
            if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+', symbol) or not any(
                    symbol.startswith(self.namespace + '.' + file[:-2] + '.') for file in files):
                raise ValueError('Proof symbols must belong to the declared source modules')

    @property
    def semantic_key(self):
        return digest({'contract': 'rocq-artifact-proof-v1', 'statement_file': self.statement_file,
            'statement_symbol': self.statement_symbol, 'proof_files': self.proof_files,
            'proof_symbol': self.proof_symbol, 'namespace': self.namespace,
            'program': self.program.identity(), 'policy': 'closed-kernel-checked'})


def decode_proof(data):
    program = data['program']
    return ProofTask(data['id'], data['statement_file'], data['statement_symbol'],
        tuple(data['proof_files']), data['proof_symbol'], data['namespace'],
        ProgramSnapshot(tuple(ProgramFile(**f) for f in program['files']), tuple(program['sources'])),
        Budget(**data['budget']))
