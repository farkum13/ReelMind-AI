import json
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from agents.scehmas import Script, VideoState
from agents.llm import get_llm
from agents.scriptwriter.prompt import (
    SCRIPTWRITER_SYSTEM_PROMPT,
    SCRIPTWRITER_USER_TEMPLATE,
    REVISION_BLOCK,
)

_llm = get_llm(temperature=0.7).with_structured_output(
    Script, method="function_calling"
)


def _as_dict(value: object) -> dict:
    if isinstance(value, BaseModel):
        return value.model_dump()
    return value if isinstance(value, dict) else {}


def _trim_research(research: dict) -> dict:
    """Only pass what the scriptwriter needs. Cuts input tokens significantly."""
    return {
        "summary": research.get("summary", ""),
        "why_trending": research.get("why_trending", ""),
        "key_facts": research.get("key_facts", []),
        "stats": research.get("stats", []),
        "angles": research.get("angles", []),
        "controversies": research.get("controversies", []),
        "sources": research.get("sources", []),
    }


def scriptwriter_node(state: VideoState) -> dict:
    is_revision = bool(state.get("critique"))

    revision_block = ""
    if is_revision:
        revision_block = REVISION_BLOCK.format(
            script_json=json.dumps(_as_dict(state.get("script")), indent=2),
            critique_json=json.dumps(_as_dict(state.get("critique")), indent=2),
        )

    user_prompt = SCRIPTWRITER_USER_TEMPLATE.format(
        topic=state["current_topic"],
        platform=state.get("platform", "youtube_shorts"),
        duration_seconds=state.get("duration_seconds", 45),
        audience=state.get("audience", "general"),
        tone=state.get("tone", "engaging"),
        goal=state.get("goal", "educate and entertain"),
        research_json=json.dumps(
            _trim_research(_as_dict(state.get("research"))), indent=2
        ),
        revision_block=revision_block,
    )

    script: Script = _llm.invoke([
        SystemMessage(content=SCRIPTWRITER_SYSTEM_PROMPT),
        HumanMessage(content=user_prompt),
    ])

    return {
        "script": script,
        "revision_count": state.get("revision_count", 0) + 1,
    }