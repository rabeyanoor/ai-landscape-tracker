"""
Scrapes 5 AI-focused job boards and keeps only postings from the last 24
hours. Same SiteConfig pattern as news.py so the two modules stay symmetric
and easy to maintain together.

Sites covered by default:
  1. SimplyHired (query: "artificial intelligence")
  2. AI Jobs (ai-jobs.net)
  3. Wellfound / AngelList AI jobs
  4. RemoteOK (tag: ai)
  5. WeWorkRemotely (category: programming, filtered client-side for AI keywords)
"""
import asyncio
import logging
from dataclasses import dataclass
from typing import List, Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from src.config import config
from src.utils.http_client import HttpClient, FetchError, RateLimitedError
from src.utils.freshness import parse_relative_or_absolute, is_within_freshness_window

logger = logging.getLogger("jobs_scraper")


@dataclass
class JobSiteConfig:
    name: str
    listing_url: str
    card_selector: str
    title_selector: str
    company_selector: str
    link_selector: str
    time_selector: str
    time_attr: Optional[str] = "datetime"
    location_selector: Optional[str] = None


JOB_SITE_CONFIGS = [
    JobSiteConfig(
        name="SimplyHired",
        listing_url="https://www.simplyhired.com/search?q=artificial+intelligence",
        card_selector="div.SerpJob-jobCard",
        title_selector="a.chakra-button",
        company_selector="span[data-testid='companyName']",
        link_selector="a.chakra-button",
        time_selector="span[data-testid='job-age']",
        time_attr=None,
        location_selector="span[data-testid='searchSerpJobLocation']",
    ),
    JobSiteConfig(
        name="AI-Jobs.net",
        listing_url="https://ai-jobs.net/",
        card_selector="li.job-list-item",
        title_selector="a.job-list-item-title",
        company_selector="span.job-list-item-company",
        link_selector="a.job-list-item-title",
        time_selector="time",
        location_selector="span.job-list-item-location",
    ),
    JobSiteConfig(
        name="RemoteOK (AI)",
        listing_url="https://remoteok.com/remote-ai-jobs",
        card_selector="tr.job",
        title_selector="h2",
        company_selector="h3",
        link_selector="a.preventLink",
        time_selector="time",
        location_selector="div.location",
    ),
    JobSiteConfig(
        name="WeWorkRemotely (AI)",
        listing_url="https://weworkremotely.com/categories/remote-programming-jobs",
        card_selector="li.feature",
        title_selector="span.title",
        company_selector="span.company",
        link_selector="a",
        time_selector="time",
        location_selector="span.region",
    ),
    JobSiteConfig(
        name="Wellfound AI Jobs",
        listing_url="https://wellfound.com/role/artificial-intelligence-engineer",
        card_selector="div[data-test='StartupResult']",
        title_selector="a[data-test='job-title']",
        company_selector="a[data-test='company-name']",
        link_selector="a[data-test='job-title']",
        time_selector="span.job-age",
        time_attr=None,
    ),
]

_AI_KEYWORDS = ["ai", "artificial intelligence", "machine learning", "ml", "llm", "genai", "deep learning"]


def _looks_ai_related(title: str) -> bool:
    lowered = title.lower()
    return any(kw in lowered for kw in _AI_KEYWORDS)


async def _scrape_site(client: HttpClient, site: JobSiteConfig, now) -> List[dict]:
    jobs = []
    try:
        html = await client.get_text(site.listing_url)
    except (FetchError, RateLimitedError) as e:
        logger.warning(f"Skipping {site.name}: {e}")
        return jobs

    soup = BeautifulSoup(html, "lxml")
    cards = soup.select(site.card_selector)

    for card in cards:
        title_el = card.select_one(site.title_selector)
        company_el = card.select_one(site.company_selector)
        link_el = card.select_one(site.link_selector)
        time_el = card.select_one(site.time_selector)
        loc_el = card.select_one(site.location_selector) if site.location_selector else None

        title = title_el.get_text(strip=True) if title_el else None
        company = company_el.get_text(strip=True) if company_el else None
        url = link_el.get("href") if link_el else None
        if not title or not url:
            continue
        if not _looks_ai_related(title) and site.name == "WeWorkRemotely (AI)":
            continue  # WWR isn't AI-specific, so filter client-side
        if url.startswith("/"):
            url = urljoin(site.listing_url, url)

        raw_time = None
        if time_el:
            raw_time = time_el.get(site.time_attr) if site.time_attr and time_el.has_attr(site.time_attr) else time_el.get_text(strip=True)
        iso_time = parse_relative_or_absolute(raw_time, now=now) if raw_time else None
        if not iso_time or not is_within_freshness_window(iso_time, config.FRESHNESS_WINDOW_HOURS, now=now):
            continue

        jobs.append({
            "title": title,
            "company": company or "Unknown",
            "location": loc_el.get_text(strip=True) if loc_el else None,
            "remote": "remote" in (loc_el.get_text(strip=True).lower() if loc_el else ""),
            "salary_text": None,
            "url": url,
            "posted_at_iso": iso_time,
            "posted_at_raw": raw_time,
            "source": site.name,
        })

    return jobs


async def scrape_jobs() -> List[dict]:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    all_jobs: List[dict] = []

    async with HttpClient() as client:
        results = await asyncio.gather(*[_scrape_site(client, s, now) for s in JOB_SITE_CONFIGS])
    for r in results:
        all_jobs.extend(r)

    logger.info(f"Collected {len(all_jobs)} fresh (<{config.FRESHNESS_WINDOW_HOURS}h) job postings")
    return all_jobs


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    data = asyncio.run(scrape_jobs())
    print(f"Collected {len(data)} fresh jobs")
