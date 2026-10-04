import os
import dotenv

from agents.llm import get_llm
from langchain_core.tools import tool
from tavily import TavilyClient

dotenv.load_dotenv()
tavily_client = TavilyClient(api_key=os.getenv("TAVILY_API_KEY"))

model = get_llm()


# Define tools
@tool
def web_search(query: str) -> str:
    """Search the web and return result snippets with their source URLs.
    each results has url, title, content, score and id.
    """
    response = tavily_client.search(query, max_results=5)
    results = response.get("results", [])
    if not results:
        return f"No web results found for: {query}"

    return "\n\n".join(
        f"Title: {result.get('title', '')}\n"
        f"URL: {result.get('url', '')}\n"
        f"Content: {result.get('content', '')}"
        for result in results
    )


# Augment the LLM with tools
tools = [web_search]
tools_by_name = {tool.name: tool for tool in tools}
model_with_tools = model.bind_tools(tools)