from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from temporalio import activity

from .data_types import IntegrationTestResult
from common.services.base import (
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

    # Optional: customer fixture data loaded at startup for richer output
    customer_data: dict | None = None

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

        now = datetime.now(timezone.utc).isoformat()

        # Pull from customer fixture data if available
        cdata = self.customer_data or {}
        contact = cdata.get("contact", {})
        service_plan = cdata.get("service_plan", {})
        tier = cdata.get("tier", "enterprise")
        billing_status = cdata.get("billing_status", "active")
        integrations = cdata.get("integrations", [])

        # Detailed verification checks
        checks = {
            "billing_active": {
                "passed": billing_status == "active",
                "detail": (
                    f"Billing status: {billing_status} | "
                    f"Plan: {service_plan.get('plan_name', 'Enterprise Pro')}"
                ),
            },
            "api_keys_configured": {
                "passed": True,
                "detail": (
                    f"API rate limit: {service_plan.get('api_rate_limit', 10000):,} req/hr | "
                    f"Webhook endpoints: {service_plan.get('webhook_endpoints', 5)} allocated"
                ),
            },
            "admin_users_setup": {
                "passed": bool(contact.get("primary_email")),
                "detail": (
                    f"Primary admin: {contact.get('primary_name', 'Jane Doe')} "
                    f"<{contact.get('primary_email', 'admin@customer.com')}> | "
                    f"Timezone: {contact.get('timezone', 'UTC')}"
                ),
            },
            "contact_info_complete": {
                "passed": True,
                "detail": (
                    f"Phone: {contact.get('phone', '+1-555-0100')} | "
                    f"Email verified: Yes"
                ),
            },
            "service_tier_confirmed": {
                "passed": True,
                "detail": (
                    f"Tier: {tier} | "
                    f"Support level: {service_plan.get('support_level', 'dedicated')} | "
                    f"SLA response: {service_plan.get('sla_response_hours', 4)}h"
                ),
            },
            "integrations_registered": {
                "passed": len(integrations) > 0,
                "detail": (
                    f"{len(integrations)} integration(s) registered: "
                    + ", ".join(
                        f"{i.get('integration_id', '?')} ({i.get('type', '?')})"
                        for i in integrations[:3]
                    )
                    if integrations
                    else "No integrations registered yet"
                ),
            },
            "prerequisites_met": {
                "passed": True,
                "detail": "All onboarding prerequisites satisfied",
            },
        }

        all_passed = all(c["passed"] for c in checks.values())
        missing = [name for name, c in checks.items() if not c["passed"]]

        recommendations = []
        if not all_passed:
            for name in missing:
                if name == "billing_active":
                    recommendations.append(
                        "Activate billing before proceeding with onboarding."
                    )
                elif name == "integrations_registered":
                    recommendations.append(
                        "Register at least one integration endpoint in the dashboard."
                    )
                else:
                    recommendations.append(
                        f"Resolve issue with: {name.replace('_', ' ')}"
                    )
        else:
            recommendations.append(
                "All checks passed. Ready to proceed with integration testing."
            )

        return {
            "customer_id": customer_id,
            "verified": all_passed,
            "checks": checks,
            "summary": {
                "total_checks": len(checks),
                "passed": sum(1 for c in checks.values() if c["passed"]),
                "failed": sum(1 for c in checks.values() if not c["passed"]),
            },
            "missing": missing,
            "recommendations": recommendations,
            "verified_at": now,
            "notes": (
                "All account verification checks passed successfully."
                if all_passed
                else f"{len(missing)} check(s) require attention."
            ),
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
        auth_type = integration_config.get("auth_type", "bearer_token")

        # Test the endpoint using the integration test service
        test_result = await self.integration_test.test_endpoint(
            url=endpoint_url, method=method
        )

        response_status = test_result.get("response_status")
        response_time_ms = test_result.get("response_time_ms")

        if test_result.get("success"):
            # Build a rich diagnostic report even on success
            diagnostic = await self.integration_test.run_diagnostics(integration_id)
            checks = diagnostic.get("checks", [])

            diagnostic_summary = {
                "test_id": test_result.get("test_id", "unknown"),
                "endpoint": endpoint_url,
                "method": method,
                "auth_type": auth_type,
                "response_code": response_status,
                "latency_ms": response_time_ms,
                "diagnostic_checks": {
                    c["check"]: {
                        "status": c["status"],
                        "detail": c["detail"],
                    }
                    for c in checks
                },
                "overall": "HEALTHY",
                "tested_at": test_result.get("tested_at"),
            }

            return IntegrationTestResult(
                integration_id=integration_id,
                endpoint_url=endpoint_url,
                test_passed=True,
                response_status=response_status,
                response_time_ms=response_time_ms,
                diagnostic_report=json.dumps(diagnostic_summary, indent=2, default=str),
            )

        # Test failed -- run diagnostics
        diagnostic = await self.integration_test.run_diagnostics(integration_id)

        # Build detailed diagnostic checks
        check_results = []
        for c in diagnostic.get("checks", []):
            check_results.append({
                "check": c.get("check"),
                "status": c.get("status"),
                "detail": c.get("detail"),
            })

        failed_checks = [c for c in check_results if c.get("status") != "passed"]

        # Build a comprehensive error report
        error_report = {
            "test_id": test_result.get("test_id", "unknown"),
            "endpoint": endpoint_url,
            "method": method,
            "auth_type": auth_type,
            "error": test_result.get("error"),
            "response_code": response_status,
            "latency_ms": response_time_ms,
            "diagnostic_checks": check_results,
            "failed_checks_count": len(failed_checks),
            "overall": "UNHEALTHY",
            "tested_at": test_result.get("tested_at"),
        }

        # Build troubleshooting suggestions based on the failure
        error_msg = test_result.get("error", "")
        suggestions = []

        if "timeout" in error_msg.lower():
            suggestions.extend([
                "Check that the endpoint is reachable from our infrastructure.",
                "Verify firewall rules allow inbound traffic on the endpoint port.",
                "Consider increasing the endpoint's request timeout threshold.",
                "Check for DNS resolution delays or misconfigurations.",
            ])
        elif "500" in str(response_status) or "500" in error_msg:
            suggestions.extend([
                "The endpoint returned a server error (HTTP 500).",
                "Check the application logs on the customer's endpoint server.",
                "Verify the request payload matches the expected schema.",
                "Check upstream dependencies the endpoint relies on.",
            ])
        elif "connection refused" in error_msg.lower():
            suggestions.extend([
                "The endpoint host refused the connection.",
                "Verify the endpoint URL and port are correct.",
                "Check that the service is running and accepting connections.",
                "Verify security group and firewall configurations.",
            ])
        elif "schema" in error_msg.lower() or "invalid_response" in error_msg.lower():
            suggestions.extend([
                "The endpoint response did not match the expected JSON schema.",
                "Verify the response Content-Type header is application/json.",
                "Check that the response body structure matches our API spec.",
                "Test the endpoint manually with a sample payload.",
            ])
        else:
            suggestions.extend([
                "Verify the endpoint URL is correct and accessible.",
                "Check that authentication credentials are valid.",
                "Ensure the endpoint accepts the expected request format.",
            ])

        if failed_checks:
            for c in failed_checks:
                suggestions.append(
                    f"Diagnostic '{c['check']}': {c.get('detail', 'Check failed')}"
                )

        return IntegrationTestResult(
            integration_id=integration_id,
            endpoint_url=endpoint_url,
            test_passed=False,
            response_status=response_status,
            response_time_ms=response_time_ms,
            error_message=test_result.get("error"),
            diagnostic_report=json.dumps(error_report, indent=2, default=str),
            suggestions=suggestions,
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

        # Pull integration details from customer data if available
        cdata = self.customer_data or {}
        integrations = cdata.get("integrations", [])
        service_plan = cdata.get("service_plan", {})
        contact = cdata.get("contact", {})

        int_names = ", ".join(
            f"`{i.get('integration_id', '?')}`" for i in integrations
        ) if integrations else "`int-webhook-01`"

        rate_limit = service_plan.get("api_rate_limit", 10000)
        plan_name = service_plan.get("plan_name", "Enterprise Pro")
        primary_name = contact.get("primary_name", "Team")

        materials = f"""# Personalized Onboarding Guide for {customer_name}

> Prepared for: {primary_name} | Plan: {plan_name} | Customer ID: {customer_id}
> Integrations: {int_names}

---

## 1. Getting Started

Welcome to the platform, {primary_name}! This guide is tailored specifically for
{customer_name}'s {plan_name} deployment. Below you will find everything you need
to get your integrations running smoothly.

**Your Onboarding Timeline:**
| Day  | Milestone                  | Status    |
|------|----------------------------|-----------|
| 1    | Account Verification       | Complete  |
| 3    | Integration Setup & Test   | Complete  |
| 5    | Training (this guide)      | Current   |
| 10   | Mid-Onboarding Check-In    | Upcoming  |
| 14   | Final Review & Handoff     | Upcoming  |

---

## 2. API Authentication

Your API credentials are available in the dashboard under **Settings > API Keys**.

```
# Example: Authenticating with Bearer Token
curl -X POST https://api.platform.io/v2/events \\
  -H "Authorization: Bearer YOUR_API_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{{"event": "test", "timestamp": "2026-03-06T00:00:00Z"}}'
```

**Key details for {customer_name}:**
- **Rate limit:** {rate_limit:,} requests/hour (Enterprise tier)
- **Auth method:** Bearer token (recommended) or API key header
- **Key rotation:** Rotate every 90 days; both old and new keys work during a
  24-hour grace period

**Security best practices:**
- Store API keys in environment variables or a secrets manager (never in code)
- Use separate keys for development, staging, and production
- Enable IP allowlisting in **Settings > Security > IP Restrictions**

---

## 3. Webhook Configuration

Configure your webhook endpoints in **Settings > Integrations > Webhooks**.

**Webhook signature verification (recommended):**
```python
import hmac, hashlib

def verify_signature(payload: bytes, signature: str, secret: str) -> bool:
    expected = hmac.new(
        secret.encode(), payload, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(f"sha256={{expected}}", signature)
```

**Your registered webhooks:**
"""

        for integ in integrations:
            if integ.get("type") == "webhook":
                materials += f"""
| Field       | Value                                    |
|-------------|------------------------------------------|
| ID          | `{integ.get('integration_id', 'N/A')}`   |
| Endpoint    | `{integ.get('endpoint_url', 'N/A')}`     |
| Method      | {integ.get('method', 'POST')}            |
| Auth        | {integ.get('auth_type', 'bearer_token')} |
| Status      | Verified                                 |
"""

        if not any(i.get("type") == "webhook" for i in integrations):
            materials += """
| Field       | Value                                    |
|-------------|------------------------------------------|
| ID          | `int-webhook-01`                         |
| Endpoint    | `https://api.customer.example.com/hook`  |
| Method      | POST                                     |
| Auth        | bearer_token                             |
| Status      | Pending setup                            |
"""

        materials += f"""
**Webhook payload format:**
```json
{{
  "event_type": "ticket.updated",
  "event_id": "evt_a1b2c3d4",
  "timestamp": "2026-03-06T12:00:00Z",
  "data": {{
    "ticket_id": "TKT-12345",
    "status": "resolved",
    "updated_fields": ["status", "assignee"]
  }},
  "signature": "sha256=..."
}}
```

---

## 4. Testing Your Integration

Use our sandbox environment to test without affecting production data.

**Sandbox endpoint:** `https://sandbox.platform.io/v2/`

**Automated test command:**
```bash
curl -X POST https://sandbox.platform.io/v2/test \\
  -H "Authorization: Bearer YOUR_SANDBOX_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{{"integration_id": "{integrations[0].get("integration_id", "int-test") if integrations else "int-test"}"}}'
```

**What the test validates:**
1. DNS resolution and TLS certificate validity
2. Endpoint connectivity and response time (< 5s threshold)
3. Authentication handshake
4. Response format (JSON schema compliance)
5. Retry behavior under simulated failures

---

## 5. Dashboard & Monitoring

- **Real-time metrics:** Main dashboard shows request volume, error rates, and p99 latency
- **Integration health:** **Integrations > Health** shows per-endpoint status
- **Custom alerts:** Set up in **Monitoring > Alert Rules**
  - Recommended: alert on error rate > 5% or p99 > 2000ms
- **Audit log:** All API calls are logged under **Settings > Audit Log**

---

## 6. Best Practices for {customer_name}

1. **Implement retry logic** with exponential backoff (base 2s, max 60s, 5 retries)
2. **Use idempotency keys** for all mutation API calls to prevent duplicates
3. **Monitor integration health** via the `/v2/status` endpoint
4. **Set up alerting** for webhook delivery failures (> 3 consecutive failures)
5. **Rate limit awareness:** Your plan allows {rate_limit:,} req/hr; implement
   client-side throttling at 80% capacity ({int(rate_limit * 0.8):,} req/hr)
6. **Versioned endpoints:** Always pin to a specific API version (currently v2)

---

## Support & Next Steps

Your dedicated onboarding concierge is monitoring your progress and will check
in on **Day 10** for a mid-onboarding review.

- **Onboarding concierge:** Available via in-app chat or onboarding@platform.io
- **Emergency support:** {service_plan.get('support_level', 'dedicated')} support,
  {service_plan.get('sla_response_hours', 4)}-hour SLA response time
- **Documentation:** https://docs.platform.io/
- **API reference:** https://api.platform.io/docs/

---
*Generated on {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} for {customer_name} ({customer_id})*
"""

        return materials

    @activity.defn
    async def send_checkin(
        self, customer_id: str, milestone_id: str, message: str
    ) -> dict:
        """Send a check-in email to the customer."""
        activity.logger.info(
            f"Sending check-in for milestone {milestone_id} to customer {customer_id}"
        )

        cdata = self.customer_data or {}
        contact = cdata.get("contact", {})
        to_email = contact.get(
            "primary_email", f"{customer_id}@customer.example.com"
        )
        to_name = contact.get("primary_name", "Customer")

        subject = f"Onboarding Check-In: {milestone_id}"

        result = await self.email.send_email(
            to=to_email,
            subject=subject,
            body=message,
        )

        now = datetime.now(timezone.utc)

        # Build a preview of the message (first 200 chars)
        preview = message[:200].replace("\n", " ").strip()
        if len(message) > 200:
            preview += "..."

        return {
            "customer_id": customer_id,
            "milestone_id": milestone_id,
            "delivery": {
                "message_id": result.get("message_id"),
                "status": result.get("status", "delivered"),
                "to": to_email,
                "to_name": to_name,
                "subject": subject,
                "sent_at": now.isoformat(),
            },
            "message_preview": preview,
            "message_length": len(message),
            "status": result.get("status", "delivered"),
            "sent_at": now.isoformat(),
        }

    @activity.defn
    async def create_support_ticket(
        self, customer_id: str, title: str, description: str
    ) -> dict:
        """Create a Zendesk ticket for the engineering team.

        Produces rich output showing the ticket that would be created,
        and attempts to use the zendesk service to record it.
        """
        activity.logger.info(
            f"Creating support ticket for customer {customer_id}: {title}"
        )

        ticket_id = f"TKT-{uuid.uuid4().hex[:6].upper()}"
        now = datetime.now(timezone.utc)

        cdata = self.customer_data or {}
        contact = cdata.get("contact", {})
        tier = cdata.get("tier", "enterprise")
        service_plan = cdata.get("service_plan", {})
        sla_hours = service_plan.get("sla_response_hours", 4)

        # Determine priority based on tier
        priority_map = {
            "enterprise": "urgent",
            "premium": "high",
            "standard": "normal",
        }
        priority = priority_map.get(tier, "normal")

        # Build the ticket data
        ticket_data = {
            "ticket_id": ticket_id,
            "customer_id": customer_id,
            "requester": {
                "name": contact.get("primary_name", "Unknown"),
                "email": contact.get("primary_email", f"{customer_id}@customer.example.com"),
            },
            "title": title,
            "description": description,
            "status": "open",
            "priority": priority,
            "group": "engineering-integrations",
            "assignee": cdata.get("assigned_concierge", "auto-assign"),
            "tags": [
                "onboarding",
                "integration",
                "auto-created",
                tier,
                f"sla-{sla_hours}h",
            ],
            "sla": {
                "response_deadline": (
                    now + timedelta(hours=sla_hours)
                ).isoformat(),
                "tier": tier,
                "response_hours": sla_hours,
            },
            "custom_fields": {
                "customer_tier": tier,
                "onboarding_phase": "integration_setup",
                "auto_created": True,
            },
            "created_at": now.isoformat(),
        }

        # Try to record via zendesk service (add_comment to an existing ticket
        # as a record, since the protocol doesn't have create_ticket)
        try:
            existing_tickets = await self.zendesk.list_tickets(
                {"requester.email": contact.get("primary_email")}
            )
            if existing_tickets:
                await self.zendesk.add_comment(
                    existing_tickets[0].get("id", ""),
                    f"[Auto-created support ticket {ticket_id}]\n\n"
                    f"Title: {title}\n\n{description}",
                    public=False,
                )
        except Exception:
            # If zendesk service fails, we still return the ticket data
            pass

        return ticket_data

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

        cdata = self.customer_data or {}
        customer_name = cdata.get("customer_name", customer_id)
        tier = cdata.get("tier", "enterprise")
        contact = cdata.get("contact", {})

        completed = [m for m in milestones if m.get("status") == "completed"]
        failed = [m for m in milestones if m.get("status") == "failed"]
        pending = [m for m in milestones if m.get("status") in ("pending", "in_progress")]
        skipped = [m for m in milestones if m.get("status") == "skipped"]

        total = len(milestones)
        pct = (len(completed) / total * 100) if total > 0 else 0

        report_lines = [
            f"ONBOARDING PROGRESS REPORT",
            f"{'=' * 60}",
            f"Customer:    {customer_name} ({customer_id})",
            f"Tier:        {tier.upper()}",
            f"Contact:     {contact.get('primary_name', 'N/A')} "
            f"<{contact.get('primary_email', 'N/A')}>",
            f"Report Date: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
            f"{'=' * 60}",
            f"",
            f"OVERALL PROGRESS: {len(completed)}/{total} milestones "
            f"({pct:.0f}% complete)",
            f"",
        ]

        # Progress bar
        bar_len = 40
        filled = int(bar_len * pct / 100)
        bar = "#" * filled + "-" * (bar_len - filled)
        report_lines.append(f"  [{bar}] {pct:.0f}%")
        report_lines.append("")

        # Milestone details
        report_lines.append("MILESTONE STATUS:")
        report_lines.append("-" * 60)

        status_icons = {
            "completed": "DONE",
            "failed": "FAIL",
            "in_progress": "ACTV",
            "pending": "TODO",
            "skipped": "SKIP",
        }

        for m in milestones:
            status = m.get("status", "pending")
            icon = status_icons.get(status, "????")
            day = m.get("scheduled_day", "?")
            title = m.get("title", "Unknown")

            line = f"  [{icon}] Day {day:>2}: {title}"

            # Add timing info if available
            started = m.get("started_at")
            completed_at = m.get("completed_at")
            if started and completed_at:
                line += f" (completed)"
            elif started:
                line += f" (in progress)"

            report_lines.append(line)

            # Show result details for completed milestones
            result_data = m.get("result_data", {})
            if status == "completed" and result_data:
                if "checks" in result_data:
                    summary = result_data.get("summary", {})
                    report_lines.append(
                        f"           Verification: {summary.get('passed', '?')}/"
                        f"{summary.get('total_checks', '?')} checks passed"
                    )
                elif "test_passed" in result_data:
                    passed = result_data.get("test_passed")
                    latency = result_data.get("response_time_ms")
                    report_lines.append(
                        f"           Integration: {'PASS' if passed else 'FAIL'}"
                        + (f" | Latency: {latency:.0f}ms" if latency else "")
                    )
                elif "materials_sent" in result_data:
                    response = result_data.get("customer_response")
                    score = result_data.get("satisfaction_score")
                    report_lines.append(
                        f"           Training delivered"
                        + (f" | Customer: \"{response}\"" if response else "")
                        + (f" | Satisfaction: {score}/5" if score else "")
                    )

            # Show error details for failed milestones
            if status == "failed":
                error = result_data.get("error_message") or result_data.get("error", "")
                if error:
                    report_lines.append(f"           Error: {error[:80]}")

        report_lines.append("")
        report_lines.append("-" * 60)

        # Blockers section
        if failed:
            report_lines.append("")
            report_lines.append("BLOCKERS IDENTIFIED:")
            for m in failed:
                report_lines.append(f"  [!] {m.get('title', 'Unknown')}")
                result_data = m.get("result_data", {})
                suggestions = result_data.get("suggestions", [])
                if suggestions:
                    for s in suggestions[:3]:
                        report_lines.append(f"      - {s}")
                else:
                    report_lines.append(
                        f"      - Review failure details and retry or escalate"
                    )

        # Next actions
        report_lines.append("")
        report_lines.append("NEXT ACTIONS:")
        if failed:
            report_lines.append(
                "  1. Address failed milestones before proceeding"
            )
            report_lines.append(
                "  2. Schedule a troubleshooting call with the customer"
            )
            report_lines.append(
                "  3. Engage engineering support if integration issues persist"
            )
        elif len(completed) == total:
            report_lines.append(
                "  1. All milestones completed successfully!"
            )
            report_lines.append(
                "  2. Customer is ready for production handoff"
            )
            report_lines.append(
                "  3. Schedule post-onboarding review in 30 days"
            )
            report_lines.append(
                "  4. Transfer to ongoing account management team"
            )
        else:
            next_pending = next((m for m in milestones if m.get("status") in ("pending", "in_progress")), None)
            if next_pending:
                report_lines.append(
                    f"  1. Next milestone: Day {next_pending.get('scheduled_day', '?')} - "
                    f"{next_pending.get('title', 'Unknown')}"
                )
            report_lines.append(
                "  2. Continue with scheduled onboarding plan"
            )
            if pct >= 50:
                report_lines.append(
                    "  3. Onboarding is on track - no intervention needed"
                )

        report_lines.append("")
        report_lines.append(f"{'=' * 60}")
        report_lines.append(
            f"End of report | Generated by Onboarding Concierge Agent"
        )

        return "\n".join(report_lines)
