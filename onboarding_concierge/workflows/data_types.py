from __future__ import annotations

from enum import Enum
from datetime import datetime

from pydantic import BaseModel, Field


class OnboardingStage(str, Enum):
    ACCOUNT_VERIFICATION = "account_verification"
    INTEGRATION_SETUP = "integration_setup"
    TRAINING_DELIVERY = "training_delivery"
    MILESTONE_CHECKIN = "milestone_checkin"
    FINAL_REVIEW = "final_review"
    COMPLETED = "completed"
    PAUSED = "paused"


class MilestoneType(str, Enum):
    ACCOUNT_VERIFICATION = "account_verification"
    INTEGRATION_SETUP = "integration_setup"
    TRAINING_DELIVERY = "training_delivery"
    MILESTONE_CHECKIN = "milestone_checkin"
    FINAL_REVIEW = "final_review"


class MilestoneStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class Milestone(BaseModel):
    id: str
    type: MilestoneType
    title: str
    description: str
    scheduled_day: int  # Day number in the onboarding plan (1-14)
    status: MilestoneStatus = MilestoneStatus.PENDING
    started_at: datetime | None = None
    completed_at: datetime | None = None
    result_data: dict = Field(default_factory=dict)


class IntegrationTestResult(BaseModel):
    integration_id: str
    endpoint_url: str
    test_passed: bool
    response_status: int | None = None
    response_time_ms: float | None = None
    error_message: str | None = None
    diagnostic_report: str = ""
    suggestions: list[str] = Field(default_factory=list)


class CheckinResponse(BaseModel):
    customer_id: str
    milestone_id: str
    response_text: str
    satisfaction_score: int | None = None  # 1-5
    issues_reported: list[str] = Field(default_factory=list)


class OnboardingPlan(BaseModel):
    customer_id: str
    customer_name: str
    milestones: list[Milestone] = Field(default_factory=list)
    started_at: datetime | None = None
    expected_completion: datetime | None = None


class OnboardingState(BaseModel):
    customer_id: str
    plan: OnboardingPlan
    current_milestone_index: int = 0
    current_stage: OnboardingStage = OnboardingStage.ACCOUNT_VERIFICATION
    paused: bool = False
    error_message: str | None = None
