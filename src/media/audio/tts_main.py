import asyncio
import json
import logging
import os
import shutil
import sqlite3
import statistics
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

import edge_tts
from pydub import AudioSegment
from pydub.effects import normalize

from src.media.schemas import (
    AudioArtifact,
    SceneAudio,
    ScriptPayload,
    TranscriptSegment,
    WordTiming,
)
from src.media.paths import JobWorkspace

logger = logging.getLogger(__name__)

VOICE_MAP = {
    "en-IN": "en-IN-PrabhatNeural",
    "en-IN-f": "en-IN-NeerjaNeural",
    "hi-IN": "hi-IN-MadhurNeural",
    "hi-IN-f": "hi-IN-SwaraNeural",
}

VOICE_SEQUENCE = [
    "en-IN-PrabhatNeural",
    "en-IN-NeerjaNeural",
    "hi-IN-MadhurNeural",
    "hi-IN-SwaraNeural",
]

DEFAULT_VOICE = "en-IN-PrabhatNeural"
DEFAULT_RATE = "+8%"
DEFAULT_PITCH = "+0Hz"
SCENE_GAP_MS = 200
MAX_RETRIES = 3
RETRY_BASE_DELAY = 0.6
MIN_VALID_MP3_BYTES = 1024

# edge-tts emits timings in 100-nanosecond ticks
TICKS_PER_SEC = 10_000_000

# edge-tts treats WordBoundary and SentenceBoundary as mutually exclusive: the
# requested boundary type decides which metadata the service streams back.
# We ask for words because sentence spans are recoverable from word timings,
# while word timings cannot be recovered from sentence timings at all.
EDGE_BOUNDARY = "WordBoundary"

# Sentence reconstruction. WordBoundary tokens carry no punctuation, so
# sentence breaks are detected from the pause between consecutive words.
SENTENCE_GAP_S = 0.42
SENTENCE_GAP_RATIO = 3.0
SENTENCE_MAX_WORDS = 12
SENTENCE_MAX_CHARS = 84
SENTENCE_TAIL_PAD_S = 0.35
SENTENCE_TERMINATORS = (".", "!", "?", "…", "।", "॥")

# Caption timing guards applied while assembling the global transcript.
MIN_CUE_S = 0.60
MIN_WORD_CUE_S = 0.10


# ---------------------------------------------------------------------------
# Timing dataclasses
# ---------------------------------------------------------------------------
@dataclass
class TTSChunk:
    text: str
    start: float  # seconds, relative to the scene audio
    end: float
    # Half-open index range into the owning word list. Tracking indices instead
    # of re-matching on spans avoids silently dropping words whose timing
    # slightly overruns the sentence boundary.
    first_word: int = 0
    last_word: int = 0


@dataclass
class TTSResult:
    path: Path
    words: list[TTSChunk] = field(default_factory=list)
    sentences: list[TTSChunk] = field(default_factory=list)
    duration: float = 0.0
    scene_number: int = 0
    narration: str = ""

    # Per-scene timings are persisted so the voiceover can be re-concatenated
    # (changed gap, new loudness target) without re-hitting edge-tts, which is
    # the rate-limited and failure-prone step.
    def to_dict(self) -> dict:
        return {
            "scene_number": self.scene_number,
            "narration": self.narration,
            "duration": self.duration,
            "words": [
                {"text": w.text, "start": w.start, "end": w.end} for w in self.words
            ],
            "sentences": [
                {
                    "text": s.text,
                    "start": s.start,
                    "end": s.end,
                    "first_word": s.first_word,
                    "last_word": s.last_word,
                }
                for s in self.sentences
            ],
        }

    def save(self, out_path: Path) -> None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(self.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, out_path: Path, audio_path: Path | None = None) -> "TTSResult":
        data = json.loads(out_path.read_text(encoding="utf-8"))
        return cls(
            path=audio_path or Path(data.get("audio_path", out_path.with_suffix(".mp3"))),
            words=[TTSChunk(**w) for w in data.get("words", [])],
            sentences=[TTSChunk(**s) for s in data.get("sentences", [])],
            duration=float(data.get("duration", 0.0)),
            scene_number=int(data.get("scene_number", 0)),
            narration=data.get("narration", ""),
        )


