import json
import logging

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from src.agents.llm import get_llm, invoke_with_retry
from src.agents.researcher.prompt import (
    EXTRACTION_SYSTEM_PROMPT,
    RESEARCHER_USER_TEMPLATE,
    TOOL_PHASE_SYSTEM_PROMPT,
)
from src.agents.schemas import ResearchBrief, VideoState
from src.agents.tools import model_with_tools, tools_by_name

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 2
MAX_TRANSCRIPT_CHARS = 6000

# Extraction is a separate, tool-free call. ResearchBrief has free-form
# list[dict] fields, which strict schema mode rejects, so use non-strict
# json_schema; the list fields have defaults so omitted ones are tolerated.
# No tools are bound, and the prompt must NOT mention web_search.
_structured_researcher = get_llm(temperature=0.2).with_structured_output(
    ResearchBrief, method="json_schema"
)


def _transcript(messages: list) -> str:
    """Collapse the tool conversation into plain text for the extraction call."""
    parts: list[str] = []
    for message in messages:
        if isinstance(message, (SystemMessage, HumanMessage)):
            continue
        if isinstance(message, ToolMessage):
            parts.append("SEARCH RESULT:\n" + str(message.content))
        else:
            content = getattr(message, "content", "")
            if content:
                parts.append(str(content))
    text = "\n\n".join(parts) or "(no search results gathered)"
    return text[:MAX_TRANSCRIPT_CHARS]


def research_node(state: VideoState) -> dict:
    user_prompt = RESEARCHER_USER_TEMPLATE.format(
        topic=state["current_topic"],
        feed_title=state.get("feed_title", ""),
        published=state.get("published", ""),
        platform=state.get("platform", "youtube_shorts"),
        duration_seconds=state.get("duration_seconds", 45),
        audience=state.get("audience", "general"),
        tone=state.get("tone", "engaging"),
        goal=state.get("goal", "educate and entertain"),
    )

    messages = [
        SystemMessage(content=TOOL_PHASE_SYSTEM_PROMPT),
        HumanMessage(content=user_prompt),
    ]

    # Phase 1 — gather via web_search only. gpt-oss keeps calling the tool while
    # tools are bound, so cap the rounds and move on with whatever we gathered.
    for round_no in range(MAX_TOOL_ROUNDS):
        response = invoke_with_retry(model_with_tools, messages)
        messages.append(response)

        if not response.tool_calls:
            break

        for call in response.tool_calls:
            tool = tools_by_name.get(call["name"])
            if tool is None:
                logger.warning("Ignoring unknown tool call: %s", call["name"])
                continue

            args = call.get("args") or {}
            query = args.get("query") or args.get("q") or state["current_topic"]
            try:
                result = tool.invoke({"query": query})
            except Exception as exc:  # keep the loop alive on a failed search
                logger.warning("web_search failed (%s): %s", query, exc)
                result = f"search failed: {exc}"

            messages.append(
                ToolMessage(
                    content=json.dumps(result, default=str),
                    tool_call_id=call["id"],
                )
            )
        if round_no == MAX_TOOL_ROUNDS - 1:
            logger.info("Reached max tool rounds; extracting from notes")

    # Phase 2 — structure the gathered notes. No tools are bound here; the
    # prompt only asks for JSON so the model won't try to search again.
    extraction_prompt = (
        f"Topic: {state['current_topic']}\n\n"
        f"Research notes gathered so far:\n{_transcript(messages)}\n\n"
        "Return only the ResearchBrief JSON object for this topic."
    )
    brief = invoke_with_retry(
        _structured_researcher,
        [SystemMessage(content=EXTRACTION_SYSTEM_PROMPT),
         HumanMessage(content=extraction_prompt)]
    )
    return {"research": brief}