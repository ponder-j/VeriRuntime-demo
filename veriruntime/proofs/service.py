import json
import hashlib
import os
from pathlib import Path
import time
import uuid

from veriruntime.model import to_data, Verdict
from veriruntime.runtime.process import Cancellation, MemoryMonitor, execute
from veriruntime.tools.rocq import RocqAdapter


def _read_result(path, attempt):
    result = {'status': 'UNKNOWN', 'reason': attempt.diagnostic_code or 'proof_tool_unavailable'}
    if path.exists():
        try:
            report = json.loads(path.read_text())
            if not isinstance(report, dict) or report.get('status') not in ('VERIFIED', 'REJECTED'):
                raise ValueError('Invalid proof result')
            result = report
        except (ValueError, OSError):
            result['reason'] = 'incomplete_proof_report'
    if result['status'] == 'VERIFIED' and not (
            attempt.status.value == 'COMPLETED' and attempt.verdict == Verdict.SAFE):
        result.update(status='UNKNOWN', reason='proof_execution_not_completed')
    result['code_verdict'] = None
    return result


def _artifact_record(path):
    with Path(path).open('rb') as stream:
        sha256 = hashlib.file_digest(stream, 'sha256').hexdigest()
    return {'path': path, 'size_bytes': Path(path).stat().st_size, 'sha256': sha256}


async def check_proof(task, data_dir='/data/proofs', cancellation=None, execution_backend=None):
    adapter = RocqAdapter()
    if os.environ.get('VRUN_BACKEND', 'native') == 'docker':
        from veriruntime.tools.docker import DockerAdapter
        adapter = DockerAdapter(adapter)
    root = Path(data_dir).resolve() / uuid.uuid4().hex
    root.mkdir(parents=True)
    (root / 'logical-proof.json').write_text(json.dumps(to_data(task), indent=2))
    attempt = await execute(adapter, task, root / uuid.uuid4().hex, root.name,
        time.monotonic() + task.budget.wall_time_sec, Cancellation(), MemoryMonitor(task.budget.memory_mb),
        cancellation, execution_backend)
    workspace = Path(attempt.workspace)
    result = _read_result(workspace / 'proof-result.json', attempt)
    attempt_data = to_data(attempt)
    attempt_data['artifact_verdict'] = 'VALID' if result['status'] == 'VERIFIED' else 'UNVERIFIED'
    del attempt_data['verdict']
    result.update(execution_id=root.name, attempt=attempt_data, tool_profile=to_data(adapter.profile()),
                  artifacts=[str(p) for _, p in adapter.collect_artifacts(workspace)])
    result['artifact_records'] = [_artifact_record(path) for path in result['artifacts']]
    (root / 'result.json').write_text(json.dumps(result, indent=2))
    return result
