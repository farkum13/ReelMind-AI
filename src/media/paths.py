# src/media/paths.py
from pathlib import Path
from datetime import date
from uuid import uuid4

DEFAULT_ROOT = Path("data/jobs")


class JobWorkspace:
    def __init__(self, job_id: str, root: Path = DEFAULT_ROOT) -> None:
        self.job_id = job_id
        self.storage_root = root
        self.date_dir = root / date.today().isoformat()
        self.root = self.date_dir / job_id

    @classmethod
    def new(cls, root: Path = DEFAULT_ROOT) -> "JobWorkspace":
        return cls(str(uuid4()), root)

    def create(self) -> "JobWorkspace":
        for sub in ("audio/scenes", "subtitles", "visuals", "output"):
            (self.root / sub).mkdir(parents=True, exist_ok=True)
        return self

    # --- fixed paths ---
    @property
    def script_path(self) -> Path:
        return self.root / "script.json"

    @property
    def voiceover_path(self) -> Path:
        return self.root / "audio" / "voiceover.mp3"

    @property
    def scenes_audio_dir(self) -> Path:
        return self.root / "audio" / "scenes"

    @property
    def transcript_path(self) -> Path:
        return self.root / "audio" / "transcript.json"

    @property
    def srt_path(self) -> Path:
        return self.root / "subtitles" / "captions.srt"

    @property
    def word_srt_path(self) -> Path:
        return self.root / "subtitles" / "captions.word.srt"

    @property
    def meta_path(self) -> Path:
        return self.root / "meta.json"

    @property
    def voice_rotation_db_path(self) -> Path:
        return self.storage_root / ".voice_rotation.sqlite3"

    # --- dynamic paths ---
    def scene_audio_path(self, scene_number: int) -> Path:
        return self.scenes_audio_dir / f"scene_{scene_number:02d}.mp3"

    def scene_image_path(self, scene_number: int) -> Path:
        return self.root / "visuals" / f"scene_{scene_number:02d}.png"