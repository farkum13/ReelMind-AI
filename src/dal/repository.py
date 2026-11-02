from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from src.dal.db import DEFAULT_DB_PATH, _from_json, _to_json, get_conn, now_iso
from src.dal.models import (
    QUOTA_PER_CAPTION,
    QUOTA_PER_UPLOAD,
    SlotStatus,
    VideoStatus,
)

logger = logging.getLogger(__name__)


@dataclass
class VideoRow:
    id: int
    job_id: str
    topic: str
    title: str | None
    status: str
    video_path: str | None
    srt_path: str | None
    description: str | None
    tags: list[str] | None
    hashtags: list[str] | None
    sources: list[Any] | None
    duration_seconds: float
    width: int
    height: int
    language: str
    privacy: str
    youtube_video_id: str | None
    youtube_url: str | None
    scheduled_at: str | None
    uploaded_at: str | None
    error: str | None
    created_at: str
    updated_at: str


def _row_to_video(row) -> VideoRow:
    return VideoRow(
        id=row["id"],
        job_id=row["job_id"],
        topic=row["topic"],
        title=row["title"],
        status=row["status"],
        video_path=row["video_path"],
        srt_path=row["srt_path"],
        description=row["description"],
        tags=_from_json(row["tags_json"]),
        hashtags=_from_json(row["hashtags_json"]),
        sources=_from_json(row["sources_json"]),
        duration_seconds=row["duration_seconds"] or 0.0,
        width=row["width"] or 1080,
        height=row["height"] or 1920,
        language=row["language"] or "en",
        privacy=row["privacy"] or "unlisted",
        youtube_video_id=row["youtube_video_id"],
        youtube_url=row["youtube_url"],
        scheduled_at=row["scheduled_at"],
        uploaded_at=row["uploaded_at"],
        error=row["error"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


# Videos
def create_video(
    *,
    job_id: str,
    topic: str,
    title: str | None = None,
    status: VideoStatus | str = VideoStatus.PENDING,
    video_path: Path | str | None = None,
    srt_path: Path | str | None = None,
    description: str | None = None,
    tags: list[str] | None = None,
    hashtags: list[str] | None = None,
    sources: list[Any] | None = None,
    duration_seconds: float = 0.0,
    width: int = 1080,
    height: int = 1920,
    language: str = "en",
    privacy: str = "unlisted",
    db_path: Path = DEFAULT_DB_PATH,
) -> VideoRow:
    t = now_iso()
    status_str = status.value if isinstance(status, VideoStatus) else status
    with get_conn(db_path) as c:
        c.execute(
            """
            INSERT INTO videos (
                job_id, topic, title, status,
                video_path, srt_path, description,
                tags_json, hashtags_json, sources_json,
                duration_seconds, width, height, language, privacy,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                topic,
                title,
                status_str,
                str(video_path) if video_path else None,
                str(srt_path) if srt_path else None,
                description,
                _to_json(tags),
                _to_json(hashtags),
                _to_json(sources),
                duration_seconds,
                width,
                height,
                language,
                privacy,
                t,
                t,
            ),
        )
        vid = c.execute("SELECT * FROM videos WHERE id = last_insert_rowid()").fetchone()
    logger.info("video created job_id=%s status=%s", job_id, status_str)
    return _row_to_video(vid)


def upsert_video(
    *,
    job_id: str,
    topic: str,
    title: str | None = None,
    language: str = "en",
    duration_seconds: float = 0.0,
    db_path: Path = DEFAULT_DB_PATH,
) -> VideoRow:
    t = now_iso()
    with get_conn(db_path) as c:
        c.execute(
            """
            INSERT INTO videos (job_id, topic, title, status, language, duration_seconds, created_at, updated_at)
            VALUES (?, ?, ?, 'PENDING', ?, ?, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET
                topic            = COALESCE(excluded.topic, videos.topic),
                title            = COALESCE(excluded.title, videos.title),
                language         = CASE WHEN excluded.language != 'en' THEN excluded.language ELSE videos.language END,
                duration_seconds = CASE WHEN excluded.duration_seconds > 0 THEN excluded.duration_seconds ELSE videos.duration_seconds END,
                updated_at       = excluded.updated_at
            """,
            (job_id, topic, title, language, duration_seconds, t, t),
        )
        row = c.execute("SELECT * FROM videos WHERE job_id = ?", (job_id,)).fetchone()
    return _row_to_video(row)


def get_video_by_job_id(job_id: str, db_path: Path = DEFAULT_DB_PATH) -> VideoRow | None:
    with get_conn(db_path) as c:
        row = c.execute("SELECT * FROM videos WHERE job_id = ?", (job_id,)).fetchone()
    return _row_to_video(row) if row else None


def list_videos(
    status: VideoStatus | str | None = None,
    limit: int = 100,
    db_path: Path = DEFAULT_DB_PATH,
) -> list[VideoRow]:
    if status is None:
        with get_conn(db_path) as c:
            rows = c.execute(
                "SELECT * FROM videos ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
    else:
        rows = list_videos_by_status(status, limit=limit, db_path=db_path)
    return [_row_to_video(r) for r in rows]


def claim_next_pending_video(db_path: Path = DEFAULT_DB_PATH) -> VideoRow | None:
    """Atomically claim the oldest pending job for a single worker."""
    with get_conn(db_path) as c:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute(
            "SELECT * FROM videos WHERE status = ? ORDER BY created_at ASC LIMIT 1",
            (VideoStatus.PENDING.value,),
        ).fetchone()
        if row is None:
            return None

        c.execute(
            "UPDATE videos SET status = ?, updated_at = ? WHERE job_id = ?",
            (VideoStatus.PROCESSING.value, now_iso(), row["job_id"]),
        )
        claimed = c.execute(
            "SELECT * FROM videos WHERE job_id = ?", (row["job_id"],)
        ).fetchone()
    return _row_to_video(claimed)


def requeue_processing_videos(db_path: Path = DEFAULT_DB_PATH) -> int:
    """Make interrupted jobs available again after a single-worker restart."""
    with get_conn(db_path) as c:
        result = c.execute(
            "UPDATE videos SET status = ?, updated_at = ? WHERE status = ?",
            (
                VideoStatus.PENDING.value,
                now_iso(),
                VideoStatus.PROCESSING.value,
            ),
        )
    return result.rowcount


def pick_next_rendered(db_path: Path = DEFAULT_DB_PATH) -> VideoRow | None:
    """FIFO — oldest RENDERED video first."""
    with get_conn(db_path) as c:
        row = c.execute(
            "SELECT * FROM videos WHERE status = ? ORDER BY created_at ASC LIMIT 1",
            (VideoStatus.RENDERED.value,),
        ).fetchone()
    return _row_to_video(row) if row else None


def list_videos_by_status(
    status: VideoStatus | str,
    limit: int = 50,
    db_path: Path = DEFAULT_DB_PATH,
) -> list[VideoRow]:
    s = status.value if isinstance(status, VideoStatus) else status
    with get_conn(db_path) as c:
        rows = c.execute(
            "SELECT * FROM videos WHERE status = ? ORDER BY created_at ASC LIMIT ?",
            (s, limit),
        ).fetchall()
    return [_row_to_video(r) for r in rows]


def update_video_status(
    job_id: str,
    status: VideoStatus | str,
    *,
    error: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    s = status.value if isinstance(status, VideoStatus) else status
    with get_conn(db_path) as c:
        c.execute(
            "UPDATE videos SET status = ?, error = ?, updated_at = ? WHERE job_id = ?",
            (s, error, now_iso(), job_id),
        )


def mark_video_rendered(
    job_id: str,
    *,
    video_path: Path | str,
    srt_path: Path | str | None = None,
    description: str | None = None,
    tags: list[str] | None = None,
    hashtags: list[str] | None = None,
    sources: list[Any] | None = None,
    duration_seconds: float | None = None,
    width: int | None = None,
    height: int | None = None,
    language: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    sets = ["status = ?", "video_path = ?", "updated_at = ?"]
    vals: list[Any] = [VideoStatus.RENDERED.value, str(video_path), now_iso()]
    if srt_path is not None:
        sets.append("srt_path = ?")
        vals.append(str(srt_path))
    if description is not None:
        sets.append("description = ?")
        vals.append(description)
    if tags is not None:
        sets.append("tags_json = ?")
        vals.append(_to_json(tags))
    if hashtags is not None:
        sets.append("hashtags_json = ?")
        vals.append(_to_json(hashtags))
    if sources is not None:
        sets.append("sources_json = ?")
        vals.append(_to_json(sources))
    if duration_seconds is not None:
        sets.append("duration_seconds = ?")
        vals.append(duration_seconds)
    if width is not None:
        sets.append("width = ?")
        vals.append(width)
    if height is not None:
        sets.append("height = ?")
        vals.append(height)
    if language is not None:
        sets.append("language = ?")
        vals.append(language)
    sets.append("error = NULL")
    sql = f"UPDATE videos SET {', '.join(sets)} WHERE job_id = ?"
    vals.append(job_id)
    with get_conn(db_path) as c:
        c.execute(sql, vals)


def mark_video_uploaded(
    job_id: str,
    *,
    youtube_video_id: str,
    youtube_url: str,
    privacy: str = "unlisted",
    scheduled_at: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    t = now_iso()
    with get_conn(db_path) as c:
        c.execute(
            """
            UPDATE videos
            SET status = ?, youtube_video_id = ?, youtube_url = ?, privacy = ?,
                scheduled_at = ?, uploaded_at = ?, updated_at = ?, error = NULL
            WHERE job_id = ?
            """,
            (VideoStatus.UPLOADED.value, youtube_video_id, youtube_url, privacy,
             scheduled_at, t, t, job_id),
        )


def set_video_error(
    job_id: str,
    error: str,
    *,
    status: VideoStatus | str = VideoStatus.FAILED,
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    s = status.value if isinstance(status, VideoStatus) else status
    with get_conn(db_path) as c:
        c.execute(
            "UPDATE videos SET status = ?, error = ?, updated_at = ? WHERE job_id = ?",
            (s, error[:5000], now_iso(), job_id),
        )


# Slots
def ensure_slot(
    *,
    slot_utc: str,
    slot_local: str,
    db_path: Path = DEFAULT_DB_PATH,
) -> bool:
    """Create an upload slot if it doesn't exist. True when newly created."""
    t = now_iso()
    with get_conn(db_path) as c:
        cur = c.execute(
            """
            INSERT OR IGNORE INTO upload_slots (slot_utc, slot_local, status, created_at, updated_at)
            VALUES (?, ?, 'OPEN', ?, ?)
            """,
            (slot_utc, slot_local, t, t),
        )
    return bool(cur.rowcount)


def get_open_slot(db_path: Path = DEFAULT_DB_PATH) -> dict[str, Any] | None:
    with get_conn(db_path) as c:
        row = c.execute(
            """
            SELECT * FROM upload_slots
            WHERE status = 'OPEN'
            ORDER BY slot_utc ASC
            LIMIT 1
            """
        ).fetchone()
    return dict(row) if row else None


def reserve_slot(slot_id: int, job_id: str, db_path: Path = DEFAULT_DB_PATH) -> bool:
    """Reserve an OPEN slot for a job and flip the video to QUEUED.

    Returns False when the slot was no longer OPEN (already claimed).
    """
    t = now_iso()
    with get_conn(db_path) as c:
        cur = c.execute(
            """
            UPDATE upload_slots
            SET status = ?, job_id = ?, updated_at = ?
            WHERE id = ? AND status = 'OPEN'
            """,
            (SlotStatus.RESERVED.value, job_id, t, slot_id),
        )
        if not cur.rowcount:
            return False
        c.execute(
            "UPDATE videos SET status = ?, updated_at = ? WHERE job_id = ?",
            (VideoStatus.QUEUED.value, t, job_id),
        )
    return True


def due_open_slots(
    now_utc: str,
    *,
    window_hours: int = 2,
    db_path: Path = DEFAULT_DB_PATH,
) -> list[dict[str, Any]]:
    """OPEN slots whose time has arrived (within the last `window_hours`)."""
    import datetime as _dt

    lower = _dt.datetime.fromisoformat(now_utc) - _dt.timedelta(hours=window_hours)
    with get_conn(db_path) as c:
        rows = c.execute(
            """
            SELECT * FROM upload_slots
            WHERE status = ? AND slot_utc <= ? AND slot_utc >= ?
            ORDER BY slot_utc ASC
            """,
            (SlotStatus.OPEN.value, now_utc, lower.isoformat()),
        ).fetchall()
    return [dict(r) for r in rows]


def mark_slot_used(slot_id: int, db_path: Path = DEFAULT_DB_PATH) -> None:
    t = now_iso()
    with get_conn(db_path) as c:
        c.execute(
            "UPDATE upload_slots SET status = ?, updated_at = ? WHERE id = ?",
            (SlotStatus.USED.value, t, slot_id),
        )


def mark_slot_missed(slot_id: int, db_path: Path = DEFAULT_DB_PATH) -> None:
    t = now_iso()
    with get_conn(db_path) as c:
        c.execute(
            "UPDATE upload_slots SET status = ?, updated_at = ? WHERE id = ?",
            (SlotStatus.MISSED.value, t, slot_id),
        )


# Quota
def get_or_create_quota_log(qdate: date | str, db_path: Path = DEFAULT_DB_PATH) -> dict[str, Any]:
    d = qdate.isoformat() if isinstance(qdate, date) else qdate
    with get_conn(db_path) as c:
        row = c.execute("SELECT * FROM quota_log WHERE date = ?", (d,)).fetchone()
        if row:
            return dict(row)
        c.execute(
            "INSERT INTO quota_log (date, units_used, uploads_count) VALUES (?, 0, 0)",
            (d,),
        )
        row = c.execute("SELECT * FROM quota_log WHERE date = ?", (d,)).fetchone()
    return dict(row)


def increment_quota(
    qdate: date | str,
    units_used: int = 0,
    uploads_count: int = 0,
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    d = qdate.isoformat() if isinstance(qdate, date) else qdate
    get_or_create_quota_log(d, db_path=db_path)
    with get_conn(db_path) as c:
        c.execute(
            """
            UPDATE quota_log
            SET units_used = units_used + ?, uploads_count = uploads_count + ?
            WHERE date = ?
            """,
            (units_used, uploads_count, d),
        )


def _today_local() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def get_quota_used(db_path: Path = DEFAULT_DB_PATH) -> int:
    with get_conn(db_path) as c:
        row = c.execute(
            "SELECT units_used FROM quota_log WHERE date = ?", (_today_local(),)
        ).fetchone()
    return int(row["units_used"]) if row else 0


def record_quota(units: int, db_path: Path = DEFAULT_DB_PATH) -> None:
    increment_quota(_today_local(), units_used=units, uploads_count=1, db_path=db_path)


def has_quota_for_upload(
    quota_daily: int,
    reserve: int,
    db_path: Path = DEFAULT_DB_PATH,
) -> bool:
    used = get_quota_used(db_path)
    return used + QUOTA_PER_UPLOAD + QUOTA_PER_CAPTION <= quota_daily - reserve


# Run log
def log_event(
    event: str,
    *,
    job_id: str | None = None,
    detail: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    with get_conn(db_path) as c:
        c.execute(
            "INSERT INTO run_log (job_id, event, detail, created_at) VALUES (?, ?, ?, ?)",
            (job_id, event, detail, now_iso()),
        )
