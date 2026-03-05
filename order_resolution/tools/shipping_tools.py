from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from universal_computer.agents.tools import FunctionTool

from ...common.services.base import ShippingService


# ---------------------------------------------------------------------------
# GetTrackingTool
# ---------------------------------------------------------------------------

class GetTrackingArgs(BaseModel):
    tracking_id: str = Field(description="The tracking ID to look up")


class GetTrackingTool(FunctionTool[GetTrackingArgs, dict]):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    tool_name = "get_tracking"
    args_model = GetTrackingArgs
    description = "Retrieve shipment tracking information by tracking ID."
    shipping: ShippingService = Field(exclude=True)

    async def run(self, args: GetTrackingArgs) -> dict:
        return await self.shipping.get_tracking(args.tracking_id)


# ---------------------------------------------------------------------------
# CreateReturnLabelTool
# ---------------------------------------------------------------------------

class CreateReturnLabelArgs(BaseModel):
    order_id: str = Field(description="The order ID to create a return label for")
    items: list[dict] = Field(description="Items to return, each with item_id and reason")


class CreateReturnLabelTool(FunctionTool[CreateReturnLabelArgs, dict]):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    tool_name = "create_return_label"
    args_model = CreateReturnLabelArgs
    description = "Create a return shipping label for specified items from an order."
    shipping: ShippingService = Field(exclude=True)

    async def run(self, args: CreateReturnLabelArgs) -> dict:
        return await self.shipping.create_return_label(args.order_id, args.items)
