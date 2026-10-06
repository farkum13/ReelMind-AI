# src/media/contracts.py
from typing import Literal, Optional

from pydantic import BaseModel, Field


class ScriptScene(BaseModel):
    scene_number: int
    start: str = "0:00"                 # advisory hint from scriptwriter
    end: str = "0:00"
    narration: str
    on_screen_text: str = ""
    visual_direction: str = ""
    sound_music: str = ""
    source_urls: list[str] = Field(default_factory=list)


class ScriptPayload(BaseModel):
    title: str
    platform: str = "youtube_shorts"
    duration_seconds: int = 45
    tone: str = ""
    hook: str = ""
    scenes: list[ScriptScene]
    cta: str = ""
    description: str = ""
    hashtags: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    unverified_claims: list[str] = Field(default_factory=list)
    language: str = "en"                # reserved for the Hindi seam


class SceneAudio(BaseModel):
    scene_number: int
    path: str
    duration_seconds: float
    narration: str
    # Offset of this scene in the concatenated voiceover. The video compiler
    # needs it to hold image N on screen while narration N plays.
    start_seconds: float = 0.0


class AudioArtifact(BaseModel):
    voiceover_path: str
    total_duration: float
    sample_rate: int
    voice: str
    scenes: list[SceneAudio] = Field(default_factory=list)


class WordTiming(BaseModel):
    word: str
    start: float
    end: float


class TranscriptSegment(BaseModel):
    text: str
    start: float
    end: float
    words: list[WordTiming] = Field(default_factory=list)


class SubtitleArtifact(BaseModel):
    srt_path: str
    word_srt_path: Optional[str] = None
    language: str = "en"
    model_size: str = "small"
    segments: list[TranscriptSegment] = Field(default_factory=list)


class JobArtifacts(BaseModel):
    job_id: str
    topic: str = ""
    language: str = "en"
    status: Literal["pending", "running", "done", "failed"] = "pending"
    error: Optional[str] = None
    audio: Optional[AudioArtifact] = None
    subtitles: Optional[SubtitleArtifact] = None
    # later phases:
    visuals: Optional[dict] = None
    final_video_path: Optional[str] = None