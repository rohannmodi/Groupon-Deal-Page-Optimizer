"""
JSON reporter — serializes a DealAudit to audit.json in the deal's output folder.
"""

from __future__ import annotations

import json
from pathlib import Path

from models import DealAudit
from config.settings import settings


def write_audit_json(audit: DealAudit) -> Path:
    """Write audit.json to outputs/{deal_id}/1_audit/ and return the path."""
    out_dir = _audit_dir(audit.deal_id)
    out_dir.mkdir(parents=True, exist_ok=True)

    out_path = out_dir / "audit.json"
    out_path.write_text(
        json.dumps(audit.model_dump(mode="json"), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return out_path


def _audit_dir(deal_id: str) -> Path:
    return settings.outputs_dir / deal_id / "1_audit"
