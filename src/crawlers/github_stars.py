"""
Batch GitHub star-count lookups used to enrich research papers with their
associated repo's popularity. Falls back gracefully to unauthenticated calls
(60 req/hour) if no GITHUB_TOKEN is configured.
"""
import re
from typing import Dict, Iterable, Optional, Set

from loguru import logger

from src.config import config
from src.utils.http_client import FetchError, HttpClient

_REPO_PATH_RE = re.compile(r"github\.com/([\w.-]+)/([\w.-]+)")


def _owner_repo_slug(url: str) -> Optional[str]:
    match = _REPO_PATH_RE.search(url)
    if not match:
        return None
    owner, repo = match.groups()
    if repo.endswith(".git"):
        repo = repo[: -len(".git")]
    return f"{owner}/{repo}"


async def fetch_star_counts(client: HttpClient, repo_urls: Iterable[str]) -> Dict[str, int]:
    """Returns {original_repo_url: star_count} for every repo URL that resolved."""
    headers = {"Accept": "application/vnd.github+json"}
    if config.GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {config.GITHUB_TOKEN}"
    else:
        logger.warning("GITHUB_TOKEN not set -- GitHub calls will be unauthenticated (60 req/hour)")

    results: Dict[str, int] = {}
    seen_slugs: Set[str] = set()
    for url in repo_urls:
        slug = _owner_repo_slug(url)
        if not slug or slug in seen_slugs:
            continue
        seen_slugs.add(slug)
        try:
            data = await client.get_json(f"https://api.github.com/repos/{slug}", headers=headers)
            results[url] = data.get("stargazers_count", 0)
        except FetchError as e:
            logger.debug(f"github_stars: skipping {slug}: {e}")
    return results
