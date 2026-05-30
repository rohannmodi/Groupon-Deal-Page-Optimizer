-- ============================================================
-- Groupon Deal Optimizer — DuckDB Schema
-- Applied once on first run via database.py
-- ============================================================
-- Design notes:
--   • No FOREIGN KEY constraints — DuckDB support is version-dependent.
--     Referential integrity is enforced at the application layer.
--   • 1:many tables have no surrogate PK — we never query by row id.
--     DuckDB provides an implicit rowid for ORDER BY when needed.
-- ============================================================

-- -------------------------
-- Core deal identity
-- -------------------------
CREATE TABLE IF NOT EXISTS deals (
    deal_id             TEXT PRIMARY KEY,
    url                 TEXT NOT NULL,
    title               TEXT,
    subtitle            TEXT,
    merchant_name       TEXT,
    category            TEXT,
    city                TEXT,
    state               TEXT,
    description         TEXT,
    reviews_count       INTEGER,
    reviews_avg_rating  DOUBLE,
    scraped_at          TIMESTAMP,
    scrape_duration_s   DOUBLE,
    raw_html_path       TEXT,
    scrape_error        TEXT
);

-- -------------------------
-- Pricing tiers (1-to-many)
-- -------------------------
CREATE TABLE IF NOT EXISTS pricing_options (
    deal_id         TEXT NOT NULL,
    option_name     TEXT,
    original_price  DOUBLE,
    deal_price      DOUBLE,
    discount_pct    DOUBLE,
    savings_amount  DOUBLE,
    is_primary      BOOLEAN DEFAULT FALSE
);

-- -------------------------
-- Content blocks
-- -------------------------
CREATE TABLE IF NOT EXISTS deal_content (
    deal_id         TEXT PRIMARY KEY,
    highlights      JSON,
    fine_print      JSON,
    faqs            JSON
);

-- -------------------------
-- Images
-- -------------------------
CREATE TABLE IF NOT EXISTS deal_images (
    deal_id         TEXT NOT NULL,
    src_url         TEXT,
    alt_text        TEXT,
    estimated_width INTEGER,
    image_type      TEXT DEFAULT 'unknown',
    position        INTEGER DEFAULT 0
);

-- -------------------------
-- SEO
-- -------------------------
CREATE TABLE IF NOT EXISTS seo_elements (
    deal_id             TEXT PRIMARY KEY,
    meta_title          TEXT,
    meta_description    TEXT,
    h1_text             TEXT,
    h2_texts            JSON,
    has_schema_markup   BOOLEAN DEFAULT FALSE,
    schema_type         TEXT,
    canonical_url       TEXT,
    og_title            TEXT,
    og_description      TEXT,
    og_image_url        TEXT,
    image_count         INTEGER DEFAULT 0,
    script_count        INTEGER DEFAULT 0
);

-- -------------------------
-- Trust & urgency signals
-- -------------------------
CREATE TABLE IF NOT EXISTS trust_signals (
    deal_id         TEXT NOT NULL,
    signal_type     TEXT,
    signal_value    TEXT,
    impact_score    TEXT
);

CREATE TABLE IF NOT EXISTS urgency_elements (
    deal_id         TEXT NOT NULL,
    element_type    TEXT,
    element_text    TEXT,
    is_visible_atf  BOOLEAN DEFAULT FALSE
);

-- -------------------------
-- Review samples
-- -------------------------
CREATE TABLE IF NOT EXISTS review_samples (
    deal_id         TEXT NOT NULL,
    author          TEXT,
    rating          DOUBLE,
    review_text     TEXT,
    review_date     TEXT
);

-- -------------------------
-- Research (Stage 2)
-- -------------------------
CREATE TABLE IF NOT EXISTS competitor_prices (
    deal_id                 TEXT NOT NULL,
    competitor_name         TEXT,
    service_name            TEXT,
    regular_price           DOUBLE,
    sale_price              DOUBLE,
    source_url              TEXT,
    scraped_at              TIMESTAMP,
    notes                   TEXT,
    sku_match_confidence    DOUBLE DEFAULT 0.5,
    is_merchant             BOOLEAN DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS merchant_reviews (
    deal_id         TEXT NOT NULL,
    platform        TEXT,
    overall_rating  DOUBLE,
    review_count    INTEGER,
    scraped_at      TIMESTAMP
);

CREATE TABLE IF NOT EXISTS review_themes (
    deal_id         TEXT NOT NULL,
    platform        TEXT,
    theme           TEXT,
    sentiment       TEXT,
    mention_count   INTEGER,
    sample_quote    TEXT
);

CREATE TABLE IF NOT EXISTS research_sources (
    deal_id         TEXT NOT NULL,
    source_type     TEXT,
    source_url      TEXT,
    source_title    TEXT,
    fetched_at      TIMESTAMP,
    relevance       TEXT
);

CREATE TABLE IF NOT EXISTS research_synthesis (
    deal_id                         TEXT PRIMARY KEY,
    value_assessment                TEXT,
    value_reasoning                 TEXT,
    groupon_vs_direct_savings       DOUBLE,
    groupon_vs_competitor_savings   DOUBLE,
    merchant_differentiators        JSON,
    red_flags                       JSON,
    deal_quality                    TEXT,
    synthesized_at                  TIMESTAMP
);

-- -------------------------
-- AI outputs (Stage 1)
-- -------------------------
CREATE TABLE IF NOT EXISTS audit_scores (
    deal_id                 TEXT PRIMARY KEY,
    clarity_score           INTEGER,
    trust_score             INTEGER,
    urgency_score           INTEGER,
    seo_score               INTEGER,
    value_comm_score        INTEGER,
    completeness_score      INTEGER,
    overall_score           DOUBLE,
    content_gaps            JSON,
    ai_flags                JSON,
    analyzed_at             TIMESTAMP,
    prompt_tokens           INTEGER,
    completion_tokens       INTEGER
);

-- -------------------------
-- AI outputs (Stage 3)
-- -------------------------
CREATE TABLE IF NOT EXISTS optimization_proposals (
    deal_id                   TEXT PRIMARY KEY,
    proposed_title            TEXT,
    proposed_meta_title       TEXT,
    proposed_meta_description TEXT,
    proposed_h1               TEXT,
    proposed_highlights       JSON,
    pricing_framing           TEXT,
    executive_summary         TEXT,
    generated_at              TIMESTAMP,
    prompt_tokens             INTEGER,
    completion_tokens         INTEGER
);

CREATE TABLE IF NOT EXISTS proposal_recommendations (
    deal_id         TEXT NOT NULL,
    priority_rank   INTEGER,
    category        TEXT,
    recommendation  TEXT,
    current_state   TEXT,
    proposed_state  TEXT,
    data_citation   TEXT,
    expected_impact TEXT,
    impact_rationale TEXT
);

-- -------------------------
-- Pipeline orchestration
-- -------------------------
CREATE TABLE IF NOT EXISTS pipeline_runs (
    deal_id         TEXT NOT NULL,
    stage           TEXT NOT NULL,
    status          TEXT NOT NULL,
    started_at      TIMESTAMP,
    completed_at    TIMESTAMP,
    error_message   TEXT,
    retry_count     INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_pipeline_runs_deal_stage
    ON pipeline_runs(deal_id, stage);
