"""YouTube Data API v3 client (Module 5).

`upload_video` — resumable video upload with title/description/tags/category,
privacy, optional `publishAt` scheduling (requires privacy="private").
`upload_captions` — attach an SRT track (captions.insert).

Costs: videos.insert = 1600 quota units, captions.insert = 400 units.
Scopes needed: youtube.upload + youtube.force-ssl (see src/upload/auth.py).
"""

from __future__ import annotations

import logging
import socket
import time
from dataclasses import dataclass
from pathlib import Path

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from src.upload.auth import (
    DEFAULT_CLIENT_SECRETS,
    DEFAULT_TOKEN_FILE,
    get_credentials,
)

logger = logging.getLogger(__name__)

RETRIABLE_STATUS_CODES = {500, 502, 503, 504}
MAX_RETRIES = 5
RETRIABLE_EXCEPTIONS = (OSError, socket.error, TimeoutError)

# YouTube category NAME -> numeric categoryId (snippet.categoryId expects an ID).
_CATEGORY_IDS = {
    "22": "22",                       # People & Blogs (default)
    "23": "23",                       # Comedy
    "24": "24",                       # Entertainment
    "25": "25",                       # News & Politics
    "26": "26",                       # Howto & Style
    "27": "27",                       # Education
    "28": "28",                       # Science & Technology
    "people & blogs": "22",
    "comedy": "23",
    "entertainment": "24",
    "news & politics": "25",
    "howto & style": "26",
    "education": "27",
    "science": "28",
}


@dataclass
class UploadResult:
    video_id: str
    url: str
    privacy_status: str = "unlisted"
    scheduled_at: str | None = None


def _category_id(category: str) -> str:
    return _CATEGORY_IDS.get(str(category).strip().lower(), "22")


def _full_description(description: str, hashtags, sources) -> str:
    parts = [description] if description else []
    if hashtags:
        parts.append(" ".join(h for h in hashtags if isinstance(h, str)))
    if sources:
        parts.append("Sources:\n" + "\n".join(s for s in sources if isinstance(s, str)))
    return "\n\n".join(p for p in parts if p)


def upload_video(
    video_path: Path,
    title: str,
    description: str = "",
    tags: list[str] | None = None,
    category: str = "22",
    privacy: str = "unlisted",
    hashtags: list[str] | None = None,
    sources: list[str] | None = None,
    duration_seconds: int = 0,
    width: int = 1080,
    height: int = 1920,
    scheduled_at: str | None = None,
    client_secrets_file: Path | str = DEFAULT_CLIENT_SECRETS,
    token_file: Path | str = DEFAULT_TOKEN_FILE,
) -> UploadResult:
    """Upload `video_path` to the authorized channel and return its URL."""
    del duration_seconds, width, height  # informational only (from our renderer)

    privacy = privacy if privacy in {"private", "unlisted", "public"} else "unlisted"
    status: dict = {"privacyStatus": privacy}
    if scheduled_at:
        status["privacyStatus"] = "private"  # publishAt requires private
        status["publishAt"] = scheduled_at

    body = {
        "snippet": {
            "title": title[:100],
            "description": _full_description(description, hashtags, sources),
            "categoryId": _category_id(category),
        },
        "status": status,
    }
    if tags:
        body["snippet"]["tags"] = [t[:100] for t in tags]

    youtube = build("youtube", "v3", credentials=get_credentials(
        client_secrets_file=client_secrets_file, token_file=token_file))
    media = MediaFileUpload(str(video_path), chunksize=-1, resumable=True)
    request = youtube.videos().insert(
        part="snippet,status", body=body, media_body=media,
    )

    response = _resumable_upload(request)
    video_id = response["id"]
    privacy_status = response.get("status", {}).get("privacyStatus", privacy)
    returned_scheduled = response.get("status", {}).get("publishAt") or scheduled_at
    logger.info("Uploaded %s -> https://youtu.be/%s (%s)",
                video_path.name, video_id, privacy_status)
    return UploadResult(
        video_id=video_id,
        url=f"https://youtu.be/{video_id}",
        privacy_status=privacy_status,
        scheduled_at=returned_scheduled,
    )


def upload_captions(
    video_id: str,
    srt_path: Path,
    language: str = "en",
    name: str | None = None,
    client_secrets_file: Path | str = DEFAULT_CLIENT_SECRETS,
    token_file: Path | str = DEFAULT_TOKEN_FILE,
) -> str:
    """Attach an SRT track to a video. Returns the caption track ID."""
    youtube = build("youtube", "v3", credentials=get_credentials(
        client_secrets_file=client_secrets_file, token_file=token_file))
    body = {
        "snippet": {
            "videoId": video_id,
            "language": language,
            "name": (name or srt_path.stem)[:150],
            "isDraft": False,
        }
    }
    media = MediaFileUpload(str(srt_path), mimetype="application/octet-stream")
    caption = youtube.captions().insert(
        part="snippet", body=body, media_body=media, sync=False,
    ).execute()
    caption_id = caption["id"]
    logger.info("Attached captions %s to video %s", caption_id, video_id)
    return caption_id


def _resumable_upload(request):
    response = None
    retry = 0
    while response is None:
        try:
            status, response = request.next_chunk()
            if response is not None:
                if "id" not in response:
                    raise RuntimeError(f"Unexpected upload response: {response}")
                return response
        except HttpError as exc:
            if exc.resp.status in RETRIABLE_STATUS_CODES:
                retry = _backoff(retry, f"HTTP {exc.resp.status}")
                continue
            raise
        except RETRIABLE_EXCEPTIONS:
            retry = _backoff(retry, "network error")
            continue

        if status and status.progress() is not None:
            logger.info("Uploading... %d%%", int(status.progress() * 100))
    raise RuntimeError("Upload never completed")


def _backoff(retry: int, reason: str) -> int:
    if retry >= MAX_RETRIES:
        raise RuntimeError(f"Upload failed after {MAX_RETRIES} retries ({reason})")
    sleep = 2 ** retry
    logger.warning("%s — retrying in %ds", reason, sleep)
    time.sleep(sleep)
    return retry + 1