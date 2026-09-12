"""
Signal crawler: 5 AI news sources + 5 AI job boards, both strictly filtered to
the last FRESHNESS_WINDOW_HOURS via src.utils.freshness.

Each site is scraped with Playwright (Chromium) through playwright-stealth so
headless traffic is less likely to trip Cloudflare-style bot checks, with
per-context user-agent rotation and an optional upstream proxy. A site whose
CSS selectors turn up zero fresh rows (e.g. it redesigned its DOM) simply
yields 0 rows for that source -- there is intentionally no LLM fallback here,
since asking an LLM to fill in a required field (a posting date, a company
name) it can't find in the actual page risks fabricating data.
"""
import asyncio
import random
from dataclasses import dataclass
from typing import List, Optional
from urllib.parse import urljoin

from loguru import logger
from playwright.async_api import Browser, async_playwright
from playwright_stealth import stealth_async

from src.config import config
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
    js_row_extractor: Optional[str] = None  # if set, bypasses the generic CSS path entirely


# RemoteOK renders skeleton `tr.job.placeholder` rows first and fills real ones
# in via JS a few seconds later; each real row embeds a JobPosting JSON-LD
# script with an exact `datePosted`, which is far more reliable than scraping
# visible text/relative-time strings.
_REMOTEOK_JS_EXTRACTOR = """
() => {
    const rows = [...document.querySelectorAll('tr.job[data-id]')];
    return rows.map(r => {
        const script = r.querySelector('script[type="application/ld+json"]');
        let title = null, datePosted = null;
        if (script) {
            try {
                const data = JSON.parse(script.textContent);
                title = data.title;
                datePosted = data.datePosted;
            } catch (e) {}
        }
        const href = r.getAttribute('data-href');
        const company = r.getAttribute('data-company');
        return (title && href && datePosted) ? {title, href, datePosted, company} : null;
    }).filter(Boolean);
}
"""

NEWS_SITES: List[SiteConfig] = [
    SiteConfig("VentureBeat AI", "https://venturebeat.com/category/ai/", "article", "h2 a, h3 a", "h2 a, h3 a", "time"),
    SiteConfig("TechCrunch AI", "https://techcrunch.com/category/artificial-intelligence/", "div.loop-card", "h3.loop-card__title a", "h3.loop-card__title a", "time"),
    # The Verge / MIT Tech Review / Ars Technica: verified broken as of 2026-09-12.
    # The Verge uses build-hashed CSS classes with no stable ancestor within 8
    # levels of its <time> element (same failure mode YC's old selectors had).
    # MIT Tech Review's listing renders no article markup at all even after a
    # networkidle wait (client-side fetch failing silently). Ars Technica now
    # serves a "Human Verification" challenge page to headless traffic --
    # a genuine bot-wall, not a selector problem (same category as ProductHunt).
    # Left in place so the LLM fallback gets a chance, but selectors are stale.
    SiteConfig("The Verge AI", "https://www.theverge.com/ai-artificial-intelligence", "article", "h2 a", "h2 a", "time"),
    SiteConfig("MIT Tech Review AI", "https://www.technologyreview.com/topic/artificial-intelligence/", "article", "h3 a, h2 a", "h3 a, h2 a", "time"),
    SiteConfig("Ars Technica AI", "https://arstechnica.com/ai/", "li.tease-full, article", "h2 a", "h2 a", "time"),
]

JOB_SITES: List[SiteConfig] = [
    # SimplyHired / WeWorkRemotely: verified Cloudflare/"Just a moment..." bot
    # walls as of 2026-09-12 (403 before any of our code runs) -- not fixable
    # via selectors, same category as Ars Technica / ProductHunt.
    SiteConfig("SimplyHired", "https://www.simplyhired.com/search?q=artificial+intelligence", "div.SerpJob, li.SerpJob-jobCard", "a", "a", "time"),
    # AI-Jobs.net / Wellfound: page loads (200, no bot wall) but neither renders
    # a <time> element anywhere in the DOM even after a settle wait -- listing
    # markup has changed shape and needs fresh reverse-engineering.
    SiteConfig("AI-Jobs.net", "https://ai-jobs.net/", "li.job-list-item", "a", "a", "time"),
    SiteConfig("WeWorkRemotely", "https://weworkremotely.com/categories/remote-programming-jobs?term=ai", "li.feature, article", "a", "a", "time"),
    SiteConfig("Wellfound", "https://wellfound.com/role/ai-engineer", "div[data-test='StartupResult'], div[data-test='JobSearchResult']", "a", "a", "time"),
    SiteConfig("RemoteOK", "https://remoteok.com/remote-ai-jobs", "tr.job[data-id]", "", "", "", js_row_extractor=_REMOTEOK_JS_EXTRACTOR),
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
    # state="attached" (not the default "visible"): we only need the elements
    # to exist to read their text/attributes -- waiting for visibility fails
    # whenever the *first* DOM match happens to be off-screen/hidden even
    # though later matches (and the ones we'll actually read) are fine.
    await page.wait_for_selector(site.card_selector, timeout=15_000, state="attached")
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


async def _extract_via_js(page, site: SiteConfig, hours: int) -> List[dict]:
    """Sites whose freshness data only exists inside a JS-rendered payload
    (e.g. RemoteOK's per-row JSON-LD) get a dedicated extractor instead of the
    generic CSS-selector path."""
    await page.wait_for_timeout(4_000)  # let placeholder rows get replaced with real ones
    raw_rows = await page.evaluate(site.js_row_extractor)
    rows = []
    for r in raw_rows:
        if not is_within_freshness_window(r["datePosted"], hours=hours):
            continue
        href = r["href"]
        if href.startswith("/"):
            href = urljoin(site.url, href)
        rows.append({"title": r["title"], "url": href, "published_at": r["datePosted"], "company": r.get("company")})
    return rows


async def _scrape_site(browser: Browser, site: SiteConfig, hours: int) -> List[dict]:
    context, page = await _new_stealth_page(browser)
    rows: List[dict] = []
    try:
        await page.goto(site.url, timeout=config.REQUEST_TIMEOUT_SECONDS * 1000, wait_until="domcontentloaded")
        if site.js_row_extractor:
            rows = await _extract_via_js(page, site, hours)
        else:
            # No LLM fallback here on purpose: asking an LLM to fill in a
            # required field (a posting date, a company name) it can't find
            # in the actual page text is exactly how a fabricated timestamp
            # got into a real run (2026-09-12) -- a job entry dated exactly
            # "2024-01-01T00:00:00Z", which is not a real value any source
            # page produced. Every record must trace to a real, sourced
            # field, so a site with stale selectors just yields 0 rows and
            # gets logged, rather than risking invented data.
            try:
                rows = await _extract_via_selectors(page, site, hours)
            except Exception as e:
                logger.warning(f"{site.name}: selector extraction failed, yielding 0 rows for this source ({e})")
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
                *(_scrape_site(browser, site, hours) for site in NEWS_SITES),
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
                *(_scrape_site(browser, site, hours) for site in JOB_SITES),
                return_exceptions=True,
            )
        finally:
            await browser.close()

    for site, rows in zip(JOB_SITES, per_site):
        if isinstance(rows, Exception):
            logger.warning(f"{site.name}: {rows}")
            continue
        for row in rows:
            if "role_family" in row:  # came from the LLM fallback, already fully shaped
                entities.append(JobEntity(content=JobContent(**row)))
                continue
            entities.append(
                JobEntity(
                    content=JobContent(
                        company=row.get("company") or site.name,
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
