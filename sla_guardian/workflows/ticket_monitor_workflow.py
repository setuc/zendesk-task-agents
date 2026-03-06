from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from .activities import SLAGuardianActivities
    from .data_types import (
        EscalationAction,
        EscalationResult,
        EscalationTier,
        SLAStatus,
        SentimentReport,
        TicketMonitorState,
        UrgencyClassification,
    )


# Ordered escalation path
_ESCALATION_PATH: list[EscalationTier] = [
    EscalationTier.L1,
    EscalationTier.L2,
    EscalationTier.L3,
    EscalationTier.MANAGER,
]


def _next_tier(current: EscalationTier) -> EscalationTier | None:
    """Return the next escalation tier, or None if already at the highest."""
    try:
        idx = _ESCALATION_PATH.index(current)
        if idx + 1 < len(_ESCALATION_PATH):
            return _ESCALATION_PATH[idx + 1]
    except ValueError:
        pass
    return None


@workflow.defn
class TicketMonitorWorkflow:
    """Tracks SLA compliance for a single ticket with durable timers.

    Performs urgency classification and sentiment analysis, then sets a
    durable timer to fire before the SLA deadline. When the timer expires
    (and no resolution signal has been received), the workflow drafts and
    executes an escalation, then loops to the next tier.
    """

    def __init__(self) -> None:
        self._state = TicketMonitorState(ticket_id="")
        self._override_escalation: bool = False
        self._ticket_resolved: bool = False
        self._priority_adjustment: str | None = None

    # ------------------------------------------------------------------ #
    # Signals                                                              #
    # ------------------------------------------------------------------ #

    @workflow.signal
    async def override_escalation(self) -> None:
        """Signal to skip the next automatic escalation."""
        self._override_escalation = True

    @workflow.signal
    async def ticket_resolved(self) -> None:
        """Signal that the ticket has been resolved externally."""
        self._ticket_resolved = True

    @workflow.signal
    async def adjust_priority(self, new_priority: str) -> None:
        """Signal to manually adjust the assessed priority."""
        self._priority_adjustment = new_priority

    # ------------------------------------------------------------------ #
    # Queries                                                              #
    # ------------------------------------------------------------------ #

    @workflow.query
    def get_sla_status(self) -> str:
        return self._state.sla_status.value

    @workflow.query
    def get_sentiment_report(self) -> SentimentReport | None:
        return self._state.sentiment

    @workflow.query
    def get_escalation_history(self) -> list[EscalationAction]:
        return list(self._state.escalation_history)

    @workflow.query
    def get_monitor_state(self) -> TicketMonitorState:
        return self._state

    # ------------------------------------------------------------------ #
    # Main workflow run                                                    #
    # ------------------------------------------------------------------ #

    @workflow.run
    async def run(
        self,
        ticket_id: str,
        sla_deadline_iso: str,
        escalation_buffer_minutes: int = 30,
    ) -> TicketMonitorState:
        """Monitor a single ticket through its SLA lifecycle.

        Args:
            ticket_id: The Zendesk ticket ID to monitor.
            sla_deadline_iso: ISO-8601 SLA deadline string.
            escalation_buffer_minutes: Minutes before SLA breach to trigger escalation.

        Returns:
            Final TicketMonitorState when the ticket is resolved or fully escalated.
        """
        self._state.ticket_id = ticket_id
        self._state.sla_deadline = datetime.fromisoformat(sla_deadline_iso)

        activity_timeout = timedelta(seconds=120)
        retry_policy = RetryPolicy(
            maximum_attempts=3,
            initial_interval=timedelta(seconds=2),
            maximum_interval=timedelta(seconds=30),
            backoff_coefficient=2.0,
        )

        try:
            # Step 1: Classify urgency
            urgency_result: UrgencyClassification = (
                await workflow.execute_activity_method(
                    SLAGuardianActivities.classify_urgency,
                    ticket_id,
                    start_to_close_timeout=activity_timeout,
                    retry_policy=retry_policy,
                )
            )
            self._state.urgency = urgency_result

            # Apply manual priority adjustment if signaled
            if self._priority_adjustment:
                self._state.urgency.assessed_priority = self._priority_adjustment
                self._state.urgency.priority_override = True
                self._priority_adjustment = None

            # Check for early resolution
            if self._ticket_resolved:
                self._state.sla_status = SLAStatus.RESOLVED
                return self._state

            # Step 2: Analyze sentiment
            sentiment_result: SentimentReport = (
                await workflow.execute_activity_method(
                    SLAGuardianActivities.analyze_sentiment,
                    ticket_id,
                    start_to_close_timeout=activity_timeout,
                    retry_policy=retry_policy,
                )
            )
            self._state.sentiment = sentiment_result

            # Check for early resolution
            if self._ticket_resolved:
                self._state.sla_status = SLAStatus.RESOLVED
                return self._state

            # Step 3: Escalation timer loop
            while True:
                next_tier = _next_tier(self._state.current_tier)
                if next_tier is None:
                    # Already at highest tier, nothing more to escalate
                    workflow.logger.info(
                        f"Ticket {ticket_id} at highest tier ({self._state.current_tier.value}), "
                        f"monitoring complete"
                    )
                    break

                # Calculate time until escalation
                now = workflow.now()
                deadline = self._state.sla_deadline
                if deadline is None:
                    break

                # Ensure deadline is timezone-aware
                if deadline.tzinfo is None:
                    deadline = deadline.replace(tzinfo=timezone.utc)

                escalation_time = deadline - timedelta(
                    minutes=escalation_buffer_minutes
                )
                wait_duration = escalation_time - now

                if wait_duration.total_seconds() <= 0:
                    # Already past escalation time -- SLA is at risk or breached
                    if now >= deadline:
                        self._state.sla_status = SLAStatus.BREACHED
                    else:
                        self._state.sla_status = SLAStatus.AT_RISK
                else:
                    self._state.sla_status = SLAStatus.AT_RISK
                    self._state.next_check = escalation_time

                    # Wait for either timer, resolution signal, or override signal
                    try:
                        await workflow.wait_condition(
                            lambda: self._ticket_resolved
                            or self._override_escalation,
                            timeout=wait_duration,
                        )
                    except asyncio.TimeoutError:
                        # Timer fired -- proceed with escalation
                        pass

                # Check signals after wait
                if self._ticket_resolved:
                    self._state.sla_status = SLAStatus.RESOLVED
                    return self._state

                if self._override_escalation:
                    workflow.logger.info(
                        f"Escalation override received for {ticket_id}, skipping"
                    )
                    self._override_escalation = False
                    # Move deadline forward by the buffer to give more time
                    if self._state.sla_deadline is not None:
                        self._state.sla_deadline = (
                            self._state.sla_deadline
                            + timedelta(minutes=escalation_buffer_minutes)
                        )
                    continue

                # Step 4: Draft escalation message
                drafted_message: str = await workflow.execute_activity_method(
                    SLAGuardianActivities.draft_escalation,
                    args=[
                        ticket_id,
                        self._state.current_tier.value,
                        next_tier.value,
                    ],
                    start_to_close_timeout=activity_timeout,
                    retry_policy=retry_policy,
                )

                # Step 5: Execute escalation
                escalation_result: EscalationResult = (
                    await workflow.execute_activity_method(
                        SLAGuardianActivities.escalate_ticket,
                        args=[ticket_id, next_tier.value, drafted_message],
                        start_to_close_timeout=activity_timeout,
                        retry_policy=retry_policy,
                    )
                )

                # Record escalation in history
                action = EscalationAction(
                    ticket_id=ticket_id,
                    from_tier=self._state.current_tier,
                    to_tier=next_tier,
                    reason=(
                        f"SLA {self._state.sla_status.value}: automatic escalation "
                        f"triggered {escalation_buffer_minutes}min before deadline"
                    ),
                    drafted_message=drafted_message,
                    auto_escalated=True,
                )
                self._state.escalation_history.append(action)
                self._state.current_tier = next_tier

                workflow.logger.info(
                    f"Ticket {ticket_id} escalated to {next_tier.value}"
                )

                # Re-analyze sentiment after escalation for next cycle
                sentiment_result = await workflow.execute_activity_method(
                    SLAGuardianActivities.analyze_sentiment,
                    ticket_id,
                    start_to_close_timeout=activity_timeout,
                    retry_policy=retry_policy,
                )
                self._state.sentiment = sentiment_result

        except Exception as e:
            workflow.logger.error(
                f"Error monitoring ticket {ticket_id}: {e}"
            )
            self._state.sla_status = SLAStatus.BREACHED

        return self._state
