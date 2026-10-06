"""Export bounded real execution records for an offline, self-contained HTML page."""
import json
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
source = json.loads(Path('/data/acceptance/latest/acceptance.json').read_text())
scenarios = {}
for case in ('safe', 'unsafe'):
    request = json.loads((ROOT / f'examples/tasks/{case}_assert.json').read_text())
    request['requirements']['min_confirmations'] = source['confirmations']
    for mode in ('first', 'repeat'):
        execution = source['records'][case][mode]
        attempts = []
        for attempt in execution['report']['attempts']:
            workspace = Path(attempt['workspace'])
            native = workspace / 'native-command.json'
            state = workspace / 'container-state.json'
            inner = workspace / 'adapter-command.json'
            attempts.append({**attempt,
                'native_command': json.loads(native.read_text())['argv'] if native.exists() else [],
                'container': json.loads(state.read_text()) if state.exists() else {},
                'compilation': json.loads(inner.read_text()) if inner.exists() else None})
        scenarios[f'{case}-{mode}'] = {'request': request, 'execution': execution, 'attempts': attempts,
            'program': (ROOT / f'examples/c/{case}_assert.c').read_text()}
workflow = source['records']['workflow']
scenarios['workflow'] = {'request': json.loads((ROOT / 'examples/tasks/assertion_workflow.json').read_text()),
                        'execution': workflow}
unsupported_request = json.loads((ROOT / 'examples/tasks/safe_assert.json').read_text())
unsupported_request['property']['kind'] = 'memory_safety'
scenarios['unsupported'] = {'request': unsupported_request, 'execution': source['records']['unsupported'], 'attempts': []}
payload = {'generated_at_utc': datetime.now(timezone.utc).isoformat(), 'platform': source['platform'],
           'profiles': source['profiles'], 'scenarios': scenarios,
           'checks': {k: v for k, v in source.items() if k not in ('records', 'profiles')}}
Path('/data/acceptance/latest/dispatch-evidence.json').write_text(json.dumps(payload, indent=2, ensure_ascii=False))
print('/data/acceptance/latest/dispatch-evidence.json')
