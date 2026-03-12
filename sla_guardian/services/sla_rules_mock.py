from __future__ import annotations

import json
from pathlib import Path


# Pre-defined SLA policies keyed by (priority, customer_tier)
_DEFAULT_POLICIES: dict[tuple[str, str], dict] = {
    # Standard tier
    ("low", "standard"): {
        "first_response_hours": 48,
        "resolution_hours": 120,
        "priority": "low",
        "customer_tier": "standard",
    },
    ("normal", "standard"): {
        "first_response_hours": 24,
        "resolution_hours": 72,
        "priority": "normal",
        "customer_tier": "standard",
    },
    ("high", "standard"): {
        "first_response_hours": 12,
        "resolution_hours": 48,
        "priority": "high",
        "customer_tier": "standard",
    },
    ("urgent", "standard"): {
        "first_response_hours": 8,
        "resolution_hours": 24,
        "priority": "urgent",
        "customer_tier": "standard",
    },
    # Premium tier
    ("low", "premium"): {
        "first_response_hours": 24,
        "resolution_hours": 72,
        "priority": "low",
        "customer_tier": "premium",
    },
    ("normal", "premium"): {
        "first_response_hours": 8,
        "resolution_hours": 24,
        "priority": "normal",
        "customer_tier": "premium",
    },
    ("high", "premium"): {
        "first_response_hours": 4,
        "resolution_hours": 16,
        "priority": "high",
        "customer_tier": "premium",
    },
    ("urgent", "premium"): {
        "first_response_hours": 2,
        "resolution_hours": 8,
        "priority": "urgent",
        "customer_tier": "premium",
    },
    # Enterprise tier
    ("low", "enterprise"): {
        "first_response_hours": 8,
        "resolution_hours": 24,
        "priority": "low",
        "customer_tier": "enterprise",
    },
    ("normal", "enterprise"): {
        "first_response_hours": 4,
        "resolution_hours": 8,
        "priority": "normal",
        "customer_tier": "enterprise",
    },
    ("high", "enterprise"): {
        "first_response_hours": 2,
        "resolution_hours": 4,
        "priority": "high",
        "customer_tier": "enterprise",
    },
    ("urgent", "enterprise"): {
        "first_response_hours": 1,
        "resolution_hours": 2,
        "priority": "urgent",
        "customer_tier": "enterprise",
    },
}


class MockSLARulesService:
    """In-memory mock implementation of the SLARulesService protocol.

    Returns SLA policies based on ticket priority and customer tier:
    - Standard: 24h first response, 72h resolution (normal priority)
    - Premium: 8h first response, 24h resolution (normal priority)
    - Enterprise: 4h first response, 8h resolution (normal priority)
    Higher priority tickets receive shorter SLA windows.
    """

    def __init__(self) -> None:
        self._policies = dict(_DEFAULT_POLICIES)

    async def get_policy(self, priority: str, customer_tier: str) -> dict:
        """Return SLA policy for the given priority and customer tier.

        Falls back to standard tier and/or normal priority when an exact
        match is not found.
        """
        key = (priority.lower(), customer_tier.lower())
        policy = self._policies.get(key)
        if policy is not None:
            return dict(policy)

        # Fallback: try standard tier with the requested priority
        fallback_key = (priority.lower(), "standard")
        policy = self._policies.get(fallback_key)
        if policy is not None:
            return dict(policy)

        # Final fallback: normal priority, standard tier
        return dict(self._policies[("normal", "standard")])

    async def list_policies(self) -> list[dict]:
        """Return all defined SLA policies."""
        return [dict(p) for p in self._policies.values()]
