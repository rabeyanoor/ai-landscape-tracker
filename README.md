# AI Landscape Tracker

An end-to-end pipeline that crawls AI research papers, 24-hour-fresh AI news, and AI job postings, structures them with a multi-tier LLM fallback chain, resolves duplicate entities, and publishes everything to a 6-tab Google Sheet.

## Project Structure

```
ai-landscape-tracker/
├── main.py                          # End-to-end orchestrator
├── requirements.txt
├── .env.example                     # Copy to .env and fill in your own API keys
├── src/
│   ├── config.py                     # All env variables in one place
│   ├── models/
│   │   └── schemas.py                 # Pydantic schemas (Startup, Product, ResearchPaper, Job, News)
│   ├── crawlers/
│   │   ├── papers_scraper.py         # arXiv API + GitHub star enrichment
│   │   ├── signal_scraper.py         # 5 news sites + 5 job boards (Playwright + stealth), 24h freshness filter
│   │   └── github_stars.py           # GitHub REST API star lookups
│   ├── llm/
│   │   └── orchestrator.py           # Gemini -> Groq -> DeepSeek fallback chain + chunking
│   ├── resolvers/
│   │   └── entity_resolver.py        # Fuzzy matching + canonical name mapping
│   ├── exporters/
│   │   └── gsheet_exporter.py        # 6-tab Google Sheet export
│   └── utils/
│       ├── http_client.py            # Retry/backoff/429/413-safe HTTP client
│       └── freshness.py              # "2 hours ago" -> ISO date parser
├── data/                              # Local CSV backups (git-ignored)
└── docs/
    └── architecture.md               # Scaling / rate-limit / dedup / storage design doc
```

## Setup

```bash
git clone <your-repo-url>
cd ai-landscape-tracker
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium

cp .env.example .env
# Open .env and fill in your own API keys:
#   GEMINI_API_KEY, GROQ_API_KEY, DEEPSEEK_API_KEY, GITHUB_TOKEN
```

### For Google Sheets export (optional)
1. Create a project in Google Cloud Console and enable the Sheets API and Drive API.
2. Create a Service Account, download its JSON key, and save it as `service_account.json` in the project root.
3. Set `GOOGLE_SERVICE_ACCOUNT_FILE` and `GOOGLE_SHEET_NAME` in `.env`.

## Running

```bash
# Full pipeline (crawl + LLM extraction fallback + entity resolution + Google Sheets export)
python main.py

# Crawl + entity resolution only, skip Sheets (useful without a service account)
python main.py --skip-sheets

# Test a single crawler in isolation
python -m src.crawlers.papers_scraper
python -m src.crawlers.signal_scraper
```

Every run writes CSV backups to `data/`, so a failed Sheets export never loses data.

## Important Notes

- **Freshness filter:** `signal_scraper.py` strictly enforces a 24-hour window (`FRESHNESS_WINDOW_HOURS` in `.env`) using `src/utils/freshness.py`'s relative/absolute date parser.
- **Selector maintenance:** the per-site configs in `signal_scraper.py` (`NEWS_SITES`, `JOB_SITES`) are structural CSS selectors tied to each site's current DOM. If a site redesigns, update that site's `SiteConfig` entry — the scraping/retry logic itself is unchanged. As a safety net, if a site's selectors return zero fresh rows, the scraper automatically falls back once to `src/llm/orchestrator.py` to structure the raw page text instead of giving up entirely.
- **Rate limiting:** the arXiv API is queried with a mandatory 3-second delay between requests per its usage policy; `MAX_CONCURRENT_REQUESTS` throttles everything else.
- **413/429 handling** and the **LLM fallback chain** are both documented in detail in `docs/architecture.md`, along with the strategy for scaling to 500k+ records.

## Known Gap

`StartupEntity` and `ProductEntity` schemas exist in `src/models/schemas.py` and have tabs reserved in the Sheets exporter, but **no crawler currently populates them** — only `papers_scraper.py` and `signal_scraper.py` (news + jobs) have been built so far. `main.py` passes empty lists for Startups/Products until a dedicated crawler is added.

### GitHub star enrichment
Without a `GITHUB_TOKEN` in `.env`, GitHub API calls are made unauthenticated (60 req/hour) and will frequently 403 under load — set your own Personal Access Token to get reliable star counts.

> ⚠️ **Security:** never commit real values into `.env.example` or `.env` — `.env` is already git-ignored. If any real key was ever committed to this repo's history, revoke and rotate it.

## License / Usage

This code is built for educational/assignment purposes. Respect each site's `robots.txt` and Terms of Service before scraping it.
