# Groupon Deal Page Optimizer

A Python pipeline that takes Groupon deal URLs and produces three outputs per deal:

1. **Deal Page Audit** — structured extraction of every element on the page
2. **Competitive & Market Research** — competitor pricing, review themes, value assessment *(Stage 2)*
3. **Optimization Proposal** — specific, data-backed recommendations ranked by expected impact *(Stage 3)*

## Quick Start

```bash
# 1. Enter the project directory
cd "Groupon Deal Page Optimizer"

# 2. Activate the virtual environment (already created)
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 3. Confirm environment variables are set
cat .env   # should have ANTHROPIC_API_KEY and SERPAPI_KEY

# 4. Run the full 20-deal batch (recommended: concurrency 2-3 to avoid rate limits)
python run.py --concurrency 3

# Other useful commands:
python run.py --url https://groupon.com/deals/...    # single URL for testing
python run.py --force                                # re-run all stages (ignores checkpoints)
python run.py --stage scrape                         # scrape only — no AI, no research
python run.py --stage audit_ai                       # scrape + AI score (no research)
python run.py --stage research_ai                    # everything except final proposal
```

### If starting fresh

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
cp .env.example .env
# Edit .env: add ANTHROPIC_API_KEY and SERPAPI_KEY
python run.py --concurrency 3
```

## Output Structure

```
outputs/
└── {deal_id}/
    ├── 1_audit/
    │   ├── audit.json               ← full structured extraction
    │   ├── audit_scores.json        ← AI dimension scores (1–10 each)
    │   └── audit_summary.md         ← human-readable scorecard
    ├── 2_research/
    │   ├── research.json            ← raw competitor + Yelp/Google + synthesis
    │   └── research_report.md       ← sourced research report with deal verdict
    └── 3_proposal/
        ├── proposal.json            ← structured recommendations + proposed copy
        └── optimization_proposal.md ← CEO-ready ranked recommendations
```

## Adding More Deals

Edit `deals.txt` — one URL per line, `#` for comments:

```
https://www.groupon.com/deals/...
https://www.groupon.com/deals/...
```

Then run `python run.py` again. Already-processed deals are skipped (checkpointing).

## Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full design: modules, data flow, DuckDB schema, and AI integration strategy.

### Key design decisions

- **Playwright + stealth** for Groupon's JS-rendered pages
- **DuckDB** for structured storage (JSON columns, zero server setup)
- **Checkpointing** via `pipeline_runs` table — re-runs skip completed stages
- **Asyncio semaphore** for configurable deal-level parallelism
- **Parser isolation** — each parser fails independently; partial data is still stored

## Project Structure

```
groupon-optimizer/
├── run.py                  ← entrypoint
├── deals.txt               ← input URLs
├── models.py               ← shared Pydantic data models
├── config/
│   └── settings.py         ← Pydantic Settings (env vars + paths)
├── pipeline/
│   ├── orchestrator.py     ← asyncio coordinator
│   ├── ingestion.py        ← URL → DealInput + stable deal_id
│   └── checkpoint.py       ← stage skip/retry logic
├── scraper/
│   ├── browser.py          ← Playwright manager (stealth, retry, scroll)
│   ├── groupon.py          ← orchestrates all parsers → DealAudit
│   └── parsers/
│       ├── deal_parser.py      ← title, merchant, highlights, fine print, FAQs
│       ├── jsonld_parser.py    ← ProductGroup JSON-LD (primary data source)
│       ├── next_data_parser.py ← __NEXT_DATA__ fallback (Next.js blob)
│       ├── pricing_parser.py   ← fallback HTML pricing extraction
│       ├── seo_parser.py
│       ├── trust_parser.py     ← trust signals, urgency elements, reviews
│       └── image_parser.py
├── storage/
│   ├── database.py         ← DuckDB singleton
│   ├── schema.sql          ← all table definitions (16 tables)
│   └── repositories/
│       ├── deal_repo.py    ← scrape stage persistence
│       ├── research_repo.py← Stage 2 persistence (competitors, reviews, synthesis)
│       └── proposal_repo.py← Stage 3 persistence (proposals, recommendations)
├── reporter/
│   ├── json_reporter.py         ← writes .json for all 3 stages
│   ├── markdown_reporter.py     ← audit_summary.md
│   ├── research_markdown.py     ← research_report.md with sources
│   └── proposal_markdown.py     ← optimization_proposal.md (CEO-ready)
├── ai/
│   ├── client.py           ← Anthropic SDK wrapper (sync + async, retries)
│   ├── audit_analyzer.py   ← Stage 1 AI: score page on 6 dimensions
│   ├── research_synthesizer.py ← Stage 2 AI: verdict + review theme coding
│   ├── proposal_generator.py   ← Stage 3 AI: ranked recommendations (extended thinking)
│   └── schemas/            ← tool schemas forcing structured JSON output
├── researcher/
│   ├── runner.py           ← parallel research coordinator (asyncio.gather)
│   ├── web_search.py       ← SerpAPI primary, Google SERP fallback
│   ├── yelp_scraper.py     ← 3-strategy Yelp data extractor
│   ├── google_reviews.py   ← knowledge panel extractor
│   ├── competitor_finder.py← searches + scrapes 3–5 competitors
│   ├── category_benchmarker.py ← typical price range for 25+ categories
│   └── source_tracker.py   ← citation recorder for all fetched URLs
└── data/
    └── groupon_optimizer.duckdb
```

## Requirements

- Python 3.11+
- Playwright Chromium (installed via `playwright install chromium`)
- For AI stages: `ANTHROPIC_API_KEY` in `.env`
- For research stage: `SERPAPI_KEY` in `.env`

## Querying the Database

```python
import duckdb
conn = duckdb.connect("data/groupon_optimizer.duckdb")

# All deals with their primary price
conn.execute("""
    SELECT d.title, d.merchant_name, d.city,
           p.deal_price, p.discount_pct
    FROM deals d
    LEFT JOIN pricing_options p ON d.deal_id = p.deal_id AND p.is_primary
    ORDER BY p.discount_pct DESC
""").df()

# Deals missing trust signals
conn.execute("""
    SELECT d.deal_id, d.title
    FROM deals d
    WHERE d.deal_id NOT IN (SELECT DISTINCT deal_id FROM trust_signals)
""").df()
```
