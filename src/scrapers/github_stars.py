"""
Fetches current star counts for GitHub repos linked from research papers.
Uses a Personal Access Token (config.GITHUB_TOKEN) to get the higher
5,000 req/hour authenticated rate limit instead of the 60 req/hour anonymous one.
"""
import asyncio
import logging
import re
from typing import Optional

from src.config import config
from src.utils.http_client import HttpClient, FetchError

logger = logging.getLogger("github_stars")

REPO_URL_PATTERN = re.compile(r"github\.com/([\w.-]+)/([\w.-]+)")


def extract_owner_repo(github_url: str) -> Optional[tuple[str, str]]:
    match = REPO_URL_PATTERN.search(github_url)
    if not match:
        return None
    owner, repo = match.group(1), match.group(2).rstrip("/").removesuffix(".git")
    return owner, repo


async def get_star_count(client: HttpClient, github_url: str) -> Optional[int]:
    parsed = extract_owner_repo(github_url)
    if not parsed:
        return None
    owner, repo = parsed
    api_url = f"https://api.github.com/repos/{owner}/{repo}"
    headers = {"Accept": "application/vnd.github+json"}
    if config.GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {config.GITHUB_TOKEN}"

    try:
        data = await client.get_json(api_url, headers=headers)
        return data.get("stargazers_count")
    except FetchError as e:
        logger.warning(f"Could not fetch stars for {github_url}: {e}")
        return None


async def enrich_papers_with_stars(client: HttpClient, papers: list[dict]) -> list[dict]:
    """Mutates each paper dict in-place, adding github_stars where a github_url exists."""
    sem = asyncio.Semaphore(config.MAX_CONCURRENT_REQUESTS)

    async def _one(paper):
        if not paper.get("github_url"):
            return
        async with sem:
            stars = await get_star_count(client, paper["github_url"])
            paper["github_stars"] = stars

    await asyncio.gather(*[_one(p) for p in papers])
    return papers
