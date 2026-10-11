from dataclasses import dataclass
from pathlib import Path

from src.media.schemas import AudioArtifact, VisualArtifact


@dataclass
class TimelineEntry:
    scene_number: int
    image_path: Path
    start: float            # seconds
    end: float              # seconds
    duration: float


def build_timeline(
    audio: AudioArtifact,
    visuals: VisualArtifact,
) -> list[TimelineEntry]:
    """Zip audio scene spans with their matching images.

    Scene starts come from `SceneAudio.start_seconds` so the silent gaps the
    audio concatenator inserts between scenes are covered: image N is held on
    screen until image N+1 begins (and the last image until the audio ends).
    Order follows AudioArtifact.scenes, and every scene must have exactly one
    matching image, otherwise the video would desync.
    """
    images_by_scene = {sv.scene_number: Path(sv.image_path) for sv in visuals.scenes}
    missing = [
        sa.scene_number for sa in audio.scenes if sa.scene_number not in images_by_scene
    ]
    if missing:
        raise ValueError(
            f"Missing images for scenes: {missing}. "
            f"Have: {sorted(images_by_scene)}"
        )

    scenes = audio.scenes
    timeline: list[TimelineEntry] = []
    cursor = 0.0
    for i, sa in enumerate(scenes):
        start = sa.start_seconds if sa.start_seconds > 0 else cursor

        if i + 1 < len(scenes) and scenes[i + 1].start_seconds > start:
            end = scenes[i + 1].start_seconds
        else:
            end = start + sa.duration_seconds
        if i == len(scenes) - 1 and audio.total_duration > end:
            end = audio.total_duration

        duration = max(end - start, 0.0)
        timeline.append(
            TimelineEntry(
                scene_number=sa.scene_number,
                image_path=images_by_scene[sa.scene_number],
                start=start,
                end=end,
                duration=duration,
            )
        )
        cursor = end

    return timeline
