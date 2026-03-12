from __future__ import annotations

from datetime import datetime, timedelta, timezone

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from .activities import SLAGuardianActivities
    from .data_types import GuardianState


@workflow.defn
class SLAGuardianWorkflow:
    """Periodic scanner that monitors all open Zendesk tickets for SLA compliance.

    Runs on a configurable scan interval, discovers new tickets, starts child
    TicketMonitorWorkflow instances for each, and uses continue-as-new to
    prevent unbounded event history growth.
    """

    def __init__(self) -> None:
        self._state = GuardianState()
        self._shutdown_requested = False

    # ------------------------------------------------------------------ #
    # Signals                                                              #
    # ------------------------------------------------------------------ #

    @workflow.signal
    async def shutdown(self) -> None:
        """Signal the guardian to stop after the current scan cycle."""
        self._shutdown_requested = True

    # ------------------------------------------------------------------ #
    # Queries                                                              #
    # ------------------------------------------------------------------ #

    @workflow.query
    def get_state(self) -> GuardianState:
        return self._state

    @workflow.query
    def get_monitored_tickets(self) -> list[str]:
        return list(self._state.monitored_tickets)

    # ------------------------------------------------------------------ #
    # Main workflow run                                                    #
    # ------------------------------------------------------------------ #

    @workflow.run
    async def run(
        self,
        state: GuardianState | None = None,
        scan_interval_seconds: int = 300,
        escalation_buffer_minutes: int = 30,
    ) -> GuardianState:
        """Run a single scan cycle and then continue-as-new.

        Args:
            state: Carried-over state from the previous cycle (or None on first run).
            scan_interval_seconds: How long to sleep between scan cycles.
            escalation_buffer_minutes: How many minutes before SLA breach to escalate.
        """
        if state is not None:
            self._state = state

        activity_timeout = timedelta(seconds=120)

        # Step 1: Scan for open tickets
        workflow.logger.info(
            f"Guardian scan #{self._state.scan_count + 1} starting"
        )

        open_tickets: list[dict] = await workflow.execute_activity_method(
            SLAGuardianActivities.scan_open_tickets,
            None,  # filters
            start_to_close_timeout=activity_timeout,
        )

        self._state.scan_count += 1
        self._state.last_scan = workflow.now()

        # Step 2: For each new ticket, start a child TicketMonitorWorkflow
        from .ticket_monitor_workflow import TicketMonitorWorkflow

        for ticket in open_tickets:
            ticket_id = ticket.get("id", "")
            if not ticket_id or ticket_id in self._state.monitored_tickets:
                continue

            # Determine SLA deadline from ticket or default to 24h from now
            sla_deadline_raw = ticket.get("sla_deadline")
            if sla_deadline_raw:
                if isinstance(sla_deadline_raw, str):
                    sla_deadline_iso = sla_deadline_raw
                else:
                    sla_deadline_iso = sla_deadline_raw.isoformat()
            else:
                default_deadline = workflow.now() + timedelta(hours=24)
                sla_deadline_iso = default_deadline.isoformat()

            # Start child workflow for this ticket (fire-and-forget)
            await workflow.start_child_workflow(
                TicketMonitorWorkflow.run,
                args=[ticket_id, sla_deadline_iso, escalation_buffer_minutes],
                id=f"ticket-monitor-{ticket_id}",
                parent_close_policy=workflow.ParentClosePolicy.ABANDON,
            )

            self._state.monitored_tickets.append(ticket_id)
            workflow.logger.info(
                f"Started TicketMonitorWorkflow for {ticket_id}"
            )

        # Step 3: Check for shutdown signal
        if self._shutdown_requested:
            workflow.logger.info("Guardian shutdown requested, stopping")
            return self._state

        # Step 4: Sleep for scan_interval, then continue-as-new
        workflow.logger.info(
            f"Sleeping {scan_interval_seconds}s until next scan cycle"
        )
        await workflow.sleep(timedelta(seconds=scan_interval_seconds))

        # Continue-as-new to prevent unbounded event history
        workflow.continue_as_new(
            args=[self._state, scan_interval_seconds, escalation_buffer_minutes],
        )

        # Unreachable, but satisfies the type checker
        return self._state  # pragma: no cover