# ---------------------------------------------------------------------------
# ffmpeg discovery
# ---------------------------------------------------------------------------
def ensure_ffmpeg() -> None:
    """Make ffmpeg/ffprobe resolvable by pydub.

    pydub locates its converters with `shutil.which()` against PATH and ignores
    `AudioSegment.converter` / `AudioSegment.ffprobe` for probing, so a venv-local
    install is invisible unless its bin directory is on PATH. `.venv/bin` is
    absent from PATH unless the virtualenv is activated, which otherwise makes
    `AudioSegment.from_mp3` raise FileNotFoundError('ffprobe').
    """
    # Do not resolve sys.executable: in a venv it is a symlink to the base
    # interpreter, and resolving it would point outside the virtualenv.
    bindir = Path(sys.executable).parent
    missing = [b for b in ("ffmpeg", "ffprobe") if shutil.which(b) is None]
    if not missing:
        return

    absent = [b for b in missing if not (bindir / b).exists()]
    if len(absent) < len(missing):
        bindir_str = str(bindir)
        if bindir_str not in os.environ.get("PATH", "").split(os.pathsep):
            os.environ["PATH"] = os.pathsep.join(
                [bindir_str, *os.environ.get("PATH", "").split(os.pathsep)]
            )
        logger.debug("Prepended %s to PATH for pydub.", bindir_str)
    if absent:
        logger.warning(
            "Missing %s; pydub audio I/O will fail. Install it or add it to PATH.",
            ", ".join(absent),
        )


# ---------------------------------------------------------------------------
# Voice rotation (unchanged)
# ---------------------------------------------------------------------------
def _resolve_voice_pool(voice: str | Sequence[str] | None = None) -> list[str]:
    if voice is None:
        return list(VOICE_SEQUENCE)
    if isinstance(voice, str):
        if voice in VOICE_MAP:
            return [VOICE_MAP[voice]]
        if voice in VOICE_SEQUENCE:
            return [voice]
        return list(VOICE_SEQUENCE)
    pool = [v for v in voice if v]
    return pool or list(VOICE_SEQUENCE)


def choose_voice_for_script(
    workspace: JobWorkspace,
    voice_pool: Sequence[str] | None = None,
) -> str:
    """Reserve one voice for a script and persist the next turn across runs."""
    pool = list(_resolve_voice_pool(voice_pool))
    if not pool:
        raise ValueError("At least one voice is required for TTS generation.")

    database_path = workspace.voice_rotation_db_path
    database_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS voice_rotation ("
            "id INTEGER PRIMARY KEY CHECK (id = 1), next_index INTEGER NOT NULL)"
        )
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT next_index FROM voice_rotation WHERE id = 1"
        ).fetchone()
        voice_index = row[0] % len(pool) if row else 0
        connection.execute(
            "INSERT INTO voice_rotation (id, next_index) VALUES (1, ?) "
            "ON CONFLICT(id) DO UPDATE SET next_index = excluded.next_index",
            ((voice_index + 1) % len(pool),),
        )
    return pool[voice_index]


# ---------------------------------------------------------------------------
# Sentence grouping from word timings
# ---------------------------------------------------------------------------
def _sentence_threshold(words: list[TTSChunk]) -> float:
    """Adaptive inter-sentence silence threshold, in seconds.

    A fixed threshold cannot serve both slow narration (where it never trips)
    and fast narration (where it splits mid-phrase), so this scales with the
    median inter-word gap.

    The median is taken over *all* gaps, including the zero-length ones that
    contiguous words produce. Filtering those out first would leave only the
    outliers, and the outlier would then define the threshold that is supposed
    to detect it.
    """
    if len(words) < 2:
        return SENTENCE_GAP_S
    gaps = [words[i + 1].start - words[i].end for i in range(len(words) - 1)]
    return max(SENTENCE_GAP_S, statistics.median(gaps) * SENTENCE_GAP_RATIO)


def _make_sentence(
    words: list[TTSChunk],
    first: int,
    last: int,
) -> TTSChunk:
    return TTSChunk(
        text=" ".join(w.text for w in words[first:last]).strip(),
        start=words[first].start,
        end=words[last - 1].end,
        first_word=first,
        last_word=last,
    )


