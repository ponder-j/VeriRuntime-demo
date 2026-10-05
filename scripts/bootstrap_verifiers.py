#!/usr/bin/env python3
"""Install pinned official packages locally, checking all archive hashes."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import stat
import subprocess
import sys
import tarfile
import urllib.parse
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def download(url, destination, package):
    command = ["curl", "--fail", "--silent", "--show-error", "--location", "--retry", "3",
               "--connect-timeout", "20", "--user-agent", "VeriRuntime-bootstrap/0.1"]
    if url.startswith("https://ghcr.io/"):
        if shutil.which("brew"):
            env = dict(os.environ, HOMEBREW_NO_AUTO_UPDATE="1")
            subprocess.run(["brew", "fetch", "--force-bottle", "--retry", package["name"]], env=env, check=True)
            cached = subprocess.check_output(["brew", "--cache", package["name"]], env=env, text=True).strip()
            shutil.copyfile(cached, destination)
            # main validates the pinned hash, rejecting changed formula versions.
            return
        repository = url.split("/v2/", 1)[1].split("/blobs/", 1)[0]
        token_url = "https://ghcr.io/token?" + urllib.parse.urlencode(
            {"service": "ghcr.io", "scope": f"repository:{repository}:pull"})
        token = json.loads(subprocess.check_output(command + [token_url]))["token"]
        command += ["--header", "Authorization: Bearer " + token]
    temporary = destination.with_suffix(".partial")
    subprocess.run(command + [url, "--output", str(temporary)], check=True)
    temporary.replace(destination)


def safe_path(destination, name):
    path = (destination / name).resolve()
    if not path.is_relative_to(destination.resolve()):
        raise ValueError(f"Unsafe archive path: {name}")
    return path


def make_writable(path):
    # Homebrew bottles contain read-only files. Reinstalling an identical pinned
    # package must work without altering anything outside the toolchain root.
    if path.is_file() and not path.is_symlink():
        path.chmod(path.stat().st_mode | stat.S_IWUSR)


def extract(archive, package, destination):
    destination.mkdir(parents=True, exist_ok=True)
    if package["format"] == "zip":
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.infolist():
                make_writable(safe_path(destination, member.filename))
                if stat.S_ISLNK(member.external_attr >> 16):
                    raise ValueError("Zip symlinks are not supported")
                bundle.extract(member, destination)
                permissions = (member.external_attr >> 16) & 0o777
                if permissions:
                    (destination / member.filename).chmod(permissions)
    else:
        with tarfile.open(archive) as bundle:
            for member in bundle:
                selected = package.get("select", [])
                if selected and not any(s in member.name for s in selected):
                    continue
                make_writable(safe_path(destination, member.name))
                if member.isdev() or member.isfifo():
                    raise ValueError("Special archive files are not supported")
                if member.issym():
                    safe_path(destination, str(Path(member.name).parent / member.linkname))
                if member.islnk():
                    safe_path(destination, member.linkname)
                bundle.extract(member, destination)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cpachecker", action="store_true", help="Also install optional third verifier")
    args = parser.parse_args()
    if sys.version_info < (3, 11):
        parser.error("Bootstrap requires Python 3.11+; create the project .venv first")
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        parser.error("Pinned bootstrap supports macOS ARM64 Tahoe. On Linux install official releases and set VRUN_<TOOL>; see docs/verifier-support.md.")
    lock = json.loads((ROOT / "verifiers.lock.json").read_text())
    toolchains = ROOT / ".veriruntime/toolchains"
    downloads = toolchains / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    installed = []
    for package in lock["packages"]:
        if package.get("optional") and not args.cpachecker:
            continue
        filename = f"{package['name'].replace('@', '-')}-{package['version']}.{package['format']}"
        archive = downloads / filename
        print(f"Installing {package['name']} {package['version']}", flush=True)
        if not archive.exists():
            download(package["url"], archive, package)
        with archive.open("rb") as stream:
            checksum = hashlib.file_digest(stream, "sha256").hexdigest()
        if checksum != package["sha256"]:
            raise ValueError(f"Checksum mismatch for {archive}; remove only this archive and retry")
        extract(archive, package, toolchains / package["destination"])
        installed.append(package)
    (toolchains / "manifest.json").write_text(json.dumps(installed, indent=2) + "\n")
    print("Installed locally. Run vrun doctor; no system packages were changed.")


if __name__ == "__main__":
    main()
