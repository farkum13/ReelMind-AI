import os
import asyncio
import hashlib
import logging
import dotenv
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import quote, urlencode

import httpx

from src.media.schemas import ScriptPayload, ScriptScene
from src.media.paths import JobWorkspace

logger = logging.getLogger(__name__)

dotenv.load_dotenv()
POLLINATIONS_BASE = "https://gen.pollinations.ai/image"

DEFAULT_MODEL = "flux"
DEFAULT_WIDTH = 1024
DEFAULT_HEIGHT = 1792          # ~9:16
DEFAULT_STYLE_SUFFIX = (
    "cinematic lighting, high contrast, mobile-first vertical composition, "
    "sharp focus, no text, no watermarks, no logos, no UI elements"
)
DEFAULT_NEGATIVE = (
    "text, watermark, logo, signature, blurry, distorted, low quality, "
    "deformed hands, extra fingers, cropped faces"
)

MAX_CONCURRENT = 3             # be polite to the free endpoint
REQUEST_TIMEOUT = 120.0        # flux can take 30-60s
MAX_RETRIES = 3
RETRY_BASE_DELAY = 2.0
MIN_VALID_IMAGE_BYTES = 2048
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


@dataclass
class ImageResult:
    scene_number: int
    path: Path
    prompt: str
    seed: int
    model: str
    width: int
    height: int


def _pollinations_key() -> Optional[str]:
    """Read the key lazily so tests/offline runs can control the env."""
    return os.getenv("POLLINATIONS_API_KEY") or None


# ---------------------------------------------------------------------------
# Prompt building
# ---------------------------------------------------------------------------
def _clean(text: str) -> str:
    return " ".join((text or "").split()).strip()


def build_visual_prompt(
    scene: ScriptScene,
    topic: str,
    style_suffix: str = DEFAULT_STYLE_SUFFIX,
    negative: str = DEFAULT_NEGATIVE,
) -> str:
    """Compose a single prompt from the scriptwriter's visual intent.

    Priority order:
      1. scene.visual_direction (the writer's intent)
      2. scene.narration (fallback so a missing visual_direction never blocks)
      3. topic (context anchor)
      4. scene.on_screen_text is intentionally skipped — never render text
      5. style + negative suffixes
    """
    subject = _clean(scene.visual_direction) or _clean(scene.narration)
    parts: list[str] = []
    if subject:
        parts.append(subject)
    if topic:
        parts.append(f"topic: {_clean(topic)}")
    if style_suffix:
        parts.append(style_suffix)
    if negative:
        parts.append(f"avoid: {negative}")
    return ". ".join(parts)


def _stable_seed(job_id: str, scene_number: int, user_seed: Optional[int]) -> int:
    """Deterministic seed so re-runs of the same job produce the same image.

    A caller-supplied seed is offset by the scene number so that scenes still
    differ from each other instead of all collapsing to one image.
    """
    if user_seed is not None:
        return (user_seed + scene_number) % (2**31)
    h = hashlib.sha256(f"{job_id}:{scene_number}".encode()).hexdigest()
    return int(h[:8], 16) % (2**31)


def _png_size(path: Path) -> Optional[tuple[int, int]]:
    """Read the real dimensions from a PNG's IHDR chunk (no deps)."""
    try:
        with open(path, "rb") as f:
            header = f.read(24)
    except OSError:
        return None
    if len(header) < 24 or header[:8] != _PNG_SIGNATURE or header[12:16] != b"IHDR":
        return None
    width = int.from_bytes(header[16:20], "big")
    height = int.from_bytes(header[20:24], "big")
    if width <= 0 or height <= 0:
        return None
    return width, height


# ---------------------------------------------------------------------------
# Single-image generation
# ---------------------------------------------------------------------------
def _build_url(prompt: str, seed: int, model: str, width: int, height: int) -> str:
    encoded = quote(prompt, safe="")
    params = {
        "model": model,
        "width": width,
        "height": height,
        "seed": seed,
        "nologo": "true",
    }
    key = _pollinations_key()
    if key:
        params["key"] = key

    query = urlencode(params)
    return f"{POLLINATIONS_BASE}/{encoded}?{query}"


def _retry_after_seconds(exc: Exception) -> Optional[float]:
    """Honor a Retry-After header on 429/503 when the service sends one."""
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in (429, 503):
        raw = exc.response.headers.get("Retry-After")
        if raw:
            try:
                return max(0.0, float(raw))
            except ValueError:
                return None
    return None


