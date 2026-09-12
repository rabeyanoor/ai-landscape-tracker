"""
Pushes all processed dataframes into one Google Sheet with 6 tabs, and
sets sharing to "Anyone with the link can view".

Requires a Google Cloud service-account JSON key (see .env.example ->
GOOGLE_SERVICE_ACCOUNT_FILE) with the Sheets API and Drive API enabled,
and the service account's email added as an editor if you're pushing into
an existing sheet rather than creating a fresh one.
"""
import logging
from typing import Optional

import gspread
import pandas as pd
from google.oauth2.service_account import Credentials

from src.config import config

logger = logging.getLogger("sheets_export")

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

TAB_ORDER = ["Startups", "Products", "Research Papers", "Jobs", "News", "Entity Mapping Log"]


def _get_client() -> gspread.Client:
    creds = Credentials.from_service_account_file(config.GOOGLE_SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    return gspread.authorize(creds)


def _write_df_to_worksheet(spreadsheet: gspread.Spreadsheet, tab_name: str, df: pd.DataFrame):
    try:
        ws = spreadsheet.worksheet(tab_name)
        ws.clear()
    except gspread.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=tab_name, rows=str(len(df) + 10), cols=str(len(df.columns) + 5))

    if df.empty:
        ws.update([["No data"]])
        return

    # Convert everything to strings to avoid gspread type issues with lists/dicts/NaN
    safe_df = df.copy()
    for col in safe_df.columns:
        safe_df[col] = safe_df[col].apply(
            lambda v: ", ".join(map(str, v)) if isinstance(v, list) else ("" if pd.isna(v) else v)
        )

    values = [safe_df.columns.tolist()] + safe_df.astype(str).values.tolist()
    ws.update(values)
    logger.info(f"Wrote {len(df)} rows to tab '{tab_name}'")


def export_all(
    startups_df: pd.DataFrame,
    products_df: pd.DataFrame,
    papers_df: pd.DataFrame,
    jobs_df: pd.DataFrame,
    news_df: pd.DataFrame,
    entity_log_df: pd.DataFrame,
    sheet_name: Optional[str] = None,
) -> str:
    """Creates (or reuses) the spreadsheet, writes all 6 tabs, sets public
    view access, and returns the shareable URL."""
    sheet_name = sheet_name or config.GOOGLE_SHEET_NAME
    client = _get_client()

    try:
        spreadsheet = client.open(sheet_name)
        logger.info(f"Reusing existing spreadsheet '{sheet_name}'")
    except gspread.SpreadsheetNotFound:
        spreadsheet = client.create(sheet_name)
        logger.info(f"Created new spreadsheet '{sheet_name}'")

    dataframes = {
        "Startups": startups_df,
        "Products": products_df,
        "Research Papers": papers_df,
        "Jobs": jobs_df,
        "News": news_df,
        "Entity Mapping Log": entity_log_df,
    }
    for tab in TAB_ORDER:
        _write_df_to_worksheet(spreadsheet, tab, dataframes[tab])

    # Remove the default "Sheet1" if it's still empty and unused
    try:
        default_ws = spreadsheet.worksheet("Sheet1")
        if default_ws.title not in TAB_ORDER:
            spreadsheet.del_worksheet(default_ws)
    except gspread.WorksheetNotFound:
        pass

    # Anyone with the link can view
    spreadsheet.share(None, perm_type="anyone", role="reader")

    logger.info(f"Public sheet URL: {spreadsheet.url}")
    return spreadsheet.url
