import os
from functools import lru_cache
from dotenv import load_dotenv
from langchain_groq import ChatGroq

load_dotenv()


@lru_cache
def get_llm(temperature: float = 0.6, model: str = "openai/gpt-oss-20b") -> ChatGroq:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY is required to initialize the Groq model")

    return ChatGroq(
        model=model,
        api_key=api_key,
        temperature=temperature,
        max_tokens=4096,
        reasoning_effort="medium",
        reasoning_format="parsed",
    )