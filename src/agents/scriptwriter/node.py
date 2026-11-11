import logging
from langchain_core.messages import HumanMessage, SystemMessage

from src.agents.schemas import Script, VideoState
from src.agents.llm import get_llm, invoke_with_retry
from src.agents.scriptwriter.prompt import (
    SCRIPTWRITER_SYSTEM_PROMPT,
    SCRIPTWRITER_USER_TEMPLATE,
    REVISION_BLOCK,
)
from src.agents.utils import (
    as_dict,
    compact,
    trim_critique,
    trim_research_for_scriptwriter,
)

logger = logging.getLogger(__name__)

_llm = get_llm(temperature=0.7).with_structured_output(
    Script, method="json_schema", strict=True
)


def scriptwriter_node(state: VideoState) -> dict:
    is_revision = bool(state.get("critique"))

    revision_block = ""
    if is_revision:
        revision_block = REVISION_BLOCK.format(
            script_json=compact(as_dict(state.get("script"))),
            critique_json=compact(trim_critique(as_dict(state.get("critique")))),
        )

    user_prompt = SCRIPTWRITER_USER_TEMPLATE.format(
        topic=state["current_topic"],
        platform=state.get("platform", "youtube_shorts"),
        duration_seconds=state.get("duration_seconds", 45),
        audience=state.get("audience", "general"),
        tone=state.get("tone", "engaging"),
        goal=state.get("goal", "educate and entertain"),
        research_json=compact(trim_research_for_scriptwriter(as_dict(state.get("research")))),
        revision_block=revision_block,
    )

    logger.info(
        "scriptwriter prompt chars=%d revision=%s",
        len(user_prompt) + len(SCRIPTWRITER_SYSTEM_PROMPT), is_revision,
    )

    script: Script = invoke_with_retry(_llm, [
        SystemMessage(content=SCRIPTWRITER_SYSTEM_PROMPT),
        HumanMessage(content=user_prompt),
    ])

    return {
        "script": script,
        "revision_count": state.get("revision_count", 0) + 1,
    }