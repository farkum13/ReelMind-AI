import logging
import random
from pathlib import Path

from src.media.compiler import ASSETS_DIR

logger = logging.getLogger(__name__)

MUSIC_DIR = ASSETS_DIR / "music"
SUPPORTED = {".mp3", ".m4a", ".wav", ".ogg"}


def pick_music(mood: str = "", rng: random.Random | None = None) -> Path | None:
    """Return a royalty-free track path matching mood, or any track, or None."""
    if not MUSIC_DIR.exists():
        logger.info("No music directory at %s — skipping background music", MUSIC_DIR)
        return None

    rng = rng or random.Random()
    tracks = [p for p in MUSIC_DIR.iterdir() if p.suffix.lower() in SUPPORTED]
    if not tracks:
        return None

    if mood:
        mood_lower = mood.lower()
        matching = [t for t in tracks if mood_lower in t.stem.lower()]
        if matching:
            return rng.choice(matching)

    return rng.choice(tracks)