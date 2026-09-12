"""
End-to-end pipeline runner.

Usage:
    python main.py                  # run everything
    python main.py --skip-sheets    # scrape + resolve only, skip Google Sheets export
                                       (useful for local testing without a service account)

Pipeline stages:
    1. Scrape startups, products, papers (+ GitHub stars), news, jobs
    2. Resolve entity names to canonical form (startups <-> products <-> jobs)
    3. Export everything to a 6-tab Google Sheet with a public view link
"""
import argparse
import asyncio
import logging

import pandas as pd

from src.scrapers.startups import scrape_startups
from src.scrapers.products import scrape_products
from src.scrapers.arxiv_papers import scrape_papers
from src.scrapers.news import scrape_news
from src.scrapers.jobs import scrape_jobs
from src.entity_resolution.resolver import EntityResolver, MappingLogEntry

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("main")


async def run_scrapers():
    logger.info("=== Stage 1: Scraping ===")
    scraper_names = ("startups", "products", "papers", "news", "jobs")
    # return_exceptions=True is load-bearing: without it, a single failing
    # scraper propagates out of gather() and aborts the whole run before any
    # CSV is written -- discarding the four scrapers that did succeed.
    results = await asyncio.gather(
        scrape_startups(),
        scrape_products(),
        scrape_papers(),
        scrape_news(),
        scrape_jobs(),
        return_exceptions=True,
    )

    scraped = []
    for name, result in zip(scraper_names, results):
        if isinstance(result, BaseException):
            logger.error(
                f"{name} scraper failed, continuing with 0 rows -- "
                f"{type(result).__name__}: {str(result).splitlines()[0]}"
            )
            scraped.append([])
        else:
            scraped.append(result)

    startups, products, papers, news, jobs = scraped
    logger.info(
        f"Scraped: {len(startups)} startups, {len(products)} products, "
        f"{len(papers)} papers, {len(news)} news, {len(jobs)} jobs"
    )
    return startups, products, papers, news, jobs


def run_entity_resolution(startups, products, jobs):
    logger.info("=== Stage 2: Entity Resolution ===")
    resolver = EntityResolver(entity_type="startup")

    startup_names = [s["name"] for s in startups]
    name_map = resolver.resolve_batch(startup_names)
    for s in startups:
        s["canonical_name"] = name_map.get(s["name"], s["name"])

    # Products and jobs reference startup/company names -- resolve against
    # the SAME registry so "OpenAI" everywhere collapses to one canonical form
    for p in products:
        if p.get("startup_name"):
            p["startup_canonical_name"] = resolver.resolve(p["startup_name"])
    for j in jobs:
        j["company_canonical_name"] = resolver.resolve(j["company"])

    log_entries = [
        {
            "raw_name": e.raw_name,
            "canonical_name": e.canonical_name,
            "entity_type": e.entity_type,
            "match_method": e.match_method,
            "confidence": e.confidence,
        }
        for e in resolver.log
    ]
    logger.info(f"Entity resolution produced {len(log_entries)} mapping log entries")
    return log_entries


async def main(skip_sheets: bool):
    startups, products, papers, news, jobs = await run_scrapers()
    entity_log = run_entity_resolution(startups, products, jobs)

    startups_df = pd.DataFrame(startups)
    products_df = pd.DataFrame(products)
    papers_df = pd.DataFrame(papers)
    news_df = pd.DataFrame(news)
    jobs_df = pd.DataFrame(jobs)
    entity_log_df = pd.DataFrame(entity_log)

    # Always save local CSV backups -- cheap insurance if the Sheets export fails
    startups_df.to_csv("data/startups.csv", index=False)
    products_df.to_csv("data/products.csv", index=False)
    papers_df.to_csv("data/research_papers.csv", index=False)
    news_df.to_csv("data/news.csv", index=False)
    jobs_df.to_csv("data/jobs.csv", index=False)
    entity_log_df.to_csv("data/entity_mapping_log.csv", index=False)
    logger.info("Saved local CSV backups to ./data/")

    if skip_sheets:
        logger.info("--skip-sheets flag set: skipping Google Sheets export")
        return

    logger.info("=== Stage 3: Google Sheets Export ===")
    from src.sheets.export import export_all
    url = export_all(startups_df, products_df, papers_df, jobs_df, news_df, entity_log_df)
    logger.info(f"DONE. Public sheet: {url}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-sheets", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(skip_sheets=args.skip_sheets))
