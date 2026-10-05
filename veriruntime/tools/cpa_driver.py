"""Compilation layer inside the same supervised process group as CPAchecker."""
import json
from pathlib import Path
import subprocess
import sys


def main():
    manifest = json.loads(Path(sys.argv[1]).read_text())
    for command, destination in zip(manifest['preprocessor_commands'], manifest['compiled_sources']):
        with open(destination, 'wb') as output:
            result = subprocess.run(command, stdout=output)
        if result.returncode:
            return result.returncode
    return subprocess.run(manifest['verifier_argv']).returncode


if __name__ == '__main__':
    raise SystemExit(main())
