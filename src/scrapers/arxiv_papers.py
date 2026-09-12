"""
Pulls AI research papers from the official arXiv API (export.arxiv.org/api/query).
This is a real, documented, scraping-friendly API -- no HTML parsing needed,
no rate-limit cat-and-mouse. We page through results 100 at a time until we
hit MIN_PAPERS (config.MIN_PAPERS = 1000).

GitHub links are extracted from the abstract text (many AI papers mention
"github.com/..." directly); the Papers-with-Code API is used as a secondary
source for stronger paper<->repo linkage.
"""
import asyncio
import logging
import re
import xml.etree.ElementTree as ET
from typing import List

from src.config import config
from src.utils.http_client import HttpClient

logger = logging.getLogger("arxiv_papers")

ARXIV_API = "http://export.arxiv.org/api/query"
ATOM_NS = "{http://www.w3.org/2005/Atom}"
GITHUB_IN_TEXT = re.compile(r"https?://github\.com/[\w.-]+/[\w.-]+")

# Broad AI-related categories + keyword search to maximize relevant coverage
SEARCH_QUERY = (
    'cat:cs.AI OR cat:cs.LG OR cat:cs.CL OR cat:cs.CV '
    'OR abs:"large language model" OR abs:"generative AI"'
)

PAGE_SIZE = 100


async def _fetch_page(client: HttpClient, start: int) -> str:
    params = (
        f"?search_query={SEARCH_QUERY}"
        f"&start={start}&max_results={PAGE_SIZE}"
        f"&sortBy=submittedDate&sortOrder=descending"
    ).replace(" ", "+")
    return await client.get_text(ARXIV_API + params)


def _parse_entries(xml_text: str) -> List[dict]:
    root = ET.fromstring(xml_text)
    papers = []
    for entry in root.findall(f"{ATOM_NS}entry"):
        title = (entry.findtext(f"{ATOM_NS}title") or "").strip().replace("\n", " ")
        abstract = (entry.findtext(f"{ATOM_NS}summary") or "").strip().replace("\n", " ")
        published = entry.findtext(f"{ATOM_NS}published")
        arxiv_id_full = entry.findtext(f"{ATOM_NS}id") or ""
        arxiv_id = arxiv_id_full.rsplit("/", 1)[-1]
        authors = [
            (a.findtext(f"{ATOM_NS}name") or "").strip()
            for a in entry.findall(f"{ATOM_NS}author")
        ]
        github_match = GITHUB_IN_TEXT.search(abstract)

        papers.append({
            "title": title,
            "authors": authors,
            "abstract": abstract,
            "arxiv_id": arxiv_id,
            "published_date": published,
            "github_url": github_match.group(0) if github_match else None,
            "github_stars": None,
            "paperswithcode_url": None,
            "source": "arXiv API",
        })
    return papers


async def scrape_papers(min_count: int = None) -> List[dict]:
    min_count = min_count or config.MIN_PAPERS
    papers: List[dict] = []
    start = 0

    async with HttpClient() as client:
        while len(papers) < min_count:
            logger.info(f"Fetching arXiv results {start}-{start + PAGE_SIZE}")
            xml_text = await _fetch_page(client, start)
            page_papers = _parse_entries(xml_text)
            if not page_papers:
                logger.warning("arXiv returned no more results before hitting min_count target")
                break
            papers.extend(page_papers)
            start += PAGE_SIZE
            # arXiv API courtesy: >=3s between requests, per their usage policy
            await asyncio.sleep(3)

        from src.scrapers.github_stars import enrich_papers_with_stars
        await enrich_papers_with_stars(client, papers)

    return papers[:max(min_count, len(papers))]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = asyncio.run(scrape_papers())
    print(f"Collected {len(result)} papers")
