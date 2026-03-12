from __future__ import annotations

import os
from pydantic import BaseModel

DEFAULT_PII_FIELDS: frozenset[str] = frozenset({
    "customer_name", "customer_id", "name", "email",
    "customer_message", "resolution_summary", "drafted_message",
    "response_text", "notes",
    "requester", "order_details", "shipping_details", "payment_details",
    "key_phrases", "actionable_insights",
    "summary",
})

PII_REF_PREFIX = "pii:ref:"

# Module-level alias used by the codec — points to the default set.
# When PIIConfig overrides fields, the codec receives them via constructor.
PII_FIELDS = DEFAULT_PII_FIELDS


class PIIConfig(BaseModel):
    redis_url: str = os.environ.get("PII_REDIS_URL", "redis://localhost:6379/1")
    ttl_seconds: int = 60 * 60 * 24 * 30  # 30 days
    enabled: bool = os.environ.get("PII_CODEC_ENABLED", "false").lower() == "true"
    fields: frozenset[str] = DEFAULT_PII_FIELDS