def group_words_into_sentences(
    words: list[TTSChunk],
    gap_s: float = SENTENCE_GAP_S,
    max_words: int = SENTENCE_MAX_WORDS,
    max_chars: int = SENTENCE_MAX_CHARS,
) -> list[TTSChunk]:
    """Rebuild sentence spans from word timings.

    Splits on a silent pause between words, and additionally on a hard word or
    character budget so a long unpunctuated run still yields readable cues.
    Punctuation is checked too, but only as a bonus: edge-tts WordBoundary
    tokens do not currently include it.
    """
    if not words:
        return []

    threshold = _sentence_threshold(words)
    sentences: list[TTSChunk] = []
    start = 0
    chars = 0

    for i in range(1, len(words)):
        prev, cur = words[i - 1], words[i]
        chars += len(prev.text) + 1
        ended = prev.text.rstrip().endswith(SENTENCE_TERMINATORS)
        long_enough = (i - start) >= max_words or chars > max_chars
        if (cur.start - prev.end) >= threshold or ended or long_enough:
            sentences.append(_make_sentence(words, start, i))
            start = i
            chars = 0

    sentences.append(_make_sentence(words, start, len(words)))
    return sentences


# ---------------------------------------------------------------------------
# Core synthesis (captures word timings while writing audio)
# ---------------------------------------------------------------------------
async def _synthesize_one(
    text: str,
    voice: str,
    out_path: Path,
    rate: str,
    pitch: str,
) -> TTSResult:
    """Synthesize a single scene, retrying on transient edge-tts failures.

    Streams edge-tts events once: audio chunks go to disk and WordBoundary
    timings are collected in the same pass.
    """
    last_err: Exception | None = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            words: list[TTSChunk] = []
            sentences: list[TTSChunk] = []

            comm = edge_tts.Communicate(
                text, voice, rate=rate, pitch=pitch, boundary=EDGE_BOUNDARY
            )
            with open(out_path, "wb") as f:
                async for chunk in comm.stream():
                    ctype = chunk.get("type")
                    if ctype == "audio":
                        f.write(chunk["data"])
                    elif ctype == "WordBoundary":
                        start = chunk["offset"] / TICKS_PER_SEC
                        end = (chunk["offset"] + chunk["duration"]) / TICKS_PER_SEC
                        words.append(TTSChunk(text=chunk["text"], start=start, end=end))
                    elif ctype == "SentenceBoundary":
                        start = chunk["offset"] / TICKS_PER_SEC
                        end = (chunk["offset"] + chunk["duration"]) / TICKS_PER_SEC
                        sentences.append(
                            TTSChunk(text=chunk["text"], start=start, end=end)
                        )

            size = out_path.stat().st_size if out_path.exists() else 0
            if size < MIN_VALID_MP3_BYTES:
                raise RuntimeError(
                    f"edge-tts produced empty/short file: {out_path} ({size} bytes)"
                )

            # WordBoundary is requested, so sentences are normally rebuilt from
            # word timings. Keep the fallback explicit in case the service ever
            # streams sentence metadata instead.
            if sentences:
                logger.warning(
                    "%s returned SentenceBoundary despite requesting %s; "
                    "word timings will be derived by span matching.",
                    out_path.name, EDGE_BOUNDARY,
                )
            else:
                sentences = group_words_into_sentences(words)

            if not words:
                logger.warning(
                    "No word timings captured for %s — edge-tts may have changed "
                    "its event schema. Audio was written but captions will be empty.",
                    out_path.name,
                )

            return TTSResult(path=out_path, words=words, sentences=sentences)

        except Exception as e:  # noqa: BLE001 — retry any transient error
            last_err = e
            out_path.unlink(missing_ok=True)  # drop the partial MP3
            logger.warning(
                "TTS attempt %d/%d failed for %s: %s",
                attempt, MAX_RETRIES, out_path.name, e,
            )
            await asyncio.sleep(RETRY_BASE_DELAY * attempt)

    raise RuntimeError(f"TTS failed after {MAX_RETRIES} attempts: {last_err}")


async def synthesize_scenes(
    script: ScriptPayload,
    workspace: JobWorkspace,
    voice: str | Sequence[str] | None = None,
    rate: str = DEFAULT_RATE,
    pitch: str = DEFAULT_PITCH,
) -> list[tuple[int, str, TTSResult]]:
    """Synthesize per-scene MP3s with word timings.

    Returns (scene_number, narration, TTSResult) tuples.
    """
    selected_voice = _resolve_voice_pool(voice)[0]
    results: list[tuple[int, str, TTSResult]] = []

    for scene in script.scenes:
        narration = (scene.narration or "").strip()
        if not narration:
            logger.warning(
                "Skipping empty narration for scene %s while synthesizing voiceover.",
                scene.scene_number,
            )
            continue

        out = workspace.scene_audio_path(scene.scene_number)
        tts_result = await _synthesize_one(narration, selected_voice, out, rate, pitch)
        tts_result.scene_number = scene.scene_number
        tts_result.narration = narration
        tts_result.save(workspace.scene_timing_path(scene.scene_number))
        results.append((scene.scene_number, narration, tts_result))
        await asyncio.sleep(0.4)  # gentle rate-limit against the edge endpoint

    if not results:
        raise ValueError("No valid scene narration was available for TTS generation.")

    return results


