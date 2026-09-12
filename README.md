# AI Landscape Tracker

একটি end-to-end পাইপলাইন যা AI স্টার্টআপ, প্রোডাক্ট, রিসার্চ পেপার, ২৪-ঘণ্টার ফ্রেশ নিউজ ও জব সংগ্রহ করে, LLM দিয়ে স্ট্রাকচার্ড করে, ডুপ্লিকেট এনটিটি রিজলভ করে, এবং একটি ৬-ট্যাবের Google Sheet-এ পাবলিশ করে।

## প্রজেক্ট স্ট্রাকচার

```
ai-landscape-tracker/
├── main.py                      # এন্ড-টু-এন্ড অর্কেস্ট্রেটর
├── requirements.txt
├── .env.example                 # কপি করে .env বানান, নিজের API key বসান
├── src/
│   ├── config.py                 # সব env variable এক জায়গায়
│   ├── models.py                 # Pydantic স্কিমা (Startup, Product, Paper, News, Job)
│   ├── scrapers/
│   │   ├── startups.py           # YC Directory (Playwright)
│   │   ├── products.py           # ProductHunt (Playwright)
│   │   ├── arxiv_papers.py       # arXiv API + GitHub stars enrichment
│   │   ├── github_stars.py       # GitHub REST API
│   │   ├── news.py                # ৫টি নিউজ সাইট, ২৪ঘ ফ্রেশনেস ফিল্টার
│   │   └── jobs.py                # ৫টি জব বোর্ড, ২৪ঘ ফ্রেশনেস ফিল্টার
│   ├── llm/
│   │   └── fallback_chain.py     # Gemini -> Groq -> DeepSeek fallback + chunking
│   ├── entity_resolution/
│   │   └── resolver.py           # Fuzzy matching + canonical name mapping
│   ├── sheets/
│   │   └── export.py             # ৬-ট্যাব Google Sheet export + public share
│   └── utils/
│       ├── http_client.py        # Retry/backoff/429/413-safe HTTP client
│       └── freshness.py          # "2 hours ago" -> ISO date parser
├── data/                          # লোকাল CSV ব্যাকআপ (git-ignored)
└── docs/
    └── architecture.pdf
```

## সেটআপ

```bash
git clone <your-repo-url>
cd ai-landscape-tracker
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium

cp .env.example .env
# .env ফাইল খুলে নিজের API key গুলো বসান:
#   GEMINI_API_KEY, GROQ_API_KEY, DEEPSEEK_API_KEY, GITHUB_TOKEN
```

### Google Sheets এক্সপোর্টের জন্য (ঐচ্ছিক ধাপ)
1. Google Cloud Console-এ একটি প্রজেক্ট বানান, Sheets API ও Drive API enable করুন।
2. একটি Service Account বানিয়ে JSON key ডাউনলোড করুন, `service_account.json` নামে প্রজেক্ট রুটে রাখুন।
3. `.env`-এ `GOOGLE_SERVICE_ACCOUNT_FILE` ও `GOOGLE_SHEET_NAME` সেট করুন।

## রান করা

```bash
# পুরো পাইপলাইন (scrape + LLM + entity resolution + Google Sheets export)
python main.py

# শুধু স্ক্র্যাপিং + এনটিটি রেজোলিউশন টেস্ট করতে (Sheets ছাড়া)
python main.py --skip-sheets

# আলাদাভাবে একটি স্ক্র্যাপার টেস্ট করতে
python -m src.scrapers.arxiv_papers
python -m src.scrapers.news
```

প্রতিটি রান শেষে `data/` ফোল্ডারে CSV ব্যাকআপ সেভ হয়, তাই Google Sheets এক্সপোর্ট ব্যর্থ হলেও ডাটা হারাবে না।

## গুরুত্বপূর্ণ নোট

