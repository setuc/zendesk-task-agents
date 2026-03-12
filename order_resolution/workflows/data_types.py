from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class WorkflowState(str, Enum):
    CLASSIFYING = "classifying"
    INVESTIGATING = "investigating"
    PLANNING = "planning"
    AWAITING_APPROVAL = "awaiting_approval"
    EXECUTING = "executing"
    COMPENSATING = "compensating"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class IssueType(str, Enum):
    WRONG_ITEM = "wrong_item"
    DAMAGED_ITEM = "damaged_item"
    MISSING_ITEM = "missing_item"
    OVERCHARGE = "overcharge"
    LATE_DELIVERY = "late_delivery"
    OTHER = "other"


class IntentType(str, Enum):
    REFUND = "refund"
    REPLACEMENT = "replacement"
    EXCHANGE = "exchange"
    CREDIT = "store_credit"
    ESCALATION = "escalation"


class ExtractedIssue(BaseModel):
    issue_type: IssueType
    description: str
    item_id: str | None = None
    item_name: str | None = None


class ExtractedIntent(BaseModel):
    issues: list[ExtractedIssue] = Field(default_factory=list)
    intents: list[IntentType] = Field(default_factory=list)
    customer_sentiment: str = "neutral"  # positive, neutral, frustrated, angry
    urgency: str = "normal"  # low, normal, high, critical
    summary: str = ""


class InvestigationResult(BaseModel):
    order_details: dict = Field(default_factory=dict)
    shipping_details: dict = Field(default_factory=dict)
    payment_details: dict = Field(default_factory=dict)
    findings: list[str] = Field(default_factory=list)
    discrepancies: list[str] = Field(default_factory=list)


class ResolutionStep(BaseModel):
    step_id: str
    action: str  # "refund", "replacement", "return_label", "credit", "escalate"
    description: str
    estimated_cost: float = 0.0
    requires_approval: bool = False
    params: dict = Field(default_factory=dict)


class ResolutionPlan(BaseModel):
    steps: list[ResolutionStep] = Field(default_factory=list)
    total_estimated_cost: float = 0.0
    reasoning: str = ""
    requires_human_approval: bool = False


class StepResult(BaseModel):
    step_id: str
    success: bool
    action: str
    result_data: dict = Field(default_factory=dict)
    error_message: str | None = None
    compensation_data: dict | None = None  # Data needed to reverse this step


class ApprovalDecision(BaseModel):
    approved: bool
    reviewer: str = "supervisor"
    notes: str = ""


class WorkflowProgress(BaseModel):
    state: WorkflowState = WorkflowState.CLASSIFYING
    ticket_id: str = ""
    extracted_intent: ExtractedIntent | None = None
    investigation: InvestigationResult | None = None
    plan: ResolutionPlan | None = None
    completed_steps: list[StepResult] = Field(default_factory=list)
    current_step_index: int = 0
    error_message: str | None = None
    customer_message: str | None = None
