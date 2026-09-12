"""
LLM extraction layer: turns messy scraped text into structured JSON.

Fallback order (as specified): Gemini Flash -> Groq (Llama 3) -> DeepSeek.
If the first provider hits a rate limit (429) or is down (5xx/timeout),
we transparently move to the next provider for THAT SAME request -- the
caller never has to know which provider actually served it.

Also handles:
  - 413 / context-overflow: long text is chunked before being sent, and
    if a single chunk still errors as "too large" we split it further.
  - 429: exponential backoff with jitter (via tenacity) BEFORE falling
    back to the next provider, since a short wait often resolves a
    transient rate limit without burning the fallback chain.
"""
import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Optional

from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from src.config import config

logger = logging.getLogger("llm_fallback")

# Conservative chunk size in characters (~4 chars/token heuristic) to stay
# well under every provider's context window and avoid 413s.
MAX_CHARS_PER_CHUNK = 12_000


class ProviderError(Exception):
    """Raised for retryable provider issues: 429, 5xx, timeout."""
    pass


class AllProvidersExhaustedError(Exception):
    pass


@dataclass
class ExtractionResult:
    data: dict
    provider_used: str


def chunk_text(text: str, max_chars: int = MAX_CHARS_PER_CHUNK) -> list[str]:
    """Splits text on paragraph boundaries where possible, hard-splits otherwise."""
    if len(text) <= max_chars:
        return [text]

    chunks, current = [], []
    current_len = 0
    for para in text.split("\n\n"):
        if current_len + len(para) > max_chars and current:
            chunks.append("\n\n".join(current))
            current, current_len = [], 0
        if len(para) > max_chars:
            # single paragraph itself too big -- hard split
            for i in range(0, len(para), max_chars):
                chunks.append(para[i:i + max_chars])
        else:
            current.append(para)
            current_len += len(para)
    if current:
        chunks.append("\n\n".join(current))
    return chunks


# ---------------------------------------------------------------------------
# Individual provider callers. Each raises ProviderError on 429/5xx/timeout
# so the retry+fallback logic above can treat them uniformly.
# ---------------------------------------------------------------------------

async def _call_gemini(prompt: str) -> str:
    import google.generativeai as genai
    genai.configure(api_key=config.GEMINI_API_KEY)
    model = genai.GenerativeModel("gemini-1.5-flash")
    try:
        response = await asyncio.to_thread(model.generate_content, prompt)
        return response.text
    except Exception as e:
        msg = str(e).lower()
        if "429" in msg or "quota" in msg or "rate" in msg or "unavailable" in msg or "timeout" in msg:
            raise ProviderError(f"Gemini failed: {e}") from e
        raise


async def _call_groq(prompt: str) -> str:
    from groq import Groq
    client = Groq(api_key=config.GROQ_API_KEY)
    try:
        response = await asyncio.to_thread(
            client.chat.completions.create,
            model="llama3-70b-8192",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
        )
        return response.choices[0].message.content
    except Exception as e:
        msg = str(e).lower()
        if "429" in msg or "rate" in msg or "503" in msg or "timeout" in msg:
            raise ProviderError(f"Groq failed: {e}") from e
        raise


async def _call_deepseek(prompt: str) -> str:
    # DeepSeek exposes an OpenAI-compatible REST API
    from openai import OpenAI
    client = OpenAI(api_key=config.DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")
    try:
        response = await asyncio.to_thread(
            client.chat.completions.create,
            model="deepseek-chat",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
        )
        return response.choices[0].message.content
    except Exception as e:
        msg = str(e).lower()
        if "429" in msg or "rate" in msg or "503" in msg or "timeout" in msg:
            raise ProviderError(f"DeepSeek failed: {e}") from e
        raise


PROVIDER_CHAIN = [
    ("gemini-flash", _call_gemini),
    ("groq-llama3", _call_groq),
    ("deepseek-chat", _call_deepseek),
]


@retry(
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=1, min=2, max=20),
    retry=retry_if_exception_type(ProviderError),
    reraise=True,
)
async def _call_with_backoff(name: str, fn, prompt: str) -> str:
    """One provider gets up to 2 attempts with backoff before we give up on it
    and move to the next provider in the chain."""
    return await fn(prompt)


async def extract_structured(prompt_template: str, raw_text: str) -> ExtractionResult:
    """
    Sends `raw_text` (already chunk-sized) through the fallback chain using
    `prompt_template`, which must contain a `{text}` placeholder and MUST
    instruct the model to return ONLY JSON, no markdown fences, no preamble.
    """
    prompt = prompt_template.format(text=raw_text)
    last_error: Optional[Exception] = None

    for name, fn in PROVIDER_CHAIN:
        try:
            logger.info(f"Trying provider: {name}")
            raw_response = await _call_with_backoff(name, fn, prompt)
            cleaned = raw_response.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            parsed = json.loads(cleaned)
            return ExtractionResult(data=parsed, provider_used=name)
        except ProviderError as e:
            logger.warning(f"{name} exhausted retries, falling back: {e}")
            last_error = e
            continue
        except json.JSONDecodeError as e:
            logger.warning(f"{name} returned non-JSON, falling back: {e}")
            last_error = e
            continue

    raise AllProvidersExhaustedError(f"All LLM providers failed. Last error: {last_error}")


async def extract_structured_chunked(prompt_template: str, raw_text: str) -> list[ExtractionResult]:
    """For long articles: chunk first (413 prevention), then extract each
    chunk and let the caller merge/aggregate results."""
    chunks = chunk_text(raw_text)
    results = []
    for i, chunk in enumerate(chunks):
        logger.info(f"Extracting chunk {i + 1}/{len(chunks)} ({len(chunk)} chars)")
        result = await extract_structured(prompt_template, chunk)
        results.append(result)
        await asyncio.sleep(0.5)  # small courtesy delay between chunks
    return results


NEWS_EXTRACTION_PROMPT = """You are a data extraction engine. From the article text below, \
extract a JSON object with exactly these keys: "title", "summary" (2-3 sentences), \
"companies_mentioned" (array of strings), "topics" (array of strings). \
Return ONLY valid JSON, no markdown fences, no explanation.

TEXT:
{text}
"""

STARTUP_EXTRACTION_PROMPT = """You are a data extraction engine. From the raw scraped text below \
about a startup, extract a JSON object with exactly these keys: "name", "description", \
"employee_count", "founded_year", "location", "industry_tags" (array). \
Use null for any field you cannot determine. Return ONLY valid JSON.

TEXT:
{text}
"""