- **CSS Selector মেইনটেনেন্স:** `startups.py`, `products.py`, `news.py`, `jobs.py`-তে ব্যবহৃত selector গুলো সংশ্লিষ্ট সাইটের বর্তমান DOM structure অনুযায়ী লেখা। সাইট রিডিজাইন হলে শুধু ঐ ফাইলের `_SELECTORS` / `SITE_CONFIGS` dict আপডেট করলেই হবে — বাকি স্ক্র্যাপিং/রিট্রাই লজিক অপরিবর্তিত থাকবে।
- **Rate limiting সম্মান করুন:** arXiv API নীতি অনুযায়ী রিকোয়েস্টের মাঝে ৩ সেকেন্ড বিরতি রাখা হয়েছে; অন্য সাইটেও `MAX_CONCURRENT_REQUESTS` কমিয়ে ব্যবহার করা ভালো অভ্যাস।
- **413/429 হ্যান্ডলিং** এবং **Fallback Chain**-এর বিস্তারিত ব্যাখ্যা `docs/architecture.pdf`-এ আছে।
- বড় পরিসরে (৫ লাখ+ রো) স্কেল করার স্ট্র্যাটেজিও `docs/architecture.pdf`-এ বর্ণিত আছে।

## স্ক্র্যাপারের বর্তমান অবস্থা (২০২৬-০৯-১০ যাচাই করা)

প্রতিটি সোর্স লাইভ চালিয়ে যাচাই করা হয়েছে। সাইটগুলো রিডিজাইন হলে এই টেবিল আবার আপডেট করতে হবে।

| সোর্স | অবস্থা | রো | নোট |
|---|---|---|---|
| arXiv API | ✅ কাজ করে | ১০০০ | অফিসিয়াল API, কোনো selector নির্ভরতা নেই |
| Y Combinator | ✅ ঠিক করা হয়েছে | ৯১৪ | AI-ট্যাগ করা পুরো লিস্টই ৯১৪-তে শেষ হয় (MIN_STARTUPS=1000 ছুঁইয়ে যায় না) |
| VentureBeat | ⚠️ আংশিক | ০–২ | selector ঠিক আছে, কিন্তু listing page-এ সাধারণত ২৪ঘ-এর পুরোনো আর্টিকেল থাকে |
| TechCrunch | ❌ ভাঙা | ০ | card নিজেই `<a>`, তার ভিতরে বা parent-এ কোনো `<time>` নেই → সব রো ফ্রেশনেস ফিল্টারে বাদ পড়ে |
| The Verge | ❌ ভাঙা | ০ | container মেলে (৪২টি) কিন্তু ভিতরের `h2 a` আর মেলে না |
| MIT Tech Review | ❌ ভাঙা | ০ | `div.teaserItem` DOM থেকে উঠে গেছে |
| Ars Technica | ❌ ভাঙা | ০ | রেসপন্স ০ বাইট ফেরত আসে |
| SimplyHired / AI-Jobs / WeWorkRemotely / Wellfound | ❌ ভাঙা | ০ | চারটিরই `card_selector` আর মেলে না |
| RemoteOK | ❌ ভাঙা | ০ | ৯টি card মেলে, কিন্তু ভিতরের title/link selector মেলে না |
| ProductHunt | ⛔ ব্লকড | ০ | headless ট্রাফিকে Cloudflare চ্যালেঞ্জ ("Just a moment...") |

**গুরুত্বপূর্ণ:** ভাঙা সোর্সগুলোর কোনোটিই নেটওয়ার্ক-লেভেলে ব্লকড নয় (ProductHunt ছাড়া) — HTML ঠিকমতোই আসে, শুধু CSS selector গুলো পুরোনো হয়ে গেছে। ঠিক করতে `news.py`-এর `SITE_CONFIGS` আর `jobs.py`-এর `JOB_SITE_CONFIGS` dict-এ selector আপডেট করলেই হবে; স্ক্র্যাপিং লজিক অপরিবর্তিত থাকবে।

### ProductHunt সম্পর্কে
Cloudflare চ্যালেঞ্জ selector দিয়ে ঠিক করা যাবে না। আসল সমাধান হলো ProductHunt-এর অফিসিয়াল GraphQL API (ফ্রি developer token লাগে) — `products.py`-এর module docstring-এও এটাই সুপারিশ করা আছে।