async def _fetch_image(
    client: httpx.AsyncClient,
    url: str,
    out_path: Path,
) -> None:
    """Download to a temp path, verify, then atomically rename into place."""
    tmp_path = out_path.with_suffix(out_path.suffix + ".part")
    try:
        async with client.stream("GET", url, timeout=REQUEST_TIMEOUT) as response:
            response.raise_for_status()
            with open(tmp_path, "wb") as f:
                async for chunk in response.aiter_bytes():
                    f.write(chunk)

        if not tmp_path.exists() or tmp_path.stat().st_size < MIN_VALID_IMAGE_BYTES:
            size = tmp_path.stat().st_size if tmp_path.exists() else 0
            raise RuntimeError(f"Pollinations returned tiny image ({size} bytes)")

        tmp_path.replace(out_path)
    finally:
        tmp_path.unlink(missing_ok=True)


async def generate_scene_image(
    scene: ScriptScene,
    topic: str,
    workspace: JobWorkspace,
    job_id: str,
    *,
    client: httpx.AsyncClient,
    model: str = DEFAULT_MODEL,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    user_seed: Optional[int] = None,
    style_suffix: str = DEFAULT_STYLE_SUFFIX,
    negative: str = DEFAULT_NEGATIVE,
) -> ImageResult:
    """Generate one scene image with retry + skip-if-cached."""
    out_path = workspace.scene_image_path(scene.scene_number)
    prompt = build_visual_prompt(scene, topic, style_suffix, negative)
    seed = _stable_seed(job_id, scene.scene_number, user_seed)
    url = _build_url(prompt, seed, model, width, height)

    # cache: if the file already exists (resumed job), skip regen
    if out_path.exists() and out_path.stat().st_size >= MIN_VALID_IMAGE_BYTES:
        logger.info("Scene %s image already exists, skipping", scene.scene_number)
        real = _png_size(out_path) or (width, height)
        return ImageResult(
            scene_number=scene.scene_number,
            path=out_path,
            prompt=prompt,
            seed=seed,
            model=model,
            width=real[0],
            height=real[1],
        )

    last_err: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            await _fetch_image(client, url, out_path)
            logger.info(
                "Scene %s image ready: %s (%d bytes)",
                scene.scene_number, out_path.name, out_path.stat().st_size,
            )
            real = _png_size(out_path) or (width, height)
            return ImageResult(
                scene_number=scene.scene_number,
                path=out_path,
                prompt=prompt,
                seed=seed,
                model=model,
                width=real[0],
                height=real[1],
            )
        except Exception as e:  # noqa: BLE001
            last_err = e
            logger.warning(
                "Pollinations attempt %d/%d failed for scene %s: %s",
                attempt, MAX_RETRIES, scene.scene_number, e,
            )
            if attempt < MAX_RETRIES:
                delay = _retry_after_seconds(e)
                await asyncio.sleep(delay if delay is not None else RETRY_BASE_DELAY * attempt)

    raise RuntimeError(
        f"Pollinations failed for scene {scene.scene_number} "
        f"after {MAX_RETRIES} attempts: {last_err}"
    )


# ---------------------------------------------------------------------------
# Batch generation
# ---------------------------------------------------------------------------
async def generate_scene_images(
    script: ScriptPayload,
    workspace: JobWorkspace,
    job_id: str,
    model: str = DEFAULT_MODEL,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    seed: Optional[int] = None,
    style_suffix: str = DEFAULT_STYLE_SUFFIX,
    negative: str = DEFAULT_NEGATIVE,
    max_concurrent: int = MAX_CONCURRENT,
) -> tuple[list[ImageResult], list[str]]:
    """Generate all scene images with bounded concurrency.

    Best-effort: returns (image_results, error_messages). Individual scene
    failures are logged and reported rather than aborting the batch, so the
    voiceover/subtitles already produced by the caller survive.
    """
    if not script.scenes:
        return [], ["script has no scenes to render"]

    if not _pollinations_key():
        return [], ["POLLINATIONS_API_KEY is not set"]

    semaphore = asyncio.Semaphore(max_concurrent)
    errors: list[str] = []

    async def _bounded(scene: ScriptScene, client: httpx.AsyncClient) -> Optional[ImageResult]:
        async with semaphore:
            try:
                return await generate_scene_image(
                    scene=scene,
                    topic=script.title,
                    workspace=workspace,
                    job_id=job_id,
                    client=client,
                    model=model,
                    width=width,
                    height=height,
                    user_seed=seed,
                    style_suffix=style_suffix,
                    negative=negative,
                )
            except Exception as e:  # noqa: BLE001
                logger.error("Scene %s image failed: %s", scene.scene_number, e)
                errors.append(f"scene {scene.scene_number}: {e}")
                return None

    async with httpx.AsyncClient(follow_redirects=True) as client:
        tasks = [_bounded(scene, client) for scene in script.scenes]
        results = await asyncio.gather(*tasks)

    images = sorted(
        (r for r in results if r is not None),
        key=lambda r: r.scene_number,
    )
    return images, errors
