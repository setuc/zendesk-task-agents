from __future__ import annotations

from universal_computer.agents import Agent
from universal_computer.agents.plugins.plugins import Plugins

from common.services.base import (
    EmailService,
    IntegrationTestService,
    ZendeskService,
)
from common.tools.zendesk_tools import AddCommentTool, GetTicketTool, UpdateTicketTool
from .instructions import ONBOARDING_CONCIERGE_INSTRUCTIONS
from .tools.diagnostic_tools import CheckEndpointTool, RunDiagnosticTool
from .tools.report_tools import GenerateReportTool


def create_onboarding_agent(
    *,
    zendesk: ZendeskService,
    integration_test: IntegrationTestService,
    email: EmailService,
    model: str = "gpt-5.2-codex",
) -> Agent:
    """Create a UC agent configured for onboarding concierge operations.

    The agent has tools for Zendesk ticket management, integration testing
    and diagnostics, and report generation.

    Args:
        zendesk: Service for Zendesk ticket operations.
        integration_test: Service for testing customer integration endpoints.
        email: Service for sending onboarding emails.
        model: The model identifier to use for the agent.

    Returns:
        A fully configured Agent instance ready to be started.
    """
    tools = [
        GetTicketTool(zendesk=zendesk),
        UpdateTicketTool(zendesk=zendesk),
        AddCommentTool(zendesk=zendesk),
        RunDiagnosticTool(integration_test=integration_test),
        CheckEndpointTool(integration_test=integration_test),
        GenerateReportTool(),
    ]

    plugins: Plugins = Plugins.default()

    return Agent(
        model=model,
        developer_instructions=ONBOARDING_CONCIERGE_INSTRUCTIONS,
        tools=tools,
        plugins=plugins,
    )
