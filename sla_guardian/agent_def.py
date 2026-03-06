from __future__ import annotations

from universal_computer.agents import Agent
from universal_computer.agents.plugins.plugins import Plugins

from common.services.base import SLARulesService, ZendeskService
from common.tools.zendesk_tools import (
    AddCommentTool,
    GetTicketTool,
    ListTicketsTool,
    UpdateTicketTool,
)
from .instructions import SLA_GUARDIAN_INSTRUCTIONS
from .tools.analysis_tools import AnalyzeConversationTool, ClassifyUrgencyTool


def create_sla_agent(
    *,
    zendesk: ZendeskService,
    sla_rules: SLARulesService,
    model: str = "gpt-5.2-codex",
) -> Agent:
    """Create a UC agent configured for SLA monitoring and escalation.

    The agent has tools for querying Zendesk tickets, analyzing conversations,
    classifying urgency, and managing escalations.

    Args:
        zendesk: Service for Zendesk ticket operations.
        sla_rules: Service for SLA policy lookups.
        model: The model identifier to use for the agent.

    Returns:
        A fully configured Agent instance ready to be started.
    """
    tools = [
        GetTicketTool(zendesk=zendesk),
        UpdateTicketTool(zendesk=zendesk),
        ListTicketsTool(zendesk=zendesk),
        AddCommentTool(zendesk=zendesk),
        AnalyzeConversationTool(zendesk=zendesk),
        ClassifyUrgencyTool(zendesk=zendesk),
    ]

    plugins: Plugins = Plugins.default()

    return Agent(
        model=model,
        developer_instructions=SLA_GUARDIAN_INSTRUCTIONS,
        tools=tools,
        plugins=plugins,
    )
