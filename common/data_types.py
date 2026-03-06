from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class TicketStatus(str, Enum):
    NEW = "new"
    OPEN = "open"
    PENDING = "pending"
    HOLD = "hold"
    SOLVED = "solved"
    CLOSED = "closed"


class TicketPriority(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class TicketComment(BaseModel):
    id: str
    author: str
    body: str
    created_at: datetime
    public: bool = True


class CustomerInfo(BaseModel):
    id: str
    name: str
    email: str
    tier: str = "standard"  # standard, premium, enterprise
    account_created: datetime | None = None


class TicketInfo(BaseModel):
    id: str
    subject: str
    description: str
    status: TicketStatus = TicketStatus.NEW
    priority: TicketPriority = TicketPriority.NORMAL
    requester: CustomerInfo
    assignee: str | None = None
    tags: list[str] = Field(default_factory=list)
    comments: list[TicketComment] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    sla_deadline: datetime | None = None
    custom_fields: dict[str, str] = Field(default_factory=dict)
