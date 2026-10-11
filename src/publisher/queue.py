"""Publisher data access — thin facade over src.dal.repository.

Keeps the publisher-specific vocabulary (enqueue_pending, mark_rendered,
mark_uploaded, pick_next_rendered...) while all SQL lives in the DAL layer.
"""

import logging
from pathlib import Path

from src.dal.db import DEFAULT_DB_PATH as _DEFAULT
from src.dal.models import VideoStatus
from src.dal.repository import (
    get_quota_used as _get_quota_used,
    has_quota_for_upload as _has_quota_for_upload,
    log_event,
    mark_video_rendered,
    mark_video_uploaded,
    pick_next_rendered as _pick_next_rendered,
    record_quota as _record_quota,
    set_video_error,
    upsert_video,
)
from src.dal.repository import get_video_by_job_id, list_videos as _list_videos

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enqueue / status transitions
# ---------------------------------------------------------------------------
def enqueue_pending(
    job_id: str,
    topic: str,
    title: str = "",
    db_path: Path | None = None,
) -> None:
    """Called by the agent pipeline once a final_plan is approved."""
    upsert_video(job_id=job_id, topic=topic, title=title or None,
                 db_path=db_path or _DEFAULT)
    log_event("enqueue_pending", job_id=job_id, detail="", db_path=db_path or _DEFAULT)


def mark_rendered(
    job_id: str,
    video_path: Path,
    srt_path: Path | None,
    description: str,
    tags: list[str],
    hashtags: list[str],
    sources: list[str],
    duration_seconds: float,
    width: int,
    height: int,
    language: str = "en",
    db_path: Path | None = None,
) -> None:
    """Called by the media pipeline after final.mp4 is written."""
    db = db_path or _DEFAULT
    mark_video_rendered(
        job_id,
        video_path=video_path,
        srt_path=srt_path,
        description=description or None,
        tags=tags,
        hashtags=hashtags,
        sources=sources,
        duration_seconds=duration_seconds or 0.0,
        width=width or 1080,
        height=height or 1920,
        language=language,
        db_path=db,
    )
    log_event("mark_rendered", job_id=job_id, detail=str(video_path), db_path=db)


def mark_failed(job_id: str, error: str, db_path: Path | None = None) -> None:
    db = db_path or _DEFAULT
    set_video_error(job_id, error, db_path=db)
    log_event("mark_failed", job_id=job_id, detail=error[:2000], db_path=db)


def mark_uploaded(
    job_id: str,
    youtube_video_id: str,
    youtube_url: str,
    privacy: str,
    scheduled_at: str | None,
    db_path: Path | None = None,
) -> None:
    db = db_path or _DEFAULT
    mark_video_uploaded(
        job_id,
        youtube_video_id=youtube_video_id,
        youtube_url=youtube_url,
        privacy=privacy,
        scheduled_at=scheduled_at,
        db_path=db,
    )
    log_event("mark_uploaded", job_id=job_id, detail=youtube_video_id, db_path=db)


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------
def _to_dict(row) -> dict:
    if row is None:
        return {}
    return {
        "job_id": row.job_id,
        "topic": row.topic,
        "title": row.title,
        "status": row.status,
        "video_path": row.video_path,
        "srt_path": row.srt_path,
        "description": row.description,
        "tags_json": row.tags,
        "hashtags_json": row.hashtags,
        "sources_json": row.sources,
        "duration_seconds": row.duration_seconds,
        "width": row.width,
        "height": row.height,
        "language": row.language,
        "privacy": row.privacy,
        "youtube_video_id": row.youtube_video_id,
        "youtube_url": row.youtube_url,
        "scheduled_at": row.scheduled_at,
        "uploaded_at": row.uploaded_at,
        "error": row.error,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def get_video(job_id: str, db_path: Path | None = None) -> dict:
    return _to_dict(get_video_by_job_id(job_id, db_path=db_path or _DEFAULT))


def list_videos(
    status: VideoStatus | None = None,
    limit: int = 100,
    db_path: Path | None = None,
) -> list[dict]:
    rows = _list_videos(status=status, limit=limit, db_path=db_path or _DEFAULT)
    return [_to_dict(r) for r in rows]


def pick_next_rendered(db_path: Path | None = None) -> dict | None:
    row = _pick_next_rendered(db_path=db_path or _DEFAULT)
    return _to_dict(row) or None


# ---------------------------------------------------------------------------
# Quota tracking (re-exported for convenience; logic lives in the DAL)
# ---------------------------------------------------------------------------
def get_quota_used(db_path: Path | None = None) -> int:
    return _get_quota_used(db_path=db_path or _DEFAULT)


def record_quota(units: int, db_path: Path | None = None) -> None:
    _record_quota(units, db_path=db_path or _DEFAULT)


def has_quota_for_upload(
    quota_daily: int,
    reserve: int,
    db_path: Path | None = None,
) -> bool:
    return _has_quota_for_upload(quota_daily, reserve, db_path=db_path or _DEFAULT)