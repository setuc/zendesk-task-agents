from __future__ import annotations

import argparse
import asyncio
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Worker

from .config import SLAGuardianConfig
from .workflows.guardian_workflow import SLAGuardianWorkflow
from .workflows.ticket_monitor_workflow import TicketMonitorWorkflow
from .workflows.activities import SLAGuardianActivities
from .workflows.data_types import (
    AgentResolution,
    AgentStatus,
    AgentTicketState,
    EscalationTier,
    GuardianState,
    SLAStatus,
    TicketMonitorState,
)
from .workflows.agent_ticket_workflow import AgentTicketWorkflow
from .workflows.agent_activities import AgentTicketActivities
from .fixtures.ticket_generator import generate_ticket_batch, get_ticket_ids
from common.tui import console, WorkflowDashboard

from common.services.zendesk_mock import MockZendeskService
from .services.sla_rules_mock import MockSLARulesService

from order_resolution.services.order_db_mock import MockOrderDBService
from order_resolution.services.payment_mock import MockPaymentService
from order_resolution.services.shipping_mock import MockShippingService

from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.live import Live
from rich.columns import Columns
from rich.progress import BarColumn, Progress, TextColumn, SpinnerColumn
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

    # stress-worker command - worker for the stress test
    stress_worker_parser = subparsers.add_parser(
        "stress-worker",
        help="Start a worker pre-loaded with generated tickets for stress testing",
    )
    stress_worker_parser.add_argument(
        "--tickets",
        type=int,
        default=100,
        help="Number of tickets to pre-generate (default: 100)",
    )
    stress_worker_parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for deterministic ticket generation (default: 42)",
    )
    stress_worker_parser.add_argument(
        "--sla-offset",
        type=int,
        default=3,
        help="SLA deadline offset in minutes from now (default: 3)",
    )

    # demo-stress command - stress test controller with live dashboard
    stress_demo_parser = subparsers.add_parser(
        "demo-stress",
        help="Run a stress test demo: inject 100+ tickets in waves with live dashboard",
    )
    stress_demo_parser.add_argument(
        "--tickets",
        type=int,
        default=100,
        help="Total number of tickets to generate (default: 100)",
    )
    stress_demo_parser.add_argument(
        "--waves",
        type=int,
        default=5,
        help="Number of injection waves (default: 5)",
    )
    stress_demo_parser.add_argument(
        "--wave-delay",
        type=int,
        default=10,
        help="Seconds between waves (default: 10)",
    )
    stress_demo_parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for deterministic ticket generation (default: 42)",
    )
    stress_demo_parser.add_argument(
        "--sla-offset",
        type=int,
        default=3,
        help="SLA deadline offset in minutes from now (default: 3)",
    )

    # live-inject command - inject tickets for live 3-terminal demo
    inject_parser = subparsers.add_parser(
        "live-inject", help="Inject tickets for live demo"
    )
    inject_parser.add_argument(
        "--tickets", type=int, default=100,
        help="Number of tickets to inject (default: 100)",
    )
    inject_parser.add_argument(
        "--rate", type=float, default=2.0,
        help="Tickets per second injection rate (default: 2.0)",
    )
    inject_parser.add_argument(
        "--seed", type=int, default=42,
        help="Random seed for deterministic generation (default: 42)",
    )
    inject_parser.add_argument(
        "--sla-offset", type=int, default=5,
        help="SLA deadline offset in minutes from now (default: 5)",
    )

    # live-worker command - agent worker for live 3-terminal demo
    live_worker_parser = subparsers.add_parser(
        "live-worker", help="Agent worker for live demo"
    )
    live_worker_parser.add_argument(
        "--tickets", type=int, default=100,
        help="Number of tickets to pre-generate (default: 100)",
    )
    live_worker_parser.add_argument(
        "--seed", type=int, default=42,
        help="Random seed for deterministic generation (default: 42)",
    )
    live_worker_parser.add_argument(
        "--sla-offset", type=int, default=5,
        help="SLA deadline offset in minutes from now (default: 5)",
    )
    live_worker_parser.add_argument(
        "--mock", action="store_true", default=True,
        help="Use mock agent (default)",
    )
    live_worker_parser.add_argument(
        "--live", action="store_true",
        help="Use real UC agent (requires OPENAI_API_KEY)",
    )

    # live-dashboard command - live ticket dashboard for 3-terminal demo
    dashboard_parser = subparsers.add_parser(
        "live-dashboard", help="Live ticket dashboard"
    )
    dashboard_parser.add_argument(
        "--refresh", type=float, default=2.0,
        help="Refresh interval in seconds (default: 2.0)",
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
        config.temporal_address, namespace=config.temporal_namespace,
        data_converter=pydantic_data_converter,
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
        config.temporal_address, namespace=config.temporal_namespace,
        data_converter=pydantic_data_converter,
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
        config.temporal_address, namespace=config.temporal_namespace,
        data_converter=pydantic_data_converter,
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
        config.temporal_address, namespace=config.temporal_namespace,
        data_converter=pydantic_data_converter,
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
        config.temporal_address, namespace=config.temporal_namespace,
        data_converter=pydantic_data_converter,
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


async def _query_monitor_states_batch(
    client: Client,
    ticket_ids: list[str],
    *,
    concurrency: int = 20,
) -> dict[str, TicketMonitorState]:
    """Query monitor states concurrently in batches for the stress demo."""
    states: dict[str, TicketMonitorState] = {}
    sem = asyncio.Semaphore(concurrency)

    async def _query_one(tid: str) -> None:
        async with sem:
            try:
                handle = client.get_workflow_handle(f"ticket-monitor-{tid}")
                state = await handle.query(TicketMonitorWorkflow.get_monitor_state)
                states[tid] = state
            except Exception:
                pass

    await asyncio.gather(*[_query_one(tid) for tid in ticket_ids])
    return states


# ---------------------------------------------------------------------------
# stress-worker command
# ---------------------------------------------------------------------------


async def run_stress_worker(
    config: SLAGuardianConfig,
    args: argparse.Namespace,
) -> None:
    """Start a Temporal worker pre-loaded with generated tickets.

    This is designed to run in Terminal 1.  The demo-stress command
    (Terminal 2) uses the same seed so both processes agree on ticket IDs.
    """
    ticket_count = args.tickets
    seed = args.seed
    sla_offset = args.sla_offset

    console.print()
    console.rule("[bold blue]SLA Guardian -- Stress Test Worker[/bold blue]")
    console.print()

    console.print(
        f"[dim]Generating {ticket_count} tickets with seed={seed} "
        f"and SLA offset={sla_offset}min...[/dim]"
    )
    tickets = generate_ticket_batch(
        ticket_count, seed=seed, sla_offset_minutes=sla_offset
    )
    console.print(f"[green]Generated {len(tickets)} tickets.[/green]")

    zendesk, sla_rules = create_mock_services()
    loaded = _load_tickets_into_mock(zendesk, tickets)
    console.print(f"[blue]Loaded {len(loaded)} tickets into mock Zendesk.[/blue]")

    # Show a quick category breakdown
    categories: dict[str, int] = {}
    tiers: dict[str, int] = {}
    priorities: dict[str, int] = {}
    for t in tickets:
        tier = t.get("requester", {}).get("tier", "standard")
        tiers[tier] = tiers.get(tier, 0) + 1
        pri = t.get("priority", "normal")
        priorities[pri] = priorities.get(pri, 0) + 1
        # Infer category from tags
        tags = t.get("tags", [])
        if "escalation" in tags or "urgent" in tags:
            cat = "crisis/escalation"
        elif "billing" in tags:
            cat = "billing"
        elif "shipping" in tags or "delivery" in tags:
            cat = "shipping"
        elif "api" in tags or "integration" in tags or "technical" in tags:
            cat = "technical"
        elif "feature_request" in tags:
            cat = "feature"
        elif "authentication" in tags or "account" in tags:
            cat = "account"
        else:
            cat = "general"
        categories[cat] = categories.get(cat, 0) + 1

    summary_table = Table(
        show_header=True,
        header_style="bold white on dark_blue",
        border_style="blue",
        box=box.ROUNDED,
        expand=False,
        title="[bold]Ticket Distribution[/bold]",
        title_style="bold blue",
    )
    summary_table.add_column("Dimension", style="bold")
    summary_table.add_column("Breakdown", ratio=3)

    tier_str = "  ".join(f"{k}: {v}" for k, v in sorted(tiers.items()))
    pri_str = "  ".join(f"{k}: {v}" for k, v in sorted(priorities.items()))
    cat_str = "  ".join(f"{k}: {v}" for k, v in sorted(categories.items()))

    summary_table.add_row("Tiers", tier_str)
    summary_table.add_row("Priorities", pri_str)
    summary_table.add_row("Categories", cat_str)

    console.print()
    console.print(summary_table)
    console.print()

    activities = SLAGuardianActivities(zendesk=zendesk, sla_rules=sla_rules)
    task_queue = config.task_queue + "-stress"

    worker = Worker(
        client=await Client.connect(
            config.temporal_address, namespace=config.temporal_namespace,
            data_converter=pydantic_data_converter,
        ),
        task_queue=task_queue,
        workflows=[SLAGuardianWorkflow, TicketMonitorWorkflow],
        activities=[
            activities.scan_open_tickets,
            activities.classify_urgency,
            activities.analyze_sentiment,
            activities.draft_escalation,
            activities.escalate_ticket,
        ],
        max_concurrent_activities=50,
        max_concurrent_workflow_tasks=50,
    )

    console.print(f"[bold green]Worker started on task queue '{task_queue}'.[/bold green]")
    console.print("[dim]Waiting for workflows from the demo-stress controller...[/dim]")
    console.print("[green]Press Ctrl+C to stop.[/green]")
    await worker.run()


# ---------------------------------------------------------------------------
# demo-stress command
# ---------------------------------------------------------------------------

_STRESS_SLA_STATUS_STYLE: dict[str, str] = {
    "compliant": "green",
    "at_risk": "bold yellow",
    "breached": "bold red",
    "resolved": "bold cyan",
}

_STRESS_SENTIMENT_STYLE: dict[str, str] = {
    "positive": "green",
    "satisfied": "green",
    "neutral": "dim",
    "concerned": "yellow",
    "frustrated": "bold yellow",
    "angry": "bold red",
}

_STRESS_TIER_STYLE: dict[str, str] = {
    "l1": "dim",
    "l2": "yellow",
    "l3": "bold red",
    "manager": "bold white on red",
}


def _build_stress_stats_panel(
    total: int,
    processed: int,
    escalated: int,
    at_risk: int,
    breached: int,
    resolved: int,
    avg_processing_ms: float,
    wave_info: str,
    elapsed: float,
) -> Panel:
    """Build the stats bar for the stress dashboard."""
    cols_data = [
        f"[bold]Total:[/bold] {total}",
        f"[bold green]Processed:[/bold green] {processed}",
        f"[bold yellow]Escalated:[/bold yellow] {escalated}",
        f"[bold yellow]At Risk:[/bold yellow] {at_risk}",
        f"[bold red]Breached:[/bold red] {breached}",
        f"[bold cyan]Resolved:[/bold cyan] {resolved}",
        f"[bold]Avg Time:[/bold] {avg_processing_ms:.0f}ms",
        f"[dim]Elapsed: {elapsed:.0f}s[/dim]",
    ]
    stats_text = "  |  ".join(cols_data)
    return Panel(
        f"{stats_text}\n[dim]{wave_info}[/dim]",
        title="[bold cyan]Stress Test Dashboard[/bold cyan]",
        border_style="cyan",
        padding=(0, 1),
    )


def _build_stress_ticket_table(
    tickets: list[dict],
    monitor_states: dict[str, TicketMonitorState],
    *,
    max_rows: int = 40,
) -> Table:
    """Build a compact ticket table for the stress demo."""
    table = Table(
        show_header=True,
        header_style="bold white on blue",
        border_style="bright_blue",
        box=box.SIMPLE_HEAVY,
        expand=True,
        padding=(0, 1),
    )
    table.add_column("Ticket", style="bold", width=12)
    table.add_column("Customer", width=18)
    table.add_column("Tier", justify="center", width=5)
    table.add_column("Prio", justify="center", width=7)
    table.add_column("Category", width=12)
    table.add_column("Urgency", justify="center", width=8)
    table.add_column("Sentiment", justify="center", width=12)
    table.add_column("SLA", justify="center", width=10)
    table.add_column("Esc Tier", justify="center", width=8)
    table.add_column("Time", justify="right", width=8)

    shown = 0
    for t in tickets:
        if shown >= max_rows:
            break
        tid = t.get("id", "?")
        state = monitor_states.get(tid)
        if state is None:
            continue

        customer_name = t.get("requester", {}).get("name", "?")
        if len(customer_name) > 16:
            customer_name = customer_name[:14] + ".."
        customer_tier = t.get("requester", {}).get("tier", "std")
        priority = t.get("priority", "normal")

        # Infer category from tags
        tags = t.get("tags", [])
        if "escalation" in tags and "urgent" in tags:
            cat = "crisis"
        elif "billing" in tags:
            cat = "billing"
        elif "shipping" in tags:
            cat = "shipping"
        elif "api" in tags or "technical" in tags:
            cat = "technical"
        elif "feature_request" in tags:
            cat = "feature"
        elif "account" in tags or "authentication" in tags:
            cat = "account"
        else:
            cat = "general"

        # Urgency score
        urgency_str = ""
        urgency_style = "dim"
        if state.urgency:
            score = state.urgency.urgency_score
            urgency_str = f"{score:.2f}"
            if score >= 0.80:
                urgency_style = "bold red"
            elif score >= 0.60:
                urgency_style = "bold yellow"
            elif score >= 0.40:
                urgency_style = ""
            else:
                urgency_style = "dim"

        # Sentiment
        sentiment_str = ""
        sent_style = "dim"
        if state.sentiment:
            sentiment_str = state.sentiment.overall_sentiment
            sent_style = _STRESS_SENTIMENT_STYLE.get(sentiment_str, "dim")

        # SLA status
        sla_str = state.sla_status.value.upper().replace("_", " ")
        sla_style = _STRESS_SLA_STATUS_STYLE.get(state.sla_status.value, "dim")

        # Escalation tier
        tier_str = state.current_tier.value.upper()
        tier_style = _STRESS_TIER_STYLE.get(state.current_tier.value, "dim")

        # Processing time placeholder (time since created_at)
        now = datetime.now(timezone.utc)
        created_raw = t.get("created_at", "")
        time_str = ""
        try:
            created = datetime.fromisoformat(created_raw)
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            sla_raw = t.get("sla_deadline", "")
            if sla_raw:
                deadline = datetime.fromisoformat(sla_raw)
                if deadline.tzinfo is None:
                    deadline = deadline.replace(tzinfo=timezone.utc)
                remaining = (deadline - now).total_seconds()
                if remaining <= 0:
                    mins = int(abs(remaining) // 60)
                    time_str = f"-{mins}m"
                else:
                    mins = int(remaining // 60)
                    time_str = f"{mins}m"
        except (ValueError, TypeError):
            pass

        # Tier badge
        tier_badge = {"enterprise": "ENT", "premium": "PRE", "standard": "STD"}.get(
            customer_tier, "?"
        )
        tier_badge_style = {
            "enterprise": "bold magenta",
            "premium": "bold cyan",
            "standard": "dim",
        }.get(customer_tier, "dim")

        prio_style = {
            "urgent": "bold red",
            "high": "bold yellow",
            "normal": "",
            "low": "dim",
        }.get(priority, "")

        table.add_row(
            tid,
            customer_name,
            Text(tier_badge, style=tier_badge_style),
            Text(priority.upper(), style=prio_style),
            cat,
            Text(urgency_str, style=urgency_style),
            Text(sentiment_str, style=sent_style),
            Text(sla_str, style=sla_style),
            Text(tier_str, style=tier_style),
            time_str,
        )
        shown += 1

    return table


def _build_stress_summary(
    tickets: list[dict],
    monitor_states: dict[str, TicketMonitorState],
    elapsed: float,
) -> Panel:
    """Build a final summary panel for the stress test."""
    total = len(tickets)
    processed = len(monitor_states)

    # Urgency distribution
    urgency_buckets: dict[str, int] = {"critical (0.8+)": 0, "high (0.6-0.8)": 0, "normal (0.4-0.6)": 0, "low (<0.4)": 0}
    sentiment_counts: dict[str, int] = {}
    tier_counts: dict[str, int] = {"l1": 0, "l2": 0, "l3": 0, "manager": 0}
    sla_counts: dict[str, int] = {"compliant": 0, "at_risk": 0, "breached": 0, "resolved": 0}
    total_escalations = 0
    processing_times: list[float] = []

    top_urgency: list[tuple[str, float, str]] = []

    for tid, state in monitor_states.items():
        # SLA
        sla_counts[state.sla_status.value] = sla_counts.get(state.sla_status.value, 0) + 1

        # Escalation tier
        tier_counts[state.current_tier.value] = tier_counts.get(state.current_tier.value, 0) + 1
        total_escalations += len(state.escalation_history)

        # Urgency
        if state.urgency:
            score = state.urgency.urgency_score
            if score >= 0.80:
                urgency_buckets["critical (0.8+)"] += 1
            elif score >= 0.60:
                urgency_buckets["high (0.6-0.8)"] += 1
            elif score >= 0.40:
                urgency_buckets["normal (0.4-0.6)"] += 1
            else:
                urgency_buckets["low (<0.4)"] += 1

            customer_name = "?"
            for t in tickets:
                if t.get("id") == tid:
                    customer_name = t.get("requester", {}).get("name", "?")
                    break
            top_urgency.append((tid, score, customer_name))

        # Sentiment
        if state.sentiment:
            s = state.sentiment.overall_sentiment
            sentiment_counts[s] = sentiment_counts.get(s, 0) + 1

    top_urgency.sort(key=lambda x: x[1], reverse=True)

    # Build summary text
    lines: list[str] = []
    lines.append(f"[bold]Tickets generated:[/bold]      {total}")
    lines.append(f"[bold]Tickets processed:[/bold]      {processed}")
    lines.append(f"[bold]Total escalations:[/bold]      {total_escalations}")
    lines.append(f"[bold]Elapsed time:[/bold]           {elapsed:.1f}s")
    lines.append(f"[bold]Throughput:[/bold]              {processed / max(elapsed, 0.1):.1f} tickets/sec")
    lines.append("")
    lines.append("[bold]Urgency Distribution:[/bold]")
    for bucket, count in urgency_buckets.items():
        pct = (count / max(processed, 1)) * 100
        lines.append(f"  {bucket}: {count} ({pct:.0f}%)")
    lines.append("")
    lines.append("[bold]Sentiment Distribution:[/bold]")
    for sent in ["angry", "frustrated", "concerned", "neutral", "satisfied", "positive"]:
        count = sentiment_counts.get(sent, 0)
        if count > 0:
            pct = (count / max(processed, 1)) * 100
            style = _STRESS_SENTIMENT_STYLE.get(sent, "")
            lines.append(f"  [{style}]{sent}: {count} ({pct:.0f}%)[/{style}]")
    lines.append("")
    lines.append("[bold]SLA Status:[/bold]")
    for status_name in ["compliant", "at_risk", "breached", "resolved"]:
        count = sla_counts.get(status_name, 0)
        style = _STRESS_SLA_STATUS_STYLE.get(status_name, "")
        lines.append(f"  [{style}]{status_name}: {count}[/{style}]")
    lines.append("")
    lines.append("[bold]Escalation Tier Distribution:[/bold]")
    for tier_name in ["l1", "l2", "l3", "manager"]:
        count = tier_counts.get(tier_name, 0)
        style = _STRESS_TIER_STYLE.get(tier_name, "")
        lines.append(f"  [{style}]{tier_name.upper()}: {count}[/{style}]")
    lines.append("")
    lines.append("[bold]Top 5 Highest-Urgency Tickets:[/bold]")
    for tid, score, name in top_urgency[:5]:
        score_style = "bold red" if score >= 0.8 else "bold yellow" if score >= 0.6 else ""
        lines.append(f"  [{score_style}]{tid}[/{score_style}] - {name} (score: [{score_style}]{score:.2f}[/{score_style}])")

    lines.append("")
    lines.append(
        "[dim]Each ticket was an independent Temporal child workflow with its own\n"
        "durable timer, urgency classification, and sentiment analysis.\n"
        "100+ concurrent workflows ran with fault-tolerant execution guarantees.[/dim]"
    )

    return Panel(
        "\n".join(lines),
        title="[bold cyan]Stress Test Summary[/bold cyan]",
        border_style="cyan",
        padding=(1, 2),
    )


async def run_stress_demo(
    config: SLAGuardianConfig,
    args: argparse.Namespace,
) -> None:
    """Run the stress test controller with a live Rich dashboard.

    Designed to run in Terminal 2 while stress-worker runs in Terminal 1.
    Both use the same seed so tickets are identical.
    """
    ticket_count = args.tickets
    waves = args.waves
    wave_delay = args.wave_delay
    seed = args.seed
    sla_offset = args.sla_offset

    console.print()
    console.rule("[bold blue]SLA Guardian -- Stress Test Demo[/bold blue]")
    console.print()
    console.print(
        Panel(
            f"[bold]This stress test demonstrates Temporal orchestrating 100+ "
            f"concurrent child workflows.[/bold]\n"
            f"\n"
            f"Configuration:\n"
            f"  Tickets:     {ticket_count}\n"
            f"  Waves:       {waves} (injecting {ticket_count // waves} tickets per wave)\n"
            f"  Wave delay:  {wave_delay}s between waves\n"
            f"  Seed:        {seed} (deterministic -- worker uses the same seed)\n"
            f"  SLA offset:  {sla_offset}min\n"
            f"\n"
            f"[dim]Each ticket becomes an independent child workflow that:\n"
            f"  1. Classifies urgency via heuristic keyword analysis\n"
            f"  2. Analyzes customer sentiment across all comments\n"
            f"  3. Sets a durable timer before the SLA deadline\n"
            f"  4. Auto-escalates through L1 -> L2 -> L3 -> Manager tiers\n"
            f"\n"
            f"All workflows are fault-tolerant: if the worker crashes mid-flight,\n"
            f"every workflow resumes exactly where it left off.[/dim]",
            title="[bold cyan]About this Stress Test[/bold cyan]",
            border_style="cyan",
            padding=(1, 2),
        )
    )
    console.print()

    # Generate tickets (same seed as the worker)
    console.print(f"[dim]Generating {ticket_count} tickets with seed={seed}...[/dim]")
    tickets = generate_ticket_batch(
        ticket_count, seed=seed, sla_offset_minutes=sla_offset
    )
    ticket_ids = [t["id"] for t in tickets]
    console.print(f"[green]Generated {len(tickets)} tickets.[/green]")

    # Connect to Temporal
    console.print("[dim]Connecting to Temporal...[/dim]")
    client = await Client.connect(
        config.temporal_address, namespace=config.temporal_namespace,
        data_converter=pydantic_data_converter,
    )
    console.print("[green]Connected to Temporal.[/green]")

    task_queue = config.task_queue + "-stress"
    guardian_id = "sla-guardian-stress"

    # We'll use a very fast scan interval but only run one scan cycle
    # per wave. We start a guardian workflow that scans and picks up tickets.
    # The trick: the worker already has all tickets in mock Zendesk.
    # We use the guardian workflow's scan to discover them.
    # But we need to inject in waves. So we start a guardian per wave,
    # or we just start child workflows directly.
    #
    # Simplest approach: start child workflows directly from here.
    # That way we control the wave timing precisely.

    demo_escalation_buffer = 1  # 1 minute

    # Split tickets into waves
    tickets_per_wave = ticket_count // waves
    wave_batches: list[list[dict]] = []
    for w in range(waves):
        start_idx = w * tickets_per_wave
        end_idx = start_idx + tickets_per_wave if w < waves - 1 else ticket_count
        wave_batches.append(tickets[start_idx:end_idx])

    injected_ids: list[str] = []
    start_time = time.monotonic()

    console.print()
    console.rule("[bold yellow]Injecting Tickets in Waves[/bold yellow]")
    console.print()

    for wave_num, wave_tickets in enumerate(wave_batches, 1):
        wave_start = time.monotonic()
        wave_ids = [t["id"] for t in wave_tickets]

        console.print(
            f"[bold cyan]Wave {wave_num}/{waves}:[/bold cyan] Injecting "
            f"{len(wave_tickets)} tickets..."
        )

        # Start a TicketMonitorWorkflow for each ticket in this wave
        # We do this concurrently for speed
        sem = asyncio.Semaphore(20)

        async def _start_monitor(ticket: dict) -> str | None:
            async with sem:
                tid = ticket["id"]
                sla_deadline = ticket.get("sla_deadline", "")
                if not sla_deadline:
                    now = datetime.now(timezone.utc)
                    sla_deadline = (now + timedelta(minutes=sla_offset)).isoformat()

                try:
                    await client.start_workflow(
                        TicketMonitorWorkflow.run,
                        args=[tid, sla_deadline, demo_escalation_buffer],
                        id=f"ticket-monitor-{tid}",
                        task_queue=task_queue,
                    )
                    return tid
                except Exception as exc:
                    # Workflow may already exist from a previous run
                    return None

        results = await asyncio.gather(*[_start_monitor(t) for t in wave_tickets])
        started = sum(1 for r in results if r is not None)
        injected_ids.extend(wave_ids)

        wave_elapsed = time.monotonic() - wave_start
        console.print(
            f"  [green]Started {started} workflows in {wave_elapsed:.1f}s[/green]"
        )

        if wave_num < waves:
            console.print(
                f"  [dim]Temporal is now running {len(injected_ids)} concurrent "
                f"child workflows, each with independent durable timers.[/dim]"
            )
            console.print(
                f"  [dim]Waiting {wave_delay}s before next wave...[/dim]"
            )
            await asyncio.sleep(wave_delay)

    total_inject_time = time.monotonic() - start_time
    console.print()
    console.print(
        f"[bold green]All {ticket_count} tickets injected in "
        f"{total_inject_time:.1f}s ({ticket_count / total_inject_time:.0f} tickets/sec).[/bold green]"
    )
    console.print()

    # ---- Live dashboard phase ----
    console.rule("[bold cyan]Live Dashboard -- Monitoring Workflows[/bold cyan]")
    console.print(
        "[dim]Polling workflow states. The dashboard updates as urgency "
        "classification, sentiment analysis, and escalations complete.[/dim]"
    )
    console.print()

    # Poll until most workflows have completed initial analysis or we time out
    max_poll_rounds = 60
    poll_interval = 3

    prev_processed = 0
    prev_escalations = 0
    stable_rounds = 0

    for poll_round in range(1, max_poll_rounds + 1):
        elapsed = time.monotonic() - start_time
        monitor_states = await _query_monitor_states_batch(client, injected_ids)

        processed = len(monitor_states)
        analyzed = sum(
            1 for s in monitor_states.values()
            if s.urgency is not None and s.sentiment is not None
        )
        escalated = sum(
            1 for s in monitor_states.values()
            if len(s.escalation_history) > 0
        )
        at_risk = sum(
            1 for s in monitor_states.values()
            if s.sla_status == SLAStatus.AT_RISK
        )
        breached = sum(
            1 for s in monitor_states.values()
            if s.sla_status == SLAStatus.BREACHED
        )
        resolved = sum(
            1 for s in monitor_states.values()
            if s.sla_status == SLAStatus.RESOLVED
        )
        total_escalations = sum(
            len(s.escalation_history) for s in monitor_states.values()
        )

        # Print a progress line
        console.print(
            f"  [dim]Poll {poll_round}:[/dim] "
            f"[green]{processed}[/green] active, "
            f"[green]{analyzed}[/green] analyzed, "
            f"[yellow]{escalated}[/yellow] escalated ({total_escalations} total), "
            f"[yellow]{at_risk}[/yellow] at-risk, "
            f"[red]{breached}[/red] breached  "
            f"[dim]({elapsed:.0f}s)[/dim]"
        )

        # Print new escalation alerts
        if total_escalations > prev_escalations:
            new_esc = total_escalations - prev_escalations
            console.print(
                f"    [bold red]>> {new_esc} new escalation(s) detected![/bold red]"
            )
            prev_escalations = total_escalations

        # Check for stability (no new progress)
        if processed == prev_processed and processed >= ticket_count * 0.9:
            stable_rounds += 1
        else:
            stable_rounds = 0
        prev_processed = processed

        # Break conditions
        if stable_rounds >= 3 and analyzed >= ticket_count * 0.8:
            console.print(
                f"  [dim]Workflows have stabilized. Moving to summary.[/dim]"
            )
            break

        # Show intermediate table every 5 polls
        if poll_round % 5 == 0 and processed > 0:
            console.print()
            console.print(
                _build_stress_stats_panel(
                    total=ticket_count,
                    processed=processed,
                    escalated=escalated,
                    at_risk=at_risk,
                    breached=breached,
                    resolved=resolved,
                    avg_processing_ms=0,
                    wave_info=f"All {waves} waves injected | {total_escalations} escalation events",
                    elapsed=elapsed,
                )
            )
            console.print(
                _build_stress_ticket_table(tickets, monitor_states, max_rows=20)
            )
            console.print()

        await asyncio.sleep(poll_interval)

    # ---- Final snapshot ----
    await asyncio.sleep(2)
    elapsed = time.monotonic() - start_time
    monitor_states = await _query_monitor_states_batch(client, injected_ids)

    console.print()
    console.rule("[bold green]Final Results[/bold green]")
    console.print()

    # Final stats panel
    processed = len(monitor_states)
    escalated = sum(1 for s in monitor_states.values() if len(s.escalation_history) > 0)
    at_risk = sum(1 for s in monitor_states.values() if s.sla_status == SLAStatus.AT_RISK)
    breached = sum(1 for s in monitor_states.values() if s.sla_status == SLAStatus.BREACHED)
    resolved = sum(1 for s in monitor_states.values() if s.sla_status == SLAStatus.RESOLVED)
    total_escalations = sum(len(s.escalation_history) for s in monitor_states.values())

    console.print(
        _build_stress_stats_panel(
            total=ticket_count,
            processed=processed,
            escalated=escalated,
            at_risk=at_risk,
            breached=breached,
            resolved=resolved,
            avg_processing_ms=0,
            wave_info=f"All {waves} waves injected | {total_escalations} escalation events",
            elapsed=elapsed,
        )
    )
    console.print()

    # Show the full ticket table (capped at 40 rows for readability)
    console.print(_build_stress_ticket_table(tickets, monitor_states, max_rows=40))
    console.print()

    # Show summary
    console.print(_build_stress_summary(tickets, monitor_states, elapsed))
    console.print()

    # Show sample escalation message from the highest-urgency ticket
    top_urgency_tickets = sorted(
        [
            (tid, state)
            for tid, state in monitor_states.items()
            if state.urgency is not None
        ],
        key=lambda x: x[1].urgency.urgency_score if x[1].urgency else 0,
        reverse=True,
    )
    if top_urgency_tickets:
        tid, state = top_urgency_tickets[0]
        if state.escalation_history:
            latest = state.escalation_history[-1]
            console.print(
                Panel(
                    latest.drafted_message,
                    title=(
                        f"[bold]Sample Escalation: {tid} "
                        f"({latest.from_tier.value.upper()} -> "
                        f"{latest.to_tier.value.upper()})[/bold]"
                    ),
                    border_style="red",
                    padding=(1, 2),
                )
            )
            console.print()

    console.print("[green]Stress test complete.[/green]")


# ---------------------------------------------------------------------------
# Category helper for live demo commands
# ---------------------------------------------------------------------------

_SOLVABLE_CATEGORIES = {"billing", "shipping", "account", "password"}
_UNSOLVABLE_CATEGORIES = {"technical", "crisis", "legal", "feature"}


def _infer_category(tags: list[str]) -> str:
    """Infer a ticket category from its tags."""
    tag_set = {t.lower() for t in tags}
    if tag_set & {"escalation", "urgent", "crisis"}:
        return "crisis"
    if "billing" in tag_set:
        return "billing"
    if tag_set & {"shipping", "delivery"}:
        return "shipping"
    if tag_set & {"api", "integration", "technical"}:
        return "technical"
    if "feature_request" in tag_set:
        return "feature"
    if tag_set & {"authentication", "account", "password"}:
        return "account"
    if "legal" in tag_set:
        return "legal"
    return "general"


def _is_solvable(category: str) -> bool:
    """Return True if the category is one the mock agent can resolve."""
    return category in _SOLVABLE_CATEGORIES


# ---------------------------------------------------------------------------
# live-inject command  (Terminal 3)
# ---------------------------------------------------------------------------


async def run_live_inject(
    config: SLAGuardianConfig,
    args: argparse.Namespace,
) -> None:
    """Inject tickets into Temporal as AgentTicketWorkflow instances."""
    ticket_count = args.tickets
    rate = args.rate
    seed = args.seed
    sla_offset = args.sla_offset
    task_queue = "sla-guardian-live"

    console.print()
    console.rule("[bold magenta]SLA Guardian -- Live Inject[/bold magenta]")
    console.print()
    console.print(
        f"[dim]Generating {ticket_count} tickets  seed={seed}  "
        f"SLA offset={sla_offset}min  rate={rate} tkt/s[/dim]"
    )

    tickets = generate_ticket_batch(
        ticket_count, seed=seed, sla_offset_minutes=sla_offset
    )
    console.print(f"[green]Generated {len(tickets)} tickets.[/green]")
    console.print()

    client = await Client.connect(
        config.temporal_address,
        namespace=config.temporal_namespace,
        data_converter=pydantic_data_converter,
    )

    resolved_count = 0
    escalated_count = 0

    for i, ticket in enumerate(tickets, 1):
        ticket_id = ticket["id"]
        subject = ticket.get("subject", "")[:50]
        customer_name = ticket.get("requester", {}).get("name", "Unknown")
        tier = ticket.get("requester", {}).get("tier", "standard")
        priority = ticket.get("priority", "normal")
        tags = ticket.get("tags", [])
        category = _infer_category(tags)
        solvable = _is_solvable(category)

        metadata = {
            "customer_name": customer_name,
            "customer_tier": tier,
            "priority": priority,
            "category": category,
            "subject": ticket.get("subject", ""),
        }

        await client.start_workflow(
            AgentTicketWorkflow.run,
            args=[ticket_id, metadata],
            id=f"agent-ticket-{ticket_id}",
            task_queue=task_queue,
        )

        # Color the output
        tier_colors = {"enterprise": "cyan", "premium": "yellow", "standard": "dim"}
        pri_colors = {"urgent": "red", "high": "yellow", "normal": "white", "low": "dim"}
        solvable_str = "[green]solvable[/green]" if solvable else "[red]unsolvable[/red]"
        tier_color = tier_colors.get(tier, "white")
        pri_color = pri_colors.get(priority, "white")

        console.print(
            f"  [bold][INJECT][/bold] {ticket_id} | "
            f"{subject} | "
            f"[{tier_color}]{tier}[/{tier_color}] | "
            f"[{pri_color}]{priority.upper()}[/{pri_color}] | "
            f"{solvable_str}"
        )

        if solvable:
            resolved_count += 1
        else:
            escalated_count += 1

        if i < len(tickets):
            await asyncio.sleep(1.0 / rate)

    console.print()
    console.rule("[bold magenta]Injection Complete[/bold magenta]")
    summary_table = Table(
        show_header=False,
        box=box.SIMPLE,
        padding=(0, 2),
    )
    summary_table.add_column("Label", style="bold")
    summary_table.add_column("Value")
    summary_table.add_row("Total injected", str(len(tickets)))
    summary_table.add_row("Expected solvable", f"[green]{resolved_count}[/green]")
    summary_table.add_row("Expected unsolvable", f"[red]{escalated_count}[/red]")
    summary_table.add_row("Task queue", task_queue)
    console.print(summary_table)


# ---------------------------------------------------------------------------
# live-worker command  (Terminal 2)
# ---------------------------------------------------------------------------


async def run_live_worker(
    config: SLAGuardianConfig,
    args: argparse.Namespace,
) -> None:
    """Start the Temporal worker for AgentTicketWorkflow with live activity log."""
    ticket_count = args.tickets
    seed = args.seed
    sla_offset = args.sla_offset
    use_real_agent = getattr(args, "live", False)
    task_queue = "sla-guardian-live"

    console.print()
    console.rule("[bold blue]SLA Guardian -- Live Agent Worker[/bold blue]")
    console.print()

    # Generate tickets with the same seed as the injector
    console.print(
        f"[dim]Pre-generating {ticket_count} tickets  seed={seed}  "
        f"SLA offset={sla_offset}min[/dim]"
    )
    tickets = generate_ticket_batch(
        ticket_count, seed=seed, sla_offset_minutes=sla_offset
    )

    # Load into mock Zendesk
    zendesk = MockZendeskService()
    _load_tickets_into_mock(zendesk, tickets)
    console.print(f"[blue]Loaded {len(tickets)} tickets into mock Zendesk.[/blue]")

    # Create mock services
    order_db = MockOrderDBService()
    payment = MockPaymentService()
    shipping = MockShippingService()
    memory_store: dict = {}

    activities = AgentTicketActivities(
        zendesk=zendesk,
        order_db=order_db,
        payment=payment,
        shipping=shipping,
        memory_store=memory_store,
        use_real_agent=use_real_agent,
    )

    client = await Client.connect(
        config.temporal_address,
        namespace=config.temporal_namespace,
        data_converter=pydantic_data_converter,
    )

    worker = Worker(
        client,
        task_queue=task_queue,
        workflows=[AgentTicketWorkflow],
        activities=[activities.process_ticket],
        max_concurrent_activities=50,
        max_concurrent_workflow_tasks=50,
    )

    # Show config banner
    mode_str = "[red]LIVE UC Agent[/red]" if use_real_agent else "[green]Mock Agent[/green]"
    banner = Table(
        show_header=False,
        box=box.ROUNDED,
        border_style="blue",
        padding=(0, 2),
        title="[bold]Worker Configuration[/bold]",
        title_style="bold blue",
    )
    banner.add_column("Key", style="bold")
    banner.add_column("Value")
    banner.add_row("Task queue", task_queue)
    banner.add_row("Agent mode", mode_str)
    banner.add_row("Tickets loaded", str(len(tickets)))
    banner.add_row("Max concurrent", "50 activities / 50 workflows")
    console.print(banner)
    console.print()
    console.print("[bold green]Worker started. Waiting for workflows...[/bold green]")
    console.print("[dim]Agent activity will appear below as tickets are processed.[/dim]")
    console.print("[green]Press Ctrl+C to stop.[/green]")
    console.print()

    # Run worker + background poller concurrently
    async def _poller() -> None:
        """Background poller that prints condensed agent activity lines."""
        seen: set[str] = set()
        agent_counter = 0
        # Give the worker a moment to start
        await asyncio.sleep(2.0)

        while True:
            try:
                new_completed: list[tuple[str, AgentTicketState]] = []
                async for wf in client.list_workflows(
                    query=f"TaskQueue='{task_queue}'"
                ):
                    if wf.id in seen:
                        continue
                    try:
                        handle = client.get_workflow_handle(wf.id)
                        state: AgentTicketState = await handle.query(
                            AgentTicketWorkflow.get_state
                        )
                        if state.agent_status in (
                            AgentStatus.RESOLVED,
                            AgentStatus.ESCALATED,
                            AgentStatus.FAILED,
                        ):
                            seen.add(wf.id)
                            new_completed.append((wf.id, state))
                    except Exception:
                        pass

                for _wf_id, state in new_completed:
                    agent_counter += 1
                    label = f"A-{agent_counter:02d}"

                    # Build tool chain string
                    tool_chain = ""
                    if state.resolution and state.resolution.tool_calls:
                        tool_names = [
                            tc.tool_name.split(".")[-1]
                            for tc in state.resolution.tool_calls
                        ]
                        tool_chain = " > ".join(tool_names)

                    # Determine color and outcome
                    if state.agent_status == AgentStatus.RESOLVED:
                        color = "green"
                        summary = state.resolution.resolution_summary[:60] if state.resolution else "resolved"
                        outcome = "RESOLVED"
                    elif state.agent_status == AgentStatus.ESCALATED:
                        color = "red"
                        team = state.resolution.escalation_team if state.resolution else "unknown"
                        outcome = f"ESCALATED ({team})"
                        summary = ""
                    else:
                        color = "yellow"
                        outcome = "FAILED"
                        summary = ""

                    proc_time = ""
                    if state.resolution and state.resolution.processing_time_ms:
                        proc_time = f" [{state.resolution.processing_time_ms:.1f}ms]"

                    mem_flag = ""
                    if state.resolution and state.resolution.memory_hit:
                        mem_flag = " [yellow]*MEM*[/yellow]"

                    console.print(
                        f"  [{color}][{label}][/{color}] "
                        f"{state.ticket_id} {state.category} > "
                        f"{tool_chain} > "
                        f"[{color}]{outcome}[/{color}]"
                        f"{' -- ' + summary if summary else ''}"
                        f"{proc_time}{mem_flag}"
                    )

            except Exception:
                pass

            await asyncio.sleep(2.0)

    try:
        await asyncio.gather(
            worker.run(),
            _poller(),
        )
    except asyncio.CancelledError:
        pass


# ---------------------------------------------------------------------------
# live-dashboard command  (Terminal 1)
# ---------------------------------------------------------------------------

_LIVE_STATUS_STYLE: dict[str, str] = {
    "queued": "dim",
    "investigating": "yellow",
    "resolving": "cyan",
    "escalating": "bold yellow",
    "resolved": "green",
    "escalated": "bold red",
    "failed": "red",
}

_LIVE_TIER_STYLE: dict[str, str] = {
    "enterprise": "bold cyan",
    "premium": "bold yellow",
    "standard": "dim",
}

_LIVE_PRIORITY_STYLE: dict[str, str] = {
    "urgent": "bold red",
    "high": "bold yellow",
    "normal": "white",
    "low": "dim",
}


async def _poll_live_states(
    client: Client,
    task_queue: str,
) -> dict[str, AgentTicketState]:
    """Query all workflows on the live task queue and return their states."""
    states: dict[str, AgentTicketState] = {}
    async for wf in client.list_workflows(
        query=f"TaskQueue='{task_queue}'"
    ):
        try:
            handle = client.get_workflow_handle(wf.id)
            state: AgentTicketState = await handle.query(
                AgentTicketWorkflow.get_state
            )
            states[wf.id] = state
        except Exception:
            pass
    return states


def _build_live_table(states: dict[str, AgentTicketState]) -> Table:
    """Build a Rich Table from the collected workflow states."""
    table = Table(
        show_header=True,
        header_style="bold white on dark_blue",
        border_style="blue",
        box=box.SIMPLE_HEAVY,
        expand=True,
        title="[bold]SLA Guardian -- Live Agent Dashboard[/bold]",
        title_style="bold cyan",
    )
    table.add_column("Ticket", style="bold", width=12)
    table.add_column("Customer", width=18)
    table.add_column("Tier", width=10)
    table.add_column("Priority", width=8)
    table.add_column("Category", width=12)
    table.add_column("Status", width=14)
    table.add_column("Resolution", ratio=2)
    table.add_column("Tools", width=5, justify="center")
    table.add_column("Mem", width=4, justify="center")
    table.add_column("Time", width=8, justify="right")

    # Sort: in-progress first, then by ticket_id
    def _sort_key(item: tuple[str, AgentTicketState]) -> tuple[int, str]:
        s = item[1]
        order = {
            AgentStatus.INVESTIGATING: 0,
            AgentStatus.RESOLVING: 1,
            AgentStatus.ESCALATING: 2,
            AgentStatus.QUEUED: 3,
            AgentStatus.RESOLVED: 4,
            AgentStatus.ESCALATED: 5,
            AgentStatus.FAILED: 6,
        }
        return (order.get(s.agent_status, 9), s.ticket_id)

    sorted_states = sorted(states.items(), key=_sort_key)

    # Counters for stats
    total = len(sorted_states)
    resolved = 0
    escalated = 0
    working = 0
    queued = 0
    failed = 0
    memory_hits = 0
    total_time_ms = 0.0
    timed_count = 0

    for _wf_id, state in sorted_states:
        # Count stats
        if state.agent_status == AgentStatus.RESOLVED:
            resolved += 1
        elif state.agent_status == AgentStatus.ESCALATED:
            escalated += 1
        elif state.agent_status == AgentStatus.QUEUED:
            queued += 1
        elif state.agent_status == AgentStatus.FAILED:
            failed += 1
        else:
            working += 1

        # Resolution summary
        resolution_text = ""
        tool_count = "0"
        mem_mark = ""
        time_str = ""

        if state.resolution:
            res = state.resolution
            resolution_text = res.resolution_summary[:55] + (
                "..." if len(res.resolution_summary) > 55 else ""
            ) if res.resolution_summary else ""
            tool_count = str(len(res.tool_calls))
            if res.memory_hit:
                mem_mark = "[yellow]Y[/yellow]"
                memory_hits += 1
            if res.processing_time_ms:
                time_str = f"{res.processing_time_ms:.0f}ms"
                total_time_ms += res.processing_time_ms
                timed_count += 1

        tier_style = _LIVE_TIER_STYLE.get(state.customer_tier, "white")
        pri_style = _LIVE_PRIORITY_STYLE.get(state.priority, "white")
        status_style = _LIVE_STATUS_STYLE.get(state.agent_status.value, "white")

        table.add_row(
            state.ticket_id,
            state.customer_name[:17],
            f"[{tier_style}]{state.customer_tier}[/{tier_style}]",
            f"[{pri_style}]{state.priority.upper()}[/{pri_style}]",
            state.category,
            f"[{status_style}]{state.agent_status.value.upper()}[/{status_style}]",
            resolution_text,
            tool_count,
            mem_mark,
            time_str,
        )

    # Stats bar
    avg_time = f"{total_time_ms / timed_count:.0f}ms" if timed_count else "--"
    stats_text = (
        f"  [bold]Total:[/bold] {total}  |  "
        f"[green]Resolved:[/green] {resolved}  |  "
        f"[red]Escalated:[/red] {escalated}  |  "
        f"[yellow]Working:[/yellow] {working}  |  "
        f"[dim]Queued:[/dim] {queued}  |  "
        f"[red]Failed:[/red] {failed}  |  "
        f"[bold]Avg Time:[/bold] {avg_time}  |  "
        f"[yellow]Memory Hits:[/yellow] {memory_hits}"
    )
    table.caption = stats_text
    table.caption_style = ""

    return table


async def run_live_dashboard(
    config: SLAGuardianConfig,
    args: argparse.Namespace,
) -> None:
    """Run the live dashboard that polls Temporal and shows a Rich table."""
    refresh = args.refresh
    task_queue = "sla-guardian-live"

    client = await Client.connect(
        config.temporal_address,
        namespace=config.temporal_namespace,
        data_converter=pydantic_data_converter,
    )

    console.print()
    console.print(
        f"[bold cyan]Live Dashboard[/bold cyan] polling task queue "
        f"[bold]'{task_queue}'[/bold] every {refresh}s"
    )
    console.print("[green]Press Ctrl+C to stop.[/green]")
    console.print()

    with Live(
        _build_live_table({}),
        console=console,
        refresh_per_second=0.5,
        vertical_overflow="crop",
    ) as live:
        while True:
            try:
                states = await _poll_live_states(client, task_queue)
                live.update(_build_live_table(states))
            except Exception as exc:
                console.print(f"[red]Poll error: {exc}[/red]")
            await asyncio.sleep(refresh)


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
    elif args.command == "stress-worker":
        asyncio.run(run_stress_worker(config, args))
    elif args.command == "demo-stress":
        asyncio.run(run_stress_demo(config, args))
    elif args.command == "live-inject":
        asyncio.run(run_live_inject(config, args))
    elif args.command == "live-worker":
        asyncio.run(run_live_worker(config, args))
    elif args.command == "live-dashboard":
        asyncio.run(run_live_dashboard(config, args))


if __name__ == "__main__":
    main()
