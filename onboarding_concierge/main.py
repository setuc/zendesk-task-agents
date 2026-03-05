from __future__ import annotations

import argparse
import asyncio
import json

from temporalio.client import Client
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
from ..common.tui import console
from ..common.services.zendesk_mock import MockZendeskService
from .services.integration_test_mock import MockIntegrationTestService
from .services.email_mock import MockEmailService


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


def create_mock_services(inject_failure: str | None = None):
    """Create all mock services with optional failure injection."""
    zendesk = MockZendeskService()
    integration_test = MockIntegrationTestService()
    email = MockEmailService()

    if inject_failure:
        integration_test.inject_failure(inject_failure, "500")

    return zendesk, integration_test, email


async def run_worker(config: OnboardingConfig, inject_failure: str | None = None):
    """Start the Temporal worker."""
    client = await Client.connect(
        config.temporal_address, namespace=config.temporal_namespace
    )
    zendesk, integration_test, email = create_mock_services(inject_failure)

    activities = OnboardingActivities(
        zendesk=zendesk,
        integration_test=integration_test,
        email=email,
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


async def start_onboarding(
    config: OnboardingConfig,
    customer_id: str,
    customer_name: str,
    plan_file: str | None,
    workflow_id: str | None = None,
):
    """Start a new onboarding workflow."""
    client = await Client.connect(
        config.temporal_address, namespace=config.temporal_namespace
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


async def query_status(config: OnboardingConfig, workflow_id: str, query_type: str):
    """Query onboarding workflow state."""
    client = await Client.connect(
        config.temporal_address, namespace=config.temporal_namespace
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


async def send_checkin_response(
    config: OnboardingConfig,
    workflow_id: str,
    milestone_id: str,
    message: str,
    score: int,
):
    """Send a customer check-in response signal."""
    client = await Client.connect(
        config.temporal_address, namespace=config.temporal_namespace
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
        config.temporal_address, namespace=config.temporal_namespace
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


if __name__ == "__main__":
    main()
