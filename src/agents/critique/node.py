import logging
from langchain_core.messages import HumanMessage, SystemMessage

from src.agents.llm import get_llm, invoke_with_retry
from src.agents.critique.prompt import CRITIQUE_SYSTEM_PROMPT, CRITIQUE_USER_TEMPLATE
from src.agents.schemas import Critique, VideoState
from src.agents.utils import (
    as_dict,
    compact,
    trim_research_for_critique,
    trim_script,
)

logger = logging.getLogger(__name__)


_llm = get_llm(temperature=0.1).with_structured_output(
    Critique, method="json_schema", strict=True
)


def critique_node(state: VideoState) -> dict:
    user_prompt = CRITIQUE_USER_TEMPLATE.format(
        topic=state["current_topic"],
        platform=state.get("platform", "youtube_shorts"),
        duration_seconds=state.get("duration_seconds", 45),
        audience=state.get("audience", "general"),
        tone=state.get("tone", "engaging"),
        goal=state.get("goal", "educate and entertain"),
        research_json=compact(trim_research_for_critique(as_dict(state.get("research")))),
        script_json=compact(trim_script(as_dict(state.get("script")))),
    )

    logger.info(
        "critique prompt chars=%d",
        len(user_prompt) + len(CRITIQUE_SYSTEM_PROMPT),
    )

    critique: Critique = invoke_with_retry(_llm, [
        SystemMessage(content=CRITIQUE_SYSTEM_PROMPT),
        HumanMessage(content=user_prompt),
    ])

    return {"critique": critique.model_dump()}