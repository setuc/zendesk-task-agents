from __future__ import annotations

import os

from pydantic import BaseModel

_DEFAULT_TEMPORAL_ADDRESS = os.environ.get("TEMPORAL_ADDRESS", "localhost:7233")


class SLAGuardianConfig(BaseModel):
    """Configuration for the SLA Guardian workflow."""

    temporal_address: str = _DEFAULT_TEMPORAL_ADDRESS
    temporal_namespace: str = "default"
    task_queue: str = "sla-guardian"
    scan_interval_seconds: int = 300  # 5 min scan cycle
    escalation_buffer_minutes: int = 30  # Escalate 30min before SLA breach
    model: str = "gpt-5.2-codex"
    use_real_services: bool = False
