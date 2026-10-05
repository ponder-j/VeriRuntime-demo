import io
import tarfile
import importlib.util
from pathlib import Path


def test_read_only_bottle_can_be_reinstalled(tmp_path):
    path = Path(__file__).resolve().parents[1] / "scripts/bootstrap_verifiers.py"
    spec = importlib.util.spec_from_file_location("bootstrap", path)
    bootstrap = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bootstrap)
    archive = tmp_path / "bottle.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        member = tarfile.TarInfo("tool/1/bin/verifier")
        member.size = 4
        member.mode = 0o555
        bundle.addfile(member, io.BytesIO(b"tool"))
    package = {"format": "tar.gz"}
    bootstrap.extract(archive, package, tmp_path / "installed")
    bootstrap.extract(archive, package, tmp_path / "installed")
    assert (tmp_path / "installed/tool/1/bin/verifier").read_bytes() == b"tool"
