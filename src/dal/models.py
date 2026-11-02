from enum import StrEnum


class VideoStatus(StrEnum):
    PENDING = "PENDING"       # waiting for the video worker
    PROCESSING = "PROCESSING" # agents or media pipeline is running
    RENDERED = "RENDERED"     # media done, ready for upload
    QUEUED = "QUEUED"         # assigned to a slot, awaiting upload
    UPLOADED = "UPLOADED"     # successfully uploaded to YouTube
    FAILED = "FAILED"         # unrecoverable error
    SKIPPED = "SKIPPED"       # manually excluded (e.g. rejected script)


class SlotStatus(StrEnum):
    OPEN = "OPEN"
    RESERVED = "RESERVED"
    USED = "USED"
    MISSED = "MISSED"


# YouTube quota accounting (https://developers.google.com/youtube/v3/determine_quota_cost)
QUOTA_PER_UPLOAD = 1600
QUOTA_PER_CAPTION = 400          # captions.insert costs 400 units
QUOTA_DAILY_DEFAULT = 10_000
QUOTA_RESERVE = 2_000            # headroom for retries, reads, caption attach

DEFAULT_SLOTS_IST = [
    "08:00", "12:00", "16:00", "19:00", "21:00",
]