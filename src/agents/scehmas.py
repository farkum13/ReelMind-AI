from typing import Literal, TypedDict

from pydantic import BaseModel, Field


class ResearchBrief(BaseModel):
    topic: str
    summary: str
    why_trending: str
    key_facts: list[dict]
    stats: list[dict]
    angles: list[dict]
    controversies: list[str]
    visuals: list[dict]
    sources: list[dict]
    knowledge_gaps: list[str]

class Scene(BaseModel):
    scene_number: int
    start: str = Field(description="MM:SS")
    end: str = Field(description="MM:SS")
    narration: str
    on_screen_text: str = ""
    visual_direction: str = ""
    sound_music: str = ""
    source_urls: list[str] = Field(default_factory=list)

class Script(BaseModel):
    title: str
    platform: str
    duration_seconds: int
    tone: str
    hook: str
    scenes: list[Scene]
    cta: str
    description: str
    hashtags: list[str]
    sources: list[str]
    unverified_claims: list[str] = Field(default_factory=list)

class Issue(BaseModel):
    severity: Literal["critical", "major", "minor"]
    location: str = Field(description="e.g. 'scene 2 narration' or 'hook'")
    problem: str
    evidence: str = Field(description="Quote from the script")
    suggested_fix: str

class FactCheckItem(BaseModel):
    claim: str
    supported: bool
    source_url: str = ""
    note: str = ""

class Critique(BaseModel):
    decision: Literal["APPROVE", "REVISE", "REJECT"]
    scores: dict[str, float] = Field(
        description="Keys: factual_accuracy, hook_strength, structure_pacing, "
                    "engagement, clarity, platform_fit, safety, originality. Values 1–10."
    )
    overall_score: float
    strengths: list[str]
    issues: list[Issue]
    fact_check: list[FactCheckItem]
    required_changes: list[str]
    optional_suggestions: list[str] = []
    approval_conditions: list[str] = []


class FinalPlan(TypedDict):
    topic: str
    platform: str
    approved: bool
    decision: Literal["APPROVE", "REVISE", "REJECT"]
    overall_score: float
    script: dict | None
    critique_summary: dict | None
    sources: list[str]
    revision_count: int

class VideoState(TypedDict):
    # queue / iteration
    selected_topics: list[str]
    current_topic: str
    feed_title: str
    published: str

    # per-topic config
    platform: str
    duration_seconds: int
    audience: str
    tone: str
    goal: str

    # Agent outputs are validated models; graph state remains dictionary-based.
    research: ResearchBrief | None
    script: Script | None
    critique: Critique | None

    # loop control
    revision_count: int
    max_revisions: int
    approved: bool

    # terminal
    final_plan: dict | None