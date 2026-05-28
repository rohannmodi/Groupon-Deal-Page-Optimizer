"""
Tool schema for the AI audit analyzer.

Defines the `score_deal_audit` tool that Claude must call when scoring a deal page.
Using tool_use forces structured output — Claude cannot respond with free text.
The schema is intentionally strict so that the Pydantic model can parse it directly.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Tool definition passed to the Anthropic API
# ---------------------------------------------------------------------------

AUDIT_TOOL_NAME = "score_deal_audit"

AUDIT_TOOL_SCHEMA: dict = {
    "description": (
        "Score a Groupon deal page across six quality dimensions and identify "
        "content gaps and issues. All scores are integers 1–10 where 10 is best. "
        "Every content gap must describe something ABSENT from the page that "
        "a customer would reasonably need before purchasing — do not flag things "
        "that are already present."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "clarity_score": {
                "type": "integer",
                "minimum": 1,
                "maximum": 10,
                "description": (
                    "How clearly does the page explain what the customer gets? "
                    "10 = crystal clear, no ambiguity. 1 = very confusing."
                ),
            },
            "trust_score": {
                "type": "integer",
                "minimum": 1,
                "maximum": 10,
                "description": (
                    "Strength of trust signals: ratings, review count, sold count, "
                    "guarantees, merchant credentials. 10 = very strong social proof."
                ),
            },
            "urgency_score": {
                "type": "integer",
                "minimum": 1,
                "maximum": 10,
                "description": (
                    "How well does the page create urgency to act? Countdowns, "
                    "limited quantity, selling fast. 10 = strong urgency signals."
                ),
            },
            "seo_score": {
                "type": "integer",
                "minimum": 1,
                "maximum": 10,
                "description": (
                    "Quality of SEO elements: meta title/description length and "
                    "keyword relevance, H1/H2 structure, schema markup present. "
                    "10 = fully optimised."
                ),
            },
            "value_comm_score": {
                "type": "integer",
                "minimum": 1,
                "maximum": 10,
                "description": (
                    "Does the page effectively communicate WHY this is a good deal? "
                    "Not just showing the price — framing the value, context, savings. "
                    "10 = makes the value unmistakably obvious."
                ),
            },
            "completeness_score": {
                "type": "integer",
                "minimum": 1,
                "maximum": 10,
                "description": (
                    "Is all the information a customer needs before purchasing present? "
                    "Hours, location, expiry, what's included, restrictions. "
                    "10 = no unanswered questions remain."
                ),
            },
            "overall_score": {
                "type": "number",
                "description": (
                    "Weighted average of the six scores. Weight value_comm and "
                    "completeness most heavily (they drive conversion). "
                    "Round to one decimal place."
                ),
            },
            "content_gaps": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Specific pieces of information MISSING from this page that "
                    "customers would want before buying. Each gap must be concrete — "
                    "not 'add more detail' but 'no mention of whether appointment is "
                    "required or walk-in accepted'. Aim for 3–8 gaps."
                ),
            },
            "ai_flags": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Issues or concerns flagged by analysis: misleading discount claims, "
                    "vague fine print, unverifiable superlatives, suspicious review patterns. "
                    "Be specific. Empty list if no concerns."
                ),
            },
            "score_reasoning": {
                "type": "object",
                "description": (
                    "Brief reasoning for each dimension score. Keys are dimension names: "
                    "clarity, trust, urgency, seo, value_comm, completeness. "
                    "Each value is 1–2 sentences."
                ),
                "additionalProperties": {"type": "string"},
            },
        },
        "required": [
            "clarity_score",
            "trust_score",
            "urgency_score",
            "seo_score",
            "value_comm_score",
            "completeness_score",
            "overall_score",
            "content_gaps",
            "ai_flags",
            "score_reasoning",
        ],
    },
}
