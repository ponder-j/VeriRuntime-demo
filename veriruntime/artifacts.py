"""Content-addressed immutable bytes, with per-attempt provenance references."""
import hashlib
from pathlib import Path
import uuid

from veriruntime.model import Artifact, digest


class ArtifactStore:
    def __init__(self, data_dir, execution_store):
        self.root = Path(data_dir).resolve() / "artifacts"
        self.root.mkdir(parents=True, exist_ok=True)
        self.store = execution_store

    def put_bytes(self, content: bytes, kind: str, attempt_id=None) -> Artifact:
        checksum = hashlib.sha256(content).hexdigest()
        target = self.root / checksum
        if not target.exists() or hashlib.sha256(target.read_bytes()).hexdigest() != checksum:
            temporary = self.root / f".{uuid.uuid4().hex}.tmp"
            temporary.write_bytes(content)
            temporary.chmod(0o444)
            temporary.replace(target)
        artifact = Artifact(digest({"hash": checksum, "kind": kind, "attempt": attempt_id}),
                            kind, str(target), len(content), checksum, attempt_id)
        self.store.record_artifact(artifact)
        return artifact

    def put_file(self, path, kind, attempt_id=None):
        return self.put_bytes(Path(path).read_bytes(), kind, attempt_id)

    def valid(self, artifact):
        try:
            path = Path(artifact["path"]).resolve()
            return (path.is_relative_to(self.root) and path.stat().st_size == artifact["size_bytes"] and
                    hashlib.sha256(path.read_bytes()).hexdigest() == artifact["sha256"])
        except OSError:
            return False
