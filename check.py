"""Run project checks using the current Python environment."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main():
    prefix = Path(sys.prefix)
    tool_path = os.pathsep.join(
        str(path)
        for path in (prefix / "Scripts", prefix / "Library" / "bin", prefix / "bin")
    )
    ruff = shutil.which("ruff", path=tool_path)
    if ruff is None:
        print("Ruff is missing; install dependencies from requirements.txt.")
        return 1

    commands = [
        [sys.executable, "-s", "manage.py", "check"],
        [sys.executable, "-s", "-m", "pytest", "-q"],
        [ruff, "check", "."],
        [ruff, "format", "--check", "."],
    ]
    for command in commands:
        print(f"Running: {' '.join(command)}", flush=True)
        result = subprocess.run(command, cwd=ROOT, check=False)
        if result.returncode:
            return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
