from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Worker

from .config import OnboardingConfig
from .workflows.onboarding_workflow import OnboardingWorkflow
from .workflows.integration_test_workflow import IntegrationTestWorkflow
from .workflows.activities import OnboardingActivities
from .workflows.data_types import (
    CheckinResponse,
    IntegrationTestResult,
    Milestone,
    MilestoneType,
    OnboardingPlan,
    OnboardingState,
)
from common.tui import console, WorkflowDashboard
from common.services.zendesk_mock import MockZendeskService
from .services.integration_test_mock import MockIntegrationTestService
from .services.email_mock import MockEmailService


# ======================================================================
# Fixture loading
# ======================================================================

_FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _load_customer_fixture() -> dict:
    """Load the sample customer fixture data."""
    path = _FIXTURE_DIR / "sample_customer.json"
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {}


def _load_milestones_fixture() -> list[dict]:
    """Load milestones from the fixture file."""
    path = _FIXTURE_DIR / "onboarding_milestones.json"
    if path.exists():
        with open(path) as f:
            data = json.load(f)
        return data.get("milestones", [])
    return []


# ======================================================================
# CLI argument parsing
# ======================================================================


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Onboarding Concierge Agent - UC + Temporal Demo"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # worker command
    worker_parser = subparsers.add_parser("worker", help="Start the Temporal worker")
    worker_parser.add_argument(
        "--inject-failure",
        type=str,
        help="Inject failure on integration URL (e.g., 'https://api.example.com/webhook')",
    )

    # onboard command
    onboard_parser = subparsers.add_parser("onboard", help="Start onboarding for a customer")
    onboard_parser.add_argument("customer_id", help="Customer ID (e.g., CUST-ENT-001)")
    onboard_parser.add_argument("--name", type=str, default="Acme Corp", help="Customer name")
    onboard_parser.add_argument(
        "--plan-file",
        type=str,
        default=None,
        help="Path to milestones JSON file",
    )
    onboard_parser.add_argument("--workflow-id", type=str, default=None, help="Custom workflow ID")

    # status command
    status_parser = subparsers.add_parser("status", help="Query onboarding progress")
    status_parser.add_argument("workflow_id", help="Workflow ID to query")
    status_parser.add_argument(
        "--type",
        choices=["status", "timeline", "integrations"],
        default="status",
    )

    # checkin-response command
    checkin_parser = subparsers.add_parser(
        "checkin-response", help="Send customer check-in response signal"
    )
    checkin_parser.add_argument("workflow_id", help="Workflow ID")
    checkin_parser.add_argument("milestone_id", help="Milestone ID")
    checkin_parser.add_argument("--message", type=str, default="Looks good!", help="Response text")
    checkin_parser.add_argument("--score", type=int, default=5, help="Satisfaction score (1-5)")

    # simulate-day command
    simulate_parser = subparsers.add_parser(
        "simulate-day", help="Fast-forward through onboarding timeline"
    )
    simulate_parser.add_argument("workflow_id", help="Workflow ID")

    # demo command
    subparsers.add_parser(
        "demo",
        help="Run full end-to-end demo with rich TUI output",
    )

    return parser.parse_args()


def _default_milestones() -> list[Milestone]:
    """Create the standard 14-day onboarding milestone plan."""
    return [
        Milestone(
            id="ms-1",
            type=MilestoneType.ACCOUNT_VERIFICATION,
            title="Account Verification",
            description="Verify customer account configuration and prerequisites.",
            scheduled_day=1,
        ),
        Milestone(
            id="ms-2",
            type=MilestoneType.INTEGRATION_SETUP,
            title="Integration Setup & Testing",
            description="Test customer webhook endpoints and run diagnostics in Docker sandbox.",
            scheduled_day=3,
        ),
        Milestone(
            id="ms-3",
            type=MilestoneType.TRAINING_DELIVERY,
            title="Training Material Delivery",
            description="Generate and deliver personalised training materials to the customer.",
            scheduled_day=5,
        ),
        Milestone(
            id="ms-4",
            type=MilestoneType.MILESTONE_CHECKIN,
            title="Mid-Onboarding Check-In",
            description="Analyse progress and send a check-in to the customer.",
            scheduled_day=10,
        ),
        Milestone(
            id="ms-5",
            type=MilestoneType.FINAL_REVIEW,
            title="Final Review",
            description="Comprehensive review of the onboarding journey and handoff.",
            scheduled_day=14,
        ),
    ]


def _load_plan(
    customer_id: str,
    customer_name: str,
    plan_file: str | None,
) -> OnboardingPlan:
    """Load or create an onboarding plan."""
    if plan_file:
        with open(plan_file) as f:
            data = json.load(f)
        milestones = [Milestone.model_validate(m) for m in data.get("milestones", [])]
    else:
        milestones = _default_milestones()

    return OnboardingPlan(
        customer_id=customer_id,
        customer_name=customer_name,
        milestones=milestones,
    )


# ======================================================================
# Service creation
# ======================================================================


