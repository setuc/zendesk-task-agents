from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timedelta, timezone

from temporalio.client import Client
from temporalio.worker import Worker

from .config import SLAGuardianConfig
from .workflows.guardian_workflow import SLAGuardianWorkflow
from .workflows.ticket_monitor_workflow import TicketMonitorWorkflow
from .workflows.activities import SLAGuardianActivities
from .workflows.data_types import GuardianState
from ..common.tui import console
from ..common.services.zendesk_mock import MockZendeskService
from .services.sla_rules_mock import MockSLARulesService


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="SLA Guardian - UC + Temporal SLA Monitoring Demo"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # monitor command - start the guardian workflow
    monitor_parser = subparsers.add_parser(
        "monitor", help="Start the SLA Guardian periodic scanner"
    )
    monitor_parser.add_argument(
        "--scan-interval",
        type=int,
        default=None,
        help="Override scan interval in seconds",
    )
    monitor_parser.add_argument(
        "--escalation-buffer",
        type=int,
        default=None,
        help="Override escalation buffer in minutes",
    )

    # status command - query guardian or ticket state
    status_parser = subparsers.add_parser(
        "status", help="Query SLA Guardian or ticket monitor state"
    )
    status_parser.add_argument(
        "--ticket-id",
        type=str,
        default=None,
        help="Query a specific ticket monitor (omit for guardian state)",
    )
    status_parser.add_argument(
        "--type",
        choices=["state", "sentiment", "escalations"],
        default="state",
        help="Type of query to perform",
    )

    # override command - signal an escalation override
    override_parser = subparsers.add_parser(
        "override", help="Signal an escalation override for a ticket"
    )
    override_parser.add_argument(
        "ticket_id", help="Ticket ID to override escalation for"
    )
    override_parser.add_argument(
        "--resolve",
        action="store_true",
        help="Signal ticket as resolved instead of override",
    )
    override_parser.add_argument(
        "--priority",
        type=str,
        default=None,
        help="Adjust ticket priority (low/normal/high/urgent)",
    )

    # simulate command - fast demo mode
    subparsers.add_parser(
        "simulate",
        help="Run a fast simulation with pre-loaded tickets",
    )

    return parser.parse_args()


def create_mock_services() -> tuple[MockZendeskService, MockSLARulesService]:
    """Create mock services for demo mode."""
    zendesk = MockZendeskService()
    sla_rules = MockSLARulesService()
    return zendesk, sla_rules


async def run_monitor(config: SLAGuardianConfig, args: argparse.Namespace) -> None:
    """Start the Temporal worker and the SLA Guardian workflow."""
    client = await Client.connect(
        config.temporal_address, namespace=config.temporal_namespace
    )
    zendesk, sla_rules = create_mock_services()

    activities = SLAGuardianActivities(zendesk=zendesk, sla_rules=sla_rules)

    scan_interval = args.scan_interval or config.scan_interval_seconds
    escalation_buffer = args.escalation_buffer or config.escalation_buffer_minutes

    console.print("[bold green]Starting SLA Guardian Worker[/bold green]")
    console.print(f"Task queue: {config.task_queue}")
    console.print(f"Scan interval: {scan_interval}s")
    console.print(f"Escalation buffer: {escalation_buffer}min")

    worker = Worker(
        client,
        task_queue=config.task_queue,
        workflows=[SLAGuardianWorkflow, TicketMonitorWorkflow],
        activities=[
            activities.scan_open_tickets,
            activities.classify_urgency,
            activities.analyze_sentiment,
            activities.draft_escalation,
            activities.escalate_ticket,
        ],
    )

    # Start the guardian workflow
    guardian_id = "sla-guardian-main"
    try:
        handle = await client.start_workflow(
            SLAGuardianWorkflow.run,
            args=[None, scan_interval, escalation_buffer],
            id=guardian_id,
            task_queue=config.task_queue,
        )
        console.print(
            f"[green]Guardian workflow started: {handle.id}[/green]"
        )
    except Exception:
        # Workflow may already be running
        console.print(
            f"[yellow]Guardian workflow {guardian_id} may already be running[/yellow]"
        )

    console.print("[green]Worker started. Press Ctrl+C to stop.[/green]")
    await worker.run()


