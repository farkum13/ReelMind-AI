import logging
from pathlib import Path

from moviepy import CompositeVideoClip, ImageClip

logger = logging.getLogger(__name__)

TARGET_W = 1080
TARGET_H = 1920
PRE_SCALE = 1.25           # overshoot so pan/zoom never shows an edge
DEFAULT_ZOOM = 0.10        # 10% zoom over the clip duration

# cycle directions so consecutive scenes don't look identical
DIRECTIONS = ["zoom_in", "pan_right", "zoom_out", "pan_left"]


def _cover_scale(width: int, height: int) -> float:
    """Scale factor that makes an image cover the target with overshoot."""
    return max(TARGET_W / width, TARGET_H / height) * PRE_SCALE


def build_ken_burns_clip(image_path: Path, duration: float, direction: str):
    """Produce a 1080x1920 animated clip of the given duration.

    moviepy 2 removed time-varying crop centers, so pan is done by animating
    the (overscanned) clip's position on a fixed-size canvas instead.
    """
    if direction not in DIRECTIONS:
        direction = "zoom_in"

    safe_duration = max(float(duration), 0.001)

    base = ImageClip(str(image_path)).with_duration(duration)
    bw, bh = base.size
    base = base.resized(_cover_scale(bw, bh))
    bw, bh = base.size

    if direction == "zoom_in":
        def scale(t: float) -> float:
            return 1.0 + DEFAULT_ZOOM * (min(t, safe_duration) / safe_duration)
    elif direction == "zoom_out":
        def scale(t: float) -> float:
            return 1.0 + DEFAULT_ZOOM * (1.0 - min(t, safe_duration) / safe_duration)
    else:
        def scale(t: float) -> float:
            return 1.0

    clip = base.resized(scale) if direction in ("zoom_in", "zoom_out") else base

    travel_x = max(bw - TARGET_W, 0)

    def position(t: float):
        s = scale(min(t, safe_duration))
        w = bw * s
        h = bh * s
        if direction == "pan_right":
            x = -(travel_x * s) * (min(t, safe_duration) / safe_duration)
        elif direction == "pan_left":
            x = -(travel_x * s) * (1.0 - min(t, safe_duration) / safe_duration)
        else:
            x = (TARGET_W - w) / 2.0
        y = (TARGET_H - h) / 2.0
        return (x, y)

    clip = clip.with_position(position)
    logger.debug("Ken Burns %s for %.2fs from %s", direction, duration, image_path)
    return CompositeVideoClip(
        [clip], size=(TARGET_W, TARGET_H)
    ).with_duration(duration)


def direction_for_index(index: int) -> str:
    return DIRECTIONS[index % len(DIRECTIONS)]
