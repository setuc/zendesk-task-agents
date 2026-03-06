from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from universal_computer.agents.tools import FunctionTool

from common.services.base import OrderDBService


# ---------------------------------------------------------------------------
# GetOrderTool
# ---------------------------------------------------------------------------

class GetOrderArgs(BaseModel):
    order_id: str = Field(description="The order ID to look up")


class GetOrderTool(FunctionTool[GetOrderArgs, dict]):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    tool_name = "get_order"
    args_model = GetOrderArgs
    description = "Retrieve order details including items, prices, and status."
    order_db: OrderDBService = Field(exclude=True)

    async def run(self, args: GetOrderArgs) -> dict:
        return await self.order_db.get_order(args.order_id)


# ---------------------------------------------------------------------------
# CreateReplacementOrderTool
# ---------------------------------------------------------------------------

class CreateReplacementOrderArgs(BaseModel):
    original_order_id: str = Field(description="The original order ID")
    items: list[dict] = Field(description="Items to replace, each with item_id and reason")


class CreateReplacementOrderTool(FunctionTool[CreateReplacementOrderArgs, dict]):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    tool_name = "create_replacement_order"
    args_model = CreateReplacementOrderArgs
    description = "Create a replacement order for specified items from an existing order."
    order_db: OrderDBService = Field(exclude=True)

    async def run(self, args: CreateReplacementOrderArgs) -> dict:
        return await self.order_db.create_replacement_order(args.original_order_id, args.items)
