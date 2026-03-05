from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from temporalio import activity

from .data_types import IntegrationTestResult
from ...common.services.base import (
    EmailService,
    IntegrationTestService,
    ZendeskService,
)


@dataclass
class OnboardingActivities:
    """Activity implementations for onboarding concierge workflows."""

    zendesk: ZendeskService
    integration_test: IntegrationTestService
    email: EmailService

    @activity.defn
    async def verify_account(self, customer_id: str) -> dict:
        """Check account configuration and verify prerequisites.

        In a real implementation, this would use a UC agent to:
        - Query the CRM for customer account details
        - Verify billing is set up correctly
        - Confirm contact information
        - Check service tier and entitlements
        """
        activity.logger.info(f"Verifying account for customer {customer_id}")

        # Mock verification that mirrors what the UC agent would produce
        now = datetime.now(timezone.utc).isoformat()
        return {
            "customer_id": customer_id,
            "verified": True,
            "checks": {
                "billing_active": True,
                "contact_info_complete": True,
                "service_tier_confirmed": True,
                "prerequisites_met": True,
            },
            "verified_at": now,
            "notes": "All account verification checks passed successfully.",
        }

    @activity.defn
    async def test_integration(
        self, customer_id: str, integration_config: dict
    ) -> IntegrationTestResult:
        """UC agent tests customer endpoint in a Docker sandbox.

        In a real implementation, this would:
        1. Spin up a Docker container with the test harness
        2. Pass the endpoint configuration to the harness
        3. Collect and return the test results
        """
        activity.logger.info(
            f"Testing integration for customer {customer_id}: "
            f"{integration_config.get('integration_id', 'unknown')}"
        )

        endpoint_url = integration_config.get("endpoint_url", "")
        integration_id = integration_config.get("integration_id", "")
        method = integration_config.get("method", "POST")

        # Test the endpoint using the integration test service
        test_result = await self.integration_test.test_endpoint(
            url=endpoint_url, method=method
        )

        if test_result.get("success"):
            return IntegrationTestResult(
                integration_id=integration_id,
                endpoint_url=endpoint_url,
                test_passed=True,
                response_status=test_result.get("response_status"),
                response_time_ms=test_result.get("response_time_ms"),
            )

        # Test failed -- run diagnostics
        diagnostic = await self.integration_test.run_diagnostics(integration_id)
        failed_checks = [
            c for c in diagnostic.get("checks", []) if c.get("status") != "passed"
        ]
        suggestions = [c.get("detail", "") for c in failed_checks]

        return IntegrationTestResult(
            integration_id=integration_id,
            endpoint_url=endpoint_url,
            test_passed=False,
            response_status=test_result.get("response_status"),
            response_time_ms=test_result.get("response_time_ms"),
            error_message=test_result.get("error"),
            diagnostic_report=json.dumps(diagnostic, indent=2, default=str),
            suggestions=suggestions if suggestions else [
                "Verify the endpoint URL is correct and accessible.",
                "Check that authentication credentials are valid.",
                "Ensure the endpoint accepts the expected request format.",
            ],
        )

    @activity.defn
    async def generate_training_materials(
        self, customer_id: str, customer_name: str
    ) -> str:
        """UC agent creates a personalised onboarding guide.

        In a real implementation, the UC agent would generate training content
        tailored to the customer's integration setup and service tier.
        """
        activity.logger.info(
            f"Generating training materials for {customer_name} ({customer_id})"
        )

        # Mock training materials that mirror what the UC agent would produce
        materials = (
            f"# Onboarding Guide for {customer_name}\n\n"
            f"## Getting Started\n"
            f"Welcome to our platform! This guide will walk you through everything "
            f"you need to know to get the most out of your integration.\n\n"
            f"## 1. API Authentication\n"
            f"- Your API credentials are available in the dashboard under Settings > API Keys.\n"
            f"- Use Bearer token authentication for all API requests.\n"
            f"- Rotate your keys every 90 days for security.\n\n"
            f"## 2. Webhook Configuration\n"
            f"- Configure your webhook endpoint in Settings > Integrations > Webhooks.\n"
            f"- We send JSON payloads with a signature header for verification.\n"
            f"- Test your webhook using our sandbox environment before going live.\n\n"
            f"## 3. Dashboard Navigation\n"
            f"- The main dashboard shows real-time metrics for your integration.\n"
            f"- Use the Analytics tab for historical data and trend analysis.\n"
            f"- Set up custom alerts under Monitoring > Alert Rules.\n\n"
            f"## 4. Best Practices\n"
            f"- Implement retry logic with exponential backoff for webhook delivery.\n"
            f"- Use idempotency keys for all mutation API calls.\n"
            f"- Monitor your integration health via the status endpoint.\n\n"
            f"## Support\n"
            f"Your dedicated onboarding concierge is available for any questions.\n"
            f"Contact us at onboarding@example.com or via the in-app chat.\n"
        )

        return materials

    @activity.defn
    async def send_checkin(
        self, customer_id: str, milestone_id: str, message: str
    ) -> dict:
        """Send a check-in email to the customer."""
        activity.logger.info(
            f"Sending check-in for milestone {milestone_id} to customer {customer_id}"
        )

        result = await self.email.send_email(
            to=f"{customer_id}@customer.example.com",
            subject=f"Onboarding Check-In: Milestone {milestone_id}",
            body=message,
        )

        return {
            "customer_id": customer_id,
            "milestone_id": milestone_id,
            "message_id": result.get("message_id"),
            "status": result.get("status"),
            "sent_at": datetime.now(timezone.utc).isoformat(),
        }

    @activity.defn
    async def create_support_ticket(
        self, customer_id: str, title: str, description: str
    ) -> dict:
        """Create a Zendesk ticket for the engineering team."""
        activity.logger.info(
            f"Creating support ticket for customer {customer_id}: {title}"
        )

        ticket_id = f"TKT-{uuid.uuid4().hex[:6].upper()}"

        # In a real implementation, this would create a ticket via the Zendesk API
        return {
            "ticket_id": ticket_id,
            "customer_id": customer_id,
            "title": title,
            "description": description,
            "status": "open",
            "priority": "high",
            "tags": ["onboarding", "integration", "auto-created"],
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

    @activity.defn
    async def analyze_progress(
        self, customer_id: str, milestones: list[dict]
    ) -> str:
        """UC agent generates a status report based on milestone data.

        In a real implementation, the UC agent would analyse the milestone
        data and generate an intelligent summary with recommendations.
        """
        activity.logger.info(
            f"Analysing onboarding progress for customer {customer_id}"
        )

        completed = [m for m in milestones if m.get("status") == "completed"]
        failed = [m for m in milestones if m.get("status") == "failed"]
        pending = [m for m in milestones if m.get("status") in ("pending", "in_progress")]

        report_lines = [
            f"Onboarding Progress Report for {customer_id}",
            f"{'=' * 50}",
            f"Completed: {len(completed)}/{len(milestones)} milestones",
        ]

        if completed:
            report_lines.append("\nCompleted Milestones:")
            for m in completed:
                report_lines.append(f"  [DONE] {m.get('title', 'Unknown')}")

        if failed:
            report_lines.append("\nFailed Milestones (require attention):")
            for m in failed:
                report_lines.append(f"  [FAIL] {m.get('title', 'Unknown')}")

        if pending:
            report_lines.append("\nUpcoming Milestones:")
            for m in pending:
                report_lines.append(
                    f"  [TODO] Day {m.get('scheduled_day', '?')}: {m.get('title', 'Unknown')}"
                )

        if failed:
            report_lines.append(
                "\nRecommendation: Address failed milestones before proceeding. "
                "Consider scheduling a call with the customer to troubleshoot."
            )
        elif len(completed) == len(milestones):
            report_lines.append(
                "\nAll milestones completed successfully. "
                "Customer is ready for final review and handoff."
            )
        else:
            report_lines.append(
                "\nOnboarding is on track. Continue with the scheduled plan."
            )

        return "\n".join(report_lines)
