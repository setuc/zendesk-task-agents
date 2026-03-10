from __future__ import annotations
import os
from pydantic import BaseModel, Field

_DEFAULT_TEMPORAL_ADDRESS = os.environ.get("TEMPORAL_ADDRESS", "localhost:7233")


class OrderResolutionConfig(BaseModel):
    """Configuration for the Order Resolution workflow."""
    temporal_address: str = _DEFAULT_TEMPORAL_ADDRESS
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
