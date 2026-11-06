"""Read-only formatting check for Python, Django templates, and frontend sources."""

import difflib
import subprocess
import sys
from pathlib import Path

import cssbeautifier
import jsbeautifier


def main():
    paths = [Path(p) for p in sys.argv[1:]] or [
        Path("templates"),
        Path("static/js"),
        Path("static/css"),
    ]
    files = sorted(
        {
            f
            for p in paths
            for f in (p.rglob("*") if p.is_dir() else [p])
            if f.suffix in (".html", ".js", ".css", ".py")
        }
    )
    html = [str(p) for p in files if p.suffix == ".html"]
    failed = (
        subprocess.run(["djlint", *html, "--check"], check=False).returncode
        if html
        else 0
    )
    python_paths = [str(p) for p in files if p.suffix == ".py"]
    if not sys.argv[1:]:
        python_paths = ["."]
    if python_paths:
        failed |= subprocess.run(
            ["ruff", "format", "--no-cache", "--check", *python_paths], check=False
        ).returncode
    for path in files:
        if path.suffix in (".html", ".py"):
            continue
        original = path.read_text(encoding="utf-8")
        formatter = jsbeautifier if path.suffix == ".js" else cssbeautifier
        options = formatter.default_options()
        options.indent_size = 4
        if path.suffix == ".css":
            options.space_around_combinator = True
        formatted = formatter.beautify(original, options).rstrip() + "\n"
        if original != formatted:
            failed = 1
            sys.stdout.writelines(
                difflib.unified_diff(
                    original.splitlines(True),
                    formatted.splitlines(True),
                    fromfile=str(path),
                    tofile=str(path),
                )
            )
    return int(bool(failed))


if __name__ == "__main__":
    raise SystemExit(main())
