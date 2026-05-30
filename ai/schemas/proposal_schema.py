"""
Tool schema for the proposal AI — generate_optimization_proposal.

The schema enforces:
  - Concrete rewritten copy in proposed_title, proposed_highlights, etc.
  - Every recommendation has a data_citation field (no uncited claims)
  - Priority-ranked recommendations ordered by conversion impact
  - Expected impact limited to high|medium|low
"""

PROPOSAL_TOOL_NAME = "generate_optimization_proposal"

PROPOSAL_TOOL_SCHEMA = {
    "description": (
        "Generate a complete, prioritized optimization proposal for a Groupon deal page. "
        "Every recommendation must cite a specific data point from the audit or research. "
        "Proposed copy must be concrete and ready to use — not placeholder text."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            # ── Rewritten copy ──────────────────────────────────────────────
            "proposed_title": {
                "type": "string",
                "description": (
                    "Rewritten deal title. Must be specific, keyword-rich, and conversion-focused. "
                    "Explain what you get, where, and the key value (e.g., savings or experience). "
                    "Max 100 characters."
                ),
            },
            "proposed_meta_title": {
                "type": "string",
                "description": (
                    "SEO meta title. Include primary keyword + location + value prop. "
                    "Must be under 60 characters."
                ),
            },
            "proposed_meta_description": {
                "type": "string",
                "description": (
                    "SEO meta description. Include service, location, price/discount, and a call "
                    "to action. Must be under 155 characters."
                ),
            },
            "proposed_h1": {
                "type": "string",
                "description": (
                    "Proposed H1 heading for the deal page. May differ slightly from the title "
                    "to target a specific keyword phrase. Max 80 characters."
                ),
            },
            "proposed_highlights": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Full rewrite of the 'What You Get' / highlights bullets. "
                    "Each bullet should be a complete, specific statement. "
                    "Incorporate themes from positive reviews (with mention counts). "
                    "Address any critical content gaps identified in the audit. "
                    "Aim for 5–8 bullets."
                ),
                "minItems": 3,
                "maxItems": 10,
            },
            "pricing_framing": {
                "type": "string",
                "description": (
                    "Specific proposal for how to frame the price and value section. "
                    "Should reference market context (e.g., 'X% below the competitor average of $Y'). "
                    "This is the copy instruction, not the copy itself."
                ),
            },
            "executive_summary": {
                "type": "string",
                "description": (
                    "2–3 sentence summary a CEO would read: what the key problem is, "
                    "what the top 2–3 changes are, and what conversion improvement is likely. "
                    "Be concrete — cite actual scores or data points."
                ),
            },
            "image_recommendations": {
                "type": "array",
                "description": (
                    "3–5 specific image recommendations based on the audit's image inventory "
                    "and the deal category. Each recommendation must specify: "
                    "what type of image to add or change, why it helps conversion, "
                    "and priority (High/Medium/Low). "
                    "Be specific — 'Add a photo of all 7 juice bottles lined up' not 'add more images'. "
                    "For food/wellness: product shots, before/after, facility shots. "
                    "For services: staff photos, room/space photos, work samples. "
                    "For activities: action shots, group photos, venue shots."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "image_type": {
                            "type": "string",
                            "description": (
                                "What specific image to add or change. Be precise: "
                                "'Photo of all 7 juice bottles together', 'Before/after skin treatment photo', "
                                "'Staff/therapist headshot with name', 'Reception area photo'."
                            ),
                        },
                        "conversion_reason": {
                            "type": "string",
                            "description": (
                                "Why this image improves purchase confidence. "
                                "Example: 'Buyers need to see the full product — product shots "
                                "reduce pre-purchase uncertainty and abandonment.'"
                            ),
                        },
                        "priority": {
                            "type": "string",
                            "enum": ["High", "Medium", "Low"],
                            "description": "High = directly addresses a known buyer hesitation; Low = nice-to-have.",
                        },
                    },
                    "required": ["image_type", "conversion_reason", "priority"],
                },
                "minItems": 2,
                "maxItems": 6,
            },

            # ── Ranked recommendations ──────────────────────────────────────
            "recommendations": {
                "type": "array",
                "description": (
                    "Ordered list of specific, actionable changes. Rank 1 = highest expected "
                    "conversion impact. Aim for 6–10 recommendations. "
                    "Must cover at least: title, highlights, one content gap, one SEO item, "
                    "and competitive positioning."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "priority_rank": {
                            "type": "integer",
                            "description": "1 = highest impact. No duplicate ranks.",
                            "minimum": 1,
                        },
                        "category": {
                            "type": "string",
                            "enum": [
                                "title",
                                "pricing",
                                "highlights",
                                "content",
                                "images",
                                "seo",
                                "positioning",
                                "trust",
                                "urgency",
                            ],
                            "description": "Which dimension of the page this recommendation addresses.",
                        },
                        "short_title": {
                            "type": "string",
                            "description": (
                                "Max 10 words, complete sentence fragment, no ellipsis. "
                                "Used in tables and section headers. "
                                "Example: 'Add appointment requirement to highlights' "
                                "or 'Reframe price against $75 competitor average'. "
                                "Must end at a complete word — never truncate mid-word."
                            ),
                        },
                        "recommendation": {
                            "type": "string",
                            "description": (
                                "Full imperative statement of what to do. Be specific — "
                                "name the exact element and the exact change. No length limit. "
                                "Example: 'Add appointment-booking requirement to the highlights "
                                "section (position #3): note that advance booking is required "
                                "and walk-ins may face long waits.'"
                            ),
                        },
                        "current_state": {
                            "type": "string",
                            "description": (
                                "What the page currently says or does. "
                                "Quote directly from the deal page where possible."
                            ),
                        },
                        "proposed_state": {
                            "type": "string",
                            "description": (
                                "The concrete replacement — the actual copy, structure, or "
                                "element that should appear on the page. Not a description of "
                                "the change — the change itself."
                            ),
                        },
                        "data_citation": {
                            "type": "string",
                            "description": (
                                "The specific data point from audit or research that motivates "
                                "this change. Must reference a real finding: "
                                "e.g., 'content_gap: no appointment info', "
                                "'8 Yelp reviews mention wait times', "
                                "'completeness_score: 3/10', "
                                "'competitor average: $75 vs Groupon $45'."
                            ),
                        },
                        "expected_impact": {
                            "type": "string",
                            "enum": ["high", "medium", "low"],
                            "description": (
                                "high: directly addresses a trust/clarity/value gap that "
                                "causes purchase abandonment. "
                                "medium: improves discoverability or reduces hesitation. "
                                "low: polish, minor SEO, nice-to-have."
                            ),
                        },
                        "impact_rationale": {
                            "type": "string",
                            "description": (
                                "One sentence explaining WHY this change improves conversion. "
                                "Reference buyer psychology, SEO, or competitive positioning."
                            ),
                        },
                        "supporting_evidence": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "2–4 specific evidence items justifying this recommendation. "
                                "Each must be one of: "
                                "(a) a review theme with exact mention count, "
                                "(b) a competitor price with confidence level, "
                                "(c) a specific audit score or content gap. "
                                "Example: ['8 Yelp reviews mention wait times (negative)', "
                                "'completeness_score: 4/10 — appointment policy missing', "
                                "'competitor SpaBright charges $55 (high confidence, 78% SKU match)'] "
                                "Generic statements not accepted."
                            ),
                            "minItems": 1,
                        },
                        "impact_score": {
                            "type": "integer",
                            "description": (
                                "impact_score = visibility × user_importance × evidence_strength "
                                "(each dimension 1–10). Sort all recommendations by this score DESC. "
                                "visibility: how prominently the issue appears to buyers "
                                "  (title/price = 9–10, footer fine print = 1–2). "
                                "user_importance: how much this matters to purchase decision "
                                "  (price trust = 9, address detail = 3). "
                                "evidence_strength: quality of evidence supporting the change "
                                "  (12+ review mentions = 9, one weak competitor = 2). "
                                "Examples: trust_signal_near_CTA = 9×9×8 = 648; "
                                "address_clarification = 3×4×5 = 60. Range: 1–1000."
                            ),
                            "minimum": 1,
                            "maximum": 1000,
                        },
                    },
                    "required": [
                        "priority_rank",
                        "category",
                        "short_title",
                        "recommendation",
                        "current_state",
                        "proposed_state",
                        "data_citation",
                        "expected_impact",
                        "impact_rationale",
                        "supporting_evidence",
                        "impact_score",
                    ],
                },
                "minItems": 5,
            },
        },
        "required": [
            "proposed_title",
            "proposed_meta_title",
            "proposed_meta_description",
            "proposed_h1",
            "proposed_highlights",
            "pricing_framing",
            "executive_summary",
            "image_recommendations",
            "recommendations",
        ],
    },
}