### GitHub star enrichment
`.env` ফাইল না থাকলে `GITHUB_TOKEN` খালি থাকে, ফলে GitHub API unauthenticated হিসেবে কল হয় (৬০ req/hour) এবং প্রতিটি repo-তে **403** ফেরত আসে — তাই `github_stars` কলাম সবসময় খালি থাকে। ঠিক করতে নিজের একটি Personal Access Token দিয়ে `.env` বানান।

> ⚠️ **সিকিউরিটি:** `.env.example`-এ যে API key গুলো কমিট করা আছে (`GITHUB_TOKEN`, `GEMINI_API_KEY`, `GROQ_API_KEY`, `DEEPSEEK_API_KEY`) সেগুলো আসল-ফরম্যাটের live key বলে মনে হয়। ওগুলো ব্যবহার না করে revoke করে নিজের key জেনারেট করুন।

## যেসব বাগ ঠিক করা হয়েছে

1. **`main.py` — পুরো পাইপলাইন সাইলেন্টলি hang করত, একটাও CSV লেখা হতো না।**
   `asyncio.gather(...)`-এ `return_exceptions=True` ছিল না। YC-এর stale selector থেকে ওঠা `TimeoutError` gather পার হয়ে `main()` abort করত — `to_csv` লাইনগুলোতে পৌঁছানোর *আগেই*। এক্সেপশনটা asyncio shutdown-এ ঢুকে যেত, তাই traceback পর্যন্ত প্রিন্ট হতো না; প্রসেস শুধু ঝুলে থাকত (exit code 124)। এখন প্রতিটি স্ক্র্যাপারের ব্যর্থতা আলাদাভাবে log হয় আর `[]` ধরে বাকি কাজ চলতে থাকে।

2. **`startups.py` / `products.py` — `wait_for_selector` guard করা হয়েছে।**
   টাইমআউট হলে এখন error log করে `[]` ফেরত দেয়, exception ছুঁড়ে পুরো রান নামিয়ে দেয় না।

3. **`startups.py` — YC-এর AI ফিল্টার আসলে কখনো কাজই করত না।**
   `?industry=Artificial%20Intelligence` — কিন্তু `industry` প্যারামিটার YC-এর *industry* taxonomy (Consumer, Fintech, B2B...) নেয়, যেখানে "Artificial Intelligence" নেই। পেজটা "Sorry, no matching companies found" দেখাত। সঠিক রুট হলো path-based:
   `https://www.ycombinator.com/companies/industry/artificial-intelligence`

4. **`startups.py` — selector গুলো hash-proof করা হয়েছে।**
   YC প্রতি deploy-এ CSS-module class hash বদলায় (`_company_i9oky_355` → `_company_18olp_357`), তাই পুরো class name ম্যাচ করা মানেই নিশ্চিত ভাঙা। এখন structural selector ব্যবহার হয় (`a[href^="/companies/"]:has(> li)`)। বোনাস: নতুন লেআউটে `employee_count`-ও পাওয়া যায়, যেটা আগে hard-coded `None` ছিল।

## অসম্পূর্ণ ফিচার

- **`src/llm/fallback_chain.py` কোথাও কল হয় না।** `main.py` কখনো import করে না, আর `resolve_batch()`-এ `llm_tiebreaker` পাস করা হয় না — ফলে Gemini → Groq → DeepSeek চেইনটা কখনোই চলে না এবং `news.summary` সবসময় `None` থাকে।
- **`src/models.py`-এর Pydantic স্কিমা ভ্যালিডেশনে ব্যবহার হয় না।** শুধু `PricingModel` enum import হয়; কোনো স্ক্র্যাপারের আউটপুট `Startup`/`NewsArticle`/`JobPosting`-এর বিরুদ্ধে validate করা হয় না।

## লাইসেন্স / ব্যবহারবিধি

এই কোড শিক্ষামূলক/অ্যাসাইনমেন্ট উদ্দেশ্যে তৈরি। যেকোনো সাইট স্ক্র্যাপ করার আগে তাদের `robots.txt` ও Terms of Service মেনে চলুন।
