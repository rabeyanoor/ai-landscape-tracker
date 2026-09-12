"""
Shared async HTTP client used by every scraper.

Handles the two failure modes the assignment specifically calls out:
  - 429 (rate limited)          -> exponential backoff + jitter, respects Retry-After header
  - Oversized responses / 413   -> streamed reads with a hard byte cap so a single
                                    giant page can't blow up memory or an LLM context window later
"""
import asyncio
import logging
import random
from typing import Optional

import aiohttp
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
)

from src.config import config

logger = logging.getLogger("http_client")

MAX_RESPONSE_BYTES = 5_000_000  # 5 MB hard cap per page fetch


class FetchError(Exception):
    pass


class RateLimitedError(FetchError):
    """Raised on HTTP 429 so tenacity knows to back off and retry."""
    pass


class HttpClient:
    def __init__(self, concurrency: int = None):
        self._sem = asyncio.Semaphore(concurrency or config.MAX_CONCURRENT_REQUESTS)
        self._session: Optional[aiohttp.ClientSession] = None

    async def __aenter__(self):
        timeout = aiohttp.ClientTimeout(total=config.REQUEST_TIMEOUT_SECONDS)
        self._session = aiohttp.ClientSession(
            headers={"User-Agent": config.USER_AGENT}, timeout=timeout
        )
        return self

    async def __aexit__(self, *exc):
        if self._session:
            await self._session.close()

    @retry(
        reraise=True,
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1.5, min=2, max=60),
        retry=retry_if_exception_type((RateLimitedError, aiohttp.ClientError, asyncio.TimeoutError)),
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )
    async def get_text(self, url: str, **kwargs) -> str:
        """GET a URL and return decoded text, with retry + backoff baked in."""
        async with self._sem:
            async with self._session.get(url, **kwargs) as resp:
                if resp.status == 429:
                    retry_after = resp.headers.get("Retry-After")
                    if retry_after:
                        wait_s = float(retry_after)
                        logger.warning(f"429 on {url}, sleeping {wait_s}s per Retry-After header")
                        await asyncio.sleep(wait_s)
                    else:
                        await asyncio.sleep(random.uniform(2, 5))
                    raise RateLimitedError(f"429 rate limited: {url}")

                if resp.status >= 500:
                    raise FetchError(f"Server error {resp.status} on {url}")

                if resp.status >= 400:
                    # 4xx other than 429 (404, 403 etc.) -- don't retry, just surface it
                    raise FetchError(f"Client error {resp.status} on {url}")

                content_length = resp.headers.get("Content-Length")
                if content_length and int(content_length) > MAX_RESPONSE_BYTES:
                    raise FetchError(f"Response too large ({content_length} bytes): {url}")

                chunks = []
                total = 0
                async for chunk in resp.content.iter_chunked(65536):
                    total += len(chunk)
                    if total > MAX_RESPONSE_BYTES:
                        raise FetchError(f"Response exceeded {MAX_RESPONSE_BYTES} bytes while streaming: {url}")
                    chunks.append(chunk)
                return b"".join(chunks).decode(errors="replace")

    async def get_json(self, url: str, **kwargs):
        import json
        text = await self.get_text(url, **kwargs)
        return json.loads(text)
