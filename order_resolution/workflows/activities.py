from __future__ import annotations

import re
from dataclasses import dataclass

from temporalio import activity

from .data_types import (
    ExtractedIntent,
    InvestigationResult,
    ResolutionPlan,
    ResolutionStep,
    StepResult,
)
from ...common.services.base import (
    OrderDBService,
    PaymentService,
    ShippingService,
    ZendeskService,
)


@dataclass
class OrderResolutionActivities:
    """Activity implementations that use UC agents for intelligent processing."""

    zendesk: ZendeskService
    order_db: OrderDBService
    shipping: ShippingService
    payment: PaymentService

    @activity.defn
    async def classify_and_extract(self, ticket_id: str) -> ExtractedIntent:
        """UC agent reads the ticket and extracts issues + intents."""
        ticket = await self.zendesk.get_ticket(ticket_id)

        # In a real implementation, this would use:
        # agent = create_order_resolution_agent(services=...)
        # client = UnixLocalSandboxClient()
        # async with agent.start(client=client, client_options=None) as task:
        #     result = await run_agent_to_completion(task, prompt)

        # For the demo, we parse the ticket intelligently using mock logic
        # that demonstrates the data flow. Replace with UC agent call.
        activity.logger.info(f"Classifying ticket {ticket_id}")

        # Mock extraction that mirrors what the UC agent would produce
        from .data_types import ExtractedIssue, IntentType, IssueType

        description = ticket.get("description", "")
        issues: list[ExtractedIssue] = []
        intents: list[IntentType] = []

        # Simple heuristic extraction (UC agent would do this properly)
        if "wrong" in description.lower():
            issues.append(
                ExtractedIssue(
                    issue_type=IssueType.WRONG_ITEM,
                    description="Customer received wrong item",
                )
            )
            intents.append(IntentType.REPLACEMENT)
        if "damaged" in description.lower() or "broken" in description.lower():
            issues.append(
                ExtractedIssue(
                    issue_type=IssueType.DAMAGED_ITEM,
                    description="Customer received damaged item",
                )
            )
            intents.append(IntentType.REFUND)
        if "overcharge" in description.lower() or "charged" in description.lower():
            issues.append(
                ExtractedIssue(
                    issue_type=IssueType.OVERCHARGE,
                    description="Customer was overcharged",
                )
            )
            intents.append(IntentType.REFUND)

        return ExtractedIntent(
            issues=issues,
            intents=intents,
            customer_sentiment="frustrated",
            urgency="high",
            summary=f"Customer reports {len(issues)} issue(s) requiring resolution",
        )

    @activity.defn
    async def investigate(
        self, ticket_id: str, intent: ExtractedIntent
    ) -> InvestigationResult:
        """UC agent queries order/shipping/payment systems to gather evidence."""
        activity.logger.info(f"Investigating ticket {ticket_id}")
        ticket = await self.zendesk.get_ticket(ticket_id)

        # Extract order ID from ticket
        order_id = _extract_order_id(ticket.get("description", ""))

        order_details = await self.order_db.get_order(order_id) if order_id else {}
        shipping_details = (
            await self.shipping.get_shipment_by_order(order_id) if order_id else {}
        )

        # Get payment transaction
        transaction_id = f"TXN-{order_id}" if order_id else ""
        payment_details = (
            await self.payment.get_transaction(transaction_id)
            if transaction_id
            else {}
        )

        findings: list[str] = []
        discrepancies: list[str] = []

        # Check for overcharge
        if order_details:
            charged = order_details.get("charged_total", 0)
            subtotal = order_details.get("subtotal", 0)
            if charged > subtotal:
                discrepancies.append(
                    f"Overcharge detected: charged ${charged} vs actual ${subtotal} "
                    f"(difference: ${charged - subtotal})"
                )

        return InvestigationResult(
            order_details=order_details,
            shipping_details=shipping_details,
            payment_details=payment_details,
            findings=findings,
            discrepancies=discrepancies,
        )

    @activity.defn
    async def plan_resolution(
        self, intent: ExtractedIntent, investigation: InvestigationResult
    ) -> ResolutionPlan:
        """UC agent generates a resolution plan based on findings."""
        activity.logger.info("Planning resolution")

        steps: list[ResolutionStep] = []
        order_id = investigation.order_details.get("order_id", "")

        for issue in intent.issues:
            if issue.issue_type.value == "wrong_item":
                steps.append(
                    ResolutionStep(
                        step_id=f"step-{len(steps) + 1}",
                        action="replacement",
                        description=(
                            f"Send replacement {issue.item_name or issue.item_id or 'item'} "
                            f"in correct specification"
                        ),
                        estimated_cost=0.0,
                        requires_approval=False,
                        params={
                            "item_id": issue.item_id or "",
                            "original_order_id": order_id,
                        },
                    )
                )
                steps.append(
                    ResolutionStep(
                        step_id=f"step-{len(steps) + 1}",
                        action="return_label",
                        description=(
                            f"Create return label for wrong "
                            f"{issue.item_name or issue.item_id or 'item'}"
                        ),
                        estimated_cost=0.0,
                        requires_approval=False,
                        params={
                            "item_id": issue.item_id or "",
                            "order_id": order_id,
                        },
                    )
                )
            elif issue.issue_type.value == "damaged_item":
                item_price = _find_item_price(
                    investigation.order_details, issue.item_id
                )
                steps.append(
                    ResolutionStep(
                        step_id=f"step-{len(steps) + 1}",
                        action="refund",
                        description=(
                            f"Refund for damaged "
                            f"{issue.item_name or issue.item_id or 'item'}: ${item_price}"
                        ),
                        estimated_cost=item_price,
                        requires_approval=item_price > 50.0,
                        params={
                            "amount": item_price,
                            "transaction_id": f"TXN-{order_id}",
                            "reason": "damaged_item",
                        },
                    )
                )
            elif issue.issue_type.value == "overcharge":
                overcharge = _calculate_overcharge(investigation.order_details)
                if overcharge > 0:
                    steps.append(
                        ResolutionStep(
                            step_id=f"step-{len(steps) + 1}",
                            action="refund",
                            description=f"Refund overcharge: ${overcharge}",
                            estimated_cost=overcharge,
                            requires_approval=False,
                            params={
                                "amount": overcharge,
                                "transaction_id": f"TXN-{order_id}",
                                "reason": "overcharge",
                            },
                        )
                    )

        total_cost = sum(s.estimated_cost for s in steps)
        needs_approval = total_cost > 50.0 or any(s.requires_approval for s in steps)

        return ResolutionPlan(
            steps=steps,
            total_estimated_cost=total_cost,
            reasoning=(
                f"Resolution for {len(intent.issues)} issues with total estimated "
                f"cost of ${total_cost:.2f}"
            ),
            requires_human_approval=needs_approval,
        )

    @activity.defn
    async def execute_step(self, step: ResolutionStep) -> StepResult:
        """Execute a single resolution step."""
        activity.logger.info(f"Executing step {step.step_id}: {step.action}")

        try:
            if step.action == "refund":
                result = await self.payment.process_refund(
                    step.params["transaction_id"],
                    step.params["amount"],
                    step.params.get("reason", "customer_request"),
                )
                return StepResult(
                    step_id=step.step_id,
                    success=True,
                    action=step.action,
                    result_data=result,
                    compensation_data={
                        "refund_id": result.get("refund_id"),
                        "action": "reverse_refund",
                    },
                )
            elif step.action == "replacement":
                result = await self.order_db.create_replacement_order(
                    step.params["original_order_id"],
                    [{"item_id": step.params["item_id"]}],
                )
                return StepResult(
                    step_id=step.step_id,
                    success=True,
                    action=step.action,
                    result_data=result,
                    compensation_data=None,
                )
            elif step.action == "return_label":
                result = await self.shipping.create_return_label(
                    step.params["order_id"],
                    [{"item_id": step.params["item_id"]}],
                )
                return StepResult(
                    step_id=step.step_id,
                    success=True,
                    action=step.action,
                    result_data=result,
                )
            else:
                return StepResult(
                    step_id=step.step_id,
                    success=False,
                    action=step.action,
                    error_message=f"Unknown action: {step.action}",
                )
        except Exception as e:
            return StepResult(
                step_id=step.step_id,
                success=False,
                action=step.action,
                error_message=str(e),
            )

    @activity.defn
    async def compensate_step(self, step_result: StepResult) -> bool:
        """Reverse a completed step (saga compensation)."""
        activity.logger.info(f"Compensating step {step_result.step_id}")

        if not step_result.compensation_data:
            activity.logger.info(
                f"No compensation needed for {step_result.step_id}"
            )
            return True

        comp = step_result.compensation_data
        if comp.get("action") == "reverse_refund" and comp.get("refund_id"):
            await self.payment.reverse_refund(comp["refund_id"])
            return True

        return False

    @activity.defn
    async def verify_and_summarize(
        self, ticket_id: str, completed_steps: list[StepResult]
    ) -> str:
        """UC agent verifies all steps and writes customer-facing summary."""
        activity.logger.info(f"Verifying and summarizing for ticket {ticket_id}")

        actions_taken: list[str] = []
        for step in completed_steps:
            if step.success:
                actions_taken.append(f"- {step.action}: completed successfully")

        summary = (
            "Dear Customer,\n\n"
            "We've resolved the issues with your order. Here's what we've done:\n\n"
            + "\n".join(actions_taken)
            + "\n\nIf you have any further questions, please don't hesitate to reach out.\n"
            "Thank you for your patience!"
        )
        return summary


def _extract_order_id(description: str) -> str | None:
    """Extract order ID from ticket description."""
    match = re.search(r"(ORD-\d+)", description)
    return match.group(1) if match else None


def _find_item_price(order_details: dict, item_id: str | None) -> float:
    """Find item price in order details."""
    for item in order_details.get("items", []):
        if item.get("item_id") == item_id:
            return float(item.get("price", 0))
    return 0.0


def _calculate_overcharge(order_details: dict) -> float:
    """Calculate overcharge amount."""
    charged = float(order_details.get("charged_total", 0))
    subtotal = float(order_details.get("subtotal", 0))
    return max(0.0, charged - subtotal)