def create_mock_services(
    inject_failure: str | None = None,
    customer_data: dict | None = None,
):
    """Create all mock services with optional failure injection."""
    zendesk = MockZendeskService()
    integration_test = MockIntegrationTestService()
    email = MockEmailService()

    if inject_failure:
        integration_test.inject_failure(inject_failure, "500")

    return zendesk, integration_test, email


# ======================================================================
# Worker
# ======================================================================


async def run_worker(config: OnboardingConfig, inject_failure: str | None = None):
    """Start the Temporal worker."""
    client = await Client.connect(
        config.temporal_address, namespace=config.temporal_namespace,
        data_converter=pydantic_data_converter,
    )
    customer_data = _load_customer_fixture()
    zendesk, integration_test, email = create_mock_services(inject_failure)

    activities = OnboardingActivities(
        zendesk=zendesk,
        integration_test=integration_test,
        email=email,
        customer_data=customer_data if customer_data else None,
    )

    console.print("[bold green]Starting Onboarding Concierge Worker[/bold green]")
    console.print(f"Task queue: {config.task_queue}")
    if inject_failure:
        console.print(
            f"[yellow]Failure injection enabled on: {inject_failure}[/yellow]"
        )

    worker = Worker(
        client,
        task_queue=config.task_queue,
        workflows=[OnboardingWorkflow, IntegrationTestWorkflow],
        activities=[
            activities.verify_account,
            activities.test_integration,
            activities.generate_training_materials,
            activities.send_checkin,
            activities.create_support_ticket,
            activities.analyze_progress,
        ],
    )

    console.print("[green]Worker started. Press Ctrl+C to stop.[/green]")
    await worker.run()


# ======================================================================
# Start onboarding
# ======================================================================


async def start_onboarding(
    config: OnboardingConfig,
    customer_id: str,
    customer_name: str,
    plan_file: str | None,
    workflow_id: str | None = None,
):
    """Start a new onboarding workflow."""
    client = await Client.connect(
        config.temporal_address, namespace=config.temporal_namespace,
        data_converter=pydantic_data_converter,
    )

    plan = _load_plan(customer_id, customer_name, plan_file)
    wf_id = workflow_id or f"onboarding-{customer_id}"

    console.print(f"[bold]Starting Onboarding for customer {customer_id}[/bold]")
    console.print(f"Customer: {customer_name}")
    console.print(f"Workflow ID: {wf_id}")
    console.print(f"Milestones: {len(plan.milestones)}")

    handle = await client.start_workflow(
        OnboardingWorkflow.run,
        plan,
        id=wf_id,
        task_queue=config.task_queue,
    )

    console.print(f"[green]Workflow started: {handle.id}[/green]")
    console.print(
        "Use 'status' to check progress, 'checkin-response' to send customer responses."
    )
    return handle


# ======================================================================
# Status / Query
# ======================================================================


async def query_status(config: OnboardingConfig, workflow_id: str, query_type: str):
    """Query onboarding workflow state."""
    client = await Client.connect(
        config.temporal_address, namespace=config.temporal_namespace,
        data_converter=pydantic_data_converter,
    )
    handle = client.get_workflow_handle(workflow_id)

    if query_type == "status":
        state: OnboardingState | None = await handle.query(
            OnboardingWorkflow.get_onboarding_status
        )
        if state:
            console.print(f"[bold]Customer:[/bold] {state.customer_id}")
            console.print(f"[bold]Stage:[/bold] {state.current_stage.value}")
            console.print(f"[bold]Paused:[/bold] {state.paused}")
            console.print(
                f"[bold]Milestone:[/bold] {state.current_milestone_index + 1}"
                f"/{len(state.plan.milestones)}"
            )
            if state.error_message:
                console.print(f"[red]Error: {state.error_message}[/red]")
        else:
            console.print("[yellow]No state available yet[/yellow]")

    elif query_type == "timeline":
        timeline = await handle.query(OnboardingWorkflow.get_milestone_timeline)
        console.print("[bold]Milestone Timeline:[/bold]")
        for m in timeline:
            status_style = {
                "completed": "green",
                "in_progress": "yellow",
                "failed": "red",
                "skipped": "dim",
                "pending": "white",
            }.get(m["status"], "white")
            console.print(
                f"  Day {m['scheduled_day']:>2}: "
                f"[{status_style}]{m['status'].upper():>12}[/{status_style}] "
                f"{m['title']}"
            )

    elif query_type == "integrations":
        results: list[IntegrationTestResult] = await handle.query(
            OnboardingWorkflow.get_integration_results
        )
        if results:
            console.print("[bold]Integration Test Results:[/bold]")
            for r in results:
                status = "[green]PASS[/green]" if r.test_passed else "[red]FAIL[/red]"
                console.print(
                    f"  {status} {r.integration_id} ({r.endpoint_url})"
                )
                if r.response_time_ms is not None:
                    console.print(f"       Response time: {r.response_time_ms:.1f}ms")
                if r.error_message:
                    console.print(f"       Error: {r.error_message}")
        else:
            console.print("[yellow]No integration test results yet[/yellow]")


# ======================================================================
# Checkin Response
# ======================================================================


