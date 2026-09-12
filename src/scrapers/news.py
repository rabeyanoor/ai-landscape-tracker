"""
Scrapes 5 AI news sites and keeps only articles published within the last
24 hours (config.FRESHNESS_WINDOW_HOURS). Each site gets its own small
selector config in SITE_CONFIGS -- add/remove sites there without touching
the scraping logic.

Sites covered by default:
  1. TechCrunch (AI category)
  2. VentureBeat (AI category)
  3. The Verge (AI category)
  4. MIT Technology Review (AI category)
  5. Ars Technica (AI/ML tag)
"""
import asyncio
import logging
from dataclasses import dataclass
from typing import List, Optional

from bs4 import BeautifulSoup

from src.config import config
from src.utils.http_client import HttpClient, FetchError, RateLimitedError
from src.utils.freshness import parse_relative_or_absolute, is_within_freshness_window

logger = logging.getLogger("news_scraper")


@dataclass
class SiteConfig:
    name: str
    listing_url: str
    article_selector: str        # CSS selector for each article/card container
    title_selector: str
    link_selector: str           # usually same node as title, but kept separate for flexibility
    time_selector: str           # element containing relative/absolute publish time
    time_attr: Optional[str] = "datetime"  # prefer a <time datetime="..."> attribute if present


SITE_CONFIGS = [
    SiteConfig(
        name="TechCrunch AI",
        listing_url="https://techcrunch.com/category/artificial-intelligence/",
        article_selector="a.loop-card__title-link",
        title_selector="a.loop-card__title-link",
        link_selector="a.loop-card__title-link",
        time_selector="time",
    ),
    SiteConfig(
        name="VentureBeat AI",
        listing_url="https://venturebeat.com/category/ai/",
        article_selector="article",
        title_selector="h2 a",
        link_selector="h2 a",
        time_selector="time",
    ),
    SiteConfig(
        name="The Verge AI",
        listing_url="https://www.theverge.com/ai-artificial-intelligence",
        article_selector="div[class*='duet--content-cards--content-card']",
        title_selector="h2 a",
        link_selector="h2 a",
        time_selector="time",
    ),
    SiteConfig(
        name="MIT Tech Review AI",
        listing_url="https://www.technologyreview.com/topic/artificial-intelligence/",
        article_selector="div.teaserItem",
        title_selector="a.teaserItem__link",
        link_selector="a.teaserItem__link",
        time_selector="time",
    ),
    SiteConfig(
        name="Ars Technica AI",
        listing_url="https://arstechnica.com/ai/",
        article_selector="article",
        title_selector="h2 a",
        link_selector="h2 a",
        time_selector="time",
    ),
]


def _extract_time_text(node, site: SiteConfig) -> Optional[str]:
    if node is None:
        return None
    if site.time_attr and node.has_attr(site.time_attr):
        return node[site.time_attr]
    return node.get_text(strip=True)


async def _scrape_site(client: HttpClient, site: SiteConfig, now) -> List[dict]:
    articles = []
    try:
        html = await client.get_text(site.listing_url)
    except (FetchError, RateLimitedError) as e:
        logger.warning(f"Skipping {site.name}: {e}")
        return articles

    soup = BeautifulSoup(html, "lxml")
    cards = soup.select(site.article_selector)

    for card in cards:
        title_el = card.select_one(site.title_selector) if card.name != "a" else card
        link_el = card.select_one(site.link_selector) if card.name != "a" else card
        time_el = card.select_one(site.time_selector) or (
            card.find_parent().select_one(site.time_selector) if card.find_parent() else None
        )

        title = title_el.get_text(strip=True) if title_el else None
        url = link_el.get("href") if link_el else None
        if not title or not url:
            continue
        if url.startswith("/"):
            from urllib.parse import urljoin
            url = urljoin(site.listing_url, url)

        raw_time = _extract_time_text(time_el, site)
        iso_time = parse_relative_or_absolute(raw_time, now=now) if raw_time else None
        if not iso_time or not is_within_freshness_window(iso_time, config.FRESHNESS_WINDOW_HOURS, now=now):
            continue  # enforce the "only last 24 hours" requirement

        articles.append({
            "title": title,
            "summary": None,          # filled in later by the LLM pipeline from the article body if needed
            "url": url,
            "published_at_iso": iso_time,
            "published_at_raw": raw_time,
            "source": site.name,
        })

    return articles


async def scrape_news() -> List[dict]:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    all_articles: List[dict] = []

    async with HttpClient() as client:
        results = await asyncio.gather(*[_scrape_site(client, s, now) for s in SITE_CONFIGS])
    for r in results:
        all_articles.extend(r)

    logger.info(f"Collected {len(all_articles)} fresh (<{config.FRESHNESS_WINDOW_HOURS}h) news articles")
    return all_articles


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    data = asyncio.run(scrape_news())
    print(f"Collected {len(data)} fresh news articles")
