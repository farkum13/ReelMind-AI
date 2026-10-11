import logging
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont
from moviepy import ImageClip

from src.media.compiler import ASSETS_DIR
from src.media.schemas import TranscriptSegment
from src.media.paths import JobWorkspace

logger = logging.getLogger(__name__)

TARGET_W = 1080
TARGET_H = 1920

# caption band — lower third, above typical UI safe zones
CAPTION_CENTER_Y = int(TARGET_H * 0.72)
MAX_TEXT_WIDTH = int(TARGET_W * 0.86)

FONT_SIZE = 68
FONT_STROKE = 6
LINE_SPACING = 12
MAX_LINES = 3

FILL = (255, 255, 255, 255)
STROKE = (0, 0, 0, 255)
SHADOW = (0, 0, 0, 140)

# Candidate font files, tried in order (first existing wins). Bundled fonts
# live under assets/fonts; system Noto/DejaVu is a dependency-free fallback.
FONT_CANDIDATES = {
    "hi": [
        ASSETS_DIR / "fonts" / "NotoSansDevanagari_Condensed-Light.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf",
    ],
    "default": [
        ASSETS_DIR / "fonts" / "NotoSans_Condensed-Light.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf",
    ],
}


def _resolve_font(language: str) -> Optional[str]:
    candidates = FONT_CANDIDATES["hi"] if language.startswith("hi") else FONT_CANDIDATES["default"]
    for path in candidates:
        if Path(path).exists():
            return str(path)
    logger.warning("No bundled/system font found for '%s'; using PIL default", language)
    return None


def _wrap_text(
    text: str,
    font: ImageFont.FreeTypeFont,
    draw: ImageDraw.ImageDraw,
    max_width: int,
    stroke_width: int,
) -> list[str]:
    words = text.split()
    if not words:
        return []
    lines: list[str] = []
    current = words[0]
    for w in words[1:]:
        candidate = f"{current} {w}"
        bbox = draw.textbbox((0, 0), candidate, font=font, stroke_width=stroke_width)
        if bbox[2] - bbox[0] <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = w
    lines.append(current)
    return lines


def _render_caption_png(
    text: str,
    font_path: Optional[str],
    out_path: Path,
) -> Path:
    """Render one caption to a transparent 1080x1920 PNG centered in the caption band."""
    img = Image.new("RGBA", (TARGET_W, TARGET_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    font: ImageFont.ImageFont
    if font_path:
        try:
            font = ImageFont.truetype(font_path, FONT_SIZE)
        except OSError:
            logger.warning("Font unreadable: %s — falling back to default", font_path)
            font = ImageFont.load_default()
    else:
        font = ImageFont.load_default()

    # The bitmap default font does not support stroke_width.
    stroke_width = FONT_STROKE if isinstance(font, ImageFont.FreeTypeFont) else 0

    lines = _wrap_text(text, font, draw, MAX_TEXT_WIDTH, stroke_width)
    if len(lines) > MAX_LINES:
        # keep the last words — usually the punchline
        lines = lines[-MAX_LINES:]

    # measure full block height
    line_heights = []
    line_widths = []
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font, stroke_width=stroke_width)
        line_widths.append(bbox[2] - bbox[0])
        line_heights.append(bbox[3] - bbox[1])

    total_h = sum(line_heights) + LINE_SPACING * (len(lines) - 1)
    y = CAPTION_CENTER_Y - total_h // 2

    for line, w, h in zip(lines, line_widths, line_heights):
        x = (TARGET_W - w) // 2
        if stroke_width:
            # shadow
            draw.text(
                (x + 3, y + 3), line, font=font,
                fill=SHADOW, stroke_width=stroke_width, stroke_fill=SHADOW,
            )
        # main
        draw.text(
            (x, y), line, font=font,
            fill=FILL, stroke_width=stroke_width, stroke_fill=STROKE,
        )
        y += h + LINE_SPACING

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "PNG")
    return out_path


def build_caption_clips(
    segments: list[TranscriptSegment],
    workspace: JobWorkspace,
    language: str = "en",
) -> list[ImageClip]:
    """Render each segment as a PNG and return it as a positioned ImageClip."""
    font_path = _resolve_font(language)
    cache_dir = workspace.captions_dir
    cache_dir.mkdir(parents=True, exist_ok=True)

    clips: list[ImageClip] = []
    for i, seg in enumerate(segments):
        text = (seg.text or "").strip()
        if not text:
            continue

        duration = max(seg.end - seg.start, 0.2)
        png_path = cache_dir / f"cap_{i:03d}.png"
        _render_caption_png(text, font_path, png_path)

        clip = (
            ImageClip(str(png_path))
            .with_start(seg.start)
            .with_duration(duration)
            .with_position(("center", "center"))
        )
        clips.append(clip)

    return clips
