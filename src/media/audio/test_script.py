import asyncio
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from pydub import AudioSegment

from src.media.audio.tts_main import generate_voiceover, VOICE_SEQUENCE
from src.media.paths import JobWorkspace
from src.media.schemas import ScriptPayload, ScriptScene


class FakeCommunicate:
    voices: list[str] = []

    def __init__(self, text: str, voice: str, rate: str | None = None, pitch: str | None = None):
        self.text = text
        self.voice = voice
        self.rate = rate
        self.pitch = pitch
        self.voices.append(voice)

    async def save(self, out_path: str) -> None:
        path = Path(out_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fake-audio-data-" * 200)


async def run_test_script() -> None:
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
        tts_main.AudioSegment.from_mp3 = lambda path: AudioSegment.silent(duration=1200)
        tts_main.AudioSegment.export = (
            lambda self, path, *args, **kwargs: Path(path).write_bytes(b"final-voiceover-data-" * 200)
        )
        tts_main.normalize = lambda segment: segment

        with TemporaryDirectory() as temp_dir:
            storage_root = Path(temp_dir)
            artifacts = []
            for _ in range(len(VOICE_SEQUENCE) + 1):
                workspace = JobWorkspace.new(root=storage_root).create()
                artifacts.append(await generate_voiceover(script, workspace))

            assert all(Path(item.voiceover_path).exists() for item in artifacts)
            expected_script_voices = VOICE_SEQUENCE + [VOICE_SEQUENCE[0]]
            assert [item.voice for item in artifacts] == expected_script_voices
            assert FakeCommunicate.voices == [
                voice for voice in expected_script_voices for _ in range(2)
            ], FakeCommunicate.voices
            assert all(len(item.scenes) == 2 for item in artifacts)
            assert all(
                Path(item.voiceover_path).parents[2].name
                == date.today().isoformat()
                for item in artifacts
            )
    finally:
        tts_main.edge_tts.Communicate = original_comm
        tts_main.AudioSegment.from_mp3 = original_from_mp3
        tts_main.AudioSegment.export = original_export
        tts_main.normalize = original_normalize

    print(f"script_voices={[item.voice for item in artifacts]}")
    print(f"voice_calls={FakeCommunicate.voices}")
    print(f"scene_count={len(artifacts[0].scenes)} per script")


if __name__ == "__main__":
    asyncio.run(run_test_script())