def load_scene_results(
    script: ScriptPayload,
    workspace: JobWorkspace,
) -> list[tuple[int, str, TTSResult]]:
    """Reload previously synthesized scenes and their timings from disk.

    Lets the voiceover be re-concatenated (different gap, different mix) without
    re-running edge-tts. Raises FileNotFoundError if any scene is missing, so
    callers fall back to synthesis rather than producing a partial voiceover.
    """
    results: list[tuple[int, str, TTSResult]] = []

    for scene in script.scenes:
        narration = (scene.narration or "").strip()
        if not narration:
            continue

        audio_path = workspace.scene_audio_path(scene.scene_number)
        timing_path = workspace.scene_timing_path(scene.scene_number)
        if not audio_path.exists() or not timing_path.exists():
            raise FileNotFoundError(
                f"Scene {scene.scene_number} has no cached audio/timings at "
                f"{audio_path} / {timing_path}; re-run synthesis."
            )

        tts_result = TTSResult.load(timing_path, audio_path=audio_path)
        tts_result.scene_number = scene.scene_number
        tts_result.narration = narration
        results.append((scene.scene_number, narration, tts_result))

    if not results:
        raise ValueError("No valid scene narration was available for TTS generation.")

    return results


# ---------------------------------------------------------------------------
# Global transcript assembly
# ---------------------------------------------------------------------------
def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(value, high))


def _segments_from_scene(
    tts: TTSResult,
    scene_start: float,
    scene_end: float,
) -> list[TranscriptSegment]:
    """Shift one scene's sentences and words into global audio time.

    Every value is clamped to the scene's real decoded span, because edge-tts
    word timings can overrun the MP3 (encoder padding) and the final word
    normally stops short of the trailing audio.
    """
    segments: list[TranscriptSegment] = []

    for position, sentence in enumerate(tts.sentences):
        if sentence.last_word > sentence.first_word:
            words = tts.words[sentence.first_word:sentence.last_word]
        else:
            words = _words_overlapping(tts.words, sentence)

        start = _clamp(sentence.start + scene_start, scene_start, scene_end)
        end = _clamp(sentence.end + scene_start, start, scene_end)

        # Let the final cue of a scene cover its trailing audio instead of
        # cutting out while the narrator is still settling.
        if position == len(tts.sentences) - 1 and words:
            tail = _clamp(
                words[-1].end + SENTENCE_TAIL_PAD_S + scene_start, end, scene_end
            )
            end = max(end, tail)

        segment_words: list[WordTiming] = []
        for w in words:
            w_start = _clamp(w.start + scene_start, start, end)
            w_end = _clamp(w.end + scene_start, w_start, end)
            if w_end <= w_start:
                continue
            segment_words.append(WordTiming(word=w.text, start=w_start, end=w_end))

        segments.append(
            TranscriptSegment(
                text=sentence.text,
                start=start,
                end=end,
                words=segment_words,
            )
        )

    return segments


def _words_overlapping(words: list[TTSChunk], sentence: TTSChunk) -> list[TTSChunk]:
    """Fallback word lookup by span overlap, used only for service-supplied
    sentence metadata that carries no word index range."""
    return [
        w for w in words
        if w.start < sentence.end and w.end > sentence.start
    ]


