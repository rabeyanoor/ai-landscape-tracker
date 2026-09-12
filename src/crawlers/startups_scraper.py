"""
Startup crawler: Y Combinator's public company directory.

YC's own "artificial-intelligence" industry tag alone caps out well under
1,000 unique companies (verified 2026-09-12), so this scrapes several
AI-adjacent industry tags and deduplicates by company slug to reach the
MIN_STARTUPS target. The list is virtualized/infinite-scroll, so each tag
page is scrolled until no new cards render for a few consecutive rounds.
"""
import asyncio
import time
from typing import List, Optional, Set
from urllib.parse import urljoin

from loguru import logger
from playwright.async_api import Browser, async_playwright

from src.config import config
from src.models.schemas import Source, StartupContent, StartupData, StartupEntity

YC_BASE_URL = "https://www.ycombinator.com"
YC_INDUSTRY_TAGS = ["artificial-intelligence", "generative-ai", "machine-learning", "computer-vision", "nlp"]
# YC's list keeps every previously-rendered card mounted as you scroll (it
# doesn't unmount off-screen ones), so the DOM only grows. Checking progress
# with the same combinator+:has() selector used for final extraction re-scans
# that ever-larger tree every time, which is O(n) per check and O(n^2)
# overall -- on the first tag alone, per-check latency grew from ~1s to 114s
# over 8 scrolls before it would have finished (verified 2026-09-12). Fix:
# scroll in batches with NO query in between, and use a cheap attribute-prefix
# selector (no :has()) for the interim progress check; run the expensive full
# extraction selector exactly once, after scrolling stops.
SCROLL_BATCH_SIZE = 10
SCROLL_STEP_DELAY_MS = 150
MAX_BATCHES = 40
PER_TAG_TIME_BUDGET_SECONDS = 90

# Structural selector (not a hashed CSS-module class) so it survives YC's
# per-deploy class-name churn -- every company card is an <a> wrapping a
# top-level <li>.
_CARD_JS = r"""
() => {
    const cards = document.querySelectorAll('a[href^="/companies/"]:has(> li)');
    return [...cards].map(a => {
        const href = a.getAttribute('href');
        const nameEl = a.querySelector('.text-2xl');
        const name = nameEl ? nameEl.textContent.trim() : null;
        const spans = [...a.querySelectorAll('span.text-gray-700')];
        let employeeCount = null;
        for (const s of spans) {
            const m = s.textContent.match(/([\d,]+)\s+employees?/i);
            if (m) { employeeCount = parseInt(m[1].replace(/,/g, ''), 10); break; }
        }
        return (name && href) ? {href, name, employeeCount} : null;
    }).filter(Boolean);
}
"""


async def _scrape_tag(browser: Browser, tag: str, seen: Set[str]) -> List[dict]:
    page = await browser.new_page()
    rows: List[dict] = []
    try:
        url = f"{YC_BASE_URL}/companies/industry/{tag}"
        await page.goto(url, timeout=config.REQUEST_TIMEOUT_SECONDS * 1000, wait_until="domcontentloaded")
        await page.wait_for_timeout(1500)

        # Two dead ends worth recording (verified 2026-09-12):
        # 1. Checking progress every scroll with the same :has() selector used
        #    for final extraction re-scans an ever-growing DOM every time (YC
        #    never unmounts off-screen cards) -- O(n) per check, O(n^2)
        #    overall. Per-check latency grew from ~1s to 114s over 8 scrolls.
        # 2. Swapping in a cheap `a[href^="/companies/"]` count to dodge that
        #    avoided the hang, but each real card actually contains ~3-4 such
        #    anchors (the card wrapper, its logo link, its name link), so the
        #    raw count is inflated ~3.8x versus unique companies -- comparing
        #    it straight to `target_total` tripped the exit far too early
        #    (350 "companies" after 18s instead of the ~917 a full scroll
        #    yields for this same tag).
        # Fix: just scroll for the fixed time/batch budget every time, with NO
        # per-batch progress check at all, then run the one accurate (and
        # here, one-time-cost-acceptable) :has() extraction at the end.
        deadline = time.monotonic() + PER_TAG_TIME_BUDGET_SECONDS
        for _ in range(MAX_BATCHES):
            if time.monotonic() >= deadline:
                break
            for _ in range(SCROLL_BATCH_SIZE):
                await page.mouse.wheel(0, 4000)
                await page.wait_for_timeout(SCROLL_STEP_DELAY_MS)

        cards = await page.evaluate(_CARD_JS)
        for c in cards:
            if c["href"] in seen:
                continue
            seen.add(c["href"])
            rows.append(c)
    except Exception as e:
        logger.warning(f"startups_scraper: tag '{tag}' failed: {e}")
    finally:
        await page.close()
    return rows


async def scrape_startups(target: Optional[int] = None) -> List[StartupEntity]:
    target = target or config.MIN_STARTUPS
    seen: Set[str] = set()
    raw_rows: List[dict] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            for tag in YC_INDUSTRY_TAGS:
                if len(raw_rows) >= target:
                    break
                rows = await _scrape_tag(browser, tag, seen)
                raw_rows.extend(rows)
                logger.info(f"startups_scraper: '{tag}' contributed {len(rows)} new companies (total={len(raw_rows)})")
        finally:
            await browser.close()

    entities = [
        StartupEntity(
            source=Source(name="Y Combinator", url=urljoin(YC_BASE_URL, r["href"])),
            content=StartupContent(entityName=r["name"], data=StartupData(employeeCount=r["employeeCount"])),
        )
        for r in raw_rows
    ]
    logger.info(f"startups_scraper: collected {len(entities)} unique startups")
    return entities


if __name__ == "__main__":
    results = asyncio.run(scrape_startups())
    print(f"Collected {len(results)} startups")
