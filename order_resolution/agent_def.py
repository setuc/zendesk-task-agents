from __future__ import annotations

from universal_computer.agents import Agent
from universal_computer.agents.plugins.memory import Memory
from universal_computer.agents.plugins.plugins import Plugins

from ..common.services.base import (
    OrderDBService,
    PaymentService,
    ShippingService,
    ZendeskService,
)
from ..common.tools.zendesk_tools import AddCommentTool, GetTicketTool, UpdateTicketTool
from .instructions import ORDER_RESOLUTION_INSTRUCTIONS
from .tools.order_tools import CreateReplacementOrderTool, GetOrderTool
from .tools.payment_tools import GetTransactionTool, ProcessRefundTool
from .tools.shipping_tools import CreateReturnLabelTool, GetTrackingTool


def create_order_resolution_agent(
    *,
    zendesk: ZendeskService,
    order_db: OrderDBService,
    shipping: ShippingService,
    payment: PaymentService,
    model: str = "gpt-5.2-codex",
    use_memory: bool = False,
) -> Agent:
    """Create a UC agent configured for order resolution.

    The agent has tools for querying Zendesk tickets, orders, shipping,
    and payment data, plus tools for creating refunds and replacements.

    Args:
        zendesk: Service for Zendesk ticket operations.
        order_db: Service for order database lookups and replacements.
        shipping: Service for tracking and return label creation.
        payment: Service for refunds and transaction lookups.
        model: The model identifier to use for the agent.
        use_memory: Whether to enable the Memory plugin alongside defaults.

    Returns:
        A fully configured Agent instance ready to be started.
    """
    tools = [
        GetTicketTool(zendesk=zendesk),
        UpdateTicketTool(zendesk=zendesk),
        AddCommentTool(zendesk=zendesk),
        GetOrderTool(order_db=order_db),
        CreateReplacementOrderTool(order_db=order_db),
        GetTrackingTool(shipping=shipping),
        CreateReturnLabelTool(shipping=shipping),
        ProcessRefundTool(payment=payment),
        GetTransactionTool(payment=payment),
    ]

    plugins: Plugins = Plugins.default()
    if use_memory:
        plugins = plugins.add(Memory())

    return Agent(
        model=model,
        developer_instructions=ORDER_RESOLUTION_INSTRUCTIONS,
        tools=tools,
        plugins=plugins,
    )
