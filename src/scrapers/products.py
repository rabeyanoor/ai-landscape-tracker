"""
Scrapes AI products from Product Hunt's AI topic pages
(https://www.producthunt.com/topics/artificial-intelligence).

Product Hunt actively fingerprints scrapers/bot traffic, so this uses
Playwright (real browser) with human-like scroll pacing rather than raw
HTTP requests. Pricing model text ("Free", "Free trial", "Paid", "Contact
for pricing" etc.) is normalized into the PricingModel enum via simple
keyword rules -- see `_infer_pricing_model`.

If Product Hunt's official GraphQL API is available (requires a free
Product Hunt developer token), prefer switching to that -- it's far more
stable than scraping the rendered page. This module is the scraping
fallback for when no PH API token is configured.
"""
import asyncio
import logging
from typing import List

from playwright.async_api import async_playwright, TimeoutError as PWTimeout

from src.config import config
from src.models import PricingModel

logger = logging.getLogger("products_scraper")

PH_AI_TOPIC_URL = "https://www.producthunt.com/topics/artificial-intelligence"

_SELECTORS = {
    "product_card": "[data-test^='post-item']",
    "name": "[data-test='post-name']",
    "tagline": "a[data-test='post-name'] + div",
    "upvote_count": "[data-test='vote-button'] span",
}

_PRICING_KEYWORDS = {
    PricingModel.FREE: ["free", "open source", "no cost"],
    PricingModel.FREEMIUM: ["freemium", "free plan", "free tier", "free + paid"],
    PricingModel.PAID: ["paid", "subscription", "$", "/mo", "/month", "one-time"],
    PricingModel.ENTERPRISE: ["enterprise", "contact sales", "contact us", "custom pricing"],
}


def _infer_pricing_model(text: str) -> PricingModel:
    if not text:
        return PricingModel.UNKNOWN
    lowered = text.lower()
    # Order matters: check ENTERPRISE/FREEMIUM before generic FREE/PAID keywords
    for model in [PricingModel.ENTERPRISE, PricingModel.FREEMIUM, PricingModel.FREE, PricingModel.PAID]:
        if any(kw in lowered for kw in _PRICING_KEYWORDS[model]):
            return model
    return PricingModel.UNKNOWN


async def _scroll_until_loaded(page, min_cards: int, max_scrolls: int = 300):
    prev_count = -1
    stagnant = 0
    for _ in range(max_scrolls):
        cards = await page.query_selector_all(_SELECTORS["product_card"])
        if len(cards) >= min_cards:
            break
        if len(cards) == prev_count:
            stagnant += 1
            if stagnant >= 6:
                break
        else:
            stagnant = 0
        prev_count = len(cards)
        await page.mouse.wheel(0, 3500)
        await page.wait_for_timeout(700)


async def scrape_products(min_count: int = None) -> List[dict]:
    min_count = min_count or config.MIN_PRODUCTS
    results: List[dict] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page(user_agent=config.USER_AGENT)
        try:
            try:
                await page.goto(PH_AI_TOPIC_URL, timeout=60_000, wait_until="domcontentloaded")
            except Exception as e:
                logger.warning(f"Failed to load ProductHunt directly: {e}")
                return results

            try:
                await page.wait_for_selector(_SELECTORS["product_card"], timeout=15_000)
            except PWTimeout:
                # Product Hunt fronts this page with a Cloudflare interstitial
                # ("Just a moment...") for headless traffic, so the cards never
                # mount. Degrade to an empty result rather than raising -- see
                # the module docstring: the PH GraphQL API is the real fix.
                logger.error(
                    "ProductHunt rendered no product cards within 15s (Cloudflare "
                    "bot challenge, or a stale selector). Returning 0 products."
                )
                return results

            await _scroll_until_loaded(page, min_count)

            cards = await page.query_selector_all(_SELECTORS["product_card"])
            logger.info(f"Found {len(cards)} product cards")

            for card in cards:
                name_el = await card.query_selector(_SELECTORS["name"])
                tagline_el = await card.query_selector(_SELECTORS["tagline"])
                vote_el = await card.query_selector(_SELECTORS["upvote_count"])

                name = (await name_el.inner_text()).strip() if name_el else None
                if not name:
                    continue
                tagline = (await tagline_el.inner_text()).strip() if tagline_el else ""
                href = await name_el.get_attribute("href") if name_el else None

                votes_text = (await vote_el.inner_text()).strip() if vote_el else ""
                upvotes = int(votes_text) if votes_text.isdigit() else None

                results.append({
                    "name": name,
                    "startup_name": None,     # resolved later by matching against Startups sheet
                    "description": tagline,
                    "pricing_model": _infer_pricing_model(tagline).value,
                    "price_text_raw": tagline,
                    "category": "Artificial Intelligence",
                    "upvotes": upvotes,
                    "website": f"https://www.producthunt.com{href}" if href else None,
                    "source": "ProductHunt",
                    "source_url": f"https://www.producthunt.com{href}" if href else None,
                })
        finally:
            await browser.close()

    return results[:max(min_count, len(results))]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    data = asyncio.run(scrape_products())
    print(f"Collected {len(data)} products")
