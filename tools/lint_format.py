"""Format or check Python, Django templates, and frontend sources."""

import difflib
import subprocess
import sys
from pathlib import Path

import cssbeautifier
import jsbeautifier


def main():
    write = "--write" in sys.argv[1:]
    args = [arg for arg in sys.argv[1:] if arg != "--write"]
    paths = [Path(p) for p in args] or [
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
    failed = 0
    if html:
        failed |= subprocess.run(
            ["djlint", *html, "--reformat" if write else "--check"], check=False
        ).returncode
    python_paths = [str(p) for p in files if p.suffix == ".py"]
    if not args:
        python_paths = ["."]
    if python_paths:
        command = ["ruff", "format", "--no-cache"]
        if not write:
            command.append("--check")
        failed |= subprocess.run([*command, *python_paths], check=False).returncode
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
            if write:
                path.write_text(formatted, encoding="utf-8")
                print(f"formatted {path}")
            else:
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
