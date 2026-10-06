"""Phase 1+2 of the media pipeline: voiceover synthesis, then subtitles.

Phase 1 synthesizes per-scene MP3s with edge-tts and concatenates them while
shifting every word timing into the global audio timeline.
Phase 2 writes sentence-level SRT plus the word-level karaoke track from those
same timings, so no Whisper pass is needed.
"""

import json
import logging
from pathlib import Path

from src.media.audio.tts_main import (
    SCENE_GAP_MS,
    concatenate_scenes,
    generate_voiceover,
    load_scene_results,
)
from src.media.paths import JobWorkspace
from src.media.schemas import (
    JobArtifacts,
    ScriptPayload,
    SubtitleArtifact,
    TranscriptSegment,
)
from src.media.subtitle.main import write_srt, write_transcript

logger = logging.getLogger(__name__)


async def build_media(
    script: ScriptPayload,
    workspace: JobWorkspace,
    artifacts: JobArtifacts | None = None,
    voice: str | None = None,
    word_level_srt: bool = True,
) -> JobArtifacts:
    """Run voiceover + subtitle generation for one script.

    This is the pipeline entrypoint. `build_media_for_script` is only a
    disk-reading convenience wrapper around it.

    Writes into `workspace`:
        audio/voiceover.mp3        concatenated voiceover
        audio/scenes/scene_NN.mp3  per-scene voiceover
        audio/transcript.json      word-level timings
        subtitles/captions.srt     sentence-level captions
        subtitles/captions.word.srt  karaoke captions
    """
    artifacts = artifacts or JobArtifacts(job_id=workspace.job_id)
    workspace.create()

    # Phase 1 — audio (and word timings, captured during synthesis).
    audio, segments = await generate_voiceover(script, workspace, voice=voice)
    artifacts.audio = audio
    logger.info(
        "Voiceover ready: %.2fs across %d scene(s), %d caption cue(s).",
        audio.total_duration, len(audio.scenes), len(segments),
    )

    if not segments:
        logger.warning(
            "No caption timings were produced; subtitles will be empty."
        )

    # Phase 2 — format the timings we already have as SRT.
    write_srt(segments, workspace.srt_path)
    write_transcript(segments, workspace.transcript_path)

    word_srt_path = None
    if word_level_srt and any(seg.words for seg in segments):
        write_srt(segments, workspace.word_srt_path, word_level=True)
        word_srt_path = str(workspace.word_srt_path)

    artifacts.subtitles = SubtitleArtifact(
        srt_path=str(workspace.srt_path),
        word_srt_path=word_srt_path,
        language=script.language,
        model_size="edge-tts",
        segments=segments,
    )

    workspace.meta_path.write_text(
        json.dumps(artifacts.model_dump(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    return artifacts


async def build_media_for_script(
    script_path: Path,
    root: Path | None = None,
    voice: str | None = None,
) -> JobArtifacts:
    """Load a script.json from disk and build its media in a fresh job.

    This is a convenience wrapper, NOT the pipeline entrypoint. `build_media`
    is: the agents pipeline hands over an in-memory ScriptPayload, so that is
    what production calls. Use this only for one-off/manual runs.

    Caveat: it always mints a new job_id via JobWorkspace.new(), so it cannot
    target an existing job directory and therefore cannot resume or re-render
    one. To re-render an existing job, call `build_media` with a JobWorkspace
    built for that job_id instead.
    """
    workspace = JobWorkspace.new(root=root) if root else JobWorkspace.new()
    script = ScriptPayload.model_validate_json(
        Path(script_path).read_text(encoding="utf-8")
    )
    return await build_media(script, workspace, voice=voice)


def remix_voiceover(
    script: ScriptPayload,
    workspace: JobWorkspace,
    gap_ms: int | None = None,
) -> tuple[JobArtifacts | None, list[TranscriptSegment]]:
    """Re-concatenate an existing voiceover from cached scene audio + timings.

    Changes the inter-scene gap (or any future mix setting) without calling
    edge-tts again, because edge-tts is the rate-limited step. Returns
    (None, []) when the cache is incomplete and synthesis is required.
    """
    try:
        scene_results = load_scene_results(script, workspace)
    except FileNotFoundError as exc:
        logger.info("No usable scene cache (%s); re-synthesis required.", exc)
        return None, []

    audio, segments = concatenate_scenes(
        scene_results=scene_results,
        out_path=workspace.voiceover_path,
        gap_ms=SCENE_GAP_MS if gap_ms is None else gap_ms,
    )
    logger.info(
        "Re-concatenated voiceover from cache: %.2fs, %d cue(s).",
        audio.total_duration, len(segments),
    )
    return audio, segments