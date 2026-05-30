"""
Shared Pydantic data models for the Groupon Deal Optimizer pipeline.

All stages (scraper → storage → AI → reporter) communicate through these
typed models. Parsers never raise on missing data — fields default to None
or empty lists so the pipeline degrades gracefully.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Input
# ---------------------------------------------------------------------------

class DealInput(BaseModel):
    """A single URL to process, with a stable derived ID."""
    deal_id: str           # e.g. "great-clips-chicago-a3f2"
    url: str
    added_at: datetime = Field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# Scraper sub-models
# ---------------------------------------------------------------------------

class PricingOption(BaseModel):
    option_name: str = "Standard"
    original_price: Optional[float] = None
    deal_price: Optional[float] = None
    discount_pct: Optional[float] = None
    savings_amount: Optional[float] = None
    is_primary: bool = False

    @field_validator("discount_pct", "savings_amount", "original_price", "deal_price", mode="before")
    @classmethod
    def coerce_none_string(cls, v: Any) -> Any:
        """Turn empty strings or 'N/A' into None."""
        if isinstance(v, str) and v.strip() in ("", "N/A", "n/a", "-"):
            return None
        return v


class DealImage(BaseModel):
    src_url: str
    alt_text: Optional[str] = None
    estimated_width: Optional[int] = None
    # product | lifestyle | stock | logo | unknown — classified later by AI
    image_type: str = "unknown"
    position: int = 0


class TrustSignal(BaseModel):
    # rating | review_count | sold_count | guarantee | badge
    signal_type: str
    signal_value: str
    # high | medium | low — filled in by AI audit analyzer
    impact_score: Optional[str] = None


class UrgencyElement(BaseModel):
    # countdown | limited_qty | selling_fast | ends_text
    element_type: str
    element_text: str
    is_visible_atf: bool = False   # above the fold


class FAQ(BaseModel):
    question: str
    answer: str


class ReviewSample(BaseModel):
    author: Optional[str] = None
    rating: Optional[float] = None
    text: str
    date: Optional[str] = None


class SEOElements(BaseModel):
    meta_title: Optional[str] = None
    meta_description: Optional[str] = None
    h1_text: Optional[str] = None
    h2_texts: list[str] = Field(default_factory=list)
    has_schema_markup: bool = False
    schema_type: Optional[str] = None      # "Product", "LocalBusiness", etc.
    canonical_url: Optional[str] = None
    og_title: Optional[str] = None
    og_description: Optional[str] = None
    og_image_url: Optional[str] = None
    image_count: int = 0
    script_count: int = 0


# ---------------------------------------------------------------------------
# Main audit model — output of the scraper stage
# ---------------------------------------------------------------------------

class DealAudit(BaseModel):
    """
    Complete structured representation of a Groupon deal page.
    Produced by the scraper + parsers. Stored in DuckDB. Used as
    input context for the AI audit analyzer.
    """
    deal_id: str
    url: str

    # Core identity
    title: Optional[str] = None
    subtitle: Optional[str] = None
    merchant_name: Optional[str] = None
    category: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    # Full street address if available (e.g. "38 West 32nd Street, New York")
    # Parsed from "Where To Redeem" section for local service deals.
    # None for nationwide/shipped-goods deals.
    redemption_address: Optional[str] = None
    # Human-readable location for display (e.g. "Brookfield, WI" or "Online").
    # Used when city/state aren't parsed from the page — populated by the audit
    # AI as a fallback (e.g. "Online" for virtual/shipped deals). None only when
    # the location genuinely can't be determined from the page content.
    location_label: Optional[str] = None
    description: Optional[str] = None

    # Pricing
    pricing_options: list[PricingOption] = Field(default_factory=list)

    # Content
    highlights: list[str] = Field(default_factory=list)
    fine_print: list[str] = Field(default_factory=list)
    faqs: list[FAQ] = Field(default_factory=list)

    # Media
    images: list[DealImage] = Field(default_factory=list)

    # Social proof
    reviews_count: Optional[int] = None
    reviews_avg_rating: Optional[float] = None
    review_samples: list[ReviewSample] = Field(default_factory=list)

    # SEO
    seo: SEOElements = Field(default_factory=SEOElements)

    # Trust & urgency
    trust_signals: list[TrustSignal] = Field(default_factory=list)
    urgency_elements: list[UrgencyElement] = Field(default_factory=list)

    # Content freshness warnings — stale/outdated text found on the live page
    # Each entry: {"text": "<exact text>", "location": "<highlights|fine_print|description>"}
    stale_content_warnings: list[dict] = Field(default_factory=list)

    # Metadata
    scraped_at: datetime = Field(default_factory=datetime.utcnow)
    scrape_duration_seconds: Optional[float] = None
    raw_html_path: Optional[str] = None
    scrape_error: Optional[str] = None   # populated if scrape partially failed

    @property
    def primary_pricing(self) -> Optional[PricingOption]:
        """Return the primary (featured) pricing option if present."""
        for opt in self.pricing_options:
            if opt.is_primary:
                return opt
        return self.pricing_options[0] if self.pricing_options else None

    @property
    def has_reviews(self) -> bool:
        return bool(self.reviews_count and self.reviews_count > 0)

    @property
    def image_count(self) -> int:
        return len(self.images)


# ---------------------------------------------------------------------------
# Research stage models
# ---------------------------------------------------------------------------

class SearchResult(BaseModel):
    """Single result from a web search query."""
    title: str
    url: str
    snippet: str = ""
    position: int = 0


class CompetitorPrice(BaseModel):
    """A single competitor's pricing data point."""
    competitor_name: str
    service_name: str
    regular_price: Optional[float] = None
    sale_price: Optional[float] = None
    source_url: str
    notes: Optional[str] = None
    # Confidence that this is actually the same service/product (0.0–1.0).
    # Only trust price comparisons where sku_match_confidence >= 0.7.
    sku_match_confidence: float = 0.5
    # True if this is confirmed as a direct merchant competitor (not an aggregator,
    # informational site, or government resource).
    is_merchant: bool = False
    # match_type: how closely this matches the deal being analyzed
    # exact = same product type, same duration/quantity
    # close = same category, different duration or minor variation
    # tangential = same category, different format (e.g. single bottle vs. 3-day cleanse)
    # aggregator = marketplace/aggregator site (Instacart, Amazon, DoorDash, etc.)
    match_type: str = "close"   # exact | close | tangential | aggregator


