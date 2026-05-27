# Groupon Deal Page Optimizer — Architecture Design

## Overview

A three-stage Python pipeline: **Audit → Research → Propose**. Each stage is a discrete module with clean inputs/outputs, stored in DuckDB for queryability. All 20 deals run concurrently (rate-limited). Claude is used at every stage — not just at the end for summarization.

---

## Pipeline Stages & Data Flow

```
deals.txt / CLI args
        │
        ▼
┌─────────────────┐
│   Ingestion     │  → generates deal_id, normalizes URL
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│    Scraper      │  → Playwright (JS-heavy Groupon pages)
│  (groupon.py)   │    raw HTML + screenshot
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│    Parsers      │  → deal_parser, pricing_parser,
│  (per concern)  │    seo_parser, trust_parser
└────────┬────────┘
         │ Structured audit data
         ▼
┌─────────────────┐
│  AI: Audit      │  → Claude classifies ambiguous elements,
│  Analyzer       │    scores deal quality, flags gaps
└────────┬────────┘
         │ audit.json + audit_summary.md
         ▼
┌─────────────────┐
│   Researcher    │  → Web search + Yelp/Google scraping
│                 │    competitor pricing, review themes
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  AI: Research   │  → Claude synthesizes competitor data,
│  Synthesizer    │    identifies review themes, values deal
└────────┬────────┘
         │ research.json + research_report.md
         ▼
┌─────────────────┐
│  AI: Proposal   │  → Claude generates specific, data-backed
│  Generator      │    recommendations with priority ranking
└────────┬────────┘
         │ proposal.json + optimization_proposal.md
         ▼
┌─────────────────┐
│    Reporter     │  → Jinja2 templates → Markdown + HTML
└────────┬────────┘
         │
         ▼
  outputs/{deal_id}/
  ├── 1_audit/
  ├── 2_research/
  └── 3_proposal/
```

All intermediate results written to DuckDB after each stage. If a stage fails, the pipeline resumes from the last successful checkpoint.

---

## Module Breakdown

### `pipeline/orchestrator.py`
The top-level coordinator. Reads `deals.txt`, spawns concurrent workers (one per deal, up to N=5 in parallel), manages the stage-by-stage execution, handles retries and checkpointing.

**Key responsibilities:**
- Load URLs, assign deal IDs
- For each deal: run Audit → Research → Proposal in sequence
- Across deals: run up to 5 concurrently (asyncio + semaphore)
- Log stage completion to `pipeline_runs` table
- On failure: mark stage as `failed`, skip to next deal, continue

---

### `scraper/`

**`groupon.py`** — Playwright-based scraper with stealth mode (to avoid bot detection). Renders the full page, scrolls to load lazy images, captures both the raw HTML and a screenshot.

**`parsers/deal_parser.py`** — Extracts core deal fields: title, subtitle, merchant name, category, city, description blocks.

**`parsers/pricing_parser.py`** — Extracts all pricing tiers: original price, deal price, discount %, savings amount. Handles multi-option deals (e.g., "1 session / 3 sessions / 5 sessions").

**`parsers/seo_parser.py`** — Meta title, meta description, H1/H2 structure, schema.org markup (JSON-LD), canonical URL, OG tags, script/image counts.

**`parsers/trust_parser.py`** — Extracts trust signals (review count, avg rating, "X bought", guarantee badges) and urgency elements (countdown timers, "selling fast", limited quantity text).

**`parsers/image_parser.py`** — Image count, alt text presence/quality, estimated resolution from srcset, image type classification (product vs. lifestyle vs. stock).

---

### `researcher/`

**`web_search.py`** — Wrapper around SerpAPI (primary) with a fallback to direct Google scraping. Issues structured queries like `"[merchant name] [city] price"` and `"[service type] [city] competitors"`.

**`competitor_finder.py`** — Given a merchant + service + city, finds 3–5 competitors and their pricing via search results and direct page scraping.

**`yelp_scraper.py`** — Scrapes Yelp listing for the merchant: overall rating, review count, recent review text, common praise/complaint themes.

**`google_reviews.py`** — Same for Google Maps/Business listing.

**`category_benchmarker.py`** — Issues category-level queries to establish typical discount ranges, average price points, and common deal structures for the deal's category (spa, automotive, etc.).

