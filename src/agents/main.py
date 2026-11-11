from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from src.agents.critique.node import critique_node
from src.agents.researcher.node import research_node
from src.agents.schemas import FinalPlan, VideoState
from src.agents.scriptwriter.node import scriptwriter_node
from src.agents.utils import as_dict


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------
def route_after_critic(state: VideoState) -> str:
    """Decide what to do after the critique node."""
    critique = as_dict(state.get("critique"))
    decision = critique.get("decision", "REVISE")
    revision_count = state.get("revision_count", 0)
    max_revisions = state.get("max_revisions", 2)

    if decision == "APPROVE":
        return "finalize"

    if decision == "REJECT":
        # Topic is unsalvageable — still finalize so we can record the outcome
        # and move on to the next topic.
        return "finalize"

    if revision_count >= max_revisions:
        return "finalize"

    return "scriptwriter"


# ---------------------------------------------------------------------------
# Finalize node
# ---------------------------------------------------------------------------
def finalize_node(state: VideoState) -> dict:
    """Collapse the run into a single final_plan dict and mark approval."""
    script = as_dict(state.get("script"))
    critique = as_dict(state.get("critique"))
    research = as_dict(state.get("research"))

    decision = critique.get("decision", "REVISE")
    approved = decision == "APPROVE"

    sources: list[str] = []
    for s in (script.get("sources") or []):
        if isinstance(s, str):
            sources.append(s)
        elif isinstance(s, dict) and s.get("url"):
            sources.append(s["url"])
    if not sources:
        for s in (research.get("sources") or []):
            if isinstance(s, dict) and s.get("url"):
                sources.append(s["url"])

    final_plan: FinalPlan = {
        "topic": state.get("current_topic", ""),
        "platform": state.get("platform", ""),
        "approved": approved,
        "decision": decision,
        "overall_score": critique.get("overall_score", 0.0),
        "script": script,
        "critique_summary": {
            "decision": decision,
            "overall_score": critique.get("overall_score", 0.0),
            "strengths": critique.get("strengths", []),
            "issues": critique.get("issues", []),
            "required_changes": critique.get("required_changes", []),
        },
        "sources": list(dict.fromkeys(sources)),  # dedupe, preserve order
        "revision_count": state.get("revision_count", 0),
    }

    return {"final_plan": final_plan, "approved": approved}


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------
def build_graph() -> CompiledStateGraph:
    graph = StateGraph(VideoState)

    graph.add_node("research", research_node)
    graph.add_node("scriptwriter", scriptwriter_node)
    graph.add_node("critique", critique_node)
    graph.add_node("finalize", finalize_node)

    graph.set_entry_point("research")

    graph.add_edge("research", "scriptwriter")
    graph.add_edge("scriptwriter", "critique")

    graph.add_conditional_edges(
        "critique",
        route_after_critic,
        {
            "finalize": "finalize",
            "scriptwriter": "scriptwriter",
        },
    )

    graph.add_edge("finalize", END)

    return graph.compile()


agent = build_graph()