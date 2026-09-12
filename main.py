"""
End-to-end pipeline runner.

Usage:
    python main.py                  # run everything
    python main.py --skip-sheets    # scrape + resolve only, skip Google Sheets export
                                       (useful for local testing without a service account)

Pipeline stages:
    1. Crawl papers (arXiv + GitHub star enrichment), news, and jobs concurrently
    2. Resolve job/company names to canonical form
    3. Write CSV backups to data/ (always) and export a 6-tab Google Sheet (unless skipped)

NOTE: Startups and Products entities/schemas exist (src/models/schemas.py) but
no crawler currently populates them -- only papers_scraper.py and
signal_scraper.py (news + jobs) were built. Those two tabs stay empty until a
startups/products crawler is added.
"""
import argparse
import asyncio
from pathlib import Path
from typing import List

import pandas as pd
from loguru import logger

from src.crawlers.papers_scraper import scrape_papers
from src.crawlers.signal_scraper import scrape_jobs, scrape_news
from src.exporters.gsheet_exporter import export_to_sheets
from src.models.schemas import ProductEntity, StartupEntity
from src.resolvers.entity_resolver import EntityResolver

DATA_DIR = Path(__file__).parent / "data"


def _unwrap(result, label: str) -> list:
    if isinstance(result, BaseException):
        logger.error(f"{label} failed, continuing with 0 rows -- {type(result).__name__}: {result}")
        return []
    return result


def _backup_csv(items: List, filename: str) -> None:
    rows = [item.model_dump(mode="json") for item in items]
    pd.json_normalize(rows).to_csv(DATA_DIR / filename, index=False)
    logger.info(f"Wrote {len(rows)} rows to data/{filename}")


async def run_crawlers():
    logger.info("=== Stage 1: Crawling ===")
    # return_exceptions=True is load-bearing: without it, a single failing
    # crawler propagates out of gather() and aborts the whole run before any
    # CSV is written.
    papers, news, jobs = await asyncio.gather(
        scrape_papers(),
        scrape_news(),
        scrape_jobs(),
        return_exceptions=True,
    )
    papers = _unwrap(papers, "papers_scraper")
    news = _unwrap(news, "signal_scraper (news)")
    jobs = _unwrap(jobs, "signal_scraper (jobs)")
    logger.info(f"Crawled: {len(papers)} papers, {len(news)} news, {len(jobs)} jobs")
    return papers, news, jobs


def run_entity_resolution(jobs) -> EntityResolver:
    logger.info("=== Stage 2: Entity Resolution ===")
    resolver = EntityResolver()
    for job in jobs:
        job.content.company = resolver.resolve(job.content.company)
    logger.info(f"Entity resolution produced {len(resolver.log)} mapping log entries")
    return resolver


async def run_pipeline(skip_sheets: bool = False):
    logger.info("Starting AI landscape ingestion pipeline")

    startups: List[StartupEntity] = []
    products: List[ProductEntity] = []
    papers, news, jobs = await run_crawlers()
    resolver = run_entity_resolution(jobs)

    DATA_DIR.mkdir(exist_ok=True)
    _backup_csv(startups, "startups.csv")
    _backup_csv(products, "products.csv")
    _backup_csv(papers, "research_papers.csv")
    _backup_csv(news, "news.csv")
    _backup_csv(jobs, "jobs.csv")
    pd.DataFrame(resolver.log_as_rows()).to_csv(DATA_DIR / "entity_mapping_log.csv", index=False)
    logger.info("Saved local CSV backups to ./data/")

    if skip_sheets:
        logger.info("--skip-sheets flag set: skipping Google Sheets export")
        return

    logger.info("=== Stage 3: Google Sheets Export ===")
    try:
        url = export_to_sheets(startups, products, papers, jobs, news, resolver.log_as_rows())
        logger.info(f"DONE. Public sheet: {url}")
    except Exception as e:
        logger.error(f"Google Sheets export failed, but CSV backups are safe in data/: {e}")


def main():
    parser = argparse.ArgumentParser(description="AI Landscape Tracker pipeline")
    parser.add_argument("--skip-sheets", action="store_true", help="Scrape + resolve only, skip Google Sheets export")
    args = parser.parse_args()
    asyncio.run(run_pipeline(skip_sheets=args.skip_sheets))


if __name__ == "__main__":
    main()
