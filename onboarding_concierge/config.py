from __future__ import annotations

from pydantic import BaseModel


class OnboardingConfig(BaseModel):
    """Configuration for the Onboarding Concierge workflow."""

    temporal_address: str = "localhost:7233"
    temporal_namespace: str = "default"
    task_queue: str = "onboarding-concierge"
    checkin_timeout_hours: int = 48
    followup_timeout_hours: int = 120  # 5 days
    model: str = "gpt-5.2-codex"
    use_real_services: bool = False
    simulation_day_seconds: int = 5  # For fast-forward demo mode
