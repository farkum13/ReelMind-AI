import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = REPO_ROOT / "data" / "publisher.sqlite"

SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id           TEXT UNIQUE NOT NULL,
    topic            TEXT NOT NULL,
    title            TEXT,
    status           TEXT NOT NULL DEFAULT 'PENDING',
    video_path       TEXT,
    srt_path         TEXT,
    description      TEXT,
    tags_json        TEXT,
    hashtags_json    TEXT,
    sources_json     TEXT,
    duration_seconds REAL DEFAULT 0,
    width            INTEGER DEFAULT 1080,
    height           INTEGER DEFAULT 1920,
    language         TEXT DEFAULT 'en',
    privacy          TEXT DEFAULT 'unlisted',
    youtube_video_id TEXT,
    youtube_url      TEXT,
    scheduled_at     TEXT,
    uploaded_at      TEXT,
    error            TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_videos_status ON videos(status);
CREATE INDEX IF NOT EXISTS idx_videos_created ON videos(created_at);

CREATE TABLE IF NOT EXISTS upload_slots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    slot_utc    TEXT UNIQUE NOT NULL,     -- ISO8601 UTC
    slot_local  TEXT NOT NULL,            -- display time, e.g. 2026-10-05 08:00 IST
    status      TEXT NOT NULL DEFAULT 'OPEN',
    job_id      TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_slots_utc ON upload_slots(slot_utc);
CREATE INDEX IF NOT EXISTS idx_slots_status ON upload_slots(status);

CREATE TABLE IF NOT EXISTS quota_log (
    date          TEXT PRIMARY KEY,       -- YYYY-MM-DD (local)
    units_used    INTEGER NOT NULL DEFAULT 0,
    uploads_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS run_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      TEXT,
    event       TEXT NOT NULL,
    detail      TEXT,
    created_at  TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_json(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _from_json(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return value


def init_db(db_path: Path | str = DEFAULT_DB_PATH) -> None:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(SCHEMA)
        conn.commit()
    logger.info("publisher DB ready at %s", db_path)


@contextmanager
def get_conn(db_path: Path | str = DEFAULT_DB_PATH):
    db_path = Path(db_path)
    conn = sqlite3.connect(db_path, isolation_level="DEFERRED")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
