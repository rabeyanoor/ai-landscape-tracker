"""
Central configuration. Loads everything from environment variables (.env file)
so no secret ever gets hard-coded into the codebase.
"""
import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # LLM keys
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
    GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
    DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")

    # GitHub
    GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")

    # Google Sheets
    GOOGLE_SERVICE_ACCOUNT_FILE = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json")
    GOOGLE_SHEET_NAME = os.getenv("GOOGLE_SHEET_NAME", "AI Landscape Tracker")

    # Scraper tuning
    MAX_CONCURRENT_REQUESTS = int(os.getenv("MAX_CONCURRENT_REQUESTS", "8"))
    REQUEST_TIMEOUT_SECONDS = int(os.getenv("REQUEST_TIMEOUT_SECONDS", "30"))
    FRESHNESS_WINDOW_HOURS = int(os.getenv("FRESHNESS_WINDOW_HOURS", "24"))

    # Optional upstream proxy for Playwright contexts, e.g. "http://user:pass@host:port"
    PROXY_SERVER = os.getenv("PROXY_SERVER", "") or None

    # Minimum row targets (Phase I/II requirement)
    MIN_STARTUPS = 1000
    MIN_PRODUCTS = 1000
    MIN_PAPERS = 1000

    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 AI-Landscape-Tracker/1.0"
    )


config = Config()
