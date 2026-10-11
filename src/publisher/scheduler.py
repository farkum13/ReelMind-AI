import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from src.dal.db import DEFAULT_DB_PATH, init_db
from src.dal.models import (
    DEFAULT_SLOTS_IST,
    QUOTA_DAILY_DEFAULT,
    QUOTA_PER_CAPTION,
    QUOTA_PER_UPLOAD,
    QUOTA_RESERVE,
    SlotStatus,
    VideoStatus,
)
from src.dal.repository import (
    due_open_slots,
    ensure_slot,
    mark_slot_missed,
    mark_slot_used,
    reserve_slot,
    update_video_status,
)
from src.publisher.queue import (
    has_quota_for_upload,
    mark_failed,
    mark_uploaded,
    pick_next_rendered,
    record_quota,
)
from src.upload.auth import AuthorizationRequiredError
from src.upload.youtube import upload_captions, upload_video

logger = logging.getLogger(__name__)

LOCAL_TZ = ZoneInfo("Asia/Kolkata")
IST_LABEL = "IST"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Slot seeding
# ---------------------------------------------------------------------------
def seed_slots(
    days: int = 7,
    slot_times_local: list[str] | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> int:
    """Populate upload_slots for the next N days. Idempotent."""
    slot_times_local = slot_times_local or DEFAULT_SLOTS_IST
    created = 0
    now_local = datetime.now(LOCAL_TZ)

    for day_offset in range(days):
        day = (now_local + timedelta(days=day_offset)).date()
        for hhmm in slot_times_local:
            hh, mm = map(int, hhmm.split(":"))
            slot_local = datetime(
                day.year, day.month, day.day, hh, mm, tzinfo=LOCAL_TZ,
            )
            if slot_local <= now_local:
                continue  # don't seed slots already in the past today
            slot_utc = slot_local.astimezone(timezone.utc)
            if ensure_slot(
                slot_utc=_iso(slot_utc),
                slot_local=slot_local.strftime("%Y-%m-%d %H:%M ") + IST_LABEL,
                db_path=db_path,
            ):
                created += 1

    logger.info("Seeded %d new slots", created)
    return created


# ---------------------------------------------------------------------------
# Slot reservation
# ---------------------------------------------------------------------------
def _due_open_slots(now: datetime, db_path: Path) -> list[dict]:
    """Return OPEN slots whose time has arrived (within the last 2 hours)."""
    return due_open_slots(now.isoformat(), window_hours=2, db_path=db_path)


def _reserve_slot(slot_id: int, job_id: str, db_path: Path) -> bool:
    """Reserve the slot and flip the video to QUEUED."""
    return reserve_slot(slot_id, job_id, db_path=db_path)


def _mark_slot(slot_id: int, status: SlotStatus, db_path: Path) -> None:
    if status == SlotStatus.USED:
        mark_slot_used(slot_id, db_path=db_path)
    elif status == SlotStatus.MISSED:
        mark_slot_missed(slot_id, db_path=db_path)


# ---------------------------------------------------------------------------
# The tick
# ---------------------------------------------------------------------------
def tick(
    privacy: str = "unlisted",
    quota_daily: int = QUOTA_DAILY_DEFAULT,
    reserve: int = QUOTA_RESERVE,
    db_path: Path = DEFAULT_DB_PATH,
    dry_run: bool = False,
) -> dict:
    """One pass: seed slots, then process every due slot.

    Idempotent: safe to run every 15 min from cron.
    """
    init_db(db_path)
    seed_slots(db_path=db_path)

    if not has_quota_for_upload(quota_daily, reserve, db_path):
        logger.warning("Daily quota exhausted — skipping this tick")
        return {"processed": 0, "reason": "quota_exhausted"}

    now = _now_utc()
    due = _due_open_slots(now, db_path)
    if not due:
        return {"processed": 0, "reason": "no_due_slots"}

    processed = 0
    results: list[dict] = []

    for slot in due:
        # stop if quota has been consumed mid-tick
        if not has_quota_for_upload(quota_daily, reserve, db_path):
            logger.warning("Quota hit mid-tick — stopping at %d processed", processed)
            break

        video = pick_next_rendered(db_path)
        if not video:
            logger.info("No RENDERED videos available; marking slot MISSED")
            _mark_slot(slot["id"], SlotStatus.MISSED, db_path)
            continue

        job_id = video["job_id"]
        if not _reserve_slot(slot["id"], job_id, db_path):
            logger.warning("Slot %s already reserved — skipping", slot["id"])
            continue

        logger.info(
            "[%s] slot %s fired → uploading (privacy=%s)",
            job_id, slot["slot_local"], privacy,
        )

        if dry_run:
            logger.info("[%s] DRY RUN — not calling YouTube", job_id)
            _mark_slot(slot["id"], SlotStatus.USED, db_path)
            mark_uploaded(
                job_id=job_id,
                youtube_video_id="dry-run",
                youtube_url="https://youtu.be/dry-run",
                privacy=privacy,
                scheduled_at=None,
                db_path=db_path,
            )
            results.append({"job_id": job_id, "dry_run": True})
            processed += 1
            continue

        try:
            upload_result = upload_video(
                video_path=Path(video["video_path"]),
                title=video["title"] or video["topic"],
                description=video["description"] or "",
                tags=as_list(video["tags_json"]),
                category="science",
                privacy=privacy,
                hashtags=as_list(video["hashtags_json"]),
                sources=as_list(video["sources_json"]),
                duration_seconds=video["duration_seconds"] or 0,
                width=video["width"] or 1080,
                height=video["height"] or 1920,
            )
            record_quota(QUOTA_PER_UPLOAD, db_path)

            # attach captions if present
            if video["srt_path"]:
                try:
                    upload_captions(
                        video_id=upload_result.video_id,
                        srt_path=Path(video["srt_path"]),
                        language=video["language"] or "en",
                    )
                    record_quota(QUOTA_PER_CAPTION, db_path)
                except Exception as e:  # noqa: BLE001
                    logger.warning("[%s] caption upload failed (non-fatal): %s", job_id, e)

            mark_uploaded(
                job_id=job_id,
                youtube_video_id=upload_result.video_id,
                youtube_url=upload_result.url,
                privacy=upload_result.privacy_status,
                scheduled_at=upload_result.scheduled_at,
                db_path=db_path,
            )
            _mark_slot(slot["id"], SlotStatus.USED, db_path)

            results.append({
                "job_id": job_id,
                "youtube_url": upload_result.url,
                "video_id": upload_result.video_id,
            })
            processed += 1

        except AuthorizationRequiredError as exc:
            logger.error("[%s] YouTube OAuth not configured: %s", job_id, exc)
            _mark_slot(slot["id"], SlotStatus.MISSED, db_path)
            # Config issue, not a render/upload failure — leave the video
            # RENDERED so the next tick retries it once OAuth is set up.
            update_video_status(job_id, VideoStatus.RENDERED, db_path=db_path)
            continue

        except Exception as e:  # noqa: BLE001
            logger.exception("[%s] upload failed", job_id)
            mark_failed(job_id, str(e), db_path)
            _mark_slot(slot["id"], SlotStatus.MISSED, db_path)

    return {"processed": processed, "results": results}


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------
def as_list(value) -> list:
    """Return `value` untouched when it is already a decoded list."""
    if isinstance(value, list):
        return value
    if not value:
        return []
    try:
        import json
        return json.loads(value)
    except Exception:
        return []