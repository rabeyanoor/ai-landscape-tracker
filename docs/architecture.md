# AI Landscape Tracker — Technical Architecture

## 1. Scaling to 500,000+ Records

The current pipeline runs single-process, single-machine, with an in-memory
`asyncio.Semaphore` bounding concurrency. That model works for the current
target (~1,000 papers + a rolling 24h window of news/jobs) but breaks down
well before 500k records: a single host's socket/CPU budget, one IP's rate
limit, and one process's memory all become bottlenecks.

**Distributed crawling with Celery + Redis.** Each source (an arXiv category
page, a news site, a job board) becomes a Celery task rather than a coroutine
in one `asyncio.gather()`. Redis backs both the task broker and the result
store. A single "fan-out" task enumerates work units (e.g. one arXiv page
window, one job-board search page) and pushes them onto a queue; a pool of
worker processes — scaled horizontally across machines — pull from that queue
independently. This turns the ingestion rate from "however fast one process
can drive one event loop" into "however many workers we're willing to run."

**Proxy pools.** At 500k records, no single IP survives long against
Cloudflare or a site's own rate limiter. Workers draw from a rotating pool of
residential/datacenter proxies (a `ProxyPool` service backed by Redis, storing
health/cooldown state per proxy so a proxy that just got a 403 isn't reused
for N seconds). Proxy selection is bound to the *site*, not global, since
different sites ban on different timescales.

**Rate-limiting queues.** Politeness limits (arXiv's 3-second rule, a job
board's undocumented-but-real threshold) are enforced with a per-source
token-bucket implemented in Redis (`INCR` + `EXPIRE`, or a Lua script for
atomicity), not with `asyncio.sleep()` in-process — because once there are N
workers instead of 1, only a shared, external rate limiter actually enforces
the site-wide limit. Each worker asks the bucket for a token before firing a
request and backs off (re-queues the task with a delay) if none is available.

## 2. Context Window & Rate-Limit Strategy (LLM Layer)

**Payload chunking.** `src/llm/orchestrator.py` already implements
paragraph-aware chunking (`_chunk_text`): boilerplate whitespace is collapsed,
then text is split on paragraph boundaries up to a `MAX_CHARS_PER_CHUNK`
budget, falling back to hard-slicing only for a single paragraph that alone
exceeds the budget. This keeps a chunk from ever cutting a sentence in half,
which matters for extraction quality, while guaranteeing no request trips an
HTTP 413. At 500k-record scale, the same chunker runs inside each Celery
worker rather than a single process, so chunk size should be tuned per
provider (Gemini's 1M-token context vs. Groq's smaller window) rather than
fixed globally.

**Token-bucket rate limiting per provider.** Each LLM provider (Gemini, Groq,
DeepSeek) gets its own token bucket, sized to that provider's published
RPM/TPM limit, shared across all workers via Redis the same way the crawl
rate limiter is. This is distinct from the *retry* backoff already in
`orchestrator.py` (`_backoff_sleep`, exponential + jitter on 429) — the bucket
prevents most 429s from happening in the first place, and the backoff handles
the ones that slip through (e.g. a burst from many workers hitting the bucket
in the same tick).

**Fallback chain as load-shedding.** The Gemini → Groq → DeepSeek chain
doubles as a rate-limit escape valve: when Gemini's bucket is empty, a worker
doesn't block — it fails over to Groq immediately. At scale this needs a
per-request "already tried" list so a request doesn't ping-pong back to a
provider whose bucket refilled a moment before its own next attempt exhausts
it again.

## 3. Freshness Tracking & Deduplication

**SHA256 URL hashing as the dedup key.** Every raw item is keyed by
`sha256(canonical_url)`, where "canonical" means stripped of tracking query
params (`utm_*`, `ref`, session ids) and normalized (scheme, trailing slash,
lowercased host). This hash is the row's primary key across the distributed
system — two workers scraping the same article from two different job runs
produce the same hash and can be deduplicated without a coordinating lock.

**Bloom filter for the fast-path check.** Before a worker does the (more
expensive) database lookup to check "have I seen this URL hash before,"
it checks a Redis-backed Bloom filter (`RedisBloom`, or a plain bitfield if
that module isn't available). A negative from the Bloom filter is a hard
guarantee the URL is new — no DB round-trip needed — which matters when
500k+ URLs are checked per run. A positive means "maybe seen," which then
falls through to a real lookup (Bloom filters have false positives, never
false negatives).

**Redis key TTLs for freshness, not just dedup.** The 24-hour freshness
window is enforced with `SETEX <url_hash> 86400 1` at ingestion time: a key
existing means "already ingested and still fresh," so a re-crawl within the
window is a no-op, while the same URL naturally becomes eligible again once
its TTL expires and it resurfaces (e.g. a story that gets re-syndicated).
This turns "is this fresh AND have I processed it" into one key lookup
instead of two separate checks against different systems.

**Cross-node consistency.** Because the Bloom filter, the TTL keys, and the
rate-limit token buckets all live in the same Redis cluster (not per-worker
memory), any worker on any machine sees the same dedup/freshness state
immediately — critical once crawling is spread across more than one host.

## 4. Storage Architecture

Three different access patterns need three different stores; forcing all of
them into one database (as the current CSV/Sheets model does) is what caps
this pipeline well under 500k rows.

**PostgreSQL — transactional, structured data.** The canonical store for
every `StartupEntity` / `ProductEntity` / `ResearchPaperEntity` / `JobEntity`
/ `NewsEntity` row, matching the Pydantic schemas 1:1 via a JSONB `content`
column plus indexed top-level columns (`recordType`, `collectedAt`,
`schemaVersion`) for fast filtering. Postgres gives ACID writes (important
once multiple Celery workers are writing concurrently), mature tooling, and
native JSONB querying for the semi-structured `content` payloads.

**Neo4j — startup/paper relationship graph.** The interesting *cross-entity*
questions this project cares about — "which startups came out of which
papers," "which people appear as both a paper author and a job poster's
contact," "which products share a parent startup" — are graph queries, not
relational joins. Neo4j stores nodes for `Startup`, `Paper`, `Person`,
`Product` with typed edges (`AUTHORED`, `FOUNDED`, `PUBLISHES`), and is
populated as a derived view from Postgres rather than being the system of
record — so a bad graph write never risks the canonical data.

**Qdrant/Milvus — vector search.** Paper abstracts, news summaries, and job
descriptions are embedded (e.g. via a small sentence-transformer or the
LLM provider's embedding endpoint) and stored in a vector index for semantic
search ("find papers similar to this one," "find jobs like the one I'm
looking at") and for de-duplicating near-identical stories across news
sources that cover the same event with different wording — a case SHA256 URL
hashing can't catch since the URLs differ. Milvus/Qdrant is chosen over
pgvector at this scale because dedicated ANN indexes (HNSW) stay fast well
past the row counts where pgvector's index build times become painful.

**Data flow:** crawlers → Celery task result → Postgres (system of record) →
async projections update Neo4j (relationships) and Qdrant/Milvus (embeddings)
→ Google Sheets export becomes one more read-only projection off Postgres,
generated on demand rather than being the source of truth it is today.
