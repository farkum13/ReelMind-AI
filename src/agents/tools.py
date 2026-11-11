import logging
import os
import dotenv

from src.agents.llm import get_llm
from langchain_core.tools import tool
from tavily import TavilyClient

dotenv.load_dotenv()
logger = logging.getLogger(__name__)

tavily_client = TavilyClient(api_key=os.getenv("TAVILY_API_KEY"))

model = get_llm()

MAX_RESULTS = 4
MAX_CONTENT_CHARS = 600
SEARCH_TOPIC = os.getenv("TAVILY_TOPIC", "news")
SEARCH_DAYS = int(os.getenv("TAVILY_DAYS", "7"))
FALLBACK_DAYS = int(os.getenv("TAVILY_FALLBACK_DAYS", "30"))


def _clip(text: str, limit: int = MAX_CONTENT_CHARS) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


def _search(query: str, **kwargs) -> list[dict]:
    response = tavily_client.search(query, max_results=MAX_RESULTS, **kwargs)
    return response.get("results", [])


def _format(results: list[dict]) -> str:
    return "\n\n".join(
        f"Title: {result.get('title', '')}\n"
        f"URL: {result.get('url', '')}\n"
        f"Published: {result.get('published_date') or 'unknown'}\n"
        f"Content: {_clip(result.get('content', ''))}"
        for result in results
    )


# Define tools
@tool
def web_search(query: str) -> str:
    """Search the web for recent results. Each result includes its title, URL,
    publication date, and a content snippet. Prefer recently published sources.
    """
    results = _search(query, topic=SEARCH_TOPIC, days=SEARCH_DAYS)
    if results:
        return _format(results)

    logger.info(
        "No results for %r in the last %d days; widening to %d days",
        query, SEARCH_DAYS, FALLBACK_DAYS,
    )
    results = _search(query, topic=SEARCH_TOPIC, days=FALLBACK_DAYS)
    if results:
        return (
            f"No results in the last {SEARCH_DAYS} days; "
            f"showing results from the last {FALLBACK_DAYS} days:\n\n"
            + _format(results)
        )

    logger.info(
        "No results for %r in the last %d days; searching without a date filter",
        query, FALLBACK_DAYS,
    )
    results = _search(query)
    if results:
        return (
            f"No results in the last {FALLBACK_DAYS} days; showing older results:\n\n"
            + _format(results)
        )

    return f"No web results found for: {query}"


# Augment the LLM with tools
tools = [web_search]
tools_by_name = {tool.name: tool for tool in tools}
model_with_tools = model.bind_tools(tools)
