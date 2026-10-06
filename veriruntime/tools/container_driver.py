"""Supervised bridge. Killing this process also forces removal of the named worker."""
import json
from pathlib import Path
import signal
import sys

from veriruntime.tools.docker_api import DockerEngine, EngineError, worker_config


def main():
    workspace = Path(sys.argv[1]).resolve()
    manifest = json.loads((workspace / 'container-request.json').read_text())
    engine = DockerEngine()
    name = manifest['name']
    created = manifest.get('prepared', False)
    exit_code = 125
    def terminate(*_):
        raise InterruptedError('Worker cancelled')
    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)
    try:
        if not created:
            engine.create(name, worker_config(manifest['image'], ['run', manifest['tool']],
                manifest['memory_mb'], manifest['mount']))
            created = True
        engine.request('POST', f'/containers/{name}/start')
        state = engine.request('POST', f'/containers/{name}/wait', timeout=manifest['timeout_sec'])
        exit_code = state['StatusCode']
    except InterruptedError:
        exit_code = 143
    finally:
        # Runtime also removes this deterministic name after group cleanup, covering
        # SIGKILL, failed startup, and a signal between create and its response.
        if created:
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            signal.signal(signal.SIGINT, signal.SIG_IGN)
            try:
                info = engine.request('GET', f'/containers/{name}/json')
                if info['State']['Running']:
                    engine.request('POST', f'/containers/{name}/kill')
                    engine.request('POST', f'/containers/{name}/wait')
                    info = engine.request('GET', f'/containers/{name}/json')
                (workspace / 'container-state.json').write_text(json.dumps({
                    'id': info['Id'], 'image': info['Image'], 'state': info['State'],
                    'host_config': info['HostConfig'], 'mounts': info['Mounts']}, indent=2))
                stdout, stderr = engine.logs(name)
                sys.stdout.buffer.write(stdout)
                sys.stderr.buffer.write(stderr)
                sys.stdout.flush()
                sys.stderr.flush()
            except EngineError as exc:
                if exc.status != 404:
                    raise
            finally:
                engine.remove(name)
    return exit_code


if __name__ == '__main__':
    raise SystemExit(main())
