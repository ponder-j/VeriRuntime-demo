"""Eva is a sound overapproximation: alarms are UNKNOWN, never counterexamples."""
import json
from pathlib import Path
import re
import sys

from veriruntime.model import ExecutionStatus, Verdict
from .base import ParsedResult, ToolAdapter, materialize


class FramaCAdapter(ToolAdapter):
    name = family = 'framac'
    version_flags = ('-version',)
    help_flags = ('-eva-help',)
    required_flags = ('-eva-slevel', '-eva')
    config = {'adapter': 1, 'analysis': 'eva', 'slevel': 256,
              'acsl_from_source': 'reject', 'alarms': 'unknown', 'safe_requires_all_properties_valid': True}
    estimated_memory_mb = 512
    prior_runtime_sec = 2.0

    def accepts_program(self, task):
        # Existing C DSL ignores ACSL. Letting Eva assume those contracts would
        # change the fixed proposition relative to other families.
        return task.entry == 'main' and not any(re.search(r'/\*\s*@|//\s*@', f.content) for f in task.program.files)

    def build_command(self, task, workspace):
        sources = materialize(task.program, workspace)
        manifest = workspace / 'framac-command.json'
        manifest.write_text(json.dumps({'argv': [self.profile().path, '-eva', '-eva-slevel',
            str(self.config['slevel']), '-main', task.entry, '-machdep',
            'x86_64' if task.semantics.data_model == 'LP64' else 'x86_32',
            '-cpp-extra-args=-std=' + task.semantics.c_standard,
            '-report-csv', str(workspace / 'properties.csv'), *sources, '-then', '-report']}, indent=2))
        return (sys.executable, '-m', 'veriruntime.tools.frama_driver', str(manifest))

    def parse_result(self, stdout, stderr, exit_code):
        if exit_code:
            return ParsedResult(Verdict.UNKNOWN, ExecutionStatus.ERROR, 'Frama-C analysis error', 'verifier_error')
        try:
            report = json.loads(stdout)
            if report['contract'] != 'framac-eva-report-v1' or report['exit_code'] != 0:
                raise ValueError('Invalid report')
            statuses = report['properties']
            if report['complete'] and report['has_assertion'] and statuses and all(p['valid'] for p in statuses):
                return ParsedResult(Verdict.SAFE, message='Eva completely validates all reported C properties')
            return ParsedResult(Verdict.UNKNOWN, message='Eva alarms or incomplete property coverage; no concrete counterexample',
                                diagnostic_code='inconclusive_verifier')
        except (KeyError, ValueError, TypeError):
            return ParsedResult(Verdict.UNKNOWN, ExecutionStatus.ERROR, 'Malformed Eva property report', 'verifier_error')

    def collect_artifacts(self, workspace):
        return tuple((kind, workspace / name) for kind, name in (
            ('ADAPTER_COMMAND', 'framac-command.json'), ('PROPERTY_REPORT', 'properties.csv'),
            ('TOOL_OUTPUT', 'framac.stdout.log'), ('TOOL_OUTPUT', 'framac.stderr.log')) if (workspace / name).exists())
