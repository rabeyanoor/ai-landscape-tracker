# AI Landscape Tracker

An end-to-end pipeline for tracking the AI ecosystem: it crawls research papers, fresh AI news, and job postings; structures the content with a multi-tier LLM extraction chain; resolves duplicate entities; and exports everything to a Google Sheet.

This project is built to help monitor the latest AI developments across research, startup activity, and hiring signals in a single structured dataset.

## Features

- Crawl AI research papers from arXiv
- Track fresh AI news and job opportunities from multiple sources
- Enrich GitHub project data with star counts
- Normalize and deduplicate entities such as companies, products, and AI papers
- Structure raw content with a fallback LLM chain
- Export structured results to a 6-tab Google Sheet
- Keep CSV backups after each run for reliability and debugging

## Project Structure

```text
ai-landscape-tracker/
├── main.py                          # End-to-end orchestration
├── requirements.txt                 # Python dependencies
├── .env.example                    # Template for local environment variables
├── .gitignore                      # Ignored local files and secrets
├── src/
│   ├── config.py                   # Centralized configuration and env loading
│   ├── models/
│   │   └── schemas.py              # Pydantic models for papers, news, jobs, etc.
│   ├── crawlers/
│   │   ├── papers_scraper.py       # arXiv scraping + GitHub star enrichment
│   │   ├── signal_scraper.py       # News/job scraping with freshness filtering
│   │   ├── github_stars.py          # GitHub star lookup utilities
│   │   ├── products_scraper.py     # Product/entity scraping pipeline
│   │   └── startups_scraper.py     # Startup/entity scraping pipeline
│   ├── llm/
│   │   └── orchestrator.py         # Gemini -> Groq -> DeepSeek fallback chain
│   ├── resolvers/
│   │   └── entity_resolver.py      # Duplicate resolution + canonical matching
│   ├── exporters/
│   │   └── gsheet_exporter.py      # Google Sheets export logic
│   └── utils/
│       ├── http_client.py          # HTTP client with retry/backoff handling
│       └── freshness.py            # Relative/absolute time parsing
├── data/                           # Local CSV output (git-ignored)
├── docs/
│   └── architecture.md             # Design notes for scaling and reliability
└── README.md                       # Project documentation
```

## Tech Stack

- Python 3.11+
- Pydantic
- Playwright
- Google Sheets API
- arXiv API
- GitHub REST API
- Gemini / Groq / DeepSeek LLMs

## Setup

### 1) Clone the repository

```bash
git clone <your-repo-url>
cd ai-landscape-tracker
```

### 2) Create a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3) Install dependencies

```bash
pip install -r requirements.txt
playwright install chromium
```

### 4) Configure environment variables

Copy the example file and fill in the required keys:

```bash
cp .env.example .env
```

Then update `.env` with your own values, including:

- `GEMINI_API_KEY`
- `GROQ_API_KEY`
- `DEEPSEEK_API_KEY`
- `GITHUB_TOKEN`

Optional Google Sheets configuration:

- `GOOGLE_SERVICE_ACCOUNT_FILE`
- `GOOGLE_SHEET_NAME`

> ⚠️ Never commit real secrets. `.env` is git-ignored and must remain local only.

## Running the Pipeline

### Full pipeline

```bash
python main.py
```

This runs the full flow:

- crawler execution
- freshness filtering
- LLM extraction fallback
- entity resolution
- optional Google Sheet export

### Skip Google Sheets export

```bash
python main.py --skip-sheets
```

This is useful when you want to run the data pipeline without a service account or spreadsheet setup.

### Run specific crawlers directly

```bash
python -m src.crawlers.papers_scraper
python -m src.crawlers.signal_scraper
```

## Output and Data Storage

Every run writes local CSV backups into the `data/` folder. This ensures that even if Google Sheets export fails, the extracted data is still preserved locally.

## Important Notes

### Freshness filter

The news/job pipeline enforces a strict time window using `FRESHNESS_WINDOW_HOURS` from `.env` and the date parser in `src/utils/freshness.py`.

### Selector maintenance

Per-site selectors in `signal_scraper.py` are tied to each source website's DOM structure. If a site redesigns, update the relevant `SiteConfig` entry. The core scraping and retry logic remains reusable.

If selector-based extraction returns zero relevant fresh rows, the scraper falls back to the LLM-based text structuring path instead of failing completely.

### Rate limiting

- The arXiv API requires a mandatory delay between requests
- `MAX_CONCURRENT_REQUESTS` is used to throttle other requests and reduce API strain
- GitHub unauthenticated requests are limited and may hit 403s under heavy load

### LLM fallback behavior

The system tries a structured fallback order:

1. Gemini
2. Groq
3. DeepSeek

This ensures the pipeline keeps running even if one provider fails or returns poor extraction quality.

## Google Sheets Export

To enable Google Sheets export:

1. Create a Google Cloud project
2. Enable the Sheets API and Drive API
3. Create a service account
4. Download the JSON key file
5. Save it as `service_account.json` in the project root
6. Set the relevant environment variables in `.env`

Example:

```bash
GOOGLE_SERVICE_ACCOUNT_FILE=service_account.json
GOOGLE_SHEET_NAME=ai_landscape_tracker
```

## Known Gaps

Some schemas and sheet tabs exist for startup and product entities, but dedicated crawlers for those categories are still being expanded. The current pipeline focuses strongly on papers and fresh news/job data.

## Security

- Never commit `.env` values to Git
- Never commit real API keys into `README.md`, `.env.example`, or any tracked file
- If a secret was ever exposed in the repository history, rotate it immediately

## License

This project is licensed under the MIT License. See the [`LICENSE`](LICENSE) file for details.

This project is intended for educational and research-oriented usage. Please respect the robots.txt and Terms of Service of all sites you scrape.
