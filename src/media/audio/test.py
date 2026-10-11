"""Smoke test for the voiceover + caption pipeline.

Runs the real orchestration against a fake edge-tts so no network calls are
made, then asserts voice rotation, scene handling and caption timing sanity.

Run with: python -m src.media.audio.test
"""

import asyncio
import json
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from pydub import AudioSegment

from src.media.audio.tts_main import (
    TICKS_PER_SEC,
    VOICE_SEQUENCE,
    concatenate_scenes,
    generate_voiceover,
    load_scene_results,
)
from src.media.main import build_media, remix_voiceover
from src.media.paths import JobWorkspace
from src.media.schemas import ScriptPayload, ScriptScene
from src.media.subtitle.main import format_timestamp

WORD_SECONDS = 0.30
TAIL_SECONDS = 0.25
MIN_VALID_MP3_BYTES = 1024
TOLERANCE_S = 0.002


def assert_close(actual: float, expected: float, label: str) -> None:
    assert abs(actual - expected) <= TOLERANCE_S, (
        f"{label}: {actual} != {expected} (tolerance {TOLERANCE_S})"
    )


class FakeCommunicate:
    """Stands in for edge_tts.Communicate, streaming audio + WordBoundary events.

    The audio payload length encodes the scene duration in milliseconds, so the
    patched `AudioSegment.from_mp3` can recover the real duration from the file
    without the fake needing to know the output path.
    """

    voices: list[str] = []

    def __init__(self, text: str, voice: str, rate: str | None = None,
                 pitch: str | None = None, boundary: str | None = None):
        self.text = text
        self.voice = voice
        self.rate = rate
        self.pitch = pitch
        self.boundary = boundary
        self.words = text.split()
        FakeCommunicate.voices.append(voice)

    def _duration_ms(self) -> int:
        offset = TAIL_SECONDS * 1000
        for index, _ in enumerate(self.words):
            offset += WORD_SECONDS * 1000
            # A long pause after every third word so sentence grouping has
            # something real to detect.
            if index and index % 3 == 0:
                offset += 550
        return int(offset)

    async def stream(self):
        assert self.boundary == "WordBoundary", (
            f"word timings require boundary='WordBoundary', got {self.boundary!r}"
        )

        offset = 0.0
        for index, word in enumerate(self.words):
            if index and index % 3 == 0:
                offset += 0.55
            start = offset
            end = start + WORD_SECONDS
            offset = end
            yield {
                "type": "WordBoundary",
                "text": word,
                "offset": int(start * TICKS_PER_SEC),
                "duration": int(WORD_SECONDS * TICKS_PER_SEC),
            }

        yield {
            "type": "audio",
            "data": b"\x00" * max(self._duration_ms(), MIN_VALID_MP3_BYTES),
        }


