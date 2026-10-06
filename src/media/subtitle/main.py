import json
from pathlib import Path

from src.media.schemas import TranscriptSegment

MAX_CHARS_PER_LINE = 42
MIN_CUE_SECONDS = 0.60
MIN_WORD_CUE_SECONDS = 0.10


def format_timestamp(seconds: float) -> str:
    """Format as SRT `HH:MM:SS,mmm`.

    Rounding is done on the total millisecond count so the millisecond field can
    never roll over to 1000 (which would produce an invalid cue like `00:00:01,1000`).
    """
    total_ms = int(round(max(seconds, 0.0) * 1000))
    total_s, ms = divmod(total_ms, 1000)
    total_m, s = divmod(total_s, 60)
    h, m = divmod(total_m, 60)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def wrap_caption(text: str, max_chars: int = MAX_CHARS_PER_LINE) -> list[str]:
    """Greedy word wrap to `max_chars` per line."""
    words = text.split()
    if not words:
        return []

    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        if len(current) + 1 + len(word) <= max_chars:
            current = f"{current} {word}"
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def _fit_cues(
    cues: list[tuple[float, float, str]],
    min_seconds: float,
) -> list[tuple[float, float, str]]:
    """Drop unusable cues and stretch short ones without letting them collide.

    A cue is never pushed past the start of the cue that follows it, so the
    output stays strictly increasing and non-overlapping as SRT requires.
    """
    fitted: list[tuple[float, float, str]] = []

    for index, (start, end, text) in enumerate(cues):
        text = text.strip()
        if not text or end <= start:
            continue

        next_start = cues[index + 1][0] if index + 1 < len(cues) else None
        limit = next_start if next_start is not None and next_start > start else end

        if end - start < min_seconds:
            end = min(start + min_seconds, limit)

        if end <= start:
            continue

        fitted.append((start, end, text))

    return fitted


def build_cues(
    segments: list[TranscriptSegment],
    word_level: bool = False,
) -> list[tuple[float, float, str]]:
    """Flatten segments into sorted, render-ready SRT cues."""
    if word_level:
        raw = [(w.start, w.end, w.word) for s in segments for w in s.words]
    else:
        raw = [(s.start, s.end, s.text) for s in segments]

    raw.sort(key=lambda c: (c[0], c[1]))
    return _fit_cues(raw, MIN_WORD_CUE_SECONDS if word_level else MIN_CUE_SECONDS)


def write_srt(
    segments: list[TranscriptSegment],
    out_path: Path,
    word_level: bool = False,
) -> None:
    """Write an SRT file from TranscriptSegments.

    word_level=False: one cue per segment (sentence-level subtitles).
    word_level=True:  one cue per word (karaoke captions).

    Cue width is enforced upstream, in `group_words_into_sentences`, where word
    and character budgets are applied while the timings are still known. Here
    text is only wrapped, never truncated — dropping narration silently would
    be worse than an over-long cue.
    """
    cues = build_cues(segments, word_level=word_level)

    blocks: list[str] = []
    for index, (start, end, text) in enumerate(cues, 1):
        body = "\n".join(wrap_caption(text)) or text
        blocks.append(
            f"{index}\n"
            f"{format_timestamp(start)} --> {format_timestamp(end)}\n"
            f"{body}\n"
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(blocks), encoding="utf-8")


def write_transcript(segments: list[TranscriptSegment], out_path: Path) -> None:
    """Write the raw word-level transcript as JSON."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps([s.model_dump() for s in segments], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
