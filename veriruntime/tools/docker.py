"""Container transport preserving native adapters, evidence and scheduler contracts."""
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
import uuid

from veriruntime.model import ExecutionStatus, ToolProfile, Verdict, digest, to_data
from .base import ParsedResult, ToolAdapter
from .docker_api import DockerEngine, worker_config


class DockerAdapter(ToolAdapter):
    def __init__(self, native):
        super().__init__(native.root)
        self.native = native
        self.name = self.family = native.name
        self.image = os.environ.get(f'VRUN_{self.name.upper()}_IMAGE', f'veriruntime/{self.name}:local')
        self.engine = DockerEngine()

    def detect(self):
        name = 'vrun-probe-' + uuid.uuid4().hex
        try:
            self.image_id = self.engine.inspect_image(self.image)['Id']
            self.engine.create(name, worker_config(self.image_id, ['probe', self.name], 256))
            self.engine.request('POST', f'/containers/{name}/start')
            state = self.engine.request('POST', f'/containers/{name}/wait', timeout=45)
            stdout, stderr = self.engine.logs(name)
            if state['StatusCode']:
                raise RuntimeError(stderr.decode(errors='replace'))
            data = json.loads(stdout)
            for key in ('languages', 'properties', 'c_standards', 'data_models'):
                data[key] = tuple(data[key])
            self._profile = replace(ToolProfile(**data), path=self.image_id,
                config_id=digest({'transport': 'docker-attempt-v2', 'image': self.image_id,
                                  'adapter': data['config_id']}))
        except (OSError, ValueError, RuntimeError) as exc:
            self._profile = ToolProfile(self.name, self.family, False, '', None,
                diagnostic=f'{self.image}: {exc}')
        finally:
            # Missing optional images never create a container.
            if hasattr(self, 'image_id'):
                self.engine.remove(name)
        return self._profile

    def supports(self, task):
        return super().supports(task)

    def accepts_program(self, task):
        return self.native.accepts_program(task)

    def attempt_environment(self, workspace):
        # The root-owned bridge must not pre-create the non-root worker's HOME
        # or TMPDIR. Each side gets separate, attempt-local writable directories.
        return super().attempt_environment(workspace / '.bridge')

    def build_command(self, task, workspace):
        root = Path(os.environ['VRUN_DOCKER_DATA_ROOT']).resolve()
        subpath = workspace.resolve().relative_to(root).as_posix()
        if ',' in subpath or subpath == '.':
            raise ValueError('Container attempt must be a subdirectory of the data volume')
        self.profile()
        # Only this subdirectory is mounted; the tool has no store/socket/source checkout.
        workspace.chmod(0o777)
        manifest = {'name': 'vrun-' + workspace.name, 'image': self.image_id, 'tool': self.name,
            'task': to_data(task), 'memory_mb': max(16, task.budget.memory_mb // task.budget.max_parallel),
            'timeout_sec': task.budget.wall_time_sec + 30,
            'mount': {'Type': 'volume', 'Source': os.environ['VRUN_DOCKER_VOLUME'], 'Target': '/work',
                      'VolumeOptions': {'NoCopy': True, 'Subpath': subpath}}}
        (workspace / 'container-request.json').write_text(json.dumps(manifest, indent=2))
        return (sys.executable, '-m', 'veriruntime.tools.container_driver', str(workspace))

    def cleanup(self, workspace):
        self.engine.remove('vrun-' + workspace.name)

    def prepare(self, task, workspace):
        path = workspace / 'container-request.json'
        manifest = json.loads(path.read_text())
        self.engine.create(manifest['name'], worker_config(manifest['image'], ['run', self.name],
            manifest['memory_mb'], manifest['mount']))
        manifest['prepared'] = True
        path.write_text(json.dumps(manifest, indent=2))

    def parse_result(self, stdout, stderr, exit_code):
        return self.native.parse_result(stdout, stderr, exit_code)

    def parse_attempt_result(self, stdout, stderr, exit_code, workspace):
        path = workspace / 'container-state.json'
        if path.exists() and json.loads(path.read_text())['state'].get('OOMKilled'):
            return ParsedResult(Verdict.UNKNOWN, ExecutionStatus.OOM,
                'Verifier container exceeded its memory limit', 'out_of_memory')
        return self.parse_result(stdout, stderr, exit_code)

    def collect_artifacts(self, workspace):
        records = [(kind, workspace / name) for kind, name in (
            ('CONTAINER_REQUEST', 'container-request.json'), ('CONTAINER_STATE', 'container-state.json'),
            ('NATIVE_COMMAND', 'native-command.json'), ('NATIVE_BACKEND_OUTCOME', 'native-outcome.json'))
            if (workspace / name).exists()]
        return tuple(records) + self.native.collect_artifacts(workspace)
