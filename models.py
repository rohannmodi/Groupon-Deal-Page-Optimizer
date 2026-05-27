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
