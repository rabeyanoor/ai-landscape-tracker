"""
Signal crawler: 5 AI news sources + 5 AI job boards, both strictly filtered to
the last FRESHNESS_WINDOW_HOURS via src.utils.freshness.

Each site is scraped with Playwright (Chromium) through playwright-stealth so
headless traffic is less likely to trip Cloudflare-style bot checks, with
per-context user-agent rotation and an optional upstream proxy. If a site's
CSS selectors turn up zero fresh rows (e.g. the site redesigned its DOM), we
fall back once to the LLM orchestrator to structure the raw page text instead
of failing the source outright.
"""
import asyncio
import random
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from loguru import logger
from playwright.async_api import Browser, async_playwright
from playwright_stealth import stealth_async

from src.config import config
from src.llm.orchestrator import extract
from src.models.schemas import JobContent, JobEntity, NewsContent, NewsEntity, Source
from src.utils.freshness import is_within_freshness_window, parse_relative_or_absolute

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0",
]


@dataclass
class SiteConfig:
    name: str
    url: str
    card_selector: str
    title_selector: str          # "self" means the card element itself carries the title
    link_selector: str           # "self" means the card element itself is the <a>
    time_selector: str


NEWS_SITES: List[SiteConfig] = [
    SiteConfig("VentureBeat AI", "https://venturebeat.com/category/ai/", "article", "h2 a, h3 a", "h2 a, h3 a", "time"),
    SiteConfig("TechCrunch AI", "https://techcrunch.com/category/artificial-intelligence/", "a.loop-card", "self", "self", "time"),
    SiteConfig("The Verge AI", "https://www.theverge.com/ai-artificial-intelligence", "article", "h2 a", "h2 a", "time"),
    SiteConfig("MIT Tech Review AI", "https://www.technologyreview.com/topic/artificial-intelligence/", "article", "h3 a, h2 a", "h3 a, h2 a", "time"),
    SiteConfig("Ars Technica AI", "https://arstechnica.com/ai/", "li.tease-full, article", "h2 a", "h2 a", "time"),
]

JOB_SITES: List[SiteConfig] = [
    SiteConfig("SimplyHired", "https://www.simplyhired.com/search?q=artificial+intelligence", "div.SerpJob, li.SerpJob-jobCard", "a", "a", "time"),
    SiteConfig("AI-Jobs.net", "https://ai-jobs.net/", "li.job-list-item", "a", "a", "time"),
    SiteConfig("WeWorkRemotely", "https://weworkremotely.com/categories/remote-programming-jobs?term=ai", "li.feature, article", "a", "a", "time"),
    SiteConfig("Wellfound", "https://wellfound.com/role/ai-engineer", "div[data-test='StartupResult'], div[data-test='JobSearchResult']", "a", "a", "time"),
    SiteConfig("RemoteOK", "https://remoteok.com/remote-ai-jobs", "tr.job", "h2", "a.preventLink", "time"),
]

ROLE_FAMILY_KEYWORDS = {
    "ML Engineer": ["ml engineer", "machine learning engineer"],
    "Data Scientist": ["data scientist"],
    "Research Scientist": ["research scientist", "research engineer"],
    "AI Engineer": ["ai engineer", "artificial intelligence engineer"],
    "Software Engineer": ["software engineer", " swe "],
}


def _guess_role_family(title: str) -> str:
    lowered = f" {title.lower()} "
    for family, keywords in ROLE_FAMILY_KEYWORDS.items():
        if any(k in lowered for k in keywords):
            return family
    return "Other"


async def _new_stealth_page(browser: Browser):
    context_kwargs = {"user_agent": random.choice(USER_AGENTS)}
    if config.PROXY_SERVER:
        context_kwargs["proxy"] = {"server": config.PROXY_SERVER}
    context = await browser.new_context(**context_kwargs)
    page = await context.new_page()
    await stealth_async(page)
    return context, page


async def _extract_via_selectors(page, site: SiteConfig, hours: int) -> List[dict]:
    rows = []
    await page.wait_for_selector(site.card_selector, timeout=15_000)
    cards = await page.query_selector_all(site.card_selector)
    for card in cards:
        try:
            title_el = card if site.title_selector == "self" else await card.query_selector(site.title_selector)
            link_el = card if site.link_selector == "self" else await card.query_selector(site.link_selector)
            time_el = await card.query_selector(site.time_selector)
            if not title_el or not link_el:
                continue
            title = (await title_el.inner_text()).strip()
            href = await link_el.get_attribute("href")
            raw_time = None
            if time_el:
                raw_time = await time_el.get_attribute("datetime") or await time_el.inner_text()
            if not title or not href or not raw_time:
                continue
            iso_time = parse_relative_or_absolute(raw_time)
            if not iso_time or not is_within_freshness_window(iso_time, hours=hours):
                continue
            if href.startswith("/"):
                href = urljoin(site.url, href)
            rows.append({"title": title, "url": href, "published_at": iso_time})
        except Exception as e:
            logger.debug(f"{site.name}: skipping a malformed card ({e})")
    return rows