async def query_status(config: SLAGuardianConfig, args: argparse.Namespace) -> None:
    """Query guardian or ticket monitor state."""
    client = await Client.connect(
        config.temporal_address, namespace=config.temporal_namespace
    )

    if args.ticket_id:
        # Query a specific ticket monitor
        workflow_id = f"ticket-monitor-{args.ticket_id}"
        handle = client.get_workflow_handle(workflow_id)

        if args.type == "state":
            state = await handle.query(TicketMonitorWorkflow.get_monitor_state)
            console.print(f"[bold]Ticket Monitor State:[/bold]")
            console.print(f"  Ticket: {state.ticket_id}")
            console.print(f"  SLA Status: {state.sla_status.value}")
            console.print(f"  Current Tier: {state.current_tier.value}")
            if state.urgency:
                console.print(
                    f"  Urgency: {state.urgency.assessed_priority} "
                    f"(score: {state.urgency.urgency_score})"
                )
            if state.sla_deadline:
                console.print(f"  SLA Deadline: {state.sla_deadline.isoformat()}")
            console.print(
                f"  Escalations: {len(state.escalation_history)}"
            )

        elif args.type == "sentiment":
            sentiment = await handle.query(
                TicketMonitorWorkflow.get_sentiment_report
            )
            if sentiment:
                console.print(f"[bold]Sentiment Report for {args.ticket_id}:[/bold]")
                console.print(f"  Sentiment: {sentiment.overall_sentiment}")
                console.print(f"  Trajectory: {sentiment.frustration_trajectory}")
                console.print(f"  Escalation Risk: {sentiment.escalation_risk}")
                if sentiment.key_phrases:
                    console.print(
                        f"  Key Phrases: {', '.join(sentiment.key_phrases)}"
                    )
            else:
                console.print("[yellow]No sentiment report available yet[/yellow]")

        elif args.type == "escalations":
            history = await handle.query(
                TicketMonitorWorkflow.get_escalation_history
            )
            if history:
                console.print(
                    f"[bold]Escalation History for {args.ticket_id}:[/bold]"
                )
                for i, action in enumerate(history, 1):
                    console.print(
                        f"  {i}. {action.from_tier.value} -> {action.to_tier.value}: "
                        f"{action.reason}"
                    )
            else:
                console.print("[yellow]No escalations recorded yet[/yellow]")
    else:
        # Query guardian state
        handle = client.get_workflow_handle("sla-guardian-main")
        state = await handle.query(SLAGuardianWorkflow.get_state)
        console.print("[bold]SLA Guardian State:[/bold]")
        console.print(f"  Scan Count: {state.scan_count}")
        console.print(
            f"  Last Scan: {state.last_scan.isoformat() if state.last_scan else 'N/A'}"
        )
        console.print(f"  Monitored Tickets: {len(state.monitored_tickets)}")
        if state.monitored_tickets:
            for tid in state.monitored_tickets:
                console.print(f"    - {tid}")
        console.print(f"  Total Escalations: {state.total_escalations}")


async def send_override(config: SLAGuardianConfig, args: argparse.Namespace) -> None:
    """Send a signal to a ticket monitor workflow."""
    client = await Client.connect(
        config.temporal_address, namespace=config.temporal_namespace
    )

    workflow_id = f"ticket-monitor-{args.ticket_id}"
    handle = client.get_workflow_handle(workflow_id)

    if args.resolve:
        await handle.signal(TicketMonitorWorkflow.ticket_resolved)
        console.print(
            f"[green]Sent resolution signal to {args.ticket_id}[/green]"
        )
    elif args.priority:
        await handle.signal(
            TicketMonitorWorkflow.adjust_priority, args.priority
        )
        console.print(
            f"[green]Adjusted priority to {args.priority} for {args.ticket_id}[/green]"
        )
    else:
        await handle.signal(TicketMonitorWorkflow.override_escalation)
        console.print(
            f"[green]Sent escalation override to {args.ticket_id}[/green]"
        )


async def run_simulation(config: SLAGuardianConfig) -> None:
    """Run a fast simulation with pre-loaded tickets and short intervals.

    Loads sample tickets, starts the worker with accelerated timers,
    and displays real-time status updates.
    """
    client = await Client.connect(
        config.temporal_address, namespace=config.temporal_namespace
    )

    zendesk, sla_rules = create_mock_services()

    # Load fixture tickets into the mock zendesk service
    import importlib.resources as pkg_resources
    from pathlib import Path

    fixtures_dir = Path(__file__).parent / "fixtures"
    sample_tickets_path = fixtures_dir / "sample_open_tickets.json"

    if sample_tickets_path.exists():
        with open(sample_tickets_path) as f:
            sample_tickets = json.load(f)
        for ticket in sample_tickets:
            zendesk._tickets[ticket["id"]] = ticket
        console.print(
            f"[blue]Loaded {len(sample_tickets)} sample tickets[/blue]"
        )
    else:
        console.print(
            "[yellow]No fixture file found, using default mock tickets[/yellow]"
        )

    activities = SLAGuardianActivities(zendesk=zendesk, sla_rules=sla_rules)

    # Use fast intervals for simulation
    sim_scan_interval = 10  # 10 seconds
    sim_escalation_buffer = 1  # 1 minute

    console.print("[bold cyan]SLA Guardian Simulation Mode[/bold cyan]")
    console.print(f"Scan interval: {sim_scan_interval}s (accelerated)")
    console.print(f"Escalation buffer: {sim_escalation_buffer}min (accelerated)")
    console.print()

    worker = Worker(
        client,
        task_queue=config.task_queue,
        workflows=[SLAGuardianWorkflow, TicketMonitorWorkflow],
        activities=[
            activities.scan_open_tickets,
            activities.classify_urgency,
            activities.analyze_sentiment,
            activities.draft_escalation,
            activities.escalate_ticket,
        ],
    )

    guardian_id = "sla-guardian-simulation"
    try:
        handle = await client.start_workflow(
            SLAGuardianWorkflow.run,
            args=[None, sim_scan_interval, sim_escalation_buffer],
            id=guardian_id,
            task_queue=config.task_queue,
        )
        console.print(
            f"[green]Simulation guardian started: {handle.id}[/green]"
        )
    except Exception:
        console.print(
            f"[yellow]Simulation guardian {guardian_id} may already be running[/yellow]"
        )

    console.print("[green]Simulation running. Press Ctrl+C to stop.[/green]")
    await worker.run()


def main() -> None:
    args = parse_args()
    config = SLAGuardianConfig()

    if args.command == "monitor":
        asyncio.run(run_monitor(config, args))
    elif args.command == "status":
        asyncio.run(query_status(config, args))
    elif args.command == "override":
        asyncio.run(send_override(config, args))
    elif args.command == "simulate":
        asyncio.run(run_simulation(config))


if __name__ == "__main__":
    main()
