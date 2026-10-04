import json
from langchain_core.messages import HumanMessage, SystemMessage

from agents.llm import get_llm
from agents.critique.prompt import CRITIQUE_SYSTEM_PROMPT, CRITIQUE_USER_TEMPLATE
from agents.scehmas import Critique, VideoState


_llm = get_llm(temperature=0.1).with_structured_output(
    Critique, method="function_calling"
)


def _trim_research(research: dict) -> dict:
    return {
        "summary": research.get("summary", ""),
        "key_facts": research.get("key_facts", []),
        "stats": research.get("stats", []),
        "controversies": research.get("controversies", []),
        "sources": research.get("sources", []),
    }


def critique_node(state: VideoState) -> dict:
    user_prompt = CRITIQUE_USER_TEMPLATE.format(
        topic=state["current_topic"],
        platform=state.get("platform", "youtube_shorts"),
        duration_seconds=state.get("duration_seconds", 45),
        audience=state.get("audience", "general"),
        tone=state.get("tone", "engaging"),
        goal=state.get("goal", "educate and entertain"),
        research_json=json.dumps(_trim_research(state.get("research") or {}), indent=2),
        script_json=json.dumps(state.get("script") or {}, indent=2),
    )

    critique: Critique = _llm.invoke([
        SystemMessage(content=CRITIQUE_SYSTEM_PROMPT),
        HumanMessage(content=user_prompt),
    ])

    return {"critique": critique.model_dump()}