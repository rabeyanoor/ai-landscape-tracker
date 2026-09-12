"""
Multi-tier LLM extraction engine.

Fallback order: Gemini Flash -> Groq (Llama 3) -> DeepSeek (OpenAI-compatible
endpoint). Any provider error, including rate limits, moves to the next tier;
extract() only returns None once every tier has failed for every chunk.
"""
import asyncio
import json
import random
import re
from typing import List, Optional, Type, TypeVar

from loguru import logger
from pydantic import BaseModel, ValidationError

from src.config import config

T = TypeVar("T", bound=BaseModel)

MAX_CHARS_PER_CHUNK = 12_000  # comfortably under every provider's context/payload limits
MAX_RETRIES_PER_TIER = 3


def _chunk_text(text: str, max_chars: int = MAX_CHARS_PER_CHUNK) -> List[str]:
    """Collapse boilerplate whitespace, then split on paragraph boundaries so a
    chunk never cuts a sentence mid-way -- keeps payloads under the size that
    would trigger an HTTP 413 while preserving enough context to extract from."""
    cleaned = re.sub(r"\n{3,}", "\n\n", text)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    if len(cleaned) <= max_chars:
        return [cleaned]

    chunks, current = [], ""
    for para in cleaned.split("\n\n"):
        if len(current) + len(para) + 2 > max_chars:
            if current:
                chunks.append(current)
            if len(para) > max_chars:
                chunks.extend(para[i:i + max_chars] for i in range(0, len(para), max_chars))
                current = ""
            else:
                current = para
        else:
            current = f"{current}\n\n{para}" if current else para
    if current:
        chunks.append(current)
    return chunks


async def _backoff_sleep(attempt: int) -> None:
    await asyncio.sleep(min(2 ** attempt, 30) + random.uniform(0, 1))


async def _call_gemini(prompt: str) -> str:
    from google import genai

    client = genai.Client(api_key=config.GEMINI_API_KEY)
    response = await asyncio.to_thread(
        client.models.generate_content, model="gemini-3.6-flash", contents=prompt
    )
    return response.text


async def _call_groq(prompt: str) -> str:
    from groq import AsyncGroq

    client = AsyncGroq(api_key=config.GROQ_API_KEY)
    response = await client.chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
    )
    return response.choices[0].message.content


async def _call_deepseek(prompt: str) -> str:
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=config.DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")
    response = await client.chat.completions.create(
        model="deepseek-chat",
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
    )
    return response.choices[0].message.content


PROVIDER_CHAIN = (
    ("gemini", _call_gemini),
    ("groq", _call_groq),
    ("deepseek", _call_deepseek),
)


async def _call_with_backoff(name: str, fn, prompt: str) -> Optional[str]:
    for attempt in range(MAX_RETRIES_PER_TIER):
        try:
            return await fn(prompt)
        except Exception as e:
            is_rate_limited = "429" in str(e) or "rate" in str(e).lower()
            logger.warning(f"[{name}] attempt {attempt + 1}/{MAX_RETRIES_PER_TIER} failed: {e}")
            if is_rate_limited and attempt < MAX_RETRIES_PER_TIER - 1:
                await _backoff_sleep(attempt)
                continue
            return None
    return None


def _build_prompt(chunk: str, schema: Type[T]) -> str:
    schema_json = json.dumps(schema.model_json_schema(), indent=2)
    return (
        "Extract structured data from the content below and return ONLY a single "
        "JSON object that strictly matches this JSON Schema -- no prose, no markdown "
        "code fences, just the raw JSON object.\n\n"
        f"SCHEMA:\n{schema_json}\n\nCONTENT:\n{chunk}"
    )


async def extract(raw_text: str, schema: Type[T]) -> Optional[T]:
    """Runs the Gemini -> Groq -> DeepSeek fallback chain over `raw_text`
    (chunked if needed) and returns the first chunk that yields a valid
    instance of `schema`, or None if every provider failed on every chunk."""
    for chunk in _chunk_text(raw_text):
        prompt = _build_prompt(chunk, schema)
        for name, fn in PROVIDER_CHAIN:
            raw_response = await _call_with_backoff(name, fn, prompt)
            if raw_response is None:
                continue
            try:
                data = json.loads(raw_response)
                return schema.model_validate(data)
            except (json.JSONDecodeError, ValidationError) as e:
                logger.warning(f"[{name}] returned invalid JSON/schema: {e}")
        logger.error("All LLM providers failed for this chunk")
    return None
