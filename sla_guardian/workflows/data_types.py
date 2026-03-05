from __future__ import annotations

from enum import Enum
from datetime import datetime

from pydantic import BaseModel, Field


class EscalationTier(str, Enum):
    L1 = "l1"
    L2 = "l2"
    L3 = "l3"
    MANAGER = "manager"


class SLAStatus(str, Enum):
    COMPLIANT = "compliant"
    AT_RISK = "at_risk"
    BREACHED = "breached"
    RESOLVED = "resolved"


class UrgencyClassification(BaseModel):
    ticket_id: str
    original_priority: str
    assessed_priority: str
    reasoning: str
    urgency_score: float = 0.0  # 0-1 scale
    priority_override: bool = False
    customer_tier: str = "standard"
    time_pressure: str = ""  # e.g. "2h 15m remaining of 4h SLA"
    comment_count: int = 0
    signals_detected: list[str] = Field(default_factory=list)


class SentimentReport(BaseModel):
    ticket_id: str
    overall_sentiment: str  # positive, neutral, frustrated, angry
    frustration_trajectory: str  # improving, stable, worsening
    key_phrases: list[str] = Field(default_factory=list)
    escalation_risk: float = 0.0  # 0-1
    frustration_score: float = 0.0  # 0-10 scale
    positive_score: float = 0.0  # 0-10 scale
    comment_count: int = 0
    first_comment_sentiment: str = ""
    latest_comment_sentiment: str = ""
    actionable_insights: list[str] = Field(default_factory=list)


class EscalationAction(BaseModel):
    ticket_id: str
    from_tier: EscalationTier
    to_tier: EscalationTier
    reason: str
    drafted_message: str
    auto_escalated: bool = True


class EscalationResult(BaseModel):
    ticket_id: str
    escalated_to: str
    status: str
    message_posted: bool
    timestamp: str = ""
    assigned_team: str = ""
    expected_response_time: str = ""
    internal_note_id: str = ""


class TicketMonitorState(BaseModel):
    ticket_id: str
    sla_status: SLAStatus = SLAStatus.COMPLIANT
    current_tier: EscalationTier = EscalationTier.L1
    urgency: UrgencyClassification | None = None
    sentiment: SentimentReport | None = None
    escalation_history: list[EscalationAction] = Field(default_factory=list)
    sla_deadline: datetime | None = None
    next_check: datetime | None = None


class GuardianState(BaseModel):
    monitored_tickets: list[str] = Field(default_factory=list)
    scan_count: int = 0
    last_scan: datetime | None = None
    total_escalations: int = 0