async def send_checkin_response(
    config: OnboardingConfig,
    workflow_id: str,
    milestone_id: str,
    message: str,
    score: int,
):
    """Send a customer check-in response signal."""
    client = await Client.connect(
        config.temporal_address, namespace=config.temporal_namespace,
        data_converter=pydantic_data_converter,
    )
    handle = client.get_workflow_handle(workflow_id)

    # Query for customer_id
    state: OnboardingState | None = await handle.query(
        OnboardingWorkflow.get_onboarding_status
    )
    customer_id = state.customer_id if state else "unknown"

    response = CheckinResponse(
        customer_id=customer_id,
        milestone_id=milestone_id,
        response_text=message,
        satisfaction_score=score,
    )

    await handle.signal(OnboardingWorkflow.checkin_response, response)
    console.print(f"[green]Check-in response sent for milestone {milestone_id}[/green]")


# ======================================================================
# Simulate Day
# ======================================================================


async def simulate_day(config: OnboardingConfig, workflow_id: str):
    """Fast-forward through the onboarding timeline for demo purposes."""
    console.print(
        f"[bold yellow]Simulation mode:[/bold yellow] "
        f"Each day = {config.simulation_day_seconds}s"
    )
    console.print(
        "Note: In simulation mode, durable timers must be configured with "
        "simulation_day_seconds. This command monitors progress."
    )

    client = await Client.connect(
        config.temporal_address, namespace=config.temporal_namespace,
        data_converter=pydantic_data_converter,
    )
    handle = client.get_workflow_handle(workflow_id)

    last_stage = ""
    while True:
        try:
            state: OnboardingState | None = await handle.query(
                OnboardingWorkflow.get_onboarding_status
            )
            if state and state.current_stage.value != last_stage:
                last_stage = state.current_stage.value
                console.print(
                    f"[cyan]Stage:[/cyan] {last_stage} | "
                    f"Milestone {state.current_milestone_index + 1}"
                    f"/{len(state.plan.milestones)}"
                )
            if state and state.current_stage.value == "completed":
                console.print("[bold green]Onboarding complete![/bold green]")
                break
        except Exception:
            console.print("[dim]Waiting for workflow...[/dim]")

        await asyncio.sleep(config.simulation_day_seconds)


# ======================================================================
# Demo helpers
# ======================================================================

_DEMO_STAGES = [
    "Account Verification",
    "Integration Testing",
    "Training Delivery",
    "Mid-Onboarding Check-In",
    "Final Review",
]


def _commentary(text: str) -> None:
    """Print educational commentary below the dashboard."""
    console.print(f"\n[dim italic]>> {text}[/dim italic]")


async def _poll_until_stage(
    handle,
    target_stages: set[str],
    *,
    timeout: float = 60.0,
    interval: float = 0.3,
) -> OnboardingState | None:
    """Poll the workflow until it reaches one of the target stages."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            state: OnboardingState | None = await handle.query(
                OnboardingWorkflow.get_onboarding_status
            )
            if state and state.current_stage.value in target_stages:
                return state
        except Exception:
            pass
        await asyncio.sleep(interval)
    # Return whatever state we have
    try:
        return await handle.query(OnboardingWorkflow.get_onboarding_status)
    except Exception:
        return None


async def _poll_until_milestone_done(
    handle,
    milestone_index: int,
    *,
    timeout: float = 60.0,
    interval: float = 0.3,
) -> OnboardingState | None:
    """Poll until a specific milestone is completed/failed/skipped."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            state: OnboardingState | None = await handle.query(
                OnboardingWorkflow.get_onboarding_status
            )
            if state and milestone_index < len(state.plan.milestones):
                ms = state.plan.milestones[milestone_index]
                if ms.status.value in ("completed", "failed", "skipped"):
                    return state
            # Also check if we've moved past this milestone
            if state and state.current_milestone_index > milestone_index:
                return state
            # Check completed
            if state and state.current_stage.value == "completed":
                return state
        except Exception:
            pass
        await asyncio.sleep(interval)
    try:
        return await handle.query(OnboardingWorkflow.get_onboarding_status)
    except Exception:
        return None


# ======================================================================
# demo command
# ======================================================================


