from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from universal_computer.agents.tools import FunctionTool

from common.services.base import PaymentService, OrderDBService, ZendeskService


# ---------------------------------------------------------------------------
# ProcessRefundTool
# ---------------------------------------------------------------------------

class ProcessRefundArgs(BaseModel):
    transaction_id: str = Field(description="The transaction ID to refund")
    amount: float = Field(description="The amount to refund")
    reason: str = Field(description="The reason for the refund")


class ProcessRefundTool(FunctionTool[ProcessRefundArgs, dict]):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    tool_name = "process_refund"
    args_model = ProcessRefundArgs
    description = "Process a refund for a given transaction."
    payment: PaymentService = Field(exclude=True)

    async def run(self, args: ProcessRefundArgs) -> dict:
        return await self.payment.process_refund(args.transaction_id, args.amount, args.reason)


# ---------------------------------------------------------------------------
# GetTransactionTool
# ---------------------------------------------------------------------------

class GetTransactionArgs(BaseModel):
    transaction_id: str = Field(description="The transaction ID to look up")


class GetTransactionTool(FunctionTool[GetTransactionArgs, dict]):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    tool_name = "get_payment_transaction"
    args_model = GetTransactionArgs
    description = "Retrieve payment transaction details by transaction ID."
    payment: PaymentService = Field(exclude=True)

    async def run(self, args: GetTransactionArgs) -> dict:
        return await self.payment.get_transaction(args.transaction_id)


# ---------------------------------------------------------------------------
# GetOrderTool
# ---------------------------------------------------------------------------

class GetOrderArgs(BaseModel):
    order_id: str = Field(description="The order ID to look up")


class GetOrderTool(FunctionTool[GetOrderArgs, dict]):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    tool_name = "get_order_details"
    args_model = GetOrderArgs
    description = "Retrieve order details by order ID."
    order_db: OrderDBService = Field(exclude=True)

    async def run(self, args: GetOrderArgs) -> dict:
        return await self.order_db.get_order(args.order_id)


# ---------------------------------------------------------------------------
# CheckEndpointTool
# ---------------------------------------------------------------------------

class CheckEndpointArgs(BaseModel):
    url: str = Field(description="The URL endpoint to check")


class CheckEndpointTool(FunctionTool[CheckEndpointArgs, dict]):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    tool_name = "check_endpoint"
    args_model = CheckEndpointArgs
    description = "Perform a mock HTTP health check against an endpoint URL. Returns status 500 for URLs containing '500' or 'error', 200 otherwise."

    async def run(self, args: CheckEndpointArgs) -> dict:
        url_lower = args.url.lower()
        if "500" in url_lower or "error" in url_lower:
            return {"url": args.url, "status_code": 500, "healthy": False, "message": "Internal Server Error"}
        return {"url": args.url, "status_code": 200, "healthy": True, "message": "OK"}


# ---------------------------------------------------------------------------
# CreateEscalationTool
# ---------------------------------------------------------------------------

class CreateEscalationArgs(BaseModel):
    ticket_id: str = Field(description="The Zendesk ticket ID to escalate")
    team: str = Field(description="The team to escalate to (e.g. 'engineering', 'billing')")
    reason: str = Field(description="The reason for escalation")
    reproduction_steps: str = Field(description="Steps to reproduce the issue")


class CreateEscalationTool(FunctionTool[CreateEscalationArgs, dict]):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    tool_name = "create_escalation"
    args_model = CreateEscalationArgs
    description = "Escalate a ticket by adding an internal comment with escalation details and updating the ticket."
    zendesk: ZendeskService = Field(exclude=True)

    async def run(self, args: CreateEscalationArgs) -> dict:
        comment_body = (
            f"**Escalation to {args.team}**\n\n"
            f"**Reason:** {args.reason}\n\n"
            f"**Reproduction Steps:**\n{args.reproduction_steps}"
        )
        await self.zendesk.add_comment(args.ticket_id, comment_body, public=False)
        await self.zendesk.update_ticket(args.ticket_id, {
            "tags": [f"escalated_{args.team}"],
            "priority": "high",
        })
        return {
            "ticket_id": args.ticket_id,
            "team": args.team,
            "status": "escalated",
        }


# ---------------------------------------------------------------------------
# SearchMemoryTool
# ---------------------------------------------------------------------------

class SearchMemoryArgs(BaseModel):
    keyword: str = Field(description="Keyword to search for in the memory store")


class SearchMemoryTool(FunctionTool[SearchMemoryArgs, list[dict]]):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    tool_name = "search_memory"
    args_model = SearchMemoryArgs
    description = "Search the shared memory store for entries matching a keyword."
    memory_store: dict = Field(default_factory=dict, exclude=True)

    async def run(self, args: SearchMemoryArgs) -> list[dict]:
        keyword_lower = args.keyword.lower()
        results = []
        for pattern, action in self.memory_store.items():
            if keyword_lower in pattern.lower() or keyword_lower in str(action).lower():
                results.append({"pattern": pattern, "action": action})
        return results


# ---------------------------------------------------------------------------
# WriteMemoryTool
# ---------------------------------------------------------------------------

class WriteMemoryArgs(BaseModel):
    pattern: str = Field(description="The pattern or key to store in memory")
    action: str = Field(description="The action or resolution associated with this pattern")


class WriteMemoryTool(FunctionTool[WriteMemoryArgs, dict]):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    tool_name = "write_memory"
    args_model = WriteMemoryArgs
    description = "Save a pattern and its associated action to the shared memory store."
    memory_store: dict = Field(default_factory=dict, exclude=True)

    async def run(self, args: WriteMemoryArgs) -> dict:
        self.memory_store[args.pattern] = args.action
        return {"pattern": args.pattern, "action": args.action, "status": "saved"}
