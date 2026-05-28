-- ============================================================
-- Groupon Deal Optimizer — DuckDB Schema
-- Applied once on first run via database.py
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
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    deal_id         TEXT NOT NULL REFERENCES deals(deal_id),
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
    deal_id         TEXT PRIMARY KEY REFERENCES deals(deal_id),
    highlights      JSON,   -- list[str]
    fine_print      JSON,   -- list[str]
    faqs            JSON    -- list[{question, answer}]
);

-- -------------------------
-- Images
-- -------------------------
CREATE TABLE IF NOT EXISTS deal_images (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    deal_id         TEXT NOT NULL REFERENCES deals(deal_id),
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
    deal_id             TEXT PRIMARY KEY REFERENCES deals(deal_id),
    meta_title          TEXT,
    meta_description    TEXT,
    h1_text             TEXT,
    h2_texts            JSON,   -- list[str]
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
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    deal_id         TEXT NOT NULL REFERENCES deals(deal_id),
    signal_type     TEXT,   -- rating|review_count|sold_count|guarantee|badge
    signal_value    TEXT,
    impact_score    TEXT    -- high|medium|low (set by AI)
);

CREATE TABLE IF NOT EXISTS urgency_elements (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    deal_id         TEXT NOT NULL REFERENCES deals(deal_id),
    element_type    TEXT,   -- countdown|limited_qty|selling_fast|ends_text
    element_text    TEXT,
    is_visible_atf  BOOLEAN DEFAULT FALSE
);

-- -------------------------
-- Review samples
-- -------------------------
CREATE TABLE IF NOT EXISTS review_samples (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    deal_id     TEXT NOT NULL REFERENCES deals(deal_id),
    author      TEXT,
    rating      DOUBLE,
    review_text TEXT,
    review_date TEXT
);

-- -------------------------
-- Research (Stage 2 — written later)
-- -------------------------
CREATE TABLE IF NOT EXISTS competitor_prices (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    deal_id             TEXT NOT NULL REFERENCES deals(deal_id),
    competitor_name     TEXT,
    service_name        TEXT,
    regular_price       DOUBLE,
    sale_price          DOUBLE,
    source_url          TEXT,
    scraped_at          TIMESTAMP,
    notes               TEXT
);

CREATE TABLE IF NOT EXISTS merchant_reviews (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    deal_id         TEXT NOT NULL REFERENCES deals(deal_id),
    platform        TEXT,   -- yelp|google|tripadvisor
    overall_rating  DOUBLE,
    review_count    INTEGER,
    scraped_at      TIMESTAMP
);

CREATE TABLE IF NOT EXISTS review_themes (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    deal_id         TEXT NOT NULL REFERENCES deals(deal_id),
    platform        TEXT,
    theme           TEXT,
    sentiment       TEXT,   -- positive|negative|neutral
    mention_count   INTEGER,
    sample_quote    TEXT
);

CREATE TABLE IF NOT EXISTS research_sources (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    deal_id         TEXT NOT NULL REFERENCES deals(deal_id),
    source_type     TEXT,   -- web_search|yelp|google|direct_scrape
    source_url      TEXT,
    source_title    TEXT,
    fetched_at      TIMESTAMP,
    relevance       TEXT    -- competitor_price|review|category_context
);

CREATE TABLE IF NOT EXISTS research_synthesis (
    deal_id                         TEXT PRIMARY KEY REFERENCES deals(deal_id),
    value_assessment                TEXT,   -- genuine_deal|marginal|overpriced
    groupon_vs_direct_savings       DOUBLE,
    groupon_vs_competitor_savings   DOUBLE,
    merchant_differentiators        JSON,   -- list[str]
    red_flags                       JSON,   -- list[str]
    deal_quality                    TEXT,   -- strong|average|weak
    synthesized_at                  TIMESTAMP
);

-- -------------------------
-- AI outputs (Stage 1 AI)
-- -------------------------
CREATE TABLE IF NOT EXISTS audit_scores (
    deal_id                 TEXT PRIMARY KEY REFERENCES deals(deal_id),
    clarity_score           INTEGER,
    trust_score             INTEGER,
    urgency_score           INTEGER,
    seo_score               INTEGER,
    value_comm_score        INTEGER,
    completeness_score      INTEGER,
    overall_score           DOUBLE,
    content_gaps            JSON,   -- list[str]
    ai_flags                JSON,   -- list[str]
    analyzed_at             TIMESTAMP,
    prompt_tokens           INTEGER,
    completion_tokens       INTEGER
);

-- -------------------------
-- AI outputs (Stage 3)
-- -------------------------
CREATE TABLE IF NOT EXISTS optimization_proposals (
    deal_id                 TEXT PRIMARY KEY REFERENCES deals(deal_id),
    proposed_title          TEXT,
    proposed_meta_title     TEXT,
    proposed_meta_description TEXT,
    proposed_highlights     JSON,   -- list[str]
    title_reasoning         TEXT,
    generated_at            TIMESTAMP
);

CREATE TABLE IF NOT EXISTS proposal_recommendations (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    deal_id         TEXT NOT NULL REFERENCES deals(deal_id),
    priority_rank   INTEGER,
    category        TEXT,   -- title|pricing|highlights|content|images|seo|positioning
    recommendation  TEXT,
    current_state   TEXT,
    proposed_state  TEXT,
    data_citation   TEXT,
    expected_impact TEXT,   -- high|medium|low
    impact_rationale TEXT
);

-- -------------------------
-- Pipeline orchestration
-- -------------------------
CREATE TABLE IF NOT EXISTS pipeline_runs (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    deal_id         TEXT NOT NULL REFERENCES deals(deal_id),
    stage           TEXT NOT NULL,  -- scrape|parse|audit_ai|research|research_ai|proposal_ai|report
    status          TEXT NOT NULL,  -- pending|running|success|failed|skipped
    started_at      TIMESTAMP,
    completed_at    TIMESTAMP,
    error_message   TEXT,
    retry_count     INTEGER DEFAULT 0
);

-- Index for fast checkpoint lookups
CREATE INDEX IF NOT EXISTS idx_pipeline_runs_deal_stage
    ON pipeline_runs(deal_id, stage);