async def _extract_via_llm_fallback(page, site: SiteConfig, hours: int, kind: str) -> List[dict]:
    """Last resort when selectors find nothing: hand the LLM cleaned page text
    and ask it to find one fresh item, matching a Pydantic content schema."""
    try:
        html = await page.content()
    except Exception as e:
        logger.warning(f"{site.name}: could not read page content for LLM fallback ({e})")
        return []

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "svg"]):
        tag.decompose()
    text = soup.get_text(separator="\n", strip=True)
    if not text:
        return []

    schema = NewsContent if kind == "news" else JobContent
    result = await extract(text[:20_000], schema)
    if result is None:
        logger.warning(f"{site.name}: LLM fallback found nothing usable")
        return []

    if kind == "news":
        iso_time = result.publishedAt.isoformat() if isinstance(result.publishedAt, datetime) else str(result.publishedAt)
        if not is_within_freshness_window(iso_time, hours=hours):
            return []
        return [{"title": result.headline, "url": result.url, "published_at": iso_time}]

    return [{
        "company": result.company,
        "date": result.date,
        "is_remote": result.is_remote,
        "role_family": result.role_family,
    }]


async def _scrape_site(browser: Browser, site: SiteConfig, hours: int, kind: str) -> List[dict]:
    context, page = await _new_stealth_page(browser)
    rows: List[dict] = []
    try:
        await page.goto(site.url, timeout=config.REQUEST_TIMEOUT_SECONDS * 1000, wait_until="domcontentloaded")
        try:
            rows = await _extract_via_selectors(page, site, hours)
        except Exception as e:
            logger.warning(f"{site.name}: selector extraction failed ({e}); trying LLM fallback")
        if not rows:
            rows = await _extract_via_llm_fallback(page, site, hours, kind)
    except Exception as e:
        logger.warning(f"{site.name}: scrape failed entirely ({e})")
    finally:
        await context.close()
    return rows


async def scrape_news(hours: Optional[int] = None) -> List[NewsEntity]:
    hours = hours or config.FRESHNESS_WINDOW_HOURS
    entities: List[NewsEntity] = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            per_site = await asyncio.gather(
                *(_scrape_site(browser, site, hours, "news") for site in NEWS_SITES),
                return_exceptions=True,
            )
        finally:
            await browser.close()

    for site, rows in zip(NEWS_SITES, per_site):
        if isinstance(rows, Exception):
            logger.warning(f"{site.name}: {rows}")
            continue
        for row in rows:
            entities.append(
                NewsEntity(
                    source=Source(name=site.name, url=site.url),
                    content=NewsContent(headline=row["title"], url=row["url"], publishedAt=row["published_at"]),
                )
            )
    logger.info(f"signal_scraper: collected {len(entities)} fresh news items")
    return entities


async def scrape_jobs(hours: Optional[int] = None) -> List[JobEntity]:
    hours = hours or config.FRESHNESS_WINDOW_HOURS
    entities: List[JobEntity] = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            per_site = await asyncio.gather(
                *(_scrape_site(browser, site, hours, "job") for site in JOB_SITES),
                return_exceptions=True,
            )
        finally:
            await browser.close()

    for site, rows in zip(JOB_SITES, per_site):
        if isinstance(rows, Exception):
            logger.warning(f"{site.name}: {rows}")
            continue
        for row in rows:
            if "company" in row:  # came from the LLM fallback, already fully shaped
                entities.append(JobEntity(content=JobContent(**row)))
                continue
            entities.append(
                JobEntity(
                    content=JobContent(
                        company=site.name,
                        date=row["published_at"],
                        is_remote="remote" in row["title"].lower(),
                        role_family=_guess_role_family(row["title"]),
                    )
                )
            )
    logger.info(f"signal_scraper: collected {len(entities)} fresh job postings")
    return entities


if __name__ == "__main__":
    async def _main():
        news, jobs = await asyncio.gather(scrape_news(), scrape_jobs())
        print(f"news={len(news)} jobs={len(jobs)}")

    asyncio.run(_main())