async def run_demo(config: OnboardingConfig) -> None:
    """Run a full end-to-end demo with rich TUI output.

    Starts a worker and onboarding workflow inline, uses a fast timeline,
    and shows each milestone executing with rich detail.
    """
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    console.rule("[bold blue]Onboarding Concierge Demo[/bold blue]")
    console.print()
    _commentary(
        "This demo shows a complete 14-day enterprise customer onboarding "
        "workflow powered by Temporal + Universal Computer agents. The workflow "
        "orchestrates account verification, integration testing (with failure "
        "diagnostics), personalized training delivery, milestone check-ins, and "
        "a final review -- all with durable timers between milestones."
    )
    console.print()

    # --- Load fixture data ---
    customer_data = _load_customer_fixture()
    milestone_fixtures = _load_milestones_fixture()

    customer_id = customer_data.get("customer_id", "CUST-ENT-001")
    customer_name = customer_data.get("customer_name", "Acme Corp")
    contact = customer_data.get("contact", {})
    service_plan = customer_data.get("service_plan", {})
    integrations = customer_data.get("integrations", [])

    # --- Display customer profile ---
    profile_text = (
        f"[bold]Customer:[/bold] {customer_name} ({customer_id})\n"
        f"[bold]Tier:[/bold] {customer_data.get('tier', 'enterprise').upper()}\n"
        f"[bold]Contact:[/bold] {contact.get('primary_name', 'N/A')} "
        f"<{contact.get('primary_email', 'N/A')}>\n"
        f"[bold]Plan:[/bold] {service_plan.get('plan_name', 'Enterprise Pro')} | "
        f"Rate limit: {service_plan.get('api_rate_limit', 10000):,} req/hr\n"
        f"[bold]Support:[/bold] {service_plan.get('support_level', 'dedicated')} | "
        f"SLA: {service_plan.get('sla_response_hours', 4)}h response\n"
        f"[bold]Integrations:[/bold] {len(integrations)} registered"
    )

    for integ in integrations:
        profile_text += (
            f"\n  - {integ.get('integration_id', '?')} | "
            f"{integ.get('type', '?')} | "
            f"{integ.get('endpoint_url', '?')} | "
            f"{integ.get('auth_type', '?')}"
        )

    console.print(Panel(
        profile_text,
        title="[bold cyan]Customer Profile[/bold cyan]",
        border_style="cyan",
        padding=(1, 2),
    ))
    console.print()

    # --- Display milestone timeline ---
    timeline_table = Table(
        title="[bold]14-Day Onboarding Plan[/bold]",
        show_header=True,
        header_style="bold white",
        border_style="bright_blue",
        expand=True,
        padding=(0, 1),
    )
    timeline_table.add_column("Day", justify="center", width=5)
    timeline_table.add_column("Milestone", ratio=3)
    timeline_table.add_column("Type", ratio=2)
    timeline_table.add_column("Description", ratio=5)

    milestones = _default_milestones()
    for ms in milestones:
        timeline_table.add_row(
            str(ms.scheduled_day),
            ms.title,
            ms.type.value.replace("_", " ").title(),
            ms.description,
        )

    console.print(timeline_table)
    console.print()

    _commentary(
        "MULTI-DAY ORCHESTRATION: In production, this workflow spans 14 real days. "
        "Temporal's durable timers sleep between milestones without consuming compute. "
        "If the worker crashes during a multi-day wait, the timer resumes exactly "
        f"where it left off. For this demo, each 'day' = {config.simulation_day_seconds}s."
    )
    console.print()

    # --- Start worker with failure injection on second integration ---
    _commentary("Starting Temporal worker with mock services (failure injected on second integration endpoint)...")

    client = await Client.connect(
        config.temporal_address, namespace=config.temporal_namespace,
        data_converter=pydantic_data_converter,
    )

    zendesk = MockZendeskService()
    integration_test_svc = MockIntegrationTestService()
    email_svc = MockEmailService()

    # Inject a failure on the second integration endpoint for dramatic effect
    fail_url = None
    if len(integrations) > 1:
        fail_url = integrations[1].get("endpoint_url", "")
        if fail_url:
            integration_test_svc.inject_failure(fail_url, "500")

    activities = OnboardingActivities(
        zendesk=zendesk,
        integration_test=integration_test_svc,
        email=email_svc,
        customer_data=customer_data,
    )

    worker = Worker(
        client,
        task_queue=config.task_queue,
        workflows=[OnboardingWorkflow, IntegrationTestWorkflow],
        activities=[
            activities.verify_account,
            activities.test_integration,
            activities.generate_training_materials,
            activities.send_checkin,
            activities.create_support_ticket,
            activities.analyze_progress,
        ],
    )

    worker_task = asyncio.create_task(worker.run())

    try:
        await asyncio.sleep(0.5)

        # --- Start the dashboard ---
        dashboard = WorkflowDashboard(
            f"Onboarding: {customer_name} ({customer_id})", _DEMO_STAGES
        )
        dashboard.start()

        # --- Start the workflow ---
        wf_id = f"demo-onboarding-{customer_id}-{int(time.time())}"
        plan = OnboardingPlan(
            customer_id=customer_id,
            customer_name=customer_name,
            milestones=milestones,
        )
        handle = await client.start_workflow(
            OnboardingWorkflow.run,
            plan,
            id=wf_id,
            task_queue=config.task_queue,
        )
        dashboard.set_detail(f"Workflow started: {wf_id}")

        # ============================================================
        # MILESTONE 1: Account Verification (Day 1)
        # ============================================================
        t0 = time.monotonic()
        dashboard.update_stage("Account Verification", "running")
        act_idx = dashboard.add_activity("verify_account")
        dashboard.set_detail("Day 1: Running account verification checks...")

        state = await _poll_until_milestone_done(handle, 0, timeout=30.0)
        elapsed = time.monotonic() - t0
        dashboard.update_activity(act_idx, "done", duration=elapsed)
        dashboard.update_stage("Account Verification", "done")

        if state and state.plan.milestones[0].result_data:
            result = state.plan.milestones[0].result_data
            summary = result.get("summary", {})
            dashboard.set_detail(
                f"Verification complete: {summary.get('passed', '?')}/"
                f"{summary.get('total_checks', '?')} checks passed"
            )

        # Stop dashboard to show verification details
        dashboard.stop()
        console.print()

        if state and state.plan.milestones[0].result_data:
            result = state.plan.milestones[0].result_data
            checks = result.get("checks", {})

            check_table = Table(
                title="[bold]Account Verification Checklist[/bold]",
                show_header=True,
                header_style="bold white",
                border_style="bright_blue",
                expand=True,
                padding=(0, 1),
            )
            check_table.add_column("Check", ratio=2)
            check_table.add_column("Status", justify="center", width=8)
            check_table.add_column("Details", ratio=5)

            for name, check in checks.items():
                passed = check.get("passed", False)
                status_text = Text(
                    "PASS" if passed else "FAIL",
                    style="bold green" if passed else "bold red",
                )
                check_table.add_row(
                    name.replace("_", " ").title(),
                    status_text,
                    check.get("detail", ""),
                )

            console.print(check_table)

            recs = result.get("recommendations", [])
            if recs:
                console.print()
                console.print("[bold]Recommendations:[/bold]")
                for r in recs:
                    console.print(f"  [green]+[/green] {r}")
            console.print()

        _commentary(
            "DURABLE TIMER: The workflow now sleeps for 2 days (until Day 3) "
            "before the next milestone. In production, the worker can shut down "
            "during this wait and restart later -- the timer is persisted by "
            f"Temporal. Demo: sleeping {config.simulation_day_seconds * 2}s..."
        )
        console.print()

        # Resume dashboard for integration testing
        dashboard = WorkflowDashboard(
            f"Onboarding: {customer_name} -- Integration Testing", _DEMO_STAGES
        )
        dashboard.update_stage("Account Verification", "done")
        dashboard.update_stage("Integration Testing", "running")
        dashboard.start()

        # ============================================================
        # MILESTONE 2: Integration Setup & Testing (Day 3)
        # ============================================================
        t0 = time.monotonic()
        act_idx = dashboard.add_activity("test_integration (child workflow)")
        dashboard.set_detail("Day 3: Testing customer integration endpoints...")

        state = await _poll_until_milestone_done(handle, 1, timeout=60.0)
        elapsed = time.monotonic() - t0
        ms2 = state.plan.milestones[1] if state else None

        if ms2 and ms2.status.value == "completed":
            dashboard.update_activity(act_idx, "done", duration=elapsed)
        else:
            dashboard.update_activity(
                act_idx, "failed", duration=elapsed,
                details=ms2.result_data.get("error_message", "Test failed") if ms2 else "Failed",
            )
        dashboard.update_stage(
            "Integration Testing",
            "done" if (ms2 and ms2.status.value == "completed") else "failed",
        )

        if ms2 and ms2.result_data:
            rd = ms2.result_data
            dashboard.set_detail(
                f"Integration: {'PASS' if rd.get('test_passed') else 'FAIL'} | "
                f"Endpoint: {rd.get('endpoint_url', '?')} | "
                f"Latency: {rd.get('response_time_ms', '?')}ms"
            )

        dashboard.stop()
        console.print()

        # Show integration test results in detail
        if ms2 and ms2.result_data:
            rd = ms2.result_data
            passed = rd.get("test_passed", False)
            diag_str = rd.get("diagnostic_report", "")

            result_table = Table(
                title="[bold]Integration Test Results[/bold]",
                show_header=True,
                header_style="bold white",
                border_style="green" if passed else "red",
                expand=True,
                padding=(0, 1),
            )
            result_table.add_column("Field", ratio=2, style="bold")
            result_table.add_column("Value", ratio=5)

            result_table.add_row("Integration ID", rd.get("integration_id", "?"))
            result_table.add_row("Endpoint URL", rd.get("endpoint_url", "?"))
            result_table.add_row(
                "Result",
                Text("PASS", style="bold green") if passed else Text("FAIL", style="bold red"),
            )
            result_table.add_row(
                "HTTP Status",
                str(rd.get("response_status", "N/A")),
            )
            result_table.add_row(
                "Latency",
                f"{rd.get('response_time_ms', 'N/A')}ms" if rd.get("response_time_ms") else "N/A",
            )

            if rd.get("error_message"):
                result_table.add_row(
                    "Error",
                    Text(rd["error_message"], style="red"),
                )

            console.print(result_table)

            # Show diagnostic report
            if diag_str:
                try:
                    diag = json.loads(diag_str)
                    diag_table = Table(
                        title="[bold]Diagnostic Report[/bold]",
                        show_header=True,
                        header_style="bold white",
                        border_style="bright_blue",
                        expand=True,
                        padding=(0, 1),
                    )
                    diag_table.add_column("Check", ratio=2)
                    diag_table.add_column("Status", justify="center", width=8)
                    diag_table.add_column("Detail", ratio=5)

                    diag_checks = diag.get("diagnostic_checks", {})
                    if isinstance(diag_checks, dict):
                        for check_name, check_info in diag_checks.items():
                            if isinstance(check_info, dict):
                                s = check_info.get("status", "unknown")
                                d = check_info.get("detail", "")
                            else:
                                s = "unknown"
                                d = str(check_info)
                            status_text = Text(
                                s.upper(),
                                style="bold green" if s == "passed" else "bold red",
                            )
                            diag_table.add_row(
                                check_name.replace("_", " ").title(),
                                status_text,
                                d,
                            )
                    elif isinstance(diag_checks, list):
                        for check_info in diag_checks:
                            s = check_info.get("status", "unknown")
                            d = check_info.get("detail", "")
                            status_text = Text(
                                s.upper(),
                                style="bold green" if s == "passed" else "bold red",
                            )
                            diag_table.add_row(
                                check_info.get("check", "?").replace("_", " ").title(),
                                status_text,
                                d,
                            )

                    console.print(diag_table)
                except (json.JSONDecodeError, AttributeError):
                    console.print(Panel(
                        diag_str[:500],
                        title="[bold]Raw Diagnostic[/bold]",
                        border_style="bright_blue",
                    ))

            # Show suggestions if test failed
            suggestions = rd.get("suggestions", [])
            if suggestions and not passed:
                console.print()
                console.print("[bold yellow]Troubleshooting Suggestions:[/bold yellow]")
                for i, s in enumerate(suggestions, 1):
                    console.print(f"  {i}. {s}")

            console.print()

        if ms2 and ms2.status.value == "failed":
            _commentary(
                "CHILD WORKFLOW + SUPPORT TICKET: The integration test ran as a "
                "child workflow (IntegrationTestWorkflow). When it failed, the child "
                "workflow automatically created a support ticket for the engineering "
                "team with the diagnostic report and troubleshooting suggestions. "
                "The parent workflow recorded the failure and continued."
            )
        else:
            _commentary(
                "CHILD WORKFLOW: The integration test ran as a separate child "
                "workflow (IntegrationTestWorkflow). This isolates the test logic "
                "and enables independent retries. The parent workflow awaited the "
                "child's completion before proceeding."
            )
        console.print()

        _commentary(
            f"DURABLE TIMER: Sleeping until Day 5 for training delivery "
            f"(demo: {config.simulation_day_seconds * 2}s)..."
        )
        console.print()

        # ============================================================
        # MILESTONE 3: Training Delivery (Day 5)
        # ============================================================
        dashboard = WorkflowDashboard(
            f"Onboarding: {customer_name} -- Training Delivery", _DEMO_STAGES
        )
        dashboard.update_stage("Account Verification", "done")
        dashboard.update_stage(
            "Integration Testing",
            "done" if (ms2 and ms2.status.value == "completed") else "failed",
        )
        dashboard.update_stage("Training Delivery", "running")
        dashboard.start()

        t0 = time.monotonic()
        gen_idx = dashboard.add_activity("generate_training_materials")
        send_idx = dashboard.add_activity("send_checkin (training email)")
        wait_idx = dashboard.add_activity("wait for customer response")
        dashboard.set_detail("Day 5: Generating personalized training materials...")

        # Wait for the training milestone to start, then auto-send checkin
        # The workflow will wait for a checkin_response signal, so we send one
        async def _auto_respond_training():
            """Automatically respond to the training checkin after a delay."""
            await asyncio.sleep(3.0)
            try:
                current_state: OnboardingState | None = await handle.query(
                    OnboardingWorkflow.get_onboarding_status
                )
                if current_state and current_state.current_stage.value == "training_delivery":
                    response = CheckinResponse(
                        customer_id=customer_id,
                        milestone_id="ms-3",
                        response_text=(
                            "Excellent training guide! The webhook signature verification "
                            "example was especially helpful. Our team is already using the "
                            "sandbox environment to test. Looking forward to the check-in."
                        ),
                        satisfaction_score=5,
                    )
                    await handle.signal(OnboardingWorkflow.checkin_response, response)
            except Exception:
                pass

        responder_task = asyncio.create_task(_auto_respond_training())

        state = await _poll_until_milestone_done(handle, 2, timeout=60.0)
        elapsed = time.monotonic() - t0

        dashboard.update_activity(gen_idx, "done", duration=elapsed * 0.3)
        dashboard.update_activity(send_idx, "done", duration=elapsed * 0.2)
        dashboard.update_activity(wait_idx, "done", duration=elapsed * 0.5, details="Customer responded")
        dashboard.update_stage("Training Delivery", "done")
        dashboard.set_detail("Training materials delivered and acknowledged")

        dashboard.stop()
        console.print()

        # Show the training materials
        ms3 = state.plan.milestones[2] if state else None
        if ms3 and ms3.result_data:
            rd = ms3.result_data
            customer_response = rd.get("customer_response")
            score = rd.get("satisfaction_score")

            if customer_response:
                console.print(Panel(
                    (
                        f"[bold]Customer Response:[/bold] {customer_response}\n"
                        f"[bold]Satisfaction Score:[/bold] {'*' * (score or 0)}"
                        f"{'.' * (5 - (score or 0))} ({score}/5)"
                    ),
                    title="[bold green]Customer Feedback[/bold green]",
                    border_style="green",
                    padding=(1, 2),
                ))
                console.print()

        # Show a preview of the generated training guide
        # (The actual guide was sent via email; let's show a snippet)
        if email_svc.sent_emails:
            # Find the training email
            for sent in email_svc.sent_emails:
                if "ms-3" in sent.get("subject", ""):
                    body = sent.get("body", "")
                    # Show first ~40 lines
                    lines = body.split("\n")
                    preview = "\n".join(lines[:40])
                    if len(lines) > 40:
                        preview += f"\n\n... ({len(lines) - 40} more lines)"

                    console.print(Panel(
                        preview,
                        title="[bold cyan]Generated Training Guide (Preview)[/bold cyan]",
                        border_style="cyan",
                        padding=(1, 2),
                    ))
                    console.print()
                    break

        _commentary(
            "SIGNAL-BASED COORDINATION: The workflow sent the training materials "
            "via email and then waited for a customer response signal. In production "
            "this could wait up to 48 hours. The workflow's wait_condition is durable "
            "-- even if the worker restarts, the signal is not lost."
        )
        console.print()

        _commentary(
            f"DURABLE TIMER: Sleeping until Day 10 for mid-onboarding check-in "
            f"(demo: {config.simulation_day_seconds * 5}s)..."
        )
        console.print()

        # Wait for the responder task to finish
        try:
            await responder_task
        except Exception:
            pass

        # ============================================================
        # MILESTONE 4: Mid-Onboarding Check-In (Day 10)
        # ============================================================
        dashboard = WorkflowDashboard(
            f"Onboarding: {customer_name} -- Check-In", _DEMO_STAGES
        )
        dashboard.update_stage("Account Verification", "done")
        dashboard.update_stage(
            "Integration Testing",
            "done" if (ms2 and ms2.status.value == "completed") else "failed",
        )
        dashboard.update_stage("Training Delivery", "done")
        dashboard.update_stage("Mid-Onboarding Check-In", "running")
        dashboard.start()

        t0 = time.monotonic()
        analyze_idx = dashboard.add_activity("analyze_progress")
        checkin_idx = dashboard.add_activity("send_checkin (progress report)")
        dashboard.set_detail("Day 10: Analyzing onboarding progress...")

        state = await _poll_until_milestone_done(handle, 3, timeout=60.0)
        elapsed = time.monotonic() - t0

        dashboard.update_activity(analyze_idx, "done", duration=elapsed * 0.6)
        dashboard.update_activity(checkin_idx, "done", duration=elapsed * 0.4)
        dashboard.update_stage("Mid-Onboarding Check-In", "done")

        ms4 = state.plan.milestones[3] if state else None
        if ms4 and ms4.result_data:
            dashboard.set_detail("Progress report generated and sent to customer")

        dashboard.stop()
        console.print()

        # Show the progress report
        if ms4 and ms4.result_data:
            report = ms4.result_data.get("progress_report", "")
            if report:
                console.print(Panel(
                    report,
                    title="[bold yellow]Mid-Onboarding Progress Report[/bold yellow]",
                    border_style="yellow",
                    padding=(1, 2),
                ))
                console.print()

        _commentary(
            "PROGRESS ANALYSIS: The UC agent analyzed all milestone data and "
            "generated a detailed progress report. This report identifies blockers, "
            "calculates completion percentage, and recommends next actions."
        )
        console.print()

        _commentary(
            f"DURABLE TIMER: Sleeping until Day 14 for final review "
            f"(demo: {config.simulation_day_seconds * 4}s)..."
        )
        console.print()

        # ============================================================
        # MILESTONE 5: Final Review (Day 14)
        # ============================================================
        dashboard = WorkflowDashboard(
            f"Onboarding: {customer_name} -- Final Review", _DEMO_STAGES
        )
        dashboard.update_stage("Account Verification", "done")
        dashboard.update_stage(
            "Integration Testing",
            "done" if (ms2 and ms2.status.value == "completed") else "failed",
        )
        dashboard.update_stage("Training Delivery", "done")
        dashboard.update_stage("Mid-Onboarding Check-In", "done")
        dashboard.update_stage("Final Review", "running")
        dashboard.start()

        t0 = time.monotonic()
        review_idx = dashboard.add_activity("analyze_progress (comprehensive)")
        final_email_idx = dashboard.add_activity("send_checkin (final report)")
        dashboard.set_detail("Day 14: Conducting final onboarding review...")

        state = await _poll_until_milestone_done(handle, 4, timeout=60.0)
        elapsed = time.monotonic() - t0

        dashboard.update_activity(review_idx, "done", duration=elapsed * 0.6)
        dashboard.update_activity(final_email_idx, "done", duration=elapsed * 0.4)
        dashboard.update_stage("Final Review", "done")
        dashboard.set_detail("Onboarding complete!")

        dashboard.stop()
        console.print()

        # Show the final report
        ms5 = state.plan.milestones[4] if state else None
        if ms5 and ms5.result_data:
            report = ms5.result_data.get("final_report", "")
            if report:
                console.print(Panel(
                    report,
                    title="[bold green]Final Onboarding Report[/bold green]",
                    border_style="green",
                    padding=(1, 2),
                ))
                console.print()

        _commentary(
            "CONTINUE-AS-NEW: In a real 14-day workflow, the event history could "
            "grow very large. Temporal workflows use continue-as-new to checkpoint "
            "their state and start a fresh history, preventing unbounded growth. "
            "This workflow checks the history length after each milestone and "
            "triggers continue-as-new if it exceeds 10,000 events."
        )
        console.print()

        # ============================================================
        # Final summary
        # ============================================================
        console.rule("[bold blue]Demo Complete[/bold blue]")
        console.print()

        # Build final summary table
        summary_table = Table(
            title="[bold]Onboarding Summary[/bold]",
            show_header=True,
            header_style="bold white",
            border_style="bright_blue",
            expand=True,
            padding=(0, 1),
        )
        summary_table.add_column("Day", justify="center", width=5)
        summary_table.add_column("Milestone", ratio=3)
        summary_table.add_column("Status", justify="center", width=10)
        summary_table.add_column("Highlights", ratio=5)

        if state:
            for ms in state.plan.milestones:
                status_style = {
                    "completed": "bold green",
                    "failed": "bold red",
                    "skipped": "dim",
                    "pending": "white",
                    "in_progress": "bold yellow",
                }.get(ms.status.value, "white")

                highlights = ""
                rd = ms.result_data
                if ms.type == MilestoneType.ACCOUNT_VERIFICATION and rd:
                    s = rd.get("summary", {})
                    highlights = f"{s.get('passed', '?')}/{s.get('total_checks', '?')} checks passed"
                elif ms.type == MilestoneType.INTEGRATION_SETUP and rd:
                    highlights = (
                        f"{'PASS' if rd.get('test_passed') else 'FAIL'} | "
                        f"{rd.get('endpoint_url', '?')}"
                    )
                    if rd.get("response_time_ms"):
                        highlights += f" | {rd['response_time_ms']:.0f}ms"
                elif ms.type == MilestoneType.TRAINING_DELIVERY and rd:
                    if rd.get("customer_response"):
                        highlights = f"Score: {rd.get('satisfaction_score', '?')}/5 | Customer acknowledged"
                    else:
                        highlights = "Materials sent"
                elif ms.type in (MilestoneType.MILESTONE_CHECKIN, MilestoneType.FINAL_REVIEW) and rd:
                    highlights = "Report generated and delivered"

                summary_table.add_row(
                    str(ms.scheduled_day),
                    ms.title,
                    Text(ms.status.value.upper(), style=status_style),
                    highlights,
                )

        console.print(summary_table)
        console.print()

        # Print emails sent
        if email_svc.sent_emails:
            email_table = Table(
                title="[bold]Emails Sent During Onboarding[/bold]",
                show_header=True,
                header_style="bold white",
                border_style="bright_blue",
                expand=True,
                padding=(0, 1),
            )
            email_table.add_column("To", ratio=3)
            email_table.add_column("Subject", ratio=4)
            email_table.add_column("Status", justify="center", width=10)
            email_table.add_column("Sent At", ratio=3)

            for em in email_svc.sent_emails:
                email_table.add_row(
                    em.get("to", "?"),
                    em.get("subject", "?"),
                    Text("Delivered", style="green"),
                    em.get("sent_at", "?")[:19],
                )

            console.print(email_table)
            console.print()

        # Final commentary
        _commentary(
            "KEY TAKEAWAYS:\n"
            "  1. Multi-day orchestration: The workflow spanned 14 simulated days "
            "with durable timers between milestones.\n"
            "  2. Child workflows: Integration testing ran as an isolated child "
            "workflow with independent retries.\n"
            "  3. Signal-based coordination: Customer responses were received "
            "via Temporal signals with configurable timeouts.\n"
            "  4. Continue-as-new: Long-running workflows checkpoint state to "
            "prevent unbounded event history growth.\n"
            "  5. Fault tolerance: Every activity has retry policies. If the "
            "worker crashes mid-milestone, execution resumes exactly where it "
            "left off."
        )
        console.print()

    finally:
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass


# ======================================================================
# Main entry point
# ======================================================================


def main():
    args = parse_args()
    config = OnboardingConfig()

    if args.command == "worker":
        asyncio.run(
            run_worker(config, inject_failure=getattr(args, "inject_failure", None))
        )
    elif args.command == "onboard":
        asyncio.run(
            start_onboarding(
                config,
                args.customer_id,
                getattr(args, "name", "Acme Corp"),
                getattr(args, "plan_file", None),
                getattr(args, "workflow_id", None),
            )
        )
    elif args.command == "status":
        asyncio.run(query_status(config, args.workflow_id, args.type))
    elif args.command == "checkin-response":
        asyncio.run(
            send_checkin_response(
                config,
                args.workflow_id,
                args.milestone_id,
                getattr(args, "message", "Looks good!"),
                getattr(args, "score", 5),
            )
        )
    elif args.command == "simulate-day":
        asyncio.run(simulate_day(config, args.workflow_id))
    elif args.command == "demo":
        asyncio.run(run_demo(config))


if __name__ == "__main__":
    main()
