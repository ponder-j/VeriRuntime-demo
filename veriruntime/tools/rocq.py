"""Rocq checks proof artifacts; it is never a C verification family."""
from dataclasses import replace
from pathlib import Path
import json
import sys

from veriruntime.model import ExecutionStatus, Verdict, to_data
from .base import ParsedResult, ToolAdapter, materialize


class RocqAdapter(ToolAdapter):
    name = family = 'rocq'
    required_flags = ('compile', 'check')
    config = {'adapter': 1, 'proofs': 'upstream', 'assumptions': 'closed', 'kernel_recheck': True}
    estimated_memory_mb = 256

    def detect(self):
        self._profile = replace(super().detect(), languages=('Rocq',), properties=('proof_check',),
                                c_standards=(), data_models=())
        return self._profile

    def candidates(self):
        import os
        import shutil
        override = os.environ.get('VRUN_ROCQ')
        return [Path(override or shutil.which('rocq') or '/opt/rocq/bin/rocq')]

    def supports(self, task):
        return self.profile().available and getattr(task, 'language', None) == 'Rocq'

    def build_command(self, task, workspace):
        materialize(task.program, workspace)
        manifest = workspace / 'proof-command.json'
        manifest.write_text(json.dumps({'task': to_data(task), 'compiler': self.profile().path}, indent=2))
        return (sys.executable, '-m', 'veriruntime.proofs.worker', str(manifest))

    def parse_result(self, stdout, stderr, exit_code):
        if exit_code == 0 and stdout.splitlines()[-1:] == ['VRUN_PROOF_KERNEL_CHECKED']:
            return ParsedResult(Verdict.SAFE, message='Supplied proof kernel-checked against supplied statement; no C verdict')
        return ParsedResult(Verdict.UNKNOWN, ExecutionStatus.ERROR,
                            'Proof not accepted; inspect proof-result.json', 'proof_not_accepted')

    def collect_artifacts(self, workspace):
        return tuple(('PROOF_ARTIFACT', p) for p in workspace.glob('proof-*.json')) + tuple(
            ('PROOF_LOG', p) for p in (workspace / 'proof-build').glob('*.log')) + tuple(
            ('KERNEL_OBJECT', p) for p in (workspace / 'proof-build').glob('*.vo'))
