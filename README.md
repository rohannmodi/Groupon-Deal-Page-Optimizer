# Groupon Deal Page Optimizer

A Python pipeline that takes Groupon deal URLs and produces three outputs per deal:

1. **Deal Page Audit** — structured extraction of every element on the page
2. **Competitive & Market Research** — competitor pricing, review themes, value assessment *(Stage 2)*
3. **Optimization Proposal** — specific, data-backed recommendations ranked by expected impact *(Stage 3)*

## Quick Start

```bash
# 1. Clone and enter the project
cd groupon-optimizer

# 2. Create a virtual environment
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Install Playwright browsers (Chromium only)
playwright install chromium

# 5. Set up environment variables
cp .env.example .env
# Edit .env and add your ANTHROPIC_API_KEY (needed for Stages 2 & 3)

# 6. Run the pipeline
python run.py                          # processes deals.txt
python run.py --url https://groupon.com/deals/...   # single URL
python run.py --concurrency 3          # adjust parallelism
python run.py --force                  # re-run all stages
```

## Output Structure

```
outputs/
└── {deal_id}/
    ├── 1_audit/
    │   ├── audit.json          ← full structured extraction
    │   └── audit_summary.md    ← human-readable scorecard
    ├── 2_research/             ← Stage 2 (coming soon)
    │   ├── research.json
    │   └── research_report.md
    └── 3_proposal/             ← Stage 3 (coming soon)
        ├── proposal.json
        └── optimization_proposal.md
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
│       ├── deal_parser.py  ← title, merchant, highlights, fine print, FAQs
│       ├── pricing_parser.py
│       ├── seo_parser.py
│       ├── trust_parser.py ← trust signals, urgency elements, reviews
│       └── image_parser.py
├── storage/
│   ├── database.py         ← DuckDB singleton
│   ├── schema.sql          ← all table definitions
│   └── repositories/
│       └── deal_repo.py    ← upsert / query helpers
├── reporter/
│   ├── json_reporter.py
│   └── markdown_reporter.py
├── ai/                     ← Stage 2 & 3 (coming soon)
├── researcher/             ← Stage 2 (coming soon)
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