class YelpReview(BaseModel):
    author: Optional[str] = None
    rating: Optional[float] = None
    text: str
    date: Optional[str] = None


class YelpData(BaseModel):
    """Extracted data from the merchant's Yelp listing."""
    name: Optional[str] = None
    url: str
    rating: Optional[float] = None
    review_count: Optional[int] = None
    categories: list[str] = Field(default_factory=list)
    reviews: list[YelpReview] = Field(default_factory=list)
    # Raw review texts for AI theme analysis
    review_texts: list[str] = Field(default_factory=list)


class GoogleData(BaseModel):
    """Extracted data from Google Business / SERP knowledge panel."""
    name: Optional[str] = None
    rating: Optional[float] = None
    review_count: Optional[int] = None
    address: Optional[str] = None
    source_url: Optional[str] = None


class CategoryContext(BaseModel):
    """Market context for the deal's category."""
    category: str
    city: str
    typical_price_low: Optional[float] = None
    typical_price_high: Optional[float] = None
    typical_discount_pct: Optional[float] = None
    notes: str = ""
    search_results: list[SearchResult] = Field(default_factory=list)


class ResearchSource(BaseModel):
    """A single cited source from the research stage."""
    source_type: str        # web_search | yelp | google | direct_scrape
    source_url: str
    source_title: str = ""
    relevance: str = ""     # competitor_price | review | category_context
    fetched_at: datetime = Field(default_factory=datetime.utcnow)


class ResearchQuality(BaseModel):
    """
    Data quality / failure reporting for the research stage (Priority 10).
    Tells downstream AI exactly what data was and wasn't found, preventing
    it from inventing conclusions when evidence is thin.
    """
    competitor_pricing_status: str = "no_data"
    # "verified"     — ≥1 high-confidence (≥0.7) competitor price found
    # "low_confidence" — prices found but all confidence < 0.7
    # "partial_data" — some competitors found, prices missing
    # "no_data"      — no valid competitor sources found

    merchant_reputation_status: str = "no_data"
    # "verified"     — Yelp or Google data found
    # "partial_data" — one platform found, not both
    # "no_data"      — no reputation data

    category_context_status: str = "no_data"
    # "validated"    — price range from validated sources
    # "insufficient" — fewer than 2 accepted sources
    # "no_data"      — all sources rejected

    pricing_verifiable: bool = False
    # True if the deal page's own original prices were captured by the scraper
    # (i.e., strikethrough pricing is present and parsed)

    overall_confidence: float = 0.0
    # 0.0–1.0: composite data quality score
    # < 0.4 → report should be clearly marked as low-confidence

    sources_found: int = 0
    sources_accepted: int = 0
    sources_rejected: int = 0


class ResearchData(BaseModel):
    """
    All raw research gathered for one deal.
    Handed to the AI research synthesizer as its input context.
    """
    deal_id: str
    yelp: Optional[YelpData] = None
    google: Optional[GoogleData] = None
    competitors: list[CompetitorPrice] = Field(default_factory=list)
    category_context: Optional[CategoryContext] = None
    sources: list[ResearchSource] = Field(default_factory=list)
    quality: ResearchQuality = Field(default_factory=ResearchQuality)


# ---------------------------------------------------------------------------
# AI output models — Stage 1 (audit scoring)
# ---------------------------------------------------------------------------

class ExecutiveMetrics(BaseModel):
    """
    Portfolio-level executive dashboard metrics (Priority 8).
    All scores 0–100 for easy cross-deal comparison.
    """
    deal_strength_score: Optional[int] = None       # overall deal quality 0–100
    competitive_price_rank: Optional[int] = None     # 1=best price in category, 5=worst
    review_sentiment_score: Optional[int] = None     # 0=all negative, 100=all positive
    trust_score_pct: Optional[int] = None            # trust signals strength 0–100
    content_completeness_pct: Optional[int] = None   # how complete the deal page is 0–100
    optimization_opportunity_pct: Optional[int] = None  # how much room for improvement 0–100


