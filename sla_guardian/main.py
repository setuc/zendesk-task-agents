from __future__ import annotations

import argparse
import asyncio
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from temporalio.client import Client
from temporalio.worker import Worker

from .config import SLAGuardianConfig
from .workflows.guardian_workflow import SLAGuardianWorkflow
from .workflows.ticket_monitor_workflow import TicketMonitorWorkflow
from .workflows.activities import SLAGuardianActivities
from .workflows.data_types import (
    EscalationTier,
    GuardianState,
    SLAStatus,
    TicketMonitorState,
)
from common.tui import console, WorkflowDashboard

from common.services.zendesk_mock import MockZendeskService
from .services.sla_rules_mock import MockSLARulesService

from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

_FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_fixture_tickets() -> list[dict]:
    """Load sample tickets from the fixtures directory."""
    path = _FIXTURES_DIR / "sample_open_tickets.json"
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return []


def _load_tickets_into_mock(
    zendesk: MockZendeskService,
    tickets: list[dict],
    *,
    sla_offset_minutes: int | None = None,
) -> list[dict]:
    """Insert tickets into the mock Zendesk service.

    If *sla_offset_minutes* is given, override each ticket's SLA deadline to
    be that many minutes from now (useful for fast simulations).
    """
    now = datetime.now(timezone.utc)
    loaded: list[dict] = []
    for ticket in tickets:
        t = dict(ticket)
        if sla_offset_minutes is not None:
            t["sla_deadline"] = (
                now + timedelta(minutes=sla_offset_minutes)
            ).isoformat()
        zendesk._tickets[t["id"]] = t
        loaded.append(t)
    return loaded


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


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

    # demo command - rich automated demo with TUI
    subparsers.add_parser(
        "demo",
        help="Run an automated demo with rich dashboard output",
    )

    return parser.parse_args()


# ---------------------------------------------------------------------------
# Mock service factory
# ---------------------------------------------------------------------------


def create_mock_services() -> tuple[MockZendeskService, MockSLARulesService]:
    """Create mock services for demo mode."""
    zendesk = MockZendeskService()
    sla_rules = MockSLARulesService()
    return zendesk, sla_rules


# ---------------------------------------------------------------------------
# monitor command
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# status command
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# override command
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# simulate command
# ---------------------------------------------------------------------------


