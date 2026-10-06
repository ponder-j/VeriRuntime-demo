"""Small Docker Engine client over a Unix socket, using only the standard library."""
import http.client
import json
import socket
from urllib.parse import quote


class EngineError(RuntimeError):
    def __init__(self, status, message):
        self.status = status
        super().__init__(f'Docker HTTP {status}: {message}')


class UnixConnection(http.client.HTTPConnection):
    def __init__(self, socket_path, timeout):
        super().__init__('localhost', timeout=timeout)
        self.socket_path = socket_path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.socket_path)


class DockerEngine:
    def __init__(self, socket_path='/var/run/docker.sock'):
        self.socket_path = socket_path

    def request(self, method, path, body=None, timeout=20, raw=False):
        connection = UnixConnection(self.socket_path, timeout)
        try:
            connection.request(method, '/v1.45' + path,
                body=json.dumps(body) if body is not None else None,
                headers={'Content-Type': 'application/json'})
            response = connection.getresponse()
            data = response.read()
            if response.status >= 400:
                raise EngineError(response.status, data.decode(errors='replace'))
            return data if raw else json.loads(data) if data else None
        finally:
            connection.close()

    def inspect_image(self, image):
        return self.request('GET', '/images/' + quote(image, safe='') + '/json')

    def create(self, name, config):
        return self.request('POST', '/containers/create?name=' + quote(name, safe=''), config)['Id']

    def remove(self, name):
        try:
            self.request('DELETE', '/containers/' + name + '?force=true&v=true')
        except EngineError as exc:
            if exc.status != 404:
                raise

    def logs(self, name):
        data = self.request('GET', f'/containers/{name}/logs?stdout=true&stderr=true', raw=True)
        streams = {1: bytearray(), 2: bytearray()}
        offset = 0
        while offset < len(data):
            if offset + 8 > len(data):
                raise ValueError('Truncated Docker log frame')
            stream = data[offset]
            size = int.from_bytes(data[offset + 4:offset + 8], 'big')
            offset += 8
            if stream not in streams or offset + size > len(data):
                raise ValueError('Invalid Docker log frame')
            streams[stream].extend(data[offset:offset + size])
            offset += size
        return bytes(streams[1]), bytes(streams[2])


def worker_config(image, command, memory_mb, mount=None):
    host = {'NetworkMode': 'none', 'ReadonlyRootfs': True, 'CapDrop': ['ALL'],
            'SecurityOpt': ['no-new-privileges'], 'PidsLimit': 128,
            'Memory': memory_mb * 1024 * 1024, 'MemorySwap': memory_mb * 1024 * 1024,
            'NanoCpus': 1_000_000_000, 'Init': True,
            'Tmpfs': {'/tmp': 'rw,nosuid,nodev,size=64m,mode=1777'}}
    if mount:
        host['Mounts'] = [mount]
    return {'Image': image, 'Cmd': command, 'User': '10001:10001',
            'WorkingDir': '/work' if mount else '/tmp',
            'Env': ['HOME=/tmp/home', 'TMPDIR=/tmp', 'LC_ALL=C'],
            'Labels': {'org.veriruntime.managed': 'attempt'},
            'HostConfig': host}
