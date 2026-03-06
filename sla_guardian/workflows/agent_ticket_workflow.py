from __future__ import annotations

from datetime import datetime, timedelta, timezone

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from .agent_activities import AgentTicketActivities
    from .data_types import AgentResolution, AgentStatus, AgentTicketState


@workflow.defn
class AgentTicketWorkflow:
    """Per-ticket workflow that runs a UC agent to investigate and resolve
    (or escalate) a support ticket.

    The workflow is intentionally simple: it sets up state, invokes the
    ``process_ticket`` activity (which does all the heavy lifting), and
    records the result.  Queries expose the live state so dashboards can
    render progress in real time.
    """

    def __init__(self) -> None:
        self._state = AgentTicketState(ticket_id="")

    # ------------------------------------------------------------------ #
    # Queries                                                              #
    # ------------------------------------------------------------------ #

    @workflow.query
    def get_state(self) -> AgentTicketState:
        """Return the current agent ticket state (used by dashboards)."""
        return self._state

    # ------------------------------------------------------------------ #
    # Main workflow run                                                    #
    # ------------------------------------------------------------------ #

    @workflow.run
    async def run(
        self,
        ticket_id: str,
        ticket_metadata: dict | None = None,
    ) -> AgentTicketState:
        """Run the agent against a single ticket.

        Args:
            ticket_id: The Zendesk ticket ID to process.
            ticket_metadata: Optional dict with pre-fetched ticket fields
                (``customer_name``, ``customer_tier``, ``priority``,
                ``category``, ``subject``) so the workflow state is
                populated immediately without an extra fetch.

        Returns:
            Final ``AgentTicketState`` with resolution details.
        """

        # ---- Initialise state from metadata ----
        self._state.ticket_id = ticket_id
        self._state.started_at = datetime.now(timezone.utc).isoformat()

        if ticket_metadata:
            self._state.customer_name = ticket_metadata.get("customer_name", "")
            self._state.customer_tier = ticket_metadata.get("customer_tier", "standard")
            self._state.priority = ticket_metadata.get("priority", "normal")
            self._state.category = ticket_metadata.get("category", "")
            self._state.subject = ticket_metadata.get("subject", "")

        # ---- Mark as investigating ----
        self._state.agent_status = AgentStatus.INVESTIGATING

        # ---- Execute the agent activity ----
        retry_policy = RetryPolicy(
            maximum_attempts=3,
            initial_interval=timedelta(seconds=2),
            maximum_interval=timedelta(seconds=30),
            backoff_coefficient=2.0,
        )

        try:
            resolution: AgentResolution = (
                await workflow.execute_activity_method(
                    AgentTicketActivities.process_ticket,
                    ticket_id,
                    start_to_close_timeout=timedelta(seconds=120),
                    retry_policy=retry_policy,
                )
            )

            self._state.resolution = resolution
            self._state.agent_status = resolution.status

        except Exception as exc:
            workflow.logger.error(
                f"Agent activity failed for ticket {ticket_id}: {exc}"
            )
            self._state.agent_status = AgentStatus.FAILED
            self._state.resolution = AgentResolution(
                ticket_id=ticket_id,
                status=AgentStatus.FAILED,
                resolution_summary=f"Agent processing failed: {exc}",
            )

        # ---- Finalise ----
        self._state.completed_at = datetime.now(timezone.utc).isoformat()
        return self._state