async def run_simulation(config: SLAGuardianConfig) -> None:
    """Run a fast simulation with pre-loaded tickets and short intervals.

    Loads sample tickets, starts the worker with accelerated timers,
    and displays real-time status updates.
    """
    client = await Client.connect(
        config.temporal_address, namespace=config.temporal_namespace
    )

    zendesk, sla_rules = create_mock_services()

    # Load fixture tickets with near-future SLA deadlines for fast sim
    fixture_tickets = _load_fixture_tickets()
    if fixture_tickets:
        loaded = _load_tickets_into_mock(zendesk, fixture_tickets, sla_offset_minutes=3)
        console.print(
            f"[blue]Loaded {len(loaded)} sample tickets into mock Zendesk[/blue]"
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


# ---------------------------------------------------------------------------
# Rich display helpers for the demo
# ---------------------------------------------------------------------------

_SLA_STATUS_STYLE: dict[str, str] = {
    "compliant": "green",
    "at_risk": "bold yellow",
    "breached": "bold red",
    "resolved": "bold cyan",
}

_SENTIMENT_STYLE: dict[str, str] = {
    "positive": "green",
    "satisfied": "green",
    "neutral": "dim",
    "concerned": "yellow",
    "frustrated": "bold yellow",
    "angry": "bold red",
}

_TIER_STYLE: dict[str, str] = {
    "l1": "dim",
    "l2": "yellow",
    "l3": "bold red",
    "manager": "bold white on red",
}


def _build_ticket_table(
    tickets: list[dict],
    monitor_states: dict[str, TicketMonitorState],
) -> Table:
    """Build a rich table showing all monitored tickets."""
    table = Table(
        show_header=True,
        header_style="bold white on blue",
        border_style="bright_blue",
        box=box.ROUNDED,
        expand=True,
        padding=(0, 1),
        title="[bold]Monitored Tickets[/bold]",
        title_style="bold cyan",
    )
    table.add_column("Ticket ID", style="bold", ratio=1)
    table.add_column("Customer", ratio=2)
    table.add_column("Subject", ratio=3)
    table.add_column("Priority", justify="center", ratio=1)
    table.add_column("SLA Status", justify="center", ratio=1)
    table.add_column("Tier", justify="center", ratio=1)
    table.add_column("Sentiment", justify="center", ratio=1)
    table.add_column("Time to SLA", justify="right", ratio=1)

    now = datetime.now(timezone.utc)

    for t in tickets:
        tid = t.get("id", "?")
        customer_name = t.get("requester", {}).get("name", "Unknown")
        customer_tier = t.get("requester", {}).get("tier", "standard")
        subject = t.get("subject", "")
        if len(subject) > 35:
            subject = subject[:32] + "..."
        priority = t.get("priority", "normal")

        state = monitor_states.get(tid)

        # SLA status
        sla_status = state.sla_status.value if state else "pending"
        sla_style = _SLA_STATUS_STYLE.get(sla_status, "dim")

        # Current tier
        tier = state.current_tier.value if state else "l1"
        tier_style = _TIER_STYLE.get(tier, "dim")

        # Sentiment
        sentiment = ""
        if state and state.sentiment:
            sentiment = state.sentiment.overall_sentiment
        sent_style = _SENTIMENT_STYLE.get(sentiment, "dim")

        # Time to SLA
        sla_deadline_raw = t.get("sla_deadline", "")
        time_str = ""
        time_style = "dim"
        if sla_deadline_raw:
            try:
                if isinstance(sla_deadline_raw, str):
                    deadline = datetime.fromisoformat(sla_deadline_raw)
                else:
                    deadline = sla_deadline_raw
                if deadline.tzinfo is None:
                    deadline = deadline.replace(tzinfo=timezone.utc)
                remaining = (deadline - now).total_seconds()
                if remaining <= 0:
                    hrs = int(abs(remaining) // 3600)
                    mins = int((abs(remaining) % 3600) // 60)
                    time_str = f"-{hrs}h {mins}m"
                    time_style = "bold red"
                else:
                    hrs = int(remaining // 3600)
                    mins = int((remaining % 3600) // 60)
                    time_str = f"{hrs}h {mins}m"
                    if remaining < 3600:
                        time_style = "bold red"
                    elif remaining < 7200:
                        time_style = "bold yellow"
                    else:
                        time_style = "green"
            except (ValueError, TypeError):
                time_str = "N/A"

        # Priority style
        prio_style = {
            "urgent": "bold red",
            "high": "bold yellow",
            "normal": "",
            "low": "dim",
        }.get(priority, "")

        # Customer name with tier badge
        tier_badge = {
            "enterprise": " [bold magenta](ENT)[/]",
            "premium": " [bold cyan](PRE)[/]",
            "standard": "",
        }.get(customer_tier, "")

        table.add_row(
            Text(tid, style="bold"),
            Text.from_markup(f"{customer_name}{tier_badge}"),
            subject,
            Text(priority.upper(), style=prio_style),
            Text(sla_status.upper().replace("_", " "), style=sla_style),
            Text(tier.upper(), style=tier_style),
            Text(sentiment or "-", style=sent_style),
            Text(time_str, style=time_style),
        )

    return table


def _build_escalation_log(
    monitor_states: dict[str, TicketMonitorState],
) -> Panel:
    """Build a panel showing all escalation events."""
    table = Table(
        show_header=True,
        header_style="bold white on red",
        border_style="red",
        box=box.SIMPLE_HEAVY,
        expand=True,
        padding=(0, 1),
    )
    table.add_column("Ticket", style="bold", ratio=1)
    table.add_column("From", justify="center", ratio=1)
    table.add_column("To", justify="center", ratio=1)
    table.add_column("Reason", ratio=4)

    found = False
    for tid, state in monitor_states.items():
        for action in state.escalation_history:
            found = True
            from_style = _TIER_STYLE.get(action.from_tier.value, "")
            to_style = _TIER_STYLE.get(action.to_tier.value, "")
            table.add_row(
                tid,
                Text(action.from_tier.value.upper(), style=from_style),
                Text(action.to_tier.value.upper(), style=to_style),
                action.reason[:80],
            )

    if not found:
        table.add_row("", "", "", Text("No escalations yet", style="dim italic"))

    return Panel(
        table,
        title="[bold red]Escalation Log[/bold red]",
        border_style="red",
    )


def _build_sentiment_panel(
    monitor_states: dict[str, TicketMonitorState],
) -> Panel:
    """Build a panel showing sentiment reports."""
    table = Table(
        show_header=True,
        header_style="bold white on dark_green",
        border_style="green",
        box=box.SIMPLE,
        expand=True,
        padding=(0, 1),
    )
    table.add_column("Ticket", style="bold", ratio=1)
    table.add_column("Sentiment", justify="center", ratio=1)
    table.add_column("Frust.", justify="center", ratio=1)
    table.add_column("Trajectory", justify="center", ratio=1)
    table.add_column("Risk", justify="center", ratio=1)
    table.add_column("Key Insight", ratio=4)

    for tid, state in monitor_states.items():
        if state.sentiment:
            s = state.sentiment
            sent_style = _SENTIMENT_STYLE.get(s.overall_sentiment, "")
            traj_style = {
                "worsening": "bold red",
                "stable": "dim",
                "improving": "green",
            }.get(s.frustration_trajectory, "")
            risk_style = (
                "bold red" if s.escalation_risk >= 0.7
                else "yellow" if s.escalation_risk >= 0.4
                else "green"
            )
            insight = s.actionable_insights[0] if s.actionable_insights else "-"
            if len(insight) > 60:
                insight = insight[:57] + "..."
            table.add_row(
                tid,
                Text(s.overall_sentiment.upper(), style=sent_style),
                Text(f"{s.frustration_score}/10", style=sent_style),
                Text(s.frustration_trajectory.upper(), style=traj_style),
                Text(f"{s.escalation_risk:.0%}", style=risk_style),
                insight,
            )

    return Panel(
        table,
        title="[bold green]Sentiment Analysis[/bold green]",
        border_style="green",
    )


def _build_urgency_panel(
    monitor_states: dict[str, TicketMonitorState],
) -> Panel:
    """Build a panel showing urgency classification details."""
    table = Table(
        show_header=True,
        header_style="bold white on dark_orange",
        border_style="dark_orange",
        box=box.SIMPLE,
        expand=True,
        padding=(0, 1),
    )
    table.add_column("Ticket", style="bold", ratio=1)
    table.add_column("Orig.", justify="center", ratio=1)
    table.add_column("Assessed", justify="center", ratio=1)
    table.add_column("Score", justify="center", ratio=1)
    table.add_column("Override?", justify="center", ratio=1)
    table.add_column("Signals", ratio=3)
    table.add_column("Time Pressure", ratio=2)

    for tid, state in monitor_states.items():
        if state.urgency:
            u = state.urgency
            override_text = "YES" if u.priority_override else "-"
            override_style = "bold red" if u.priority_override else "dim"
            score_style = (
                "bold red" if u.urgency_score >= 0.8
                else "bold yellow" if u.urgency_score >= 0.6
                else "green"
            )
            signals_str = ", ".join(u.signals_detected[:4])
            if len(u.signals_detected) > 4:
                signals_str += f" (+{len(u.signals_detected) - 4})"
            table.add_row(
                tid,
                u.original_priority.upper(),
                Text(u.assessed_priority.upper(), style=score_style),
                Text(f"{u.urgency_score:.2f}", style=score_style),
                Text(override_text, style=override_style),
                signals_str,
                u.time_pressure,
            )

    return Panel(
        table,
        title="[bold dark_orange]Urgency Classification[/bold dark_orange]",
        border_style="dark_orange",
    )


# ---------------------------------------------------------------------------
# demo command
# ---------------------------------------------------------------------------


async def run_demo(config: SLAGuardianConfig) -> None:
    """Run an automated, richly-displayed demo of the SLA Guardian.

    Starts a worker, loads fixture tickets with short SLA deadlines,
    and displays a live dashboard.  Demonstrates:
      - Ticket scanning and monitoring
      - Urgency classification and sentiment analysis
      - Automatic escalation through tiers
      - Escalation override signal
      - Ticket resolution signal
    """
    console.print()
    console.rule("[bold blue]SLA Guardian -- Automated Demo[/bold blue]")
    console.print()
    console.print(
        Panel(
            "[bold]This demo shows the SLA Guardian workflow system in action.[/bold]\n"
            "\n"
            "The guardian scans for open tickets, classifies urgency, analyzes\n"
            "customer sentiment, and automatically escalates tickets that are\n"
            "approaching their SLA deadline.\n"
            "\n"
            "We load 5 sample tickets with short SLA deadlines so you can\n"
            "watch escalations happen in real time. The demo also demonstrates\n"
            "override signals (cancelling an escalation) and resolution signals\n"
            "(marking a ticket as resolved).\n"
            "\n"
            "[dim]Temporal durable timers ensure no escalation is ever lost,\n"
            "even across worker restarts.[/dim]",
            title="[bold cyan]About this Demo[/bold cyan]",
            border_style="cyan",
            padding=(1, 2),
        )
    )
    console.print()

    # ---- Connect to Temporal ----
    console.print("[dim]Connecting to Temporal...[/dim]")
    client = await Client.connect(
        config.temporal_address, namespace=config.temporal_namespace
    )
    console.print("[green]Connected to Temporal.[/green]")

    # ---- Create mock services and load fixtures ----
    zendesk, sla_rules = create_mock_services()
    fixture_tickets = _load_fixture_tickets()

    if not fixture_tickets:
        console.print(
            "[bold red]No fixture tickets found. Cannot run demo.[/bold red]"
        )
        return

    # Override SLA deadlines to 2-3 minutes from now for fast demo
    loaded = _load_tickets_into_mock(
        zendesk, fixture_tickets, sla_offset_minutes=2
    )
    console.print(
        f"[blue]Loaded {len(loaded)} tickets with SLA deadlines 2 minutes from now[/blue]"
    )

    # Show loaded tickets
    console.print()
    _print_loaded_tickets(loaded)
    console.print()

    activities = SLAGuardianActivities(zendesk=zendesk, sla_rules=sla_rules)

    # Demo uses very fast intervals
    demo_scan_interval = 5  # 5 seconds
    demo_escalation_buffer = 1  # 1 minute before SLA

    task_queue = config.task_queue + "-demo"

    worker = Worker(
        client,
        task_queue=task_queue,
        workflows=[SLAGuardianWorkflow, TicketMonitorWorkflow],
        activities=[
            activities.scan_open_tickets,
            activities.classify_urgency,
            activities.analyze_sentiment,
            activities.draft_escalation,
            activities.escalate_ticket,
        ],
    )

    guardian_id = "sla-guardian-demo"

    # ---- Start worker in background ----
    console.print(
        f"[dim]Starting worker on task queue '{task_queue}'...[/dim]"
    )

    async def _run_worker() -> None:
        await worker.run()

    worker_task = asyncio.create_task(_run_worker())

    # Small pause for worker to be ready
    await asyncio.sleep(1)

    # ---- Start guardian workflow ----
    console.print("[dim]Starting SLA Guardian workflow...[/dim]")
    try:
        guardian_handle = await client.start_workflow(
            SLAGuardianWorkflow.run,
            args=[None, demo_scan_interval, demo_escalation_buffer],
            id=guardian_id,
            task_queue=task_queue,
        )
        console.print(f"[green]Guardian workflow started: {guardian_handle.id}[/green]")
    except Exception:
        console.print(
            f"[yellow]Guardian {guardian_id} may already be running, getting handle[/yellow]"
        )
        guardian_handle = client.get_workflow_handle(guardian_id)

    console.print()
    console.rule("[bold cyan]Phase 1: Scanning & Analysis[/bold cyan]")
    console.print(
        "[dim]The guardian scans for tickets and starts a monitor workflow for each.\n"
        "Each monitor classifies urgency, analyzes sentiment, then waits on a durable timer.[/dim]"
    )
    console.print()

    # ---- Poll and display states ----
    ticket_ids = [t["id"] for t in loaded]

    # Wait for monitors to be created and do initial analysis
    for poll_round in range(1, 25):
        await asyncio.sleep(3)

        monitor_states = await _query_monitor_states(client, ticket_ids)
        analyzed_count = sum(
            1 for s in monitor_states.values()
            if s.urgency is not None and s.sentiment is not None
        )

        console.print(
            f"[dim]Poll {poll_round}: {len(monitor_states)}/{len(ticket_ids)} monitors "
            f"active, {analyzed_count} fully analyzed[/dim]"
        )

        if analyzed_count >= len(ticket_ids):
            break

    # Show full analysis results
    monitor_states = await _query_monitor_states(client, ticket_ids)

    console.print()
    console.print(_build_ticket_table(loaded, monitor_states))
    console.print()
    console.print(_build_urgency_panel(monitor_states))
    console.print()
    console.print(_build_sentiment_panel(monitor_states))
    console.print()

    # ---- Phase 2: Watch escalations ----
    console.rule("[bold yellow]Phase 2: Escalation Timers[/bold yellow]")
    console.print(
        "[dim]Durable timers are counting down. As tickets approach their SLA deadline,\n"
        "the workflow drafts personalized escalation messages and executes them.[/dim]"
    )
    console.print()

    # Pick a ticket to override and one to resolve
    override_ticket_id = ticket_ids[3] if len(ticket_ids) > 3 else ticket_ids[-1]
    resolve_ticket_id = ticket_ids[4] if len(ticket_ids) > 4 else ticket_ids[-1]

    override_sent = False
    resolve_sent = False
    prev_escalation_count = 0

    for poll_round in range(1, 40):
        await asyncio.sleep(4)

        monitor_states = await _query_monitor_states(client, ticket_ids)

        # Count total escalations
        total_escalations = sum(
            len(s.escalation_history) for s in monitor_states.values()
        )

        if total_escalations > prev_escalation_count:
            new_count = total_escalations - prev_escalation_count
            console.print(
                f"[bold red]>> {new_count} new escalation(s) detected![/bold red]"
            )
            prev_escalation_count = total_escalations

            # Show updated table
            console.print()
            console.print(_build_ticket_table(loaded, monitor_states))
            console.print()
            console.print(_build_escalation_log(monitor_states))
            console.print()

        # Send override after first escalation wave
        if total_escalations >= 2 and not override_sent:
            console.print()
            console.rule(
                f"[bold magenta]Sending Override for {override_ticket_id}[/bold magenta]"
            )
            console.print(
                f"[magenta]Demonstrating escalation override: cancelling next "
                f"escalation for {override_ticket_id}.\n"
                f"This simulates a human agent deciding to handle the ticket "
                f"manually.[/magenta]"
            )
            try:
                h = client.get_workflow_handle(
                    f"ticket-monitor-{override_ticket_id}"
                )
                await h.signal(TicketMonitorWorkflow.override_escalation)
                console.print(
                    f"[bold green]Override signal sent to {override_ticket_id}[/bold green]"
                )
            except Exception as exc:
                console.print(
                    f"[yellow]Could not send override: {exc}[/yellow]"
                )
            override_sent = True
            console.print()

        # Send resolution after more escalations
        if total_escalations >= 4 and not resolve_sent:
            console.print()
            console.rule(
                f"[bold cyan]Resolving Ticket {resolve_ticket_id}[/bold cyan]"
            )
            console.print(
                f"[cyan]Demonstrating ticket resolution: marking {resolve_ticket_id} "
                f"as resolved.\nThe monitor workflow will terminate cleanly.[/cyan]"
            )
            try:
                h = client.get_workflow_handle(
                    f"ticket-monitor-{resolve_ticket_id}"
                )
                await h.signal(TicketMonitorWorkflow.ticket_resolved)
                console.print(
                    f"[bold green]Resolution signal sent to {resolve_ticket_id}[/bold green]"
                )
            except Exception as exc:
                console.print(
                    f"[yellow]Could not send resolution: {exc}[/yellow]"
                )
            resolve_sent = True
            console.print()

        # End demo after enough has happened
        if total_escalations >= 8 or (override_sent and resolve_sent and total_escalations >= 5):
            break

    # ---- Final snapshot ----
    await asyncio.sleep(2)
    monitor_states = await _query_monitor_states(client, ticket_ids)

    console.print()
    console.rule("[bold green]Final State[/bold green]")
    console.print()
    console.print(_build_ticket_table(loaded, monitor_states))
    console.print()
    console.print(_build_escalation_log(monitor_states))
    console.print()
    console.print(_build_sentiment_panel(monitor_states))
    console.print()

    # Print any escalation messages for the most-escalated ticket
    most_escalated_tid = max(
        monitor_states,
        key=lambda k: len(monitor_states[k].escalation_history),
        default=None,
    )
    if most_escalated_tid:
        state = monitor_states[most_escalated_tid]
        if state.escalation_history:
            latest = state.escalation_history[-1]
            console.print(
                Panel(
                    latest.drafted_message,
                    title=(
                        f"[bold]Latest Escalation Message for {most_escalated_tid} "
                        f"({latest.from_tier.value.upper()} -> "
                        f"{latest.to_tier.value.upper()})[/bold]"
                    ),
                    border_style="red",
                    padding=(1, 2),
                )
            )
            console.print()

    # ---- Summary ----
    total_escalations = sum(
        len(s.escalation_history) for s in monitor_states.values()
    )
    resolved_count = sum(
        1 for s in monitor_states.values()
        if s.sla_status == SLAStatus.RESOLVED
    )
    breached_count = sum(
        1 for s in monitor_states.values()
        if s.sla_status == SLAStatus.BREACHED
    )

    console.print(
        Panel(
            f"[bold]Tickets monitored:[/bold]   {len(ticket_ids)}\n"
            f"[bold]Total escalations:[/bold]   {total_escalations}\n"
            f"[bold]Tickets resolved:[/bold]    {resolved_count}\n"
            f"[bold]SLAs breached:[/bold]       {breached_count}\n"
            f"[bold]Override sent:[/bold]       {'Yes' if override_sent else 'No'}\n"
            f"[bold]Resolution sent:[/bold]     {'Yes' if resolve_sent else 'No'}\n"
            f"\n"
            f"[dim]All escalations were tracked via Temporal durable workflows.\n"
            f"No escalation can be lost, even if the worker crashes and restarts.\n"
            f"Each ticket monitor is an independent workflow with its own timer.[/dim]",
            title="[bold cyan]Demo Summary[/bold cyan]",
            border_style="cyan",
            padding=(1, 2),
        )
    )

    # Shutdown
    console.print("[dim]Shutting down guardian workflow...[/dim]")
    try:
        await guardian_handle.signal(SLAGuardianWorkflow.shutdown)
    except Exception:
        pass

    worker_task.cancel()
    try:
        await worker_task
    except (asyncio.CancelledError, Exception):
        pass

    console.print("[green]Demo complete.[/green]")


def _print_loaded_tickets(tickets: list[dict]) -> None:
    """Print a summary table of loaded fixture tickets."""
    table = Table(
        show_header=True,
        header_style="bold white on dark_blue",
        border_style="blue",
        box=box.ROUNDED,
        expand=True,
        title="[bold]Loaded Fixture Tickets[/bold]",
        title_style="bold blue",
    )
    table.add_column("ID", style="bold")
    table.add_column("Subject")
    table.add_column("Customer")
    table.add_column("Tier", justify="center")
    table.add_column("Priority", justify="center")
    table.add_column("Tags")

    for t in tickets:
        tier = t.get("requester", {}).get("tier", "standard")
        tier_style = {
            "enterprise": "bold magenta",
            "premium": "bold cyan",
            "standard": "dim",
        }.get(tier, "")
        prio = t.get("priority", "normal")
        prio_style = {
            "urgent": "bold red",
            "high": "bold yellow",
            "normal": "",
            "low": "dim",
        }.get(prio, "")

        table.add_row(
            t.get("id", "?"),
            t.get("subject", ""),
            t.get("requester", {}).get("name", "?"),
            Text(tier.upper(), style=tier_style),
            Text(prio.upper(), style=prio_style),
            ", ".join(t.get("tags", [])),
        )

    console.print(table)


async def _query_monitor_states(
    client: Client,
    ticket_ids: list[str],
) -> dict[str, TicketMonitorState]:
    """Query monitor state for each ticket, silently skipping failures."""
    states: dict[str, TicketMonitorState] = {}
    for tid in ticket_ids:
        try:
            handle = client.get_workflow_handle(f"ticket-monitor-{tid}")
            state = await handle.query(TicketMonitorWorkflow.get_monitor_state)
            states[tid] = state
        except Exception:
            pass
    return states


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


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
    elif args.command == "demo":
        asyncio.run(run_demo(config))


if __name__ == "__main__":
    main()