def _enforce_cue_order(
    segments: list[TranscriptSegment],
    total_duration: float,
) -> list[TranscriptSegment]:
    """Sort cues and make them monotonic, non-overlapping and in-bounds.

    SRT requires strictly increasing, non-overlapping cues. Without this a
    scene whose timings overrun its decoded audio produces cues that run past
    the end of the audio or step backwards in time.
    """
    ordered = sorted(segments, key=lambda s: (s.start, s.end))
    cleaned: list[TranscriptSegment] = []
    previous_end = 0.0

    for segment in ordered:
        text = segment.text.strip()
        start = _clamp(segment.start, previous_end, total_duration)
        end = _clamp(segment.end, start, total_duration)

        if not text or end <= start:
            logger.debug("Dropping degenerate caption cue %r.", segment.text[:40])
            continue

        if end - start < MIN_CUE_S:
            end = _clamp(start + MIN_CUE_S, start, total_duration)

        if end <= start:
            continue

        kept: list[WordTiming] = []
        word_floor = start
        for w in segment.words:
            w_start = _clamp(w.start, word_floor, end)
            w_end = _clamp(w.end, w_start, end)
            if w_end < w_start:
                continue
            if w_end - w_start < MIN_WORD_CUE_S:
                w_end = _clamp(w_start + MIN_WORD_CUE_S, w_start, end)
            if w_end <= w_start:
                continue
            kept.append(WordTiming(word=w.word, start=w_start, end=w_end))
            word_floor = w_end

        cleaned.append(
            TranscriptSegment(text=text, start=start, end=end, words=kept)
        )
        previous_end = end

    return cleaned


def concatenate_scenes(
    scene_results: list[tuple[int, str, TTSResult]],
    out_path: Path,
    voice: str | Sequence[str] | None = None,
    gap_ms: int = SCENE_GAP_MS,
) -> tuple[AudioArtifact, list[TranscriptSegment]]:
    """Concatenate scene MP3s with small silent gaps, normalize, export.

    Returns:
        (AudioArtifact, list[TranscriptSegment])
        where every segment's start/end and word timings are offset into
        the global concatenated-audio timeline.
    """
    ensure_ffmpeg()

    voice_label = _resolve_voice_pool(voice)[0] if voice else DEFAULT_VOICE
    gap = AudioSegment.silent(duration=gap_ms)
    combined = AudioSegment.empty()

    scene_audios: list[SceneAudio] = []
    all_segments: list[TranscriptSegment] = []

    for scene_number, narration, tts in scene_results:
        seg = AudioSegment.from_mp3(tts.path)
        start_ms = len(combined)
        combined += seg
        end_ms = len(combined)

        scene_duration = (end_ms - start_ms) / 1000.0
        tts.duration = scene_duration
        # Refresh the cache now that the true decoded duration is known.
        timing_path = tts.path.with_suffix(".timings.json")
        if timing_path.exists():
            tts.save(timing_path)

        scene_audios.append(
            SceneAudio(
                scene_number=scene_number,
                path=str(tts.path),
                duration_seconds=scene_duration,
                narration=narration,
                start_seconds=start_ms / 1000.0,
            )
        )

        all_segments.extend(
            _segments_from_scene(
                tts,
                scene_start=start_ms / 1000.0,
                scene_end=end_ms / 1000.0,
            )
        )

        combined += gap

    # trim trailing gap
    if gap_ms > 0 and len(combined) > gap_ms:
        combined = combined[:-gap_ms]

    combined = normalize(combined)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    combined.export(str(out_path), format="mp3", bitrate="192k")

    total_duration = len(combined) / 1000.0
    all_segments = _enforce_cue_order(all_segments, total_duration)

    audio_artifact = AudioArtifact(
        voiceover_path=str(out_path),
        total_duration=total_duration,
        sample_rate=combined.frame_rate,
        voice=voice_label,
        scenes=scene_audios,
    )

    return audio_artifact, all_segments


# ---------------------------------------------------------------------------
# Phase 1 entrypoint
# ---------------------------------------------------------------------------
async def generate_voiceover(
    script: ScriptPayload,
    workspace: JobWorkspace,
    voice: str | Sequence[str] | None = None,
    rate: str = DEFAULT_RATE,
    pitch: str = DEFAULT_PITCH,
) -> tuple[AudioArtifact, list[TranscriptSegment]]:
    """Phase 1 entrypoint: scenes → concatenated voiceover + global segments.

    Returns (AudioArtifact, list[TranscriptSegment]). The pipeline writes the
    SRT and transcript.json from these segments — no Whisper pass required.
    """
    ensure_ffmpeg()
    selected_voice = choose_voice_for_script(workspace, _resolve_voice_pool(voice))
    scene_results = await synthesize_scenes(
        script, workspace, selected_voice, rate, pitch
    )
    return concatenate_scenes(
        scene_results=scene_results,
        out_path=workspace.voiceover_path,
        voice=selected_voice,
    )