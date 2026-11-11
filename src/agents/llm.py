import os
import logging
import time
from functools import lru_cache
from dotenv import load_dotenv
from langchain_groq import ChatGroq
from groq import APIStatusError

load_dotenv()

logger = logging.getLogger(__name__)


def invoke_with_retry(
    runnable,
    messages,
    *,
    attempts: int = 3,
    wait_429: float = 20.0,
    wait_413: float = 60.0,
):
    """Invoke a Groq runnable, backing off on per-minute rate limits.

    The SDK retries 429 internally, but 413 (TPM exceeded) is raised at once.
    Waiting a minute lets the rolling window clear before one more attempt.
    """
    for attempt in range(1, attempts + 1):
        try:
            return runnable.invoke(messages)
        except APIStatusError as exc:
            code = getattr(exc, "status_code", None)
            if code not in (413, 429) or attempt == attempts:
                raise
            delay = wait_413 if code == 413 else wait_429 * attempt
            logger.warning(
                "Groq %s on attempt %d/%d; retrying in %.0fs",
                code, attempt, attempts, delay,
            )
            time.sleep(delay)


@lru_cache
def get_llm(temperature: float = 0.6, model: str = "openai/gpt-oss-20b") -> ChatGroq:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY is required to initialize the Groq model")

    return ChatGroq(
        model=model,
        api_key=api_key,
        temperature=temperature,
        max_tokens=8192,
        reasoning_effort="medium",
        reasoning_format="parsed",
    )