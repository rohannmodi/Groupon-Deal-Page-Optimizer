"""
Source tracker — records every URL fetched during the research stage.

Used for two purposes:
  1. Citation: the AI research synthesizer lists sources in its output
  2. Auditability: all fetched URLs are stored in the research_sources table

Usage:
    tracker = SourceTracker(deal_id)
    tracker.add(source_type="yelp", url=..., title=..., relevance="review")
    # ... do research ...
    sources = tracker.get_all()   # → list[ResearchSource]
"""

from __future__ import annotations

from datetime import datetime
from models import ResearchSource


class SourceTracker:
    """Collects research sources for a single deal."""

    def __init__(self, deal_id: str):
        self.deal_id = deal_id
        self._sources: list[ResearchSource] = []

    def add(
        self,
        *,
        source_type: str,
        url: str,
        title: str = "",
        relevance: str = "",
    ) -> None:
        """Record a source. Duplicate URLs are deduplicated automatically."""
        # Dedup by URL
        if any(s.source_url == url for s in self._sources):
            return
        self._sources.append(
            ResearchSource(
                source_type=source_type,
                source_url=url,
                source_title=title,
                relevance=relevance,
                fetched_at=datetime.utcnow(),
            )
        )

    def add_search_results(
        self,
        results,
        relevance: str = "web_search",
    ) -> None:
        """Bulk-add SearchResult objects from a web search."""
        for r in results:
            self.add(
                source_type="web_search",
                url=r.url,
                title=r.title,
                relevance=relevance,
            )

    def get_all(self) -> list[ResearchSource]:
        return list(self._sources)

    def __len__(self) -> int:
        return len(self._sources)
