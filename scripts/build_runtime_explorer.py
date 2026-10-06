"""Inject real evidence into the standalone offline viewer; no runtime server."""
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
evidence = json.loads((root / 'docs/dispatch-evidence.json').read_text(encoding='utf-8'))
template = (root / 'docs/runtime-explorer.template.html').read_text(encoding='utf-8')
payload = json.dumps(evidence, ensure_ascii=False, separators=(',', ':')).replace('</', '<\\/')
(root / 'docs/runtime-explorer.html').write_text(template.replace('__DISPATCH_EVIDENCE__', payload), encoding='utf-8', newline='\n')
