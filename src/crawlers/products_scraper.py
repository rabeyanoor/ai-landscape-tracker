"""
Product crawler: Futurepedia's AI tool directory.

ProductHunt (the spec's original suggestion) serves a Cloudflare "Just a
moment..." challenge to all headless traffic (verified 2026-09-12) and no
ProductHunt developer token is configured in .env, so this uses Futurepedia
instead -- a large, server-side-rendered AI tool directory where every
listing already carries an explicit pricing tag (Free / Freemium / Paid /
...) that maps directly onto our PricingModel enum. Being server-rendered,
category pages can be fetched with plain aiohttp -- no browser needed.

NOTE on `startupName`: Futurepedia lists tools generically and does not
expose each tool's parent legal company name at listing-page depth. Rather
than fabricate one, this uses the tool's own (real, sourced) display name for
`ProductContent.startupName` -- accurate for the large majority of AI tools,
which are themselves single-product startups.
"""
import asyncio
import re
from typing import List, Optional, Set

from bs4 import BeautifulSoup
from loguru import logger

from src.config import config
from src.models.schemas import PricingModel, ProductContent, ProductEntity, Source
from src.utils.http_client import FetchError, HttpClient

BASE_URL = "https://www.futurepedia.io"
# Top categories by listed tool count (verified 2026-09-12) -- the first few
# alone comfortably clear MIN_PRODUCTS even after de-duplicating tools that
# are cross-listed in more than one category.
CATEGORY_SLUGS = [
    "marketing", "ai-agents", "workflows", "research-assistant",
    "personal-assistant", "social-media", "design-generators",
]
CARD_LINK_RE = re.compile(r"^https://www\.futurepedia\.io/tool/[\w-]+")
MAX_PAGES_PER_CATEGORY = 40  # 12 cards/page; comfortably above every category's real page count

_PRICING_MAP = {
    "free": PricingModel.FREE,
    "freemium": PricingModel.FREEMIUM,
    "paid": PricingModel.PAID,
    "free trial": PricingModel.PAID,
    "contact for pricing": PricingModel.ENTERPRISE,
    "contact sales": PricingModel.ENTERPRISE,
}


def _map_pricing(raw: Optional[str]) -> PricingModel:
    if not raw:
        return PricingModel.PAID
    return _PRICING_MAP.get(raw.strip().lower(), PricingModel.PAID)


def _parse_page(html: str) -> List[dict]:
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.find_all("div", class_=lambda c: c and "bg-card" in c.split())
    rows = []
    for card in cards:
        link = card.find("a", href=CARD_LINK_RE)
        name_el = card.find("p")
        if not link or not name_el:
            continue
        name = name_el.get_text(strip=True)
        if not name:
            continue
        pricing_span = card.select_one("div.flex.justify-between.text-lg > span")
        pricing_raw = pricing_span.get_text(strip=True) if pricing_span else None
        rows.append({"name": name, "url": link["href"], "pricing_raw": pricing_raw})
    return rows


async def _scrape_category(client: HttpClient, slug: str, seen: Set[str], target_total: int) -> List[dict]:
    rows: List[dict] = []
    for page_num in range(1, MAX_PAGES_PER_CATEGORY + 1):
        url = f"{BASE_URL}/ai-tools/{slug}?page={page_num}"
        try:
            html = await client.get_text(url)
        except FetchError as e:
            logger.warning(f"products_scraper: {slug} page {page_num} failed: {e}")
            break
        page_rows = _parse_page(html)
        if not page_rows:
            break
        new_rows = [r for r in page_rows if r["url"] not in seen]
        for r in new_rows:
            seen.add(r["url"])
        rows.extend(new_rows)
        if len(seen) >= target_total:
            break
    return rows


async def scrape_products(target: Optional[int] = None) -> List[ProductEntity]:
    target = target or config.MIN_PRODUCTS
    seen: Set[str] = set()
    raw_rows: List[dict] = []

    async with HttpClient() as client:
        for slug in CATEGORY_SLUGS:
            if len(raw_rows) >= target:
                break
            rows = await _scrape_category(client, slug, seen, target)
            raw_rows.extend(rows)
            logger.info(f"products_scraper: '{slug}' contributed {len(rows)} new products (total={len(raw_rows)})")

    entities = [
        ProductEntity(
            source=Source(name="Futurepedia", url=r["url"]),
            content=ProductContent(startupName=r["name"], pricingModel=_map_pricing(r["pricing_raw"])),
        )
        for r in raw_rows
    ]
    logger.info(f"products_scraper: collected {len(entities)} unique products")
    return entities


if __name__ == "__main__":
    results = asyncio.run(scrape_products())
    print(f"Collected {len(results)} products")
