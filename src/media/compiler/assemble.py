import logging
from pathlib import Path

from moviepy import (
    AudioFileClip,
    CompositeAudioClip,
    CompositeVideoClip,
    afx,
    concatenate_videoclips,
)

from src.media.compiler.captions import build_caption_clips
from src.media.compiler.kenburns import build_ken_burns_clip, direction_for_index
from src.media.compiler.timeline import TimelineEntry
from src.media.paths import JobWorkspace
from src.media.schemas import AudioArtifact, SubtitleArtifact

logger = logging.getLogger(__name__)

TARGET_W = 1080
TARGET_H = 1920
FPS = 30
MUSIC_VOLUME = 0.07          # -23dB under voice
MUSIC_FADE_OUT = 1.5
AUDIO_BITRATE = "192k"

VIDEO_CODEC = "libx264"
AUDIO_CODEC = "aac"
PRESET = "medium"
THREADS = 4
FFMPEG_PARAMS = ["-movflags", "+faststart"]
PIXEL_FORMAT = "yuv420p"


def _build_visual_track(timeline: list[TimelineEntry]):
    clips = []
    for idx, entry in enumerate(timeline):
        direction = direction_for_index(idx)
        clip = build_ken_burns_clip(entry.image_path, entry.duration, direction)
        clips.append(clip)
    logger.info("Composing %d visual clips", len(clips))
    return concatenate_videoclips(clips, method="compose")


def _build_audio_track(
    voiceover_path: Path,
    total_duration: float,
    music_path: Path | None,
) -> CompositeAudioClip:
    voice = AudioFileClip(str(voiceover_path)).with_volume_scaled(1.0)
    tracks = [voice]

    if music_path is not None:
        music = AudioFileClip(str(music_path)).with_volume_scaled(MUSIC_VOLUME)
        if music.duration < total_duration:
            music = music.with_effects([afx.AudioLoop(duration=total_duration)])
        else:
            music = music.subclipped(0, total_duration)
        music = music.with_effects([afx.AudioFadeOut(MUSIC_FADE_OUT)])
        tracks.append(music)

    return CompositeAudioClip(tracks)


def compile_video(
    timeline: list[TimelineEntry],
    audio: AudioArtifact,
    subtitles: SubtitleArtifact,
    workspace: JobWorkspace,
    language: str = "en",
    music_path: Path | None = None,
) -> Path:
    """Stitch visuals + audio + captions into a 1080x1920 MP4."""
    total_duration = audio.total_duration

    visual_track = _build_visual_track(timeline).with_duration(total_duration)

    logger.info("Rendering caption PNGs")
    caption_clips = build_caption_clips(subtitles.segments, workspace, language)

    logger.info("Composing final video")
    composite = CompositeVideoClip(
        [visual_track, *caption_clips],
        size=(TARGET_W, TARGET_H),
    ).with_duration(total_duration)

    audio_track = _build_audio_track(
        Path(audio.voiceover_path), total_duration, music_path,
    )
    composite = composite.with_audio(audio_track)

    out_path = workspace.root / "output" / "final.mp4"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    logger.info("Writing %s (this can take a few minutes)", out_path)
    composite.write_videofile(
        str(out_path),
        fps=FPS,
        codec=VIDEO_CODEC,
        audio_codec=AUDIO_CODEC,
        audio_bitrate=AUDIO_BITRATE,
        preset=PRESET,
        threads=THREADS,
        pixel_format=PIXEL_FORMAT,
        ffmpeg_params=FFMPEG_PARAMS,
        temp_audiofile=str(workspace.root / "output" / ".temp-audio.m4a"),
        remove_temp=True,
    )

    return out_path