def main() -> None:
    script = ScriptPayload(
        title="One voice per script smoke test",
        scenes=[
            ScriptScene(scene_number=1, narration="First scene intro for testing."),
            ScriptScene(scene_number=2, narration="Second scene keeps the flow moving."),
            ScriptScene(scene_number=3, narration="   "),
        ],
    )

    # Mock external dependencies so the flow runs without real network calls.
    import src.media.audio.tts_main as tts_main

    original_comm = tts_main.edge_tts.Communicate
    original_from_mp3 = tts_main.AudioSegment.from_mp3
    original_export = tts_main.AudioSegment.export
    original_normalize = tts_main.normalize

    try:
        FakeCommunicate.voices.clear()
        tts_main.edge_tts.Communicate = FakeCommunicate
        tts_main.AudioSegment.from_mp3 = staticmethod(
            lambda path: AudioSegment.silent(
                duration=max(Path(path).stat().st_size, MIN_VALID_MP3_BYTES)
            )
        )
        tts_main.AudioSegment.export = (
            lambda self, path, *args, **kwargs: Path(path).write_bytes(
                b"final-voiceover-data-" * 200
            )
        )
        tts_main.normalize = lambda segment: segment

        with TemporaryDirectory() as temp_dir:
            storage_root = Path(temp_dir)
            artifacts = []
            workspaces = []
            for _ in range(len(VOICE_SEQUENCE) + 1):
                workspace = JobWorkspace.new(root=storage_root).create()
                workspaces.append(workspace)
                artifacts.append(asyncio.run(build_media(script, workspace, with_visuals=False)))

            # --- voice rotation -------------------------------------------------
            assert all(Path(a.audio.voiceover_path).exists() for a in artifacts)
            expected_script_voices = VOICE_SEQUENCE + [VOICE_SEQUENCE[0]]
            assert [a.audio.voice for a in artifacts] == expected_script_voices
            assert FakeCommunicate.voices == [
                voice for voice in expected_script_voices for _ in range(2)
            ], FakeCommunicate.voices
            assert all(len(a.audio.scenes) == 2 for a in artifacts)
            assert all(
                Path(a.audio.voiceover_path).parents[2].name == date.today().isoformat()
                for a in artifacts
            ), "voiceover must live under the job's date directory"

            # --- caption timings ------------------------------------------------
            first = artifacts[0]
            segments = first.subtitles.segments
            assert segments, "no caption segments produced"
            assert all(seg.words for seg in segments), "word timings missing"
            assert all(seg.text.strip() for seg in segments)
            for a, b in zip(segments, segments[1:]):
                assert a.end <= b.start, f"overlapping cues: {a.end} > {b.start}"
                assert a.start <= a.end
            assert all(
                seg.end <= first.audio.total_duration for seg in segments
            ), "cue extends past the end of the audio"

            # --- subtitle artifacts ---------------------------------------------
            srt_path = Path(first.subtitles.srt_path)
            assert srt_path.exists() and srt_path.stat().st_size > 0
            srt = srt_path.read_text(encoding="utf-8")
            assert ",1000" not in srt, "invalid millisecond rollover in SRT"
            assert srt.strip().splitlines()[0] == "1"

            word_srt_path = Path(first.subtitles.word_srt_path)
            assert word_srt_path.exists() and word_srt_path.stat().st_size > 0
            word_cues = word_srt_path.read_text(encoding="utf-8").count(" --> ")
            assert word_cues == sum(len(seg.words) for seg in segments)

            transcript_path = workspaces[0].transcript_path
            assert transcript_path.exists() and transcript_path.stat().st_size > 0
            assert workspaces[0].meta_path.exists()

            # --- per-scene timing cache + start_seconds -------------------------
            first_ws = workspaces[0]
            assert first.audio.scenes[0].start_seconds == 0.0
            assert_close(
                first.audio.scenes[1].start_seconds,
                first.audio.scenes[0].duration_seconds + 0.2,
                "scene 2 start_seconds (200ms gap)",
            )

            for scene in first.audio.scenes:
                timing_path = first_ws.scene_timing_path(scene.scene_number)
                assert timing_path.exists(), f"missing {timing_path}"
                payload = json.loads(timing_path.read_text(encoding="utf-8"))
                assert payload["words"], "cached scene has no word timings"
                assert payload["scene_number"] == scene.scene_number
                assert payload["narration"] == scene.narration
                assert_close(
                    payload["duration"], scene.duration_seconds,
                    f"scene {scene.scene_number} cached duration",
                )

            # --- reload from cache, re-concatenate with a wider gap --------------
            cached = load_scene_results(script, first_ws)
            assert [s for s, _, _ in cached] == [s.scene_number for s in first.audio.scenes]
            assert all(r.words and r.sentences for _, _, r in cached)

            re_audio, re_segments = concatenate_scenes(
                scene_results=cached,
                out_path=first_ws.voiceover_path,
                voice=first.audio.voice,
                gap_ms=600,
            )
            # Re-concatenating at 600ms must shift only scene 2's cues, by
            # exactly the gap delta; scene 1 is untouched and no cue is lost.
            shift = (
                re_audio.scenes[1].start_seconds - first.audio.scenes[1].start_seconds
            )
            assert_close(shift, 0.4, "scene 2 shift from wider gap")
            assert len(re_segments) == len(segments), "cue count changed on re-concat"

            original_scene1_end = first.audio.scenes[0].duration_seconds
            for before, after in zip(segments, re_segments):
                assert before.text == after.text, "cue text changed on re-concat"
                assert len(before.words) == len(after.words)
                assert_close(
                    after.start - before.start,
                    0.0 if before.start < original_scene1_end else shift,
                    f"cue {before.text[:20]!r} start delta",
                )
            assert_close(
                re_audio.scenes[1].start_seconds,
                re_audio.scenes[0].duration_seconds + 0.6,
                "scene 2 start_seconds (600ms gap)",
            )
            assert re_audio.total_duration > first.audio.total_duration
            print(
                f"recat_gap_600ms_total={re_audio.total_duration:.2f}s "
                f"(was {first.audio.total_duration:.2f}s at 200ms)"
            )

            # a missing scene must force re-synthesis rather than a partial mix
            first_ws.scene_timing_path(2).unlink()
            assert remix_voiceover(script, first_ws) == (None, [])
    finally:
        tts_main.edge_tts.Communicate = original_comm
        tts_main.AudioSegment.from_mp3 = original_from_mp3
        tts_main.AudioSegment.export = original_export
        tts_main.normalize = original_normalize

    print(f"script_voices={[a.audio.voice for a in artifacts]}")
    print(f"voice_calls={FakeCommunicate.voices}")
    print(f"scene_count={len(artifacts[0].audio.scenes)} per script")
    print(f"caption_cues={len(artifacts[0].subtitles.segments)}")
    print(f"word_cues={sum(len(s.words) for s in artifacts[0].subtitles.segments)}")
    print(f"total_duration={artifacts[0].audio.total_duration:.2f}s")
    print(f"timestamp_sanity={format_timestamp(1.9999)}")
    print("OK")


if __name__ == "__main__":
    main()