"""
Scrapes AI startup listings from the Y Combinator public company directory
(https://www.ycombinator.com/companies?industry=Artificial%20Intelligence).

The YC directory is a JS-rendered, infinite-scroll page, so Playwright is used
(not plain requests/BeautifulSoup) to drive the browser, scroll, and let the
company cards actually mount into the DOM before we read them.

NOTE ON SELECTORS: YC (like every site) changes its front-end markup from
time to time. The CSS selectors below were correct as of this pipeline's
last verified run -- if YC ships a redesign, only the `_SELECTORS` dict
needs updating; the scraping/scrolling/retry logic does not change.
"""
import asyncio
import logging
from typing import List

from playwright.async_api import async_playwright, TimeoutError as PWTimeout

from src.config import config

logger = logging.getLogger("startups_scraper")

# The ?industry= query filter takes YC's *industry* taxonomy (Consumer,
# Fintech, B2B, ...), which has no "Artificial Intelligence" member -- passing
# it returns "Sorry, no matching companies found". The AI listing lives at its
# own path-based route instead, which also infinite-scrolls past 1,000 rows.
YC_AI_DIRECTORY_URL = "https://www.ycombinator.com/companies/industry/artificial-intelligence"

# YC ships CSS-module class names whose hash segment (the "i9oky"/"18olp"
# part) changes on every front-end deploy, so matching the full class name
# guarantees breakage. These use structural/substring matchers instead:
#   - company cards are the only anchors pointing at /companies/<slug>
#     (the batch/industry pills point at /companies?... so they don't match)
#   - the description span is the only span in a card with no class attribute
# Structural selectors only -- YC's CSS-module hashes rotate every deploy.
# Each card is an <a href="/companies/<slug>"> wrapping an <li>; :has(> li)
# excludes the bare logo/name anchors nested inside it.
_SELECTORS = {
    "company_card": 'a[href^="/companies/"]:has(> li)',
    "name": "span.text-2xl",
    "description": 'div[class*="line-clamp"]',
    # one row of " Active - N employees - City " spans
    "meta": "span.text-gray-700",
    "tags": 'div[class*="mt-2"] > div[class*="yc-tw-Pill"]',
}


_STATUS_WORDS = {"Active", "Inactive", "Acquired", "Public"}


async def _scroll_until_loaded(page, min_cards: int, max_scrolls: int = 400):
    """YC's directory lazy-loads on scroll. Keep scrolling until we have
    enough cards or the page stops growing (end of results)."""
    prev_count = -1
    stagnant_rounds = 0
    for _ in range(max_scrolls):
        cards = await page.query_selector_all(_SELECTORS["company_card"])
        if len(cards) >= min_cards:
            break
        if len(cards) == prev_count:
            stagnant_rounds += 1
            if stagnant_rounds >= 5:
                logger.warning("Page stopped growing -- reached end of available results")
                break
        else:
            stagnant_rounds = 0
        prev_count = len(cards)
        await page.mouse.wheel(0, 4000)
        await page.wait_for_timeout(600)


async def scrape_startups(min_count: int = None) -> List[dict]:
    min_count = min_count or config.MIN_STARTUPS
    results: List[dict] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page(user_agent=config.USER_AGENT)
        try:
            try:
                await page.goto(YC_AI_DIRECTORY_URL, timeout=60_000)
                await page.wait_for_selector(_SELECTORS["company_card"], timeout=15_000)
            except PWTimeout:
                # YC rotates its CSS-module class hashes on every front-end
                # deploy, so a stale entry in _SELECTORS surfaces here as a
                # timeout. Degrade to an empty result rather than raising:
                # an exception escaping this coroutine takes the whole
                # pipeline down before anything is written to disk.
                logger.error(
                    f"YC directory yielded no '{_SELECTORS['company_card']}' within 15s "
                    "-- selector is almost certainly stale. Returning 0 startups."
                )
                return results
            await _scroll_until_loaded(page, min_count)

            cards = await page.query_selector_all(_SELECTORS["company_card"])
            logger.info(f"Found {len(cards)} company cards")

            for card in cards:
                try:
                    name_el = await card.query_selector(_SELECTORS["name"])
                    desc_el = await card.query_selector(_SELECTORS["description"])
                    meta_els = await card.query_selector_all(_SELECTORS["meta"])
                    tag_els = await card.query_selector_all(_SELECTORS["tags"])

                    name = (await name_el.inner_text()).strip() if name_el else None
                    if not name:
                        continue

                    href = await card.get_attribute("href")
                    detail_url = f"https://www.ycombinator.com{href}" if href else None

                    # The meta row is a variable-length list of " Active ",
                    # " 500 employees ", " San Francisco " -- match on content
                    # rather than position, since inactive/stealth companies
                    # omit some of them.
                    employee_count, location = None, None
                    for m in meta_els:
                        text = (await m.inner_text()).strip()
                        if not text or text in _STATUS_WORDS:
                            continue
                        if "employee" in text.lower():
                            employee_count = text.split()[0]
                        else:
                            location = text

                    results.append({
                        "name": name,
                        "website": detail_url,
                        "description": (await desc_el.inner_text()).strip() if desc_el else None,
                        "employee_count": employee_count,
                        "founded_year": None,
                        "location": location,
                        "industry_tags": [
                            (await t.inner_text()).strip() for t in tag_els
                        ] if tag_els else [],
                        "source": "YCombinator",
                        "source_url": detail_url,
                    })
                except PWTimeout:
                    continue
        finally:
            await browser.close()

    return results[:max(min_count, len(results))]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    data = asyncio.run(scrape_startups())
    print(f"Collected {len(data)} startups")
