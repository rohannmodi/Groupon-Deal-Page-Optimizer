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


# ---------------------------------------------------------------------------
# AI output models — Stage 1 (audit scoring)
# ---------------------------------------------------------------------------

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
    deal_quality: str                   # strong | average | weak
    review_themes: list[ReviewTheme]    # coded themes from review corpus


# ---------------------------------------------------------------------------
# AI output models — Stage 3 (optimization proposal)
# ---------------------------------------------------------------------------

class ProposalRecommendation(BaseModel):
    """
    A single actionable change to the deal page, grounded in audit + research data.
    Recommendations are ranked by expected conversion impact (1 = highest).
    """
    priority_rank: int
    # title|pricing|highlights|content|images|seo|positioning|trust|urgency
    category: str
    recommendation: str     # what to do — specific, imperative
    current_state: str      # what the page says / does today
    proposed_state: str     # the concrete replacement copy or change
    data_citation: str      # the specific data point that motivates this change
    # high|medium|low — impact on conversion rate / deal quality perception
    expected_impact: str
    impact_rationale: str   # why this matters for buyer confidence / SEO / urgency


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
