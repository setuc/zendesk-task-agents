from __future__ import annotations

import json
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from universal_computer.agents.tools import FunctionTool


# ---------------------------------------------------------------------------
# GenerateReportTool
# ---------------------------------------------------------------------------

class GenerateReportArgs(BaseModel):
    customer_id: str = Field(description="The customer ID to generate the report for")
    report_type: str = Field(
        description="Type of report: 'onboarding_status', 'integration_summary', or 'final_review'"
    )
    data: dict = Field(
        default_factory=dict,
        description="Data to include in the report (milestones, test results, etc.)",
    )


class GenerateReportTool(FunctionTool[GenerateReportArgs, str]):
    tool_name = "generate_report"
    args_model = GenerateReportArgs
    description = (
        "Generate a formatted onboarding report for a customer. "
        "Supports onboarding_status, integration_summary, and final_review report types."
    )

    def run(self, args: GenerateReportArgs) -> str:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        if args.report_type == "onboarding_status":
            return _format_status_report(args.customer_id, args.data, now)
        elif args.report_type == "integration_summary":
            return _format_integration_report(args.customer_id, args.data, now)
        elif args.report_type == "final_review":
            return _format_final_review(args.customer_id, args.data, now)
        else:
            return (
                f"Report for customer {args.customer_id}\n"
                f"Type: {args.report_type}\n"
                f"Generated: {now}\n\n"
                f"Data:\n{json.dumps(args.data, indent=2, default=str)}"
            )


def _format_status_report(customer_id: str, data: dict, generated_at: str) -> str:
    milestones = data.get("milestones", [])
    completed = sum(1 for m in milestones if m.get("status") == "completed")
    total = len(milestones)
    current_stage = data.get("current_stage", "unknown")

    lines = [
        f"=== Onboarding Status Report ===",
        f"Customer: {customer_id}",
        f"Generated: {generated_at}",
        f"Current Stage: {current_stage}",
        f"Progress: {completed}/{total} milestones completed",
        "",
        "Milestones:",
    ]

    for m in milestones:
        status_marker = "[x]" if m.get("status") == "completed" else "[ ]"
        lines.append(f"  {status_marker} Day {m.get('scheduled_day', '?')}: {m.get('title', 'Unknown')}")

    return "\n".join(lines)


def _format_integration_report(customer_id: str, data: dict, generated_at: str) -> str:
    results = data.get("test_results", [])
    passed = sum(1 for r in results if r.get("test_passed"))
    total = len(results)

    lines = [
        f"=== Integration Summary Report ===",
        f"Customer: {customer_id}",
        f"Generated: {generated_at}",
        f"Tests Passed: {passed}/{total}",
        "",
        "Results:",
    ]

    for r in results:
        status = "PASS" if r.get("test_passed") else "FAIL"
        url = r.get("endpoint_url", "unknown")
        lines.append(f"  [{status}] {url}")
        if not r.get("test_passed") and r.get("error_message"):
            lines.append(f"         Error: {r['error_message']}")

    return "\n".join(lines)


def _format_final_review(customer_id: str, data: dict, generated_at: str) -> str:
    milestones = data.get("milestones", [])
    completed = sum(1 for m in milestones if m.get("status") == "completed")
    total = len(milestones)
    days_elapsed = data.get("days_elapsed", "N/A")

    lines = [
        f"=== Final Onboarding Review ===",
        f"Customer: {customer_id}",
        f"Generated: {generated_at}",
        f"Duration: {days_elapsed} days",
        f"Milestones Completed: {completed}/{total}",
        "",
        "Summary:",
    ]

    for m in milestones:
        status = m.get("status", "unknown").upper()
        lines.append(f"  [{status}] {m.get('title', 'Unknown')} (Day {m.get('scheduled_day', '?')})")

    lines.append("")
    if completed == total:
        lines.append("Outcome: ONBOARDING COMPLETE - All milestones achieved successfully.")
    else:
        lines.append(f"Outcome: ONBOARDING INCOMPLETE - {total - completed} milestone(s) remaining.")

    return "\n".join(lines)
