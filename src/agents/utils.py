import json

from pydantic import BaseModel


def as_dict(value: object) -> dict:
    if isinstance(value, BaseModel):
        return value.model_dump()
    return value if isinstance(value, dict) else {}


def clip(value: object, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def compact(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def trim_research_for_scriptwriter(research: dict) -> dict:
    """Only pass what the scriptwriter needs, tightly clipped to save tokens."""
    facts = []
    for item in (research.get("key_facts") or [])[:5]:
        if isinstance(item, dict):
            facts.append({
                "fact": clip(item.get("fact") or item.get("text") or item, 240),
                "source": clip(item.get("source", ""), 80),
            })
        else:
            facts.append({"fact": clip(item, 240)})

    stats = []
    for item in (research.get("stats") or [])[:3]:
        if isinstance(item, dict):
            stats.append({
                "metric": clip(item.get("metric", ""), 80),
                "value": clip(item.get("value", ""), 80),
            })
        else:
            stats.append({"value": clip(item, 120)})

    angles = [
        clip(a.get("angle") if isinstance(a, dict) else a, 160)
        for a in (research.get("angles") or [])[:3]
    ]
    controversies = [
        clip(c, 200) for c in (research.get("controversies") or [])[:3]
    ]
    sources = []
    for item in (research.get("sources") or [])[:3]:
        if isinstance(item, dict):
            sources.append({
                "title": clip(item.get("title", ""), 80),
                "url": item.get("url", ""),
            })
        else:
            sources.append(clip(item, 200))

    return {
        "summary": clip(research.get("summary", ""), 700),
        "why_trending": clip(research.get("why_trending", ""), 250),
        "key_facts": facts,
        "stats": stats,
        "angles": angles,
        "controversies": controversies,
        "sources": sources,
    }


def trim_research_for_critique(research: dict) -> dict:
    facts = [
        clip(f.get("fact") if isinstance(f, dict) else f, 200)
        for f in (research.get("key_facts") or [])[:4]
    ]
    return {
        "summary": clip(research.get("summary", ""), 600),
        "key_facts": facts,
        "stats": [
            {"metric": clip(s.get("metric", ""), 80),
             "value": clip(s.get("value", ""), 80)}
            if isinstance(s, dict) else clip(s, 120)
            for s in (research.get("stats") or [])[:3]
        ],
        "controversies": [
            clip(c, 200) for c in (research.get("controversies") or [])[:3]
        ],
        "sources": [
            {"title": clip(s.get("title", ""), 80), "url": s.get("url", "")}
            if isinstance(s, dict) else clip(s, 200)
            for s in (research.get("sources") or [])[:3]
        ],
    }


def trim_critique(critique: dict) -> dict:
    issues = []
    for issue in (critique.get("issues") or [])[:8]:
        if isinstance(issue, dict):
            issues.append({
                "severity": issue.get("severity", ""),
                "location": clip(issue.get("location", ""), 80),
                "problem": clip(issue.get("problem", ""), 200),
                "suggested_fix": clip(issue.get("suggested_fix", ""), 200),
            })
    return {
        "decision": critique.get("decision", ""),
        "overall_score": critique.get("overall_score"),
        "required_changes": [
            clip(c, 200) for c in (critique.get("required_changes") or [])[:8]
        ],
        "issues": issues,
        "optional_suggestions": [
            clip(c, 160) for c in (critique.get("optional_suggestions") or [])[:5]
        ],
    }


def trim_script(script: dict) -> dict:
    return {
        "title": script.get("title", ""),
        "hook": script.get("hook", ""),
        "duration_seconds": script.get("duration_seconds"),
        "tone": script.get("tone", ""),
        "scenes": [
            {
                "scene_number": scene.get("scene_number"),
                "start": scene.get("start"),
                "end": scene.get("end"),
                "narration": scene.get("narration", ""),
            }
            for scene in (script.get("scenes") or [])
        ],
        "cta": script.get("cta", ""),
        "description": script.get("description", ""),
        "hashtags": script.get("hashtags", []),
        "sources": script.get("sources", []),
    }
