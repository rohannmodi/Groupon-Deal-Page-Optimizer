"""
Tool schema for the AI research synthesizer.

Defines the `submit_research_synthesis` tool. The schema enforces that
every finding must be grounded in specific data — Claude cannot offer
generic advice like "add more images." Every claim needs a source or quote.
"""

from __future__ import annotations

RESEARCH_TOOL_NAME = "submit_research_synthesis"

RESEARCH_TOOL_SCHEMA: dict = {
    "description": (
        "Submit a structured synthesis of competitive research for a Groupon deal. "
        "Every field must be grounded in the specific data provided — cite source URLs, "
        "quote review text verbatim, reference exact dollar amounts. "
        "Generic observations that could apply to any deal will be rejected."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "value_assessment": {
                "type": "string",
                "enum": ["genuine_deal", "marginal", "overpriced"],
                "description": (
                    "Is the Groupon price actually a good deal? "
                    "'genuine_deal' = savings are real and significant vs market. "
                    "'marginal' = small savings or questionable original price claim. "
                    "'overpriced' = cheaper options exist or original price is inflated."
                ),
            },
            "value_reasoning": {
                "type": "string",
                "description": (
                    "Specific reasoning for the value_assessment. Must include exact "
                    "dollar amounts. Example: 'Groupon price is $49. Booking direct "
                    "on the merchant site is $75. Yelp shows three competitors "
                    "charging $55–$65 for the same service. This is a genuine deal.' "
                    "Minimum 2 sentences."
                ),
            },
            "groupon_vs_direct_savings": {
                "type": ["number", "null"],
                "description": (
                    "Dollar amount saved vs booking directly with the merchant. "
                    "Null if direct booking price could not be determined."
                ),
            },
            "groupon_vs_competitor_savings": {
                "type": ["number", "null"],
                "description": (
                    "Dollar amount saved vs the average competitor price for the same "
                    "service in the same city. Null if no competitor data available."
                ),
            },
            "merchant_differentiators": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "What does this merchant genuinely do better than competitors, "
                    "based on reviews and research? Each item must reference a specific "
                    "data source. Example: 'Yelp reviewers mention staff by name in "
                    "14 reviews — unusually personal service for a chain.' "
                    "2–5 items. Empty list if no clear differentiators found."
                ),
            },
            "red_flags": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Specific concerns found in the research. Reference exact data. "
                    "Example: '23% of Yelp reviews from the last 6 months mention "
                    "long wait times despite the deal not mentioning appointment required.' "
                    "Empty list if no red flags."
                ),
            },
            "deal_quality": {
                "type": "string",
                "enum": ["strong", "average", "weak"],
                "description": (
                    "Overall deal quality combining value assessment, merchant reputation, "
                    "and page completeness. 'strong' = great price + great merchant. "
                    "'weak' = poor price or concerning review patterns."
                ),
            },
            "review_themes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "theme": {
                            "type": "string",
                            "description": "Short phrase describing the theme, e.g. 'long wait times'",
                        },
                        "sentiment": {
                            "type": "string",
                            "enum": ["positive", "negative", "neutral"],
                        },
                        "mention_count": {
                            "type": "integer",
                            "description": "How many reviews in the provided corpus mention this theme",
                        },
                        "sample_quote": {
                            "type": "string",
                            "description": "A verbatim quote from one review illustrating this theme",
                        },
                        "platform": {
                            "type": "string",
                            "description": "yelp | google | groupon",
                        },
                    },
                    "required": ["theme", "sentiment", "mention_count", "sample_quote", "platform"],
                },
                "description": (
                    "Top 5–10 recurring themes across all provided reviews. "
                    "Count mentions carefully — these counts are what the proposal "
                    "generator will cite in recommendations."
                ),
            },
        },
        "required": [
            "value_assessment",
            "value_reasoning",
            "groupon_vs_direct_savings",
            "groupon_vs_competitor_savings",
            "merchant_differentiators",
            "red_flags",
            "deal_quality",
            "review_themes",
        ],
    },
}
