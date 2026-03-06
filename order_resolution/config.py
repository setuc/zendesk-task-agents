from __future__ import annotations
from pydantic import BaseModel, Field


class OrderResolutionConfig(BaseModel):
    """Configuration for the Order Resolution workflow."""
    temporal_address: str = "localhost:7233"
    temporal_namespace: str = "default"
    task_queue: str = "order-resolution"
    approval_threshold: float = 50.0  # Dollar amount above which human approval is required
    activity_timeout_seconds: int = 120
    approval_timeout_hours: int = 24
    model: str = "gpt-5.2-codex"
    use_memory: bool = False
    use_real_services: bool = False
    # Failure injection for demo
    inject_failure_on_step: str | None = None  # e.g., "refund" to make refund step fail
