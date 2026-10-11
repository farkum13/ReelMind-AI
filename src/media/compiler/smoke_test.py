"""Runnable smoke test for the video compiler.

Builds two synthetic scene images and a silent voiceover, then runs the full
timeline -> compile pipeline and asserts that a 1080x1920 MP4 containing both
a video and an audio stream is produced.

Run:
    env PYTHONPATH="$PWD" .venv/bin/python -m src.media.compiler.smoke_test
"""

import logging
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from src.media.audio.tts_main import ensure_ffmpeg
from src.media.compiler.assemble import compile_video
from src.media.compiler.captions import _resolve_font
from src.media.compiler.timeline import build_timeline
from src.media.paths import JobWorkspace
from src.media.schemas import (
    AudioArtifact,
    SceneAudio,
    SceneVisual,
    SubtitleArtifact,
    TranscriptSegment,
    VisualArtifact,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

logger = logging.getLogger(__name__)

AUDIO_SECONDS = 4.0
SCENE_DURATION = 2.0
SCENE_COLORS = [(32, 84, 150), (150, 84, 32)]
SCENE_LABELS = ["SCENE ONE", "SCENE TWO"]


def _font(size: int):
    path = _resolve_font("en")
    if path:
        return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _make_image(path: Path, color: tuple[int, int, int], label: str) -> None:
    img = Image.new("RGB", (1024, 1792), color)
    draw = ImageDraw.Draw(img)
    draw.rectangle([80, 700, 944, 1092], outline=(255, 255, 255), width=8)
    draw.text((140, 840), label, fill=(255, 255, 255), font=_font(72))
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "PNG")


def _make_silent_mp3(path: Path, seconds: float) -> None:
    from pydub import AudioSegment

    ensure_ffmpeg()
    path.parent.mkdir(parents=True, exist_ok=True)
    AudioSegment.silent(duration=int(seconds * 1000)).export(str(path), format="mp3")


def _probe_streams(path: Path) -> set[str]:
    ensure_ffmpeg()
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr.strip()}")
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        ws = JobWorkspace.new(root=Path(tmp)).create()

        images = [ws.scene_image_path(i + 1) for i in range(len(SCENE_LABELS))]
        for path, color, label in zip(images, SCENE_COLORS, SCENE_LABELS):
            _make_image(path, color, label)

        _make_silent_mp3(ws.voiceover_path, AUDIO_SECONDS)

        audio = AudioArtifact(
            voiceover_path=str(ws.voiceover_path),
            total_duration=AUDIO_SECONDS,
            sample_rate=24000,
            voice="en-IN-PrabhatNeural",
            scenes=[
                SceneAudio(
                    scene_number=i + 1,
                    path=str(images[i]),
                    duration_seconds=SCENE_DURATION,
                    narration=f"scene {i + 1}",
                    start_seconds=i * SCENE_DURATION,
                )
                for i in range(len(images))
            ],
        )
        visuals = VisualArtifact(
            model="smoke", width=1024, height=1792,
            scenes=[
                SceneVisual(
                    scene_number=i + 1, image_path=str(images[i]), prompt="",
                    seed=i, model="smoke", width=1024, height=1792,
                )
                for i in range(len(images))
            ],
        )
        subtitles = SubtitleArtifact(
            srt_path="", language="en",
            segments=[
                TranscriptSegment(text="hello world", start=0.0, end=2.0),
                TranscriptSegment(text="this is a smoke test", start=2.0, end=4.0),
            ],
        )

        timeline = build_timeline(audio, visuals)
        assert len(timeline) == len(images), timeline
        assert timeline[0].duration == SCENE_DURATION, timeline[0]

        out = compile_video(timeline, audio, subtitles, ws, language="en", music_path=None)

        assert out.exists(), f"missing output: {out}"
        size = out.stat().st_size
        assert size > 10_000, f"output too small: {size} bytes"

        streams = _probe_streams(out)
        assert "video" in streams and "audio" in streams, streams

        logger.info(
            "smoke_test OK: %s (%d bytes), streams=%s, timeline=%s",
            out.name, size, sorted(streams),
            [(t.scene_number, round(t.start, 2), round(t.end, 2)) for t in timeline],
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
