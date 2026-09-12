"""
Research paper crawler: arXiv API (volume source, no auth needed) cross-linked
with Papers with Code-style GitHub repo discovery, enriched with live star
counts.

Strategy:
  1. Page through the arXiv API across several AI-relevant categories to
     accumulate MIN_PAPERS unique records.
  2. For each paper, look for an associated GitHub repo via a github.com link
     already present in the abstract (arXiv authors frequently link their own
     implementation this way).
  3. Enrich every discovered repo with a live star count via github_stars.py.
"""
import asyncio
import re
from datetime import datetime
from typing import List, Optional
from xml.etree import ElementTree

from loguru import logger

from src.config import config
from src.crawlers.github_stars import fetch_star_counts
from src.models.schemas import ResearchPaperContent, ResearchPaperEntity
from src.utils.http_client import FetchError, HttpClient

ARXIV_API = "http://export.arxiv.org/api/query"
ARXIV_CATEGORIES = ["cs.AI", "cs.LG", "cs.CL", "cs.CV", "stat.ML"]
PAGE_SIZE = 100
ARXIV_POLITENESS_DELAY_SECONDS = 3  # arXiv's usage policy asks for >=3s between requests
GITHUB_URL_RE = re.compile(r"https?://github\.com/[\w.-]+/[\w.-]+")

_ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}


def _parse_entry(entry: ElementTree.Element) -> Optional[dict]:
    title = (entry.findtext("atom:title", default="", namespaces=_ATOM_NS) or "").strip().replace("\n", " ")
    arxiv_id = (entry.findtext("atom:id", default="", namespaces=_ATOM_NS) or "").strip()
    summary = entry.findtext("atom:summary", default="", namespaces=_ATOM_NS) or ""
    published = (entry.findtext("atom:published", default="", namespaces=_ATOM_NS) or "").strip()
    authors = [
        (a.findtext("atom:name", default="", namespaces=_ATOM_NS) or "").strip()
        for a in entry.findall("atom:author", _ATOM_NS)
    ]
    if not title or not arxiv_id:
        return None
    github_match = GITHUB_URL_RE.search(summary)
    return {
        "title": title,
        "authors": [a for a in authors if a],
        "paper_url": arxiv_id,
        "github_url": github_match.group(0) if github_match else None,
        "published_date": published or None,
    }


async def _fetch_category_page(client: HttpClient, category: str, start: int) -> List[dict]:
    params = (
        f"search_query=cat:{category}&start={start}&max_results={PAGE_SIZE}"
        f"&sortBy=submittedDate&sortOrder=descending"
    )
    url = f"{ARXIV_API}?{params}"
    try:
        text = await client.get_text(url)
    except FetchError as e:
        logger.warning(f"arXiv fetch failed for {category}@{start}: {e}")
        return []
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError as e:
        logger.warning(f"arXiv XML parse failed for {category}@{start}: {e}")
        return []
    parsed = [_parse_entry(e) for e in root.findall("atom:entry", _ATOM_NS)]
    return [p for p in parsed if p]


def _to_datetime(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


async def scrape_papers(target: Optional[int] = None) -> List[ResearchPaperEntity]:
    target = target or config.MIN_PAPERS
    seen_ids = set()
    raw_papers: List[dict] = []

    async with HttpClient() as client:
        for category in ARXIV_CATEGORIES:
            start = 0
            while len(raw_papers) < target and start < 2000:
                await asyncio.sleep(ARXIV_POLITENESS_DELAY_SECONDS)
                page = await _fetch_category_page(client, category, start)
                if not page:
                    break
                for paper in page:
                    if paper["paper_url"] not in seen_ids:
                        seen_ids.add(paper["paper_url"])
                        raw_papers.append(paper)
                start += PAGE_SIZE
            if len(raw_papers) >= target:
                break

        repo_urls = [p["github_url"] for p in raw_papers if p["github_url"]]
        stars_by_repo = await fetch_star_counts(client, repo_urls)

    entities = [
        ResearchPaperEntity(
            content=ResearchPaperContent(
                title=p["title"],
                authors=p["authors"],
                paper_url=p["paper_url"],
                github_url=p["github_url"],
                github_stars=stars_by_repo.get(p["github_url"]) if p["github_url"] else None,
                published_date=_to_datetime(p["published_date"]),
            )
        )
        for p in raw_papers
    ]
    logger.info(f"papers_scraper: collected {len(entities)} unique research papers")
    return entities


if __name__ == "__main__":
    results = asyncio.run(scrape_papers())
    print(f"Collected {len(results)} papers")
