import asyncio
import logging
import sqlite3
from pathlib import Path
from typing import Sequence

import edge_tts
from pydub import AudioSegment
from pydub.effects import normalize

from src.media.schemas import AudioArtifact, SceneAudio, ScriptPayload
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


def _resolve_voice_pool(voice: str | Sequence[str] | None = None) -> list[str]:
    """Return a deterministic rotation pool. The caller should not need to decide."""
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


async def _synthesize_one(
    text: str,
    voice: str,
    out_path: Path,
    rate: str,
    pitch: str,
) -> None:
    """Synthesize a single scene, retrying on transient edge-tts failures."""
    last_err: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            comm = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
            await comm.save(str(out_path))
            if out_path.exists() and out_path.stat().st_size >= MIN_VALID_MP3_BYTES:
                return
            raise RuntimeError(
                f"edge-tts produced empty/short file: "
                f"{out_path} ({out_path.stat().st_size} bytes)"
            )
        except Exception as e:  # noqa: BLE001 — retry any transient error
            last_err = e
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
) -> list[tuple[int, str, Path]]:
    """Synthesize per-scene MP3s. Returns (scene_number, narration, path) tuples."""
    selected_voice = _resolve_voice_pool(voice)[0]
    results: list[tuple[int, str, Path]] = []

    for scene in script.scenes:
        narration = (scene.narration or "").strip()
        if not narration:
            logger.warning(
                "Skipping empty narration for scene %s while synthesizing voiceover.",
                scene.scene_number,
            )
            continue

        out = workspace.scene_audio_path(scene.scene_number)
        await _synthesize_one(narration, selected_voice, out, rate, pitch)
        results.append((scene.scene_number, narration, out))
        await asyncio.sleep(0.4)  # gentle rate-limit against the edge endpoint

    if not results:
        raise ValueError("No valid scene narration was available for TTS generation.")

    return results


def concatenate_scenes(
    scene_results: list[tuple[int, str, Path]],
    out_path: Path,
    voice: str | Sequence[str] | None = None,
    gap_ms: int = SCENE_GAP_MS,
) -> AudioArtifact:
    """Concatenate scene MP3s with small silent gaps, normalize, export."""
    voice_label = _resolve_voice_pool(voice)[0] if voice else DEFAULT_VOICE
    gap = AudioSegment.silent(duration=gap_ms)
    combined = AudioSegment.empty()
    scene_audios: list[SceneAudio] = []

    for scene_number, narration, path in scene_results:
        seg = AudioSegment.from_mp3(path)
        start_ms = len(combined)
        combined += seg
        end_ms = len(combined)
        scene_audios.append(
            SceneAudio(
                scene_number=scene_number,
                path=str(path),
                duration_seconds=(end_ms - start_ms) / 1000.0,
                narration=narration,
            )
        )
        combined += gap

    # trim trailing gap
    if gap_ms > 0 and len(combined) > gap_ms:
        combined = combined[:-gap_ms]

    combined = normalize(combined)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    combined.export(str(out_path), format="mp3", bitrate="192k")

    return AudioArtifact(
        voiceover_path=str(out_path),
        total_duration=len(combined) / 1000.0,
        sample_rate=combined.frame_rate,
        voice=voice_label,
        scenes=scene_audios,
    )


async def generate_voiceover(
    script: ScriptPayload,
    workspace: JobWorkspace,
    voice: str | Sequence[str] | None = None,
    rate: str = DEFAULT_RATE,
    pitch: str = DEFAULT_PITCH,
) -> AudioArtifact:
    """Phase 1 entrypoint: scenes → concatenated voiceover + metadata."""
    selected_voice = choose_voice_for_script(workspace, _resolve_voice_pool(voice))
    scene_results = await synthesize_scenes(
        script, workspace, selected_voice, rate, pitch
    )
    return concatenate_scenes(
        scene_results=scene_results,
        out_path=workspace.voiceover_path,
        voice=selected_voice,
    )