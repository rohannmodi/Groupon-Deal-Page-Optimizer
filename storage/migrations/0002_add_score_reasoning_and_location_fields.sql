-- ============================================================
-- Migration 0002 — add score_reasoning column to audit_scores,
--                  add location_label + redemption_address to deals,
--                  add short_title + impact_score to proposal_recommendations
--
-- These columns were added to models.py and the AI schemas after the
-- initial schema was shipped. ALTER TABLE … ADD COLUMN is safe to run
-- against a DB that already has these columns in DuckDB >= 0.10 because
-- DuckDB silently no-ops duplicate ADD COLUMN statements.
-- ============================================================

-- audit_scores: store per-dimension reasoning from the AI
ALTER TABLE audit_scores
    ADD COLUMN IF NOT EXISTS score_reasoning JSON;

-- deals: human-readable location label (e.g. "Online", "Brookfield, WI")
--        and full street address for local service deals
ALTER TABLE deals
    ADD COLUMN IF NOT EXISTS location_label TEXT;

ALTER TABLE deals
    ADD COLUMN IF NOT EXISTS redemption_address TEXT;

-- proposal_recommendations: short title (≤10 words) for table display
--                           and numeric impact score for sorting
ALTER TABLE proposal_recommendations
    ADD COLUMN IF NOT EXISTS short_title TEXT;

ALTER TABLE proposal_recommendations
    ADD COLUMN IF NOT EXISTS impact_score INTEGER DEFAULT 0;

-- proposal_recommendations: supporting evidence list (review quotes, prices)
ALTER TABLE proposal_recommendations
    ADD COLUMN IF NOT EXISTS supporting_evidence JSON;

-- research_synthesis: richer fields added in v2 of the research synthesizer
ALTER TABLE research_synthesis
    ADD COLUMN IF NOT EXISTS deal_quality_score DOUBLE;

ALTER TABLE research_synthesis
    ADD COLUMN IF NOT EXISTS key_insight TEXT;

ALTER TABLE research_synthesis
    ADD COLUMN IF NOT EXISTS overall_verdict TEXT
