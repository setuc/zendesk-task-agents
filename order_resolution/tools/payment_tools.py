from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from universal_computer.agents.tools import FunctionTool

from ...common.services.base import PaymentService


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
    tool_name = "get_transaction"
    args_model = GetTransactionArgs
    description = "Retrieve transaction details by transaction ID."
    payment: PaymentService = Field(exclude=True)

    async def run(self, args: GetTransactionArgs) -> dict:
        return await self.payment.get_transaction(args.transaction_id)
