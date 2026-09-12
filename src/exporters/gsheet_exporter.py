"""
Publishes the fully processed pipeline output to a single Google Sheet with
6 tabs: Startups, Products, Research Papers, Jobs, News, Entity Mapping Log.
"""
from typing import List

import gspread
import pandas as pd
from google.oauth2.service_account import Credentials
from loguru import logger

from src.config import config
from src.models.schemas import JobEntity, NewsEntity, ProductEntity, ResearchPaperEntity, StartupEntity

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def _client() -> gspread.Client:
    creds = Credentials.from_service_account_file(config.GOOGLE_SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    return gspread.authorize(creds)


def _open_or_create_sheet(client: gspread.Client) -> gspread.Spreadsheet:
    try:
        return client.open(config.GOOGLE_SHEET_NAME)
    except gspread.SpreadsheetNotFound:
        sh = client.create(config.GOOGLE_SHEET_NAME)
        sh.share(None, perm_type="anyone", role="reader")
        return sh


def _write_tab(sh: gspread.Spreadsheet, tab_name: str, df: pd.DataFrame) -> None:
    try:
        ws = sh.worksheet(tab_name)
        ws.clear()
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=tab_name, rows=max(len(df) + 10, 100), cols=max(len(df.columns) + 2, 10))
    if df.empty:
        ws.update([["(no data collected this run)"]])
        return
    ws.update([df.columns.tolist()] + df.astype(str).values.tolist())


def _startups_df(items: List[StartupEntity]) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "entityName": i.content.entityName,
            "employeeCount": i.content.data.employeeCount,
            "source": i.source.name,
            "sourceUrl": i.source.url,
            "collectedAt": i.collectedAt,
        }
        for i in items
    ])


def _products_df(items: List[ProductEntity]) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "startupName": i.content.startupName,
            "pricingModel": i.content.pricingModel.value,
            "source": i.source.name,
            "sourceUrl": i.source.url,
            "collectedAt": i.collectedAt,
        }
        for i in items
    ])


def _papers_df(items: List[ResearchPaperEntity]) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "title": i.content.title,
            "authors": ", ".join(i.content.authors),
            "paper_url": i.content.paper_url,
            "github_url": i.content.github_url,
            "github_stars": i.content.github_stars,
            "published_date": i.content.published_date,
        }
        for i in items
    ])


def _jobs_df(items: List[JobEntity]) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "company": i.content.company,
            "date": i.content.date,
            "is_remote": i.content.is_remote,
            "role_family": i.content.role_family,
        }
        for i in items
    ])


def _news_df(items: List[NewsEntity]) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "headline": i.content.headline,
            "url": i.content.url,
            "publishedAt": i.content.publishedAt,
            "summary": i.content.summary,
            "source": i.source.name,
        }
        for i in items
    ])


def export_to_sheets(
    startups: List[StartupEntity],
    products: List[ProductEntity],
    papers: List[ResearchPaperEntity],
    jobs: List[JobEntity],
    news: List[NewsEntity],
    entity_log_rows: List[dict],
) -> str:
    """Writes all 6 tabs and returns the shareable URL of the spreadsheet."""
    client = _client()
    sh = _open_or_create_sheet(client)

    _write_tab(sh, "Startups", _startups_df(startups))
    _write_tab(sh, "Products", _products_df(products))
    _write_tab(sh, "Research Papers", _papers_df(papers))
    _write_tab(sh, "Jobs", _jobs_df(jobs))
    _write_tab(sh, "News", _news_df(news))
    _write_tab(sh, "Entity Mapping Log", pd.DataFrame(entity_log_rows))

    logger.info(f"Exported to Google Sheet: {sh.url}")
    return sh.url
