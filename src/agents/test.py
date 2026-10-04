from agents.main import agent
from agents.scehmas import FinalPlan, VideoState


def run_over_topics(
    selected_topics: list[str],
    base_config: dict,
    feed_title: str = "",
    published: str = "",
    max_revisions: int = 2,
) -> list[FinalPlan]:
    """Invoke the graph once per topic and collect final plans."""
    results: list[FinalPlan] = []

    for topic in selected_topics:
        initial_state: VideoState = {
            "selected_topics": selected_topics,
            "current_topic": topic,
            "feed_title": feed_title,
            "published": published,
            "platform": base_config.get("platform", "youtube_shorts"),
            "duration_seconds": base_config.get("duration_seconds", 45),
            "audience": base_config.get("audience", "general"),
            "tone": base_config.get("tone", "engaging"),
            "goal": base_config.get("goal", "educate and entertain"),
            "research": None,
            "script": None,
            "critique": None,
            "revision_count": 0,
            "max_revisions": max_revisions,
            "approved": False,
            "final_plan": None,
        }

        final_state = agent.invoke(
            initial_state,
            config={"recursion_limit": 25},
        )
        plan = final_state.get("final_plan")
        if plan:
            results.append(plan)

    return results


if __name__ == "__main__":
    topics = ["OpenAI GPT-5 release", "India crypto regulation 2026"]
    plans = run_over_topics(
        selected_topics=topics,
        base_config={
            "platform": "youtube_shorts",
            "duration_seconds": 45,
            "audience": "general tech-savvy viewers",
            "tone": "curious, fast-paced",
            "goal": "educate and drive saves/shares",
        },
        feed_title="Google Trends Daily",
        published="2026-10-03",
        max_revisions=2,
    )

    for p in plans:
        print(p["topic"], "->", p["decision"], p["overall_score"])