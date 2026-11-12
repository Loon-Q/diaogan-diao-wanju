"""Persistent, idempotent rotation; runtime state is outside the installed skill."""
import json
import os
import sqlite3
from pathlib import Path
from datetime import datetime, timezone

ORDER = ("follow", "like", "save", "share")


def default_dir():
    explicit = os.environ.get("FISHING_GIF_STATE_DIR")
    if explicit:
        return Path(explicit).expanduser()
    return Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "fishing-gif"


def connect(directory=None):
    directory = Path(directory) if directory else default_dir()
    directory.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(directory / "rotation.sqlite3"), timeout=20)
    db.row_factory = sqlite3.Row
    db.executescript("""
      CREATE TABLE IF NOT EXISTS cursor_state
        (id INTEGER PRIMARY KEY, next_index INTEGER NOT NULL, generation INTEGER NOT NULL);
      INSERT OR IGNORE INTO cursor_state VALUES(1,0,0);
      CREATE TABLE IF NOT EXISTS jobs
        (source_hash TEXT PRIMARY KEY, source_name TEXT NOT NULL, variant TEXT NOT NULL,
         generation INTEGER NOT NULL, status TEXT NOT NULL, outputs TEXT, created TEXT NOT NULL);
    """)
    return db


def reserve(source_hash, source_name, directory=None):
    with connect(directory) as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM jobs WHERE source_hash=?", (source_hash,)).fetchone()
        if row:
            return dict(row)
        cursor = db.execute("SELECT * FROM cursor_state WHERE id=1").fetchone()
        # A fresh upload may follow an unfinished attempt. It does not consume a turn.
        db.execute("UPDATE jobs SET status='deferred' WHERE status='pending'")
        variant = ORDER[cursor["next_index"]]
        db.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?,?)", (
            source_hash, source_name, variant, cursor["generation"], "pending", None,
            datetime.now(timezone.utc).isoformat()))
        return dict(db.execute("SELECT * FROM jobs WHERE source_hash=?", (source_hash,)).fetchone())


def finish(source_hash, outputs, directory=None, expected_variant=None):
    with connect(directory) as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM jobs WHERE source_hash=?", (source_hash,)).fetchone()
        if row is None:
            raise ValueError("No rotation reservation exists for this source.")
        if expected_variant is not None and row["variant"] != expected_variant:
            raise ValueError("Rendered variant differs from the reserved rotation variant.")
        if row["status"] == "delivered":
            return {"variant": row["variant"], "advanced": False, "already_delivered": True}
        cursor = db.execute("SELECT * FROM cursor_state WHERE id=1").fetchone()
        advance = row["generation"] == cursor["generation"]
        if advance:
            db.execute("UPDATE cursor_state SET next_index=?,generation=generation+1 WHERE id=1",
                       ((cursor["next_index"] + 1) % len(ORDER),))
        db.execute("UPDATE jobs SET status='delivered',outputs=? WHERE source_hash=?",
                   (json.dumps(outputs, ensure_ascii=False), source_hash))
        return {"variant": row["variant"], "advanced": advance, "already_delivered": False}


def status(directory=None):
    with connect(directory) as db:
        cursor = db.execute("SELECT * FROM cursor_state WHERE id=1").fetchone()
        return {"next_variant": ORDER[cursor["next_index"]], "generation": cursor["generation"],
                "recent_jobs": [dict(r) for r in db.execute(
                    "SELECT source_name,variant,status FROM jobs ORDER BY created DESC LIMIT 12")]}
