import json
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from agents.researcher.prompt import RESEARCHER_SYSTEM_PROMPT, RESEARCHER_USER_TEMPLATE
from agents.scehmas import ResearchBrief, VideoState
from agents.tools import model_with_tools, tools_by_name


def _extract_json(text: str) -> dict:
    """LLMs sometimes wrap JSON in ```json fences. Strip and parse."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip())


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
    user_prompt += "\n\nRequired JSON schema:\n" + json.dumps(
        ResearchBrief.model_json_schema(), indent=2
    )

    messages = [
        SystemMessage(content=RESEARCHER_SYSTEM_PROMPT),
        HumanMessage(content=user_prompt),
    ]

    # Tool-calling loop
    for _ in range(3):
        response = model_with_tools.invoke(messages)
        messages.append(response)

        if not response.tool_calls:
            break

        for call in response.tool_calls:
            tool = tools_by_name[call["name"]]
            result = tool.invoke(call["args"])
            messages.append(
                ToolMessage(content=json.dumps(result, default=str), tool_call_id=call["id"])
            )
    else:
        raise RuntimeError("Researcher exceeded the maximum number of tool-call rounds")

    # Final message should be the JSON brief
    final_text = messages[-1].content if hasattr(messages[-1], "content") else str(messages[-1])
    brief = ResearchBrief.model_validate(_extract_json(final_text))

    return {"research": brief}