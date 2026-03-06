from __future__ import annotations

import random
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from temporalio import activity

from .data_types import AgentResolution, AgentStatus, MemoryHit, ToolCall
from common.services.base import (
    ZendeskService,
    OrderDBService,
    PaymentService,
    ShippingService,
)


# ---------------------------------------------------------------------------
# Keyword patterns for ticket classification
# ---------------------------------------------------------------------------

_BILLING_PATTERNS = re.compile(
    r"overcharg|billing discrepancy|invoice|charged.*\$|"
    r"price increase|renewal price|prorated refund|unexpected charge",
    re.IGNORECASE,
)
_DUPLICATE_CHARGE_PATTERNS = re.compile(
    r"duplicate charge|two charges|charged twice|double charge",
    re.IGNORECASE,
)
_WRONG_ITEM_PATTERNS = re.compile(
    r"wrong item|wrong (color|size|model|version|configuration)|"
    r"received.*wrong|damaged package|missing item",
    re.IGNORECASE,
)
_PASSWORD_PATTERNS = re.compile(
    r"password reset|cannot login|can't log ?in|locked out|"
    r"account locked|authentication",
    re.IGNORECASE,
)
_SHIPPING_PATTERNS = re.compile(
    r"not delivered|where is my order|shipping delay|tracking|"
    r"hasn't arrived|no delivery",
    re.IGNORECASE,
)
_TECHNICAL_PATTERNS = re.compile(
    r"api|webhook|endpoint|integration|500 error|502|503|504|"
    r"data sync|dashboard.*slow|upload fail|search.*incorrect|"
    r"timeout|error code",
    re.IGNORECASE,
)
_CRISIS_PATTERNS = re.compile(
    r"URGENT|CRITICAL|production down|outage|complete.*down|"
    r"P0|incident|hemorrhaging|standstill|unresponsive",
    re.IGNORECASE,
)
_LEGAL_MANAGER_PATTERNS = re.compile(
    r"legal|lawyer|attorney|lawsuit|manager|supervisor|"
    r"dissatisfied.*support|escalat|complaint|3rd time|third time|"
    r"repeated issue",
    re.IGNORECASE,
)
_FEATURE_PATTERNS = re.compile(
    r"feature request|suggestion|would be great if|it would help",
    re.IGNORECASE,
)


def _random_duration(low_ms: float = 5.0, high_ms: float = 15.0) -> float:
    """Return a realistic mock tool-call duration in milliseconds."""
    return round(random.uniform(low_ms, high_ms), 1)


def _extract_amount(text: str) -> tuple[float, float]:
    """Pull dollar amounts from ticket text for billing scenarios."""
    amounts = re.findall(r"\$([0-9,]+(?:\.[0-9]{2})?)", text)
    floats = [float(a.replace(",", "")) for a in amounts]
    if len(floats) >= 2:
        return max(floats), min(floats)  # charged, correct
    if len(floats) == 1:
        return floats[0], floats[0] * 0.8
    return 149.99, 99.99


def _extract_order_id(text: str, custom_fields: dict) -> str:
    """Pull order ID from ticket text or custom fields."""
    if "order_id" in custom_fields:
        return str(custom_fields["order_id"])
    match = re.search(r"(ORD-\d+)", text)
    return match.group(1) if match else "ORD-UNKNOWN"


def _extract_txn_ids(text: str) -> tuple[str, str]:
    """Pull transaction IDs from ticket text."""
    ids = re.findall(r"(TXN-[A-Z0-9]+)", text)
    if len(ids) >= 2:
        return ids[0], ids[1]
    if len(ids) == 1:
        return ids[0], "TXN-DUP"
    return "TXN-UNKNOWN-1", "TXN-UNKNOWN-2"


def _extract_endpoint(text: str) -> str:
    """Pull API endpoint from ticket text."""
    match = re.search(r"(/api/[^\s]+|https?://[^\s]+)", text)
    return match.group(0) if match else "/api/v2/unknown"


# ---------------------------------------------------------------------------
# Activities
# ---------------------------------------------------------------------------


@dataclass
class AgentTicketActivities:
    """Temporal activities for the per-ticket agent workflow.

    The primary activity is ``process_ticket``, which runs either a mock
    agent (smart pattern-matching) or a real UC agent against a ticket.
    """

    zendesk: ZendeskService
    order_db: OrderDBService
    payment: PaymentService
    shipping: ShippingService
    memory_store: dict = field(default_factory=dict)
    use_real_agent: bool = False

    # ------------------------------------------------------------------ #
    # Public activity                                                      #
    # ------------------------------------------------------------------ #

    @activity.defn
    async def process_ticket(self, ticket_id: str) -> AgentResolution:
        """Investigate and resolve (or escalate) a support ticket."""
        if self.use_real_agent:
            return await self._process_with_uc_agent(ticket_id)
        return await self._process_with_mock(ticket_id)

    # ------------------------------------------------------------------ #
    # Mock agent                                                           #
    # ------------------------------------------------------------------ #

    async def _process_with_mock(self, ticket_id: str) -> AgentResolution:
        """Smart mock agent that classifies the ticket and follows a
        realistic resolution path with tool calls, memory, and sandbox
        commands where appropriate."""
        start = time.monotonic()
        tool_calls: list[ToolCall] = []
        sandbox_commands: list[str] = []
        sandbox_output: str = ""
        memory_hit: MemoryHit | None = None
        memory_written: str = ""

        # -------------------------------------------------------------- #
        # Step 1: Read the ticket                                         #
        # -------------------------------------------------------------- #
        try:
            ticket = await self.zendesk.get_ticket(ticket_id)
        except KeyError:
            activity.logger.warning(
                f"Ticket {ticket_id} not found -- returning FAILED resolution"
            )
            return AgentResolution(
                ticket_id=ticket_id,
                status=AgentStatus.FAILED,
                resolution_summary=f"Ticket {ticket_id} not found in Zendesk.",
                processing_time_ms=round(
                    (time.monotonic() - start) * 1000, 1
                ),
            )

        tool_calls.append(
            ToolCall(
                tool_name="zendesk.get_ticket",
                args_summary=f"ticket_id={ticket_id}",
                result_summary=(
                    f"Retrieved ticket: {ticket.get('subject', '?')[:60]}"
                ),
                duration_ms=_random_duration(6, 12),
            )
        )

        subject = ticket.get("subject", "")
        description = ticket.get("description", "")
        tags = ticket.get("tags", [])
        custom_fields = ticket.get("custom_fields", {})
        customer = ticket.get("requester", {})
        customer_name = customer.get("name", "Unknown")
        full_text = f"{subject} {description}"

        # -------------------------------------------------------------- #
        # Step 2: Search memory for similar past tickets                  #
        # -------------------------------------------------------------- #
        memory_hit = self._search_memory(full_text, tags)
        if memory_hit:
            tool_calls.append(
                ToolCall(
                    tool_name="memory.search",
                    args_summary=f"query='{subject[:40]}...'",
                    result_summary=(
                        f"Found similar past ticket {memory_hit.matched_ticket_id}: "
                        f"{memory_hit.pattern}"
                    ),
                    duration_ms=_random_duration(3, 8),
                )
            )

        # -------------------------------------------------------------- #
        # Step 3: Route to the appropriate resolution path                #
        # -------------------------------------------------------------- #

        # --- Billing / overcharge ---
        if _DUPLICATE_CHARGE_PATTERNS.search(full_text):
            return self._resolve_duplicate_charge(
                ticket_id, ticket, full_text, tool_calls, memory_hit, start
            )

        if _BILLING_PATTERNS.search(full_text):
            return await self._resolve_billing(
                ticket_id, ticket, full_text, tool_calls, memory_hit, start
            )

        # --- Wrong item / damaged / missing ---
        if _WRONG_ITEM_PATTERNS.search(full_text):
            return await self._resolve_wrong_item(
                ticket_id, ticket, full_text, tool_calls, memory_hit, start
            )

        # --- Password / login / account lock ---
        if _PASSWORD_PATTERNS.search(full_text):
            return self._resolve_password(
                ticket_id, ticket, tool_calls, memory_hit, start
            )

        # --- Shipping delay ---
        if _SHIPPING_PATTERNS.search(full_text):
            return await self._resolve_shipping(
                ticket_id, ticket, full_text, tool_calls, memory_hit, start
            )

        # --- Crisis / production outage ---
        if _CRISIS_PATTERNS.search(full_text):
            return self._escalate_crisis(
                ticket_id, ticket, tool_calls, memory_hit, start
            )

        # --- Technical / API / webhook ---
        if _TECHNICAL_PATTERNS.search(full_text):
            return self._escalate_technical(
                ticket_id, ticket, full_text, tool_calls, memory_hit, start
            )

        # --- Legal / manager request / escalation complaint ---
        if _LEGAL_MANAGER_PATTERNS.search(full_text):
            return self._escalate_management(
                ticket_id, ticket, tool_calls, memory_hit, start
            )

        # --- Feature request ---
        if _FEATURE_PATTERNS.search(full_text):
            return self._escalate_feature(
                ticket_id, ticket, tool_calls, memory_hit, start
            )

        # --- Default: escalate to L2 ---
        return self._escalate_default(
            ticket_id, ticket, tool_calls, memory_hit, start
        )

    # ------------------------------------------------------------------ #
    # Resolution paths                                                     #
    # ------------------------------------------------------------------ #

    async def _resolve_billing(
        self,
        ticket_id: str,
        ticket: dict,
        full_text: str,
        tool_calls: list[ToolCall],
        memory_hit: MemoryHit | None,
        start: float,
    ) -> AgentResolution:
        charged, correct = _extract_amount(full_text)
        overcharge = round(charged - correct, 2)
        txn_id = f"TXN-{random.randint(100000, 999999)}"

        # get_transaction
        tool_calls.append(
            ToolCall(
                tool_name="payment.get_transaction",
                args_summary=f"transaction_id={txn_id}",
                result_summary=(
                    f"Transaction found: ${charged:.2f} charged on account"
                ),
                duration_ms=_random_duration(8, 14),
            )
        )

        # compare amounts
        tool_calls.append(
            ToolCall(
                tool_name="agent.compare_amounts",
                args_summary=f"charged=${charged:.2f}, expected=${correct:.2f}",
                result_summary=(
                    f"Overcharge detected: ${overcharge:.2f} difference"
                ),
                duration_ms=_random_duration(2, 5),
            )
        )

        # process_refund
        tool_calls.append(
            ToolCall(
                tool_name="payment.process_refund",
                args_summary=(
                    f"txn={txn_id}, amount=${overcharge:.2f}, "
                    f"reason='billing discrepancy'"
                ),
                result_summary=f"Refund of ${overcharge:.2f} initiated (3-5 business days)",
                duration_ms=_random_duration(10, 15),
            )
        )

        customer_name = ticket.get("requester", {}).get("name", "Customer")
        memory_key = f"billing_overcharge_{ticket_id}"
        self._write_memory(
            memory_key,
            f"Billing overcharge of ${overcharge:.2f} resolved via refund for {customer_name}",
        )
        tool_calls.append(
            ToolCall(
                tool_name="memory.write",
                args_summary=f"key={memory_key}",
                result_summary="Resolution pattern stored for future reference",
                duration_ms=_random_duration(3, 6),
            )
        )

        return AgentResolution(
            ticket_id=ticket_id,
            status=AgentStatus.RESOLVED,
            solvable=True,
            resolution_type="refund",
            resolution_summary=(
                f"Identified billing discrepancy: customer was charged ${charged:.2f} "
                f"instead of ${correct:.2f}. Initiated refund of ${overcharge:.2f} "
                f"to original payment method."
            ),
            tool_calls=tool_calls,
            memory_hit=memory_hit,
            memory_written=memory_key,
            customer_message=(
                f"Hi {customer_name},\n\n"
                f"Thank you for bringing this to our attention. We've confirmed the "
                f"billing discrepancy on your account -- you were charged ${charged:.2f} "
                f"instead of ${correct:.2f}.\n\n"
                f"We've initiated a refund of ${overcharge:.2f} to your original payment "
                f"method. Please allow 3-5 business days for this to appear on your "
                f"statement.\n\n"
                f"We sincerely apologize for the inconvenience. If you have any further "
                f"questions, please don't hesitate to reach out.\n\n"
                f"Best regards,\nSupport Team"
            ),
            processing_time_ms=round((time.monotonic() - start) * 1000, 1),
        )

    def _resolve_duplicate_charge(
        self,
        ticket_id: str,
        ticket: dict,
        full_text: str,
        tool_calls: list[ToolCall],
        memory_hit: MemoryHit | None,
        start: float,
    ) -> AgentResolution:
        txn_1, txn_2 = _extract_txn_ids(full_text)
        amount = _extract_amount(full_text)[0]

        tool_calls.append(
            ToolCall(
                tool_name="payment.get_transaction",
                args_summary=f"transaction_id={txn_1}",
                result_summary=f"Transaction {txn_1}: ${amount:.2f} -- valid charge",
                duration_ms=_random_duration(8, 12),
            )
        )
        tool_calls.append(
            ToolCall(
                tool_name="payment.get_transaction",
                args_summary=f"transaction_id={txn_2}",
                result_summary=f"Transaction {txn_2}: ${amount:.2f} -- DUPLICATE detected",
                duration_ms=_random_duration(8, 12),
            )
        )
        tool_calls.append(
            ToolCall(
                tool_name="payment.process_refund",
                args_summary=(
                    f"txn={txn_2}, amount=${amount:.2f}, reason='duplicate charge reversal'"
                ),
                result_summary=f"Duplicate charge of ${amount:.2f} reversed successfully",
                duration_ms=_random_duration(10, 15),
            )
        )

        customer_name = ticket.get("requester", {}).get("name", "Customer")
        memory_key = f"duplicate_charge_{ticket_id}"
        self._write_memory(
            memory_key,
            f"Duplicate charge of ${amount:.2f} reversed for {customer_name}",
        )

        return AgentResolution(
            ticket_id=ticket_id,
            status=AgentStatus.RESOLVED,
            solvable=True,
            resolution_type="refund",
            resolution_summary=(
                f"Confirmed duplicate charge: transactions {txn_1} and {txn_2} both for "
                f"${amount:.2f}. Reversed duplicate transaction {txn_2}."
            ),
            tool_calls=tool_calls,
            memory_hit=memory_hit,
            memory_written=memory_key,
            customer_message=(
                f"Hi {customer_name},\n\n"
                f"We've confirmed the duplicate charge on your account. Transaction "
                f"{txn_2} (${amount:.2f}) has been reversed. The refund should appear "
                f"within 3-5 business days.\n\n"
                f"We apologize for the inconvenience.\n\n"
                f"Best regards,\nSupport Team"
            ),
            processing_time_ms=round((time.monotonic() - start) * 1000, 1),
        )

    async def _resolve_wrong_item(
        self,
        ticket_id: str,
        ticket: dict,
        full_text: str,
        tool_calls: list[ToolCall],
        memory_hit: MemoryHit | None,
        start: float,
    ) -> AgentResolution:
        order_id = _extract_order_id(full_text, ticket.get("custom_fields", {}))

        tool_calls.append(
            ToolCall(
                tool_name="order_db.get_order",
                args_summary=f"order_id={order_id}",
                result_summary=f"Order {order_id}: found, status=shipped",
                duration_ms=_random_duration(7, 13),
            )
        )

        replacement_id = f"ORD-R{random.randint(10000, 99999)}"
        tool_calls.append(
            ToolCall(
                tool_name="order_db.create_replacement_order",
                args_summary=f"original_order_id={order_id}",
                result_summary=f"Replacement order {replacement_id} created, ships within 24h",
                duration_ms=_random_duration(10, 15),
            )
        )

        tool_calls.append(
            ToolCall(
                tool_name="shipping.create_return_label",
                args_summary=f"order_id={order_id}",
                result_summary="Prepaid return label generated and emailed to customer",
                duration_ms=_random_duration(8, 12),
            )
        )

        customer_name = ticket.get("requester", {}).get("name", "Customer")
        memory_key = f"wrong_item_{ticket_id}"
        self._write_memory(
            memory_key,
            f"Wrong item in {order_id} replaced with {replacement_id} for {customer_name}",
        )
        tool_calls.append(
            ToolCall(
                tool_name="memory.write",
                args_summary=f"key={memory_key}",
                result_summary="Resolution pattern stored",
                duration_ms=_random_duration(3, 6),
            )
        )

        return AgentResolution(
            ticket_id=ticket_id,
            status=AgentStatus.RESOLVED,
            solvable=True,
            resolution_type="replacement",
            resolution_summary=(
                f"Order {order_id} had incorrect/damaged item. Created replacement "
                f"order {replacement_id} with expedited shipping. Prepaid return label "
                f"sent for original item."
            ),
            tool_calls=tool_calls,
            memory_hit=memory_hit,
            memory_written=memory_key,
            customer_message=(
                f"Hi {customer_name},\n\n"
                f"We're sorry about the issue with your order {order_id}. We've taken "
                f"the following steps to make this right:\n\n"
                f"1. Created a replacement order ({replacement_id}) with the correct "
                f"item -- it will ship within 24 hours via expedited shipping\n"
                f"2. Emailed you a prepaid return label for the incorrect item\n\n"
                f"You can return the incorrect item at your convenience using the "
                f"prepaid label. No need to rush.\n\n"
                f"We apologize for the inconvenience and appreciate your patience.\n\n"
                f"Best regards,\nSupport Team"
            ),
            processing_time_ms=round((time.monotonic() - start) * 1000, 1),
        )

    def _resolve_password(
        self,
        ticket_id: str,
        ticket: dict,
        tool_calls: list[ToolCall],
        memory_hit: MemoryHit | None,
        start: float,
    ) -> AgentResolution:
        customer_email = ticket.get("requester", {}).get("email", "user@example.com")
        customer_name = ticket.get("requester", {}).get("name", "Customer")

        tool_calls.append(
            ToolCall(
                tool_name="auth.check_account_status",
                args_summary=f"email={customer_email}",
                result_summary="Account active, no security holds, last login 3 days ago",
                duration_ms=_random_duration(6, 10),
            )
        )
        tool_calls.append(
            ToolCall(
                tool_name="auth.send_password_reset",
                args_summary=f"email={customer_email}",
                result_summary="Password reset email sent successfully",
                duration_ms=_random_duration(8, 12),
            )
        )
        tool_calls.append(
            ToolCall(
                tool_name="auth.clear_session_cache",
                args_summary=f"email={customer_email}",
                result_summary="All active sessions invalidated, cache cleared",
                duration_ms=_random_duration(5, 9),
            )
        )

        return AgentResolution(
            ticket_id=ticket_id,
            status=AgentStatus.RESOLVED,
            solvable=True,
            resolution_type="account_recovery",
            resolution_summary=(
                f"Account access issue for {customer_email}. Verified account is "
                f"active with no security holds. Sent fresh password reset email "
                f"and cleared session cache."
            ),
            tool_calls=tool_calls,
            memory_hit=memory_hit,
            customer_message=(
                f"Hi {customer_name},\n\n"
                f"We've looked into your login issue and here's what we've done:\n\n"
                f"1. Verified your account is active with no security restrictions\n"
                f"2. Sent a fresh password reset email to {customer_email}\n"
                f"3. Cleared all cached sessions to ensure a clean login\n\n"
                f"Please check your inbox (and spam folder) for the reset email. "
                f"The link will be valid for 24 hours. After resetting, try logging "
                f"in using an incognito/private browser window first.\n\n"
                f"If you're still having trouble, please let us know and we'll "
                f"arrange a screen-sharing session to help.\n\n"
                f"Best regards,\nSupport Team"
            ),
            processing_time_ms=round((time.monotonic() - start) * 1000, 1),
        )

    async def _resolve_shipping(
        self,
        ticket_id: str,
        ticket: dict,
        full_text: str,
        tool_calls: list[ToolCall],
        memory_hit: MemoryHit | None,
        start: float,
    ) -> AgentResolution:
        order_id = _extract_order_id(full_text, ticket.get("custom_fields", {}))

        tool_calls.append(
            ToolCall(
                tool_name="order_db.get_order",
                args_summary=f"order_id={order_id}",
                result_summary=f"Order {order_id}: status=shipped, carrier=FedEx",
                duration_ms=_random_duration(7, 12),
            )
        )

        tracking_id = f"FX{random.randint(100000000, 999999999)}"
        tool_calls.append(
            ToolCall(
                tool_name="shipping.get_shipment_by_order",
                args_summary=f"order_id={order_id}",
                result_summary=(
                    f"Tracking: {tracking_id}, last scan: regional hub, "
                    f"estimated delivery: 2 business days"
                ),
                duration_ms=_random_duration(8, 14),
            )
        )

        customer_name = ticket.get("requester", {}).get("name", "Customer")
        memory_key = f"shipping_delay_{ticket_id}"
        self._write_memory(
            memory_key,
            f"Shipping delay for {order_id} -- provided tracking update to {customer_name}",
        )

        return AgentResolution(
            ticket_id=ticket_id,
            status=AgentStatus.RESOLVED,
            solvable=True,
            resolution_type="shipping_update",
            resolution_summary=(
                f"Order {order_id} is in transit via FedEx (tracking: {tracking_id}). "
                f"Last scanned at regional distribution hub. Estimated delivery within "
                f"2 business days."
            ),
            tool_calls=tool_calls,
            memory_hit=memory_hit,
            memory_written=memory_key,
            customer_message=(
                f"Hi {customer_name},\n\n"
                f"We've tracked down your order {order_id} and have good news -- "
                f"it's currently in transit.\n\n"
                f"Tracking number: {tracking_id} (FedEx)\n"
                f"Last scan: Regional distribution hub\n"
                f"Estimated delivery: Within 2 business days\n\n"
                f"You can track your package in real-time at "
                f"https://www.fedex.com/tracking?id={tracking_id}\n\n"
                f"We apologize for the delay. If your package doesn't arrive within "
                f"the estimated timeframe, please reply to this ticket and we'll "
                f"arrange a replacement shipment immediately.\n\n"
                f"Best regards,\nSupport Team"
            ),
            processing_time_ms=round((time.monotonic() - start) * 1000, 1),
        )

    # ------------------------------------------------------------------ #
    # Escalation paths                                                     #
    # ------------------------------------------------------------------ #

    def _escalate_technical(
        self,
        ticket_id: str,
        ticket: dict,
        full_text: str,
        tool_calls: list[ToolCall],
        memory_hit: MemoryHit | None,
        start: float,
    ) -> AgentResolution:
        endpoint = _extract_endpoint(full_text)
        integration_id = ticket.get("custom_fields", {}).get(
            "integration_id", "int_unknown"
        )

        tool_calls.append(
            ToolCall(
                tool_name="integration.check_endpoint",
                args_summary=f"endpoint={endpoint}",
                result_summary="Endpoint responding with elevated error rate (12% 5xx)",
                duration_ms=_random_duration(10, 15),
            )
        )

        curl_cmd = f"curl -sS -o /dev/null -w '%{{http_code}}' -X GET '{endpoint}'"
        sandbox_commands = [
            curl_cmd,
            f"curl -sS -X POST '{endpoint}' -H 'Content-Type: application/json' -d '{{\"test\": true}}'",
        ]
        sandbox_output = (
            f"$ {curl_cmd}\n"
            f"HTTP/1.1 502 Bad Gateway\n"
            f"x-request-id: req_{random.randint(100000, 999999)}\n"
            f"Retry-After: 30\n\n"
            f"Diagnostic: Upstream timeout after 30s. Integration {integration_id} "
            f"shows intermittent connectivity issues. Error rate: 12% over last hour."
        )

        tool_calls.append(
            ToolCall(
                tool_name="sandbox.run_diagnostic",
                args_summary=f"integration_id={integration_id}, commands=2",
                result_summary="502 Bad Gateway from upstream, 12% error rate confirmed",
                duration_ms=_random_duration(12, 15),
            )
        )

        tool_calls.append(
            ToolCall(
                tool_name="escalation.create",
                args_summary="team=engineering, priority=high",
                result_summary="Escalation ESC-{} created, assigned to Engineering".format(
                    random.randint(1000, 9999)
                ),
                duration_ms=_random_duration(6, 10),
            )
        )

        customer_name = ticket.get("requester", {}).get("name", "Customer")

        return AgentResolution(
            ticket_id=ticket_id,
            status=AgentStatus.ESCALATED,
            solvable=False,
            resolution_type="technical_escalation",
            resolution_summary=(
                f"Technical issue confirmed: endpoint {endpoint} returning elevated "
                f"error rates (12% 5xx). Sandbox diagnostics show upstream timeout. "
                f"Escalated to Engineering team for investigation."
            ),
            tool_calls=tool_calls,
            memory_hit=memory_hit,
            escalation_reason=(
                f"Technical issue requires engineering investigation -- "
                f"endpoint {endpoint} has elevated error rates"
            ),
            escalation_team="Engineering",
            customer_message=(
                f"Hi {customer_name},\n\n"
                f"We've investigated the technical issue you reported and confirmed "
                f"there is an elevated error rate on the affected endpoint. Our "
                f"diagnostic tests show intermittent upstream connectivity issues.\n\n"
                f"We've escalated this to our Engineering team for immediate "
                f"investigation. You can expect an update within 2 hours.\n\n"
                f"In the meantime, implementing retry logic with exponential backoff "
                f"may help mitigate the impact on your integration.\n\n"
                f"Best regards,\nSupport Team"
            ),
            sandbox_commands=sandbox_commands,
            sandbox_output=sandbox_output,
            processing_time_ms=round((time.monotonic() - start) * 1000, 1),
        )

    def _escalate_crisis(
        self,
        ticket_id: str,
        ticket: dict,
        tool_calls: list[ToolCall],
        memory_hit: MemoryHit | None,
        start: float,
    ) -> AgentResolution:
        customer_name = ticket.get("requester", {}).get("name", "Customer")
        customer_tier = ticket.get("requester", {}).get("tier", "standard")
        subject = ticket.get("subject", "Production issue")

        tool_calls.append(
            ToolCall(
                tool_name="escalation.create",
                args_summary="team=engineering+management, priority=critical",
                result_summary="P0 escalation created, paging on-call engineering lead",
                duration_ms=_random_duration(5, 8),
            )
        )
        tool_calls.append(
            ToolCall(
                tool_name="notification.page_oncall",
                args_summary="team=engineering, severity=P0",
                result_summary="On-call engineer paged via PagerDuty",
                duration_ms=_random_duration(4, 7),
            )
        )
        tool_calls.append(
            ToolCall(
                tool_name="notification.alert_management",
                args_summary=f"customer_tier={customer_tier}, issue='{subject[:40]}'",
                result_summary="VP of Customer Success and Engineering Lead notified",
                duration_ms=_random_duration(4, 7),
            )
        )

        return AgentResolution(
            ticket_id=ticket_id,
            status=AgentStatus.ESCALATED,
            solvable=False,
            resolution_type="crisis_escalation",
            resolution_summary=(
                f"CRITICAL: Production incident reported by {customer_tier} customer "
                f"{customer_name}. P0 escalation created. On-call engineer paged. "
                f"Engineering Lead and VP of Customer Success notified."
            ),
            tool_calls=tool_calls,
            memory_hit=memory_hit,
            escalation_reason=(
                f"Production crisis reported: {subject}. "
                f"Requires immediate engineering and management attention."
            ),
            escalation_team="Engineering + Management",
            customer_message=(
                f"Hi {customer_name},\n\n"
                f"We understand this is a critical issue affecting your production "
                f"environment. We're treating this as a P0 incident and have taken "
                f"the following immediate actions:\n\n"
                f"1. Created a critical escalation to our Engineering team\n"
                f"2. Paged our on-call engineering lead\n"
                f"3. Notified senior management\n\n"
                f"An engineer will reach out to you directly within 15 minutes. "
                f"In the meantime, please gather any relevant logs or error messages "
                f"that could help us investigate.\n\n"
                f"Best regards,\nSupport Team"
            ),
            processing_time_ms=round((time.monotonic() - start) * 1000, 1),
        )

    def _escalate_management(
        self,
        ticket_id: str,
        ticket: dict,
        tool_calls: list[ToolCall],
        memory_hit: MemoryHit | None,
        start: float,
    ) -> AgentResolution:
        customer_name = ticket.get("requester", {}).get("name", "Customer")
        subject = ticket.get("subject", "")

        tool_calls.append(
            ToolCall(
                tool_name="escalation.create",
                args_summary="team=management, priority=high",
                result_summary="Management escalation created and assigned",
                duration_ms=_random_duration(6, 10),
            )
        )
        tool_calls.append(
            ToolCall(
                tool_name="zendesk.add_internal_note",
                args_summary=f"ticket_id={ticket_id}, note='Management review requested'",
                result_summary="Internal note added to ticket",
                duration_ms=_random_duration(5, 9),
            )
        )

        return AgentResolution(
            ticket_id=ticket_id,
            status=AgentStatus.ESCALATED,
            solvable=False,
            resolution_type="management_escalation",
            resolution_summary=(
                f"Customer {customer_name} requested management attention regarding "
                f"'{subject[:60]}'. Escalated to management team for review."
            ),
            tool_calls=tool_calls,
            memory_hit=memory_hit,
            escalation_reason=(
                f"Customer requesting management review or expressing dissatisfaction "
                f"with prior support interactions"
            ),
            escalation_team="Management",
            customer_message=(
                f"Hi {customer_name},\n\n"
                f"We hear you, and we understand your frustration. We've escalated "
                f"your case directly to our management team for personal review.\n\n"
                f"A senior team member will reach out to you within 1 hour to "
                f"discuss your concerns and work toward a resolution.\n\n"
                f"Your satisfaction is very important to us, and we want to make "
                f"sure we get this right.\n\n"
                f"Best regards,\nSupport Team"
            ),
            processing_time_ms=round((time.monotonic() - start) * 1000, 1),
        )

    def _escalate_feature(
        self,
        ticket_id: str,
        ticket: dict,
        tool_calls: list[ToolCall],
        memory_hit: MemoryHit | None,
        start: float,
    ) -> AgentResolution:
        customer_name = ticket.get("requester", {}).get("name", "Customer")
        subject = ticket.get("subject", "Feature request")

        tool_calls.append(
            ToolCall(
                tool_name="zendesk.add_tags",
                args_summary=f"ticket_id={ticket_id}, tags=['feature_request', 'product_review']",
                result_summary="Tags added for product team triage",
                duration_ms=_random_duration(5, 9),
            )
        )
        tool_calls.append(
            ToolCall(
                tool_name="product.log_feature_request",
                args_summary=f"title='{subject[:40]}', customer_tier={ticket.get('requester', {}).get('tier', 'standard')}",
                result_summary="Feature request logged in product backlog",
                duration_ms=_random_duration(6, 10),
            )
        )

        return AgentResolution(
            ticket_id=ticket_id,
            status=AgentStatus.ESCALATED,
            solvable=False,
            resolution_type="feature_request_deferred",
            resolution_summary=(
                f"Feature request from {customer_name}: '{subject[:60]}'. "
                f"Tagged for product team review and logged in backlog."
            ),
            tool_calls=tool_calls,
            memory_hit=memory_hit,
            escalation_reason="Feature request -- deferred to product team for prioritization",
            escalation_team="Product",
            customer_message=(
                f"Hi {customer_name},\n\n"
                f"Thank you for your feature suggestion! We love hearing ideas from "
                f"our customers.\n\n"
                f"We've logged your request and shared it with our Product team for "
                f"review. While we can't guarantee a specific timeline, feature "
                f"requests like yours directly influence our product roadmap.\n\n"
                f"We'll update this ticket if and when the feature moves into "
                f"development. In the meantime, if you'd like to share any "
                f"additional details about your use case, it would help our product "
                f"team prioritize.\n\n"
                f"Best regards,\nSupport Team"
            ),
            processing_time_ms=round((time.monotonic() - start) * 1000, 1),
        )

    def _escalate_default(
        self,
        ticket_id: str,
        ticket: dict,
        tool_calls: list[ToolCall],
        memory_hit: MemoryHit | None,
        start: float,
    ) -> AgentResolution:
        customer_name = ticket.get("requester", {}).get("name", "Customer")
        subject = ticket.get("subject", "Support request")

        tool_calls.append(
            ToolCall(
                tool_name="escalation.create",
                args_summary="team=l2_support, priority=normal",
                result_summary="Escalated to L2 Support for manual review",
                duration_ms=_random_duration(6, 10),
            )
        )

        return AgentResolution(
            ticket_id=ticket_id,
            status=AgentStatus.ESCALATED,
            solvable=False,
            resolution_type="l2_escalation",
            resolution_summary=(
                f"Ticket '{subject[:60]}' requires human review. "
                f"Escalated to L2 Support team."
            ),
            tool_calls=tool_calls,
            memory_hit=memory_hit,
            escalation_reason="Ticket did not match automated resolution patterns -- needs human review",
            escalation_team="L2 Support",
            customer_message=(
                f"Hi {customer_name},\n\n"
                f"Thank you for contacting us. We've reviewed your request and are "
                f"routing it to a specialized support agent who can best assist you.\n\n"
                f"You can expect a response within 2 hours. We appreciate your "
                f"patience.\n\n"
                f"Best regards,\nSupport Team"
            ),
            processing_time_ms=round((time.monotonic() - start) * 1000, 1),
        )

    # ------------------------------------------------------------------ #
    # Memory helpers                                                       #
    # ------------------------------------------------------------------ #

    def _search_memory(
        self, text: str, tags: list[str]
    ) -> MemoryHit | None:
        """Search the in-memory store for a pattern matching this ticket."""
        text_lower = text.lower()
        for key, value in self.memory_store.items():
            # Simple keyword overlap check
            key_tokens = key.replace("_", " ").lower().split()
            matches = sum(1 for t in key_tokens if t in text_lower)
            if matches >= 2:
                return MemoryHit(
                    matched_ticket_id=key,
                    pattern=value[:80] if isinstance(value, str) else str(value)[:80],
                    suggested_action="Apply similar resolution pattern",
                )
        return None

    def _write_memory(self, key: str, value: str) -> None:
        """Store a resolution pattern for future reference."""
        self.memory_store[key] = value

    # ------------------------------------------------------------------ #
    # Real UC agent (stub)                                                 #
    # ------------------------------------------------------------------ #

    async def _process_with_uc_agent(self, ticket_id: str) -> AgentResolution:
        """Process ticket using real UC agent.

        Currently falls back to the mock agent. This will be replaced with
        actual UC agent integration in a future iteration.
        """
        activity.logger.info(
            f"UC agent requested for ticket {ticket_id} -- "
            f"falling back to mock agent (not yet implemented)"
        )
        return await self._process_with_mock(ticket_id)
