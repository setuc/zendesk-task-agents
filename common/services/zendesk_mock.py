from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from ..data_types import (
    CustomerInfo,
    TicketInfo,
    TicketPriority,
    TicketStatus,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _sample_tickets() -> dict[str, dict]:
    customer_a = CustomerInfo(
        id="cust_001",
        name="Alice Johnson",
        email="alice@example.com",
        tier="premium",
        account_created=datetime(2023, 1, 15, tzinfo=timezone.utc),
    )
    customer_b = CustomerInfo(
        id="cust_002",
        name="Bob Smith",
        email="bob@example.com",
        tier="standard",
        account_created=datetime(2024, 6, 1, tzinfo=timezone.utc),
    )
    customer_c = CustomerInfo(
        id="cust_003",
        name="Carol Martinez",
        email="carol@enterprise.io",
        tier="enterprise",
        account_created=datetime(2022, 3, 10, tzinfo=timezone.utc),
    )

    tickets: list[TicketInfo] = [
        TicketInfo(
            id="ticket_1001",
            subject="Order not delivered",
            description="My order ORD-5001 was supposed to arrive last week but tracking shows no updates.",
            status=TicketStatus.OPEN,
            priority=TicketPriority.HIGH,
            requester=customer_a,
            assignee="agent_jane",
            tags=["shipping", "escalation"],
            created_at=datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc),
            updated_at=datetime(2026, 3, 2, 14, 30, tzinfo=timezone.utc),
            sla_deadline=datetime(2026, 3, 4, 10, 0, tzinfo=timezone.utc),
            custom_fields={"order_id": "ORD-5001"},
        ),
        TicketInfo(
            id="ticket_1002",
            subject="Refund request for damaged item",
            description="Received a broken widget in order ORD-5002. Requesting a full refund.",
            status=TicketStatus.NEW,
            priority=TicketPriority.URGENT,
            requester=customer_b,
            tags=["refund", "damaged"],
            created_at=datetime(2026, 3, 3, 8, 0, tzinfo=timezone.utc),
            updated_at=datetime(2026, 3, 3, 8, 0, tzinfo=timezone.utc),
            custom_fields={"order_id": "ORD-5002", "transaction_id": "txn_9001"},
        ),
        TicketInfo(
            id="ticket_1003",
            subject="API integration returning 500 errors",
            description="Our integration with endpoint /api/v2/sync has been failing since yesterday.",
            status=TicketStatus.OPEN,
            priority=TicketPriority.URGENT,
            requester=customer_c,
            assignee="agent_mike",
            tags=["api", "integration", "enterprise"],
            created_at=datetime(2026, 3, 2, 16, 0, tzinfo=timezone.utc),
            updated_at=datetime(2026, 3, 3, 9, 0, tzinfo=timezone.utc),
            sla_deadline=datetime(2026, 3, 3, 16, 0, tzinfo=timezone.utc),
            custom_fields={"integration_id": "int_301"},
        ),
    ]

    return {t.id: t.model_dump(mode="json") for t in tickets}


class MockZendeskService:
    """In-memory mock implementation of the ZendeskService protocol."""

    def __init__(self) -> None:
        self._tickets: dict[str, dict] = _sample_tickets()
        self._should_fail: dict[str, Exception] = {}

    def inject_failure(self, method_name: str, error: Exception) -> None:
        """Register an exception to raise on the next call to *method_name*."""
        self._should_fail[method_name] = error

    def clear_failure(self, method_name: str) -> None:
        self._should_fail.pop(method_name, None)

    def _check_failure(self, method_name: str) -> None:
        exc = self._should_fail.pop(method_name, None)
        if exc is not None:
            raise exc

    async def get_ticket(self, ticket_id: str) -> dict:
        self._check_failure("get_ticket")
        ticket = self._tickets.get(ticket_id)
        if ticket is None:
            raise KeyError(f"Ticket {ticket_id} not found")
        return dict(ticket)

    async def update_ticket(self, ticket_id: str, updates: dict) -> dict:
        self._check_failure("update_ticket")
        ticket = self._tickets.get(ticket_id)
        if ticket is None:
            raise KeyError(f"Ticket {ticket_id} not found")
        ticket.update(updates)
        ticket["updated_at"] = _now().isoformat()
        return dict(ticket)

    async def list_tickets(self, filters: dict[str, Any] | None = None) -> list[dict]:
        self._check_failure("list_tickets")
        results = list(self._tickets.values())
        if filters:
            for key, value in filters.items():
                results = [t for t in results if t.get(key) == value]
        return results

    async def add_comment(self, ticket_id: str, body: str, public: bool = True) -> dict:
        self._check_failure("add_comment")
        ticket = self._tickets.get(ticket_id)
        if ticket is None:
            raise KeyError(f"Ticket {ticket_id} not found")
        comment = {
            "id": f"comment_{uuid.uuid4().hex[:8]}",
            "author": "agent_bot",
            "body": body,
            "created_at": _now().isoformat(),
            "public": public,
        }
        ticket.setdefault("comments", []).append(comment)
        ticket["updated_at"] = _now().isoformat()
        return comment
