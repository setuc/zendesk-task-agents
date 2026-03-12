from __future__ import annotations

import os

from pydantic import BaseModel

_DEFAULT_TEMPORAL_ADDRESS = os.environ.get("TEMPORAL_ADDRESS", "localhost:7233")


class OnboardingConfig(BaseModel):
    """Configuration for the Onboarding Concierge workflow."""

    temporal_address: str = _DEFAULT_TEMPORAL_ADDRESS
    temporal_namespace: str = "default"
    task_queue: str = "onboarding-concierge"
    checkin_timeout_hours: int = 48
    followup_timeout_hours: int = 120  # 5 days
    model: str = "gpt-5.2-codex"
    use_real_services: bool = False
    simulation_day_seconds: int = 5  # For fast-forward demo mode