**`synthesizer.py`** — Collects all raw research data and hands it to the AI Research Synthesizer.

---

### `ai/`

This is where Claude does the heavy lifting. Three distinct AI roles, each with its own prompt design and output schema.

**`client.py`** — Anthropic SDK wrapper. Handles rate limiting (exponential backoff), token counting, structured JSON output via `tool_use` / `response_format`, and prompt logging to DuckDB.

**`audit_analyzer.py`** — Takes raw parsed deal data and asks Claude to:
- Score the deal page on 6 dimensions (clarity, trust, urgency, SEO, value communication, completeness) — 1–10 each
- Classify each trust/urgency element as high/medium/low impact
- Identify content gaps (what's missing that customers would want to know)
- Flag any misleading or unclear elements

Uses **extended thinking** for the gap analysis step, since this requires reasoning about what's *absent*, not just what's present.

**`research_synthesizer.py`** — Takes raw competitor data + reviews and asks Claude to:
- Extract the top 5 recurring themes from Yelp/Google reviews (with frequency counts)
- Assess whether the Groupon price is a genuine deal vs. booking direct (with specific $$ comparison)
- Identify the merchant's key differentiators vs. competitors
- Flag any red flags from review sentiment

Uses **structured tool_use** output so results are always parseable into the DuckDB schema.

**`proposal_generator.py`** — The most complex prompt. Takes the complete audit + research context and generates:
- Specific rewrites (title, highlights, meta description) — not just "improve this", but the actual new copy
- A priority-ranked list of changes with expected impact rationale
- Each recommendation cites a specific data point from the audit or research

Uses a **multi-step prompting** approach: first generate the full recommendation list, then re-rank by expected impact, then validate that each recommendation cites a source.

**`schemas/`** — Pydantic models for each AI output, used to validate and parse Claude's JSON responses. If Claude's output doesn't match the schema, the pipeline logs the failure and retries once with a corrective prompt.

---

### `storage/`

**`schema.sql`** — All table definitions. Applied once on first run via `database.py`.

**`database.py`** — DuckDB connection manager. Single connection, context manager pattern. All writes are transactional.

**`repositories/`** — One repo per domain: `deal_repo.py`, `research_repo.py`, `proposal_repo.py`. Each has `upsert()` (idempotent, safe to re-run), `get_by_id()`, and `get_all_complete()`. The orchestrator only uses repos, never raw SQL.

---

### `reporter/`

**`json_reporter.py`** — Serializes the DuckDB rows for a deal into the three output JSON files.

**`markdown_reporter.py`** — Renders Jinja2 templates with deal data into human-readable Markdown. Each template is authored for readability — not just a JSON dump.

**`templates/`** — Three Jinja2 templates:
- `audit_summary.md.j2` — Scorecard format with section-by-section breakdown
- `research_report.md.j2` — Competitor table + review theme breakdown + value assessment
- `optimization_proposal.md.j2` — Priority-ranked recommendation cards, each with: current state → proposed change → data rationale → expected impact

---

## DuckDB Schema

### Core Tables

**`deals`**
```
deal_id          TEXT PRIMARY KEY   -- slug: "{merchant}_{city}_{hash}"
url              TEXT NOT NULL
title            TEXT
subtitle         TEXT
merchant_name    TEXT
category         TEXT               -- spa|restaurant|activities|automotive|health
city             TEXT
state            TEXT
scraped_at       TIMESTAMP
scrape_status    TEXT               -- pending|success|failed
raw_html_path    TEXT               -- path to stored raw HTML file
```

**`pricing_options`**
```
id               INTEGER PRIMARY KEY
deal_id          TEXT REFERENCES deals
option_name      TEXT               -- "1 Session", "5 Sessions", etc.
original_price   DECIMAL(10,2)
deal_price       DECIMAL(10,2)
discount_pct     DECIMAL(5,2)
savings_amount   DECIMAL(10,2)
is_primary       BOOLEAN            -- true for the "featured" pricing option
```

**`deal_content`**
```
deal_id          TEXT REFERENCES deals
highlights       JSON               -- list of highlight strings
fine_print       JSON               -- list of terms strings
faqs             JSON               -- list of {question, answer} objects
description_html TEXT
```

**`deal_images`**
```
id               INTEGER PRIMARY KEY
deal_id          TEXT REFERENCES deals
src_url          TEXT
alt_text         TEXT
estimated_width  INTEGER
image_type       TEXT               -- product|lifestyle|stock|logo|unknown
position         INTEGER            -- order on page
```

**`seo_elements`**
```
deal_id          TEXT REFERENCES deals
meta_title       TEXT
meta_description TEXT
h1_text          TEXT
h2_texts         JSON               -- list of h2 strings
has_schema_markup BOOLEAN
schema_type      TEXT               -- "Product", "LocalBusiness", etc.
canonical_url    TEXT
og_title         TEXT
og_description   TEXT
og_image_url     TEXT
image_count      INTEGER
script_count     INTEGER
```

**`trust_signals`**
```
deal_id          TEXT REFERENCES deals
signal_type      TEXT               -- rating|review_count|sold_count|guarantee|badge
signal_value     TEXT               -- "4.7 stars", "2,847 bought", etc.
impact_score     TEXT               -- high|medium|low (set by AI)
```

**`urgency_elements`**
```
deal_id          TEXT REFERENCES deals
element_type     TEXT               -- countdown|limited_qty|selling_fast|ends_text
element_text     TEXT
is_visible_atf   BOOLEAN            -- above the fold?
```

### Research Tables

**`competitor_prices`**
```
id               INTEGER PRIMARY KEY
deal_id          TEXT REFERENCES deals
competitor_name  TEXT
service_name     TEXT
regular_price    DECIMAL(10,2)
sale_price       DECIMAL(10,2)
source_url       TEXT
scraped_at       TIMESTAMP
notes            TEXT
```

**`merchant_reviews`**
```
id               INTEGER PRIMARY KEY
deal_id          TEXT REFERENCES deals
platform         TEXT               -- yelp|google|tripadvisor
overall_rating   DECIMAL(3,1)
review_count     INTEGER
scraped_at       TIMESTAMP
```

**`review_themes`**
```
id               INTEGER PRIMARY KEY
deal_id          TEXT REFERENCES deals
platform         TEXT
theme            TEXT               -- e.g., "relaxing atmosphere"
sentiment        TEXT               -- positive|negative|neutral
mention_count    INTEGER
sample_quote     TEXT
```

**`research_sources`**
```
id               INTEGER PRIMARY KEY
deal_id          TEXT REFERENCES deals
source_type      TEXT               -- web_search|yelp|google|direct_scrape
source_url       TEXT
source_title     TEXT
fetched_at       TIMESTAMP
relevance        TEXT               -- competitor_price|review|category_context
```

### AI Output Tables

**`audit_scores`**
```
deal_id          TEXT REFERENCES deals
clarity_score    INTEGER            -- 1-10
trust_score      INTEGER
urgency_score    INTEGER
seo_score        INTEGER
value_comm_score INTEGER            -- value communication
completeness_score INTEGER
overall_score    DECIMAL(4,2)       -- weighted average
content_gaps     JSON               -- list of gap strings
ai_flags         JSON               -- list of issues flagged by Claude
analyzed_at      TIMESTAMP
prompt_tokens    INTEGER            -- for cost tracking
completion_tokens INTEGER
```

**`research_synthesis`**
```
deal_id          TEXT REFERENCES deals
value_assessment TEXT               -- genuine_deal|marginal|overpriced
groupon_vs_direct_savings DECIMAL(10,2)
groupon_vs_competitor_savings DECIMAL(10,2)
merchant_differentiators JSON       -- list of differentiator strings
red_flags        JSON               -- list of warning strings
deal_quality     TEXT               -- strong|average|weak
synthesized_at   TIMESTAMP
```

**`optimization_proposals`**
```
deal_id          TEXT REFERENCES deals
proposed_title   TEXT
proposed_meta_title TEXT
proposed_meta_description TEXT
proposed_highlights JSON            -- rewritten highlights list
title_reasoning  TEXT
generated_at     TIMESTAMP
```

**`proposal_recommendations`**
```
id               INTEGER PRIMARY KEY
deal_id          TEXT REFERENCES deals
priority_rank    INTEGER            -- 1 = highest impact
category         TEXT               -- title|pricing|highlights|content|images|seo|positioning
recommendation   TEXT               -- the specific change to make
current_state    TEXT               -- what's there now
proposed_state   TEXT               -- what to change it to
data_citation    TEXT               -- specific data point that supports this
expected_impact  TEXT               -- high|medium|low
impact_rationale TEXT               -- why this will move the needle
```

**`pipeline_runs`**
```
id               INTEGER PRIMARY KEY
deal_id          TEXT REFERENCES deals
stage            TEXT               -- scrape|parse|audit_ai|research|research_ai|proposal_ai|report
status           TEXT               -- pending|running|success|failed|skipped
started_at       TIMESTAMP
completed_at     TIMESTAMP
error_message    TEXT
retry_count      INTEGER DEFAULT 0
```

---

## Folder Structure

```
groupon-optimizer/
│
├── README.md                    # Setup + one-command run instructions
├── deals.txt                    # 20 Groupon URLs, one per line
├── requirements.txt
├── pyproject.toml               # project metadata + scripts entry point
├── .env.example                 # ANTHROPIC_API_KEY, SERPAPI_KEY, etc.
│
├── run.py                       # Entry point: python run.py [--urls deals.txt] [--concurrency 5]
│
├── config/
│   ├── settings.py              # Pydantic Settings: API keys, rate limits, paths
│   └── prompts/                 # Prompt templates as .txt files (versioned separately from code)
│       ├── audit_analysis.txt
│       ├── research_synthesis.txt
│       └── optimization_proposal.txt
│
├── pipeline/
│   ├── __init__.py
│   ├── orchestrator.py          # asyncio pipeline coordinator
│   ├── ingestion.py             # URL → DealInput (deal_id, url, metadata)
│   └── checkpoint.py            # Resume logic: which stages are already done?
│
├── scraper/
│   ├── __init__.py
│   ├── browser.py               # Playwright setup, stealth config, retry wrapper
│   ├── groupon.py               # Groupon-specific scrape logic (scroll, wait for JS)
│   └── parsers/
│       ├── deal_parser.py       # Title, merchant, category, description
│       ├── pricing_parser.py    # All pricing tiers
│       ├── seo_parser.py        # Meta tags, headings, schema markup
│       ├── trust_parser.py      # Trust signals + urgency elements
│       └── image_parser.py      # Image inventory + quality signals
│
├── researcher/
│   ├── __init__.py
│   ├── search.py                # SerpAPI wrapper + fallback
│   ├── competitor_finder.py     # Find + scrape competitor prices
│   ├── yelp_scraper.py          # Yelp merchant profile + reviews
│   ├── google_reviews.py        # Google Business profile + reviews
│   ├── category_benchmarker.py  # Category-level pricing norms
│   └── source_tracker.py        # Citation logging → research_sources table
│
├── ai/
│   ├── __init__.py
│   ├── client.py                # Anthropic SDK wrapper (rate limit, retry, logging)
│   ├── audit_analyzer.py        # Stage 1 AI: score + classify + gap-find
│   ├── research_synthesizer.py  # Stage 2 AI: synthesize competitor + review data
│   ├── proposal_generator.py    # Stage 3 AI: generate + rank recommendations
│   └── schemas/                 # Pydantic models for all AI JSON outputs
│       ├── audit_schema.py
│       ├── research_schema.py
│       └── proposal_schema.py
│
├── storage/
│   ├── __init__.py
│   ├── database.py              # DuckDB connection manager (singleton)
│   ├── schema.sql               # All CREATE TABLE statements
│   └── repositories/
│       ├── deal_repo.py         # deals + related tables
│       ├── research_repo.py     # competitor + review + synthesis tables
│       └── proposal_repo.py     # proposal + recommendation tables
│
├── reporter/
│   ├── __init__.py
│   ├── json_reporter.py         # Serialize DB rows → audit.json, research.json, proposal.json
│   ├── markdown_reporter.py     # Jinja2 render → .md files
│   └── templates/
│       ├── audit_summary.md.j2
│       ├── research_report.md.j2
│       └── optimization_proposal.md.j2
│
├── outputs/                     # Generated per-deal folders (gitignored)
│   └── {deal_id}/
│       ├── 1_audit/
│       │   ├── audit.json
│       │   └── audit_summary.md
│       ├── 2_research/
│       │   ├── research.json
│       │   └── research_report.md
│       └── 3_proposal/
│           ├── proposal.json
│           └── optimization_proposal.md
│
└── data/
    ├── groupon_optimizer.duckdb  # Primary database (gitignored)
    └── raw_html/                 # Stored raw HTML snapshots (gitignored)
        └── {deal_id}.html
```

---

## AI Integration Design (Key Detail)

The evaluators want to see Claude used for *judgment*, not just summarization. Here's exactly where Claude does the heavy lifting and why:

### Stage 1 — Audit AI (`audit_analyzer.py`)

**Input:** structured parsed data (titles, prices, images, trust signals, etc.)

**What Claude does that rules can't:**
- Scores "value communication" — does the page clearly explain *why* $49 for a massage is a good deal? A regex can find the price; only Claude can assess the framing.
- Classifies ambiguous trust signals — "over 10 years in business" is a trust signal; "open 7 days a week" probably isn't. Claude makes that call.
- Gap detection — identifies what a customer *would want to know* that isn't on the page. This requires reasoning about customer intent, not just page content.

**Prompt strategy:** Extended thinking on the gap analysis step. Structured `tool_use` output for scores (guarantees parseable JSON).

### Stage 2 — Research AI (`research_synthesizer.py`)

**Input:** raw competitor prices, Yelp review text, Google review text, category benchmarks

**What Claude does:**
- Extracts recurring themes from review text and counts them — "23 reviews mention 'relaxing atmosphere'" — this requires reading and coding the reviews, not keyword matching.
- Judges whether the Groupon price is a genuine deal. Inputs: Groupon price, competitor prices, direct booking price, typical category discount. Claude weighs these and issues a verdict with reasoning.
- Identifies merchant differentiators — what does this merchant genuinely do better than competitors, based on the review corpus?

**Prompt strategy:** Few-shot examples in the prompt to calibrate what "specific" means. Claude is explicitly told to cite source URLs and quote review text, not paraphrase.

### Stage 3 — Proposal AI (`proposal_generator.py`)

**Input:** complete audit scores + gaps + research synthesis (all in context)

**What Claude does:**
- Writes the actual new copy (title, highlights, meta description) — specific words, not just direction.
- Ranks recommendations by expected impact. Claude is given the deal's audit scores and told to prioritize changes that address the *lowest-scoring* dimensions with *highest customer sensitivity*.
- Citations are enforced — Claude is instructed that every recommendation must include a `data_citation` field referencing a specific finding from audit or research. If it can't cite one, it shouldn't make the recommendation.

**Prompt strategy:** Two-pass approach. Pass 1: generate all recommendations unconstrained. Pass 2: re-rank and validate citations. This produces better output than a single forced-format prompt.

---

## Key Technical Decisions

**Playwright over requests:** Groupon pages are heavily client-rendered. Playwright with `playwright-stealth` handles JS rendering and bot detection. Requests+BS4 is used for simpler research targets (Yelp, competitor sites).

**DuckDB over SQLite:** Better JSON support (`json_extract`, `unnest`), faster analytical queries across 20 deals, Arrow/Pandas integration for any ad-hoc analysis. The `.duckdb` file is a single file like SQLite so there's no server to run.

**Repositories over raw SQL in orchestrator:** The orchestrator shouldn't know anything about table structure. Repos handle upsert logic and return typed objects. This makes it easy to swap DuckDB for SQLite if needed.

**Prompt files as `.txt`, not f-strings:** Prompts are stored in `config/prompts/` so they can be edited, versioned, and A/B tested without touching Python code. Variable substitution uses `.format()` or a simple template class.

**Checkpointing via `pipeline_runs`:** Every stage logs its status. On re-run, the orchestrator queries `pipeline_runs` and skips any stage with `status = 'success'`. This means you can re-run the pipeline after a partial failure without re-scraping or re-paying for AI calls.

**Concurrency model:** `asyncio` with a `Semaphore(5)` for deal-level parallelism. Within each deal, stages are sequential (audit must finish before research, research before proposal). Scraping uses Playwright async API. AI calls use the async Anthropic SDK.

**Cost tracking:** Every AI call logs `prompt_tokens` and `completion_tokens` to `audit_scores` / `research_synthesis` / `optimization_proposals`. After a run, you can query total spend: `SELECT SUM(prompt_tokens + completion_tokens) FROM audit_scores`.
