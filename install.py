#!/usr/bin/env python3
"""Install the bundled Codex skill without copying state or source videos."""
import argparse
from datetime import datetime
import os
from pathlib import Path
import shutil


def install(destination=None):
    source = Path(__file__).resolve().parent / "skills/fishing-gif"
    codex = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    target = Path(destination).expanduser().resolve() if destination else codex / "skills/fishing-gif"
    if not (source / "SKILL.md").is_file():
        raise SystemExit("Cannot find bundled skills/fishing-gif/SKILL.md")
    if target == source.resolve():
        raise SystemExit("Source and installation directory must differ.")
    backup = None
    if target.exists():
        backup = codex / "skill-backups" / ("fishing-gif-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(target), str(backup))
        print(f"Previous installation preserved: {backup}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target, ignore=shutil.ignore_patterns(".venv", ".sam2-env", "__pycache__", "*.pyc", "*.sqlite3", "*.sqlite3-*", "*.pt"))
    if backup is not None:
        for name in (".venv", ".sam2-env"):
            if (backup / name).is_dir():
                shutil.move(str(backup / name), str(target / name))
    print(f"Installed: {target}")
    return target


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dest", help="Alternate destination for an isolated test")
    install(p.parse_args().dest)
