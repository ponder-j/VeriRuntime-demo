"""Build-stage installer; select only needed release files, verify pinned SHA-256."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import urllib.request
import zipfile

name = sys.argv[1]
package = next(p for p in json.loads(Path('/lock.json').read_text())['packages'] if p['name'] == name)
archive = Path('/tmp/archive')
cached = Path('/downloads') / (name + ('.deb' if package['format'] == 'deb' else '.zip'))
if cached.exists():
    archive = cached
else:
    with urllib.request.urlopen(package['url'], timeout=120) as response, archive.open('wb') as target:
        while chunk := response.read(1024 * 1024):
            target.write(chunk)
assert hashlib.file_digest(archive.open('rb'), 'sha256').hexdigest() == package['sha256'], 'Release checksum mismatch'
destination = Path('/opt') / name
destination.mkdir()
if package['format'] == 'deb':
    subprocess.run(['dpkg-deb', '-x', str(archive), '/tmp/package'], check=True)
    (destination / 'cbmc').write_bytes(Path('/tmp/package/usr/bin/cbmc').read_bytes())
    (destination / 'cbmc').chmod(0o755)
else:
    with zipfile.ZipFile(archive) as bundle:
        for item in bundle.infolist():
            # ESBMC's other solvers and language frontends are unused by this adapter.
            if name == 'esbmc' and not (item.filename.endswith('/bin/esbmc') or
                    '/include/' in item.filename or '/lib/' in item.filename or '/license/' in item.filename):
                continue
            if item.filename.endswith('/'):
                continue
            target = (destination / item.filename).resolve()
            if not target.is_relative_to(destination):
                raise ValueError('Archive path escapes installation root')
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(bundle.read(item))
            target.chmod((item.external_attr >> 16 & 0o777) or 0o644)
if name in ('cbmc', 'esbmc'):
    binary = destination / ('cbmc' if name == 'cbmc' else 'release/bin/esbmc')
    subprocess.run(['strip', '--strip-unneeded', str(binary)], check=True)
