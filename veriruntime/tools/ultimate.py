"""Ultimate Automizer assertion reachability, with an equivalent assert elaboration."""
import json
from pathlib import Path
import re
import shutil
import sys

from veriruntime.model import ExecutionStatus, Verdict
from .base import ParsedResult, ToolAdapter, materialize


class UltimateAdapter(ToolAdapter):
    name = family = 'ultimate'
    required_flags = ('--spec', '--architecture', '--file', '--data', '--witness-dir')
    config = {'adapter': 1, 'assert_model': 1, 'analysis': 'automizer', 'heap_mb': 768,
              'witness_validation': False, 'float': 'unsupported', 'translation_units': 1}
    estimated_memory_mb = 1024
    prior_runtime_sec = 8.0

    def launch_prefix(self, path):
        return (sys.executable, str(path))

    def supports(self, task):
        return super().supports(task) and shutil.which('gcc') is not None

    def accepts_program(self, task):
        return (task.entry == 'main' and len(task.program.sources) == 1 and
                not any(re.search(r'\b(float|double|__VERIFIER_error|__vr_ultimate_)\b', f.content)
                        for f in task.program.files))

    def build_command(self, task, workspace):
        sources = materialize(task.program, workspace)
        headers = workspace / 'adapter-headers'
        headers.mkdir()
        (headers / 'assert.h').write_text('''#undef assert
#ifdef NDEBUG
#define assert(e) ((void)0)
#else
extern void __VERIFIER_error(void) __attribute__((noreturn));
#define assert(e) ((e) ? (void)0 : __VERIFIER_error())
#endif
''')
        spec = workspace / 'assertion.prp'
        spec.write_text('CHECK( init(main()), LTL(G ! call(__VERIFIER_error())) )\n')
        data = workspace / 'ultimate-data'
        witness = workspace / 'witness'
        data.mkdir()
        witness.mkdir()
        root = Path(self.profile().path).parent
        # The vendor launcher requests 15G. This bounded adapter-owned copy changes
        # only JVM heap size; toolchain/settings remain the pinned vendor files.
        launcher = workspace / 'Ultimate.py'
        content = Path(self.profile().path).read_text()
        if content.count('"-Xmx15G"') != 1:
            raise ValueError('Unqualified Ultimate launcher heap configuration')
        launcher.write_text(content.replace('"-Xmx15G"', f'"-Xmx{self.config["heap_mb"]}m"'))
        compiled = workspace / 'translation.i'
        manifest = workspace / 'adapter-command.json'
        manifest.write_text(json.dumps({
            'preprocessor_commands': [[shutil.which('gcc'), '-E', '-P', f'-std={task.semantics.c_standard}',
                '-m64' if task.semantics.data_model == 'LP64' else '-m32', '-I', str(headers), sources[0]]],
            'compiled_sources': [str(compiled)],
            'verifier_argv': [sys.executable, str(launcher), '--spec', str(spec), '--architecture',
                '64bit' if task.semantics.data_model == 'LP64' else '32bit', '--file', str(compiled),
                '--config', str(root / 'config'), '--data', str(data), '--witness-dir', str(witness),
                '--full-output'], 'vendor_root': str(root)}, indent=2))
        # Launcher derives ultimatedir from itself. Bind its immutable vendor root.
        launcher.write_text(launcher.read_text().replace('ultimatedir = os.path.dirname(os.path.realpath(__file__))',
            'ultimatedir = ' + repr(str(root))))
        return (sys.executable, str(Path(__file__).with_name('cpa_driver.py')), str(manifest))

    def parse_result(self, stdout, stderr, exit_code):
        if exit_code != 0:
            return ParsedResult(Verdict.UNKNOWN, ExecutionStatus.ERROR, 'Ultimate or preprocessing error', 'verifier_error')
        results = re.findall(r'^Result:\s*\n(TRUE|FALSE|UNKNOWN(?:[^\n]*))\s*$', stdout, re.M)
        if results == ['TRUE']:
            return ParsedResult(Verdict.SAFE, message='Ultimate assertion reachability proof')
        if results == ['FALSE']:
            return ParsedResult(Verdict.UNSAFE, message='Ultimate assertion counterexample (not independently witness-checked)')
        return ParsedResult(Verdict.UNKNOWN, message='Incomplete Ultimate analysis', diagnostic_code='inconclusive_verifier')

    def collect_artifacts(self, workspace):
        return tuple((kind, p) for kind, p in (
            ('ADAPTER_COMMAND', workspace / 'adapter-command.json'), ('ADAPTER_HEADER', workspace / 'adapter-headers/assert.h'),
            ('PROPERTY_SPEC', workspace / 'assertion.prp'), ('PREPROCESSED_SOURCE', workspace / 'translation.i'),
            ('TOOL_LAUNCHER', workspace / 'Ultimate.py')) if p.exists()) + tuple(
                ('TOOL_OUTPUT', p) for p in workspace.glob('*.log')) + tuple(
                ('COUNTEREXAMPLE', p) for p in workspace.glob('*.errorpath')) + tuple(
                ('COUNTEREXAMPLE', p) for p in (workspace / 'witness').glob('*') if p.is_file())