class AuditScores(BaseModel):
    """
    Structured output from the AI audit analyzer.
    Scores are 1–10 on each dimension; content_gaps are specific, actionable.
    """
    clarity_score: int          # Is the deal clearly explained?
    trust_score: int            # Are there sufficient trust signals?
    urgency_score: int          # Is urgency communicated effectively?
    seo_score: int              # Is the page SEO-optimised?
    value_comm_score: int       # Does the page communicate *why* it's a good deal?
    completeness_score: int     # Is all relevant info present?
    overall_score: float        # Weighted average (computed by AI)
    content_gaps: list[str]     # Specific things customers would want that aren't here
    ai_flags: list[str]         # Issues: misleading claims, unclear terms, etc.
    score_reasoning: dict[str, str] = Field(default_factory=dict)  # {dimension: "why this score"}


# ---------------------------------------------------------------------------
# AI output models — Stage 2 (research synthesis)
# ---------------------------------------------------------------------------

class ReviewTheme(BaseModel):
    """A recurring theme extracted from review text by the AI."""
    theme: str                  # e.g., "relaxing atmosphere"
    sentiment: str              # positive | negative | neutral
    mention_count: int          # how many reviews mention this theme
    sample_quote: str           # verbatim quote from a review
    platform: str               # yelp | google | groupon


class ResearchSynthesis(BaseModel):
    """
    Structured output from the AI research synthesizer.
    Every field must be grounded in a specific data point from ResearchData.
    """
    value_assessment: str               # genuine_deal | marginal | overpriced
    value_reasoning: str                # specific reasoning with dollar amounts
    groupon_vs_direct_savings: Optional[float] = None   # $ saved vs booking direct
    groupon_vs_competitor_savings: Optional[float] = None  # $ saved vs competitor
    merchant_differentiators: list[str] # what makes this merchant stand out
    red_flags: list[str]                # specific concerns from data
    deal_quality: str                   # strong | good | average | weak (label)
    deal_quality_score: float = 5.0     # 1–10 numeric: price × reputation × demand
    key_insight: str = ""               # one sentence: core tension or opportunity
    overall_verdict: str = ""           # 3–4 sentence structured verdict (lead with finding)
    review_themes: list[ReviewTheme]    # coded themes from review corpus


# ---------------------------------------------------------------------------
# AI output models — Stage 3 (optimization proposal)
# ---------------------------------------------------------------------------

class ImageRecommendation(BaseModel):
    """A specific image recommendation for the deal page."""
    image_type: str         # e.g. "Photo of all 7 juice bottles together"
    conversion_reason: str  # why this image improves purchase confidence
    priority: str           # High | Medium | Low


class ProposalRecommendation(BaseModel):
    """
    A single actionable change to the deal page, grounded in audit + research data.
    Recommendations are ranked by impact_score = visibility × user_importance × evidence_strength.
    """
    priority_rank: int
    # title|pricing|highlights|content|images|seo|positioning|trust|urgency
    category: str
    # short_title: ≤10 words, used in tables and section headers (no ellipsis)
    # recommendation: full description, used in body text (no length limit)
    short_title: str = ""           # max 10 words, complete fragment, no ellipsis
    recommendation: str             # what to do — specific, imperative (full length)
    current_state: str              # what the page says / does today
    proposed_state: str             # the concrete replacement copy or change
    data_citation: str              # the specific data point that motivates this change
    expected_impact: str            # high|medium|low
    impact_rationale: str           # why this matters for conversion
    # Priority 5: evidence list (review quotes, competitor prices, audit scores)
    supporting_evidence: list[str] = Field(default_factory=list)
    # Priority 6: visibility × user_importance × evidence_strength (1–1000)
    impact_score: int = 0


class OptimizationProposal(BaseModel):
    """
    Complete optimization package for one deal page.
    Produced by the proposal AI and stored in DuckDB.
    """
    deal_id: str

    # Rewritten copy
    proposed_title: str
    proposed_meta_title: str        # ≤60 chars, keyword-optimised
    proposed_meta_description: str  # ≤155 chars, includes value prop
    proposed_h1: str                # may differ from title for SEO
    proposed_highlights: list[str]  # full rewritten highlights list
    pricing_framing: str            # suggested new framing for price/value section

    # CEO-level summary
    executive_summary: str  # 2-3 sentences: what's wrong, what to fix, expected outcome

    # Specific image recommendations (Improvement 5)
    image_recommendations: list[ImageRecommendation] = Field(default_factory=list)

    # Priority-ranked change list
    recommendations: list[ProposalRecommendation]

    generated_at: datetime = Field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# Pipeline run record — mirrors the pipeline_runs DB table
# ---------------------------------------------------------------------------

class PipelineRun(BaseModel):
    deal_id: str
    stage: str   # scrape | parse | audit_ai | research | research_ai | proposal_ai | report
    status: str  # pending | running | success | failed | skipped
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error_message: Optional[str] = None
    retry_count: int = 0
