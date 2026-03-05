from __future__ import annotations
import argparse
import asyncio
import json

from temporalio.client import Client
from temporalio.worker import Worker

from .config import OrderResolutionConfig
from .workflows.workflow import OrderResolutionWorkflow
from .workflows.activities import OrderResolutionActivities
from .workflows.data_types import ApprovalDecision, WorkflowProgress
from ..common.tui import console, WorkflowDashboard, PlanDisplay, ApprovalPrompt, BenchmarkReport
from .services.order_db_mock import MockOrderDBService
from .services.shipping_mock import MockShippingService
from .services.payment_mock import MockPaymentService
from ..common.services.zendesk_mock import MockZendeskService


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Order Resolution Agent - UC + Temporal Demo")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # worker command
    worker_parser = subparsers.add_parser("worker", help="Start the Temporal worker")
    worker_parser.add_argument("--inject-failure", type=str, help="Inject failure on step type (e.g., 'replacement')")

    # start command
    start_parser = subparsers.add_parser("start", help="Start order resolution for a ticket")
    start_parser.add_argument("ticket_id", help="Zendesk ticket ID (e.g., TKT-10042)")
    start_parser.add_argument("--workflow-id", type=str, default=None, help="Custom workflow ID")

    # approve command
    approve_parser = subparsers.add_parser("approve", help="Approve a resolution plan")
    approve_parser.add_argument("workflow_id", help="Workflow ID to approve")
    approve_parser.add_argument("--notes", type=str, default="", help="Approval notes")

    # reject command
    reject_parser = subparsers.add_parser("reject", help="Reject a resolution plan")
    reject_parser.add_argument("workflow_id", help="Workflow ID to reject")
    reject_parser.add_argument("--notes", type=str, default="", help="Rejection notes")

    # query command
    query_parser = subparsers.add_parser("query", help="Query workflow state")
    query_parser.add_argument("workflow_id", help="Workflow ID to query")
    query_parser.add_argument("--type", choices=["state", "plan", "progress"], default="progress")

    # benchmark command
    subparsers.add_parser("benchmark", help="Run resumability benchmark")

    return parser.parse_args()


def create_mock_services(inject_failure: str | None = None):
    """Create all mock services with optional failure injection."""
    zendesk = MockZendeskService()
    order_db = MockOrderDBService()
    shipping = MockShippingService()
    payment = MockPaymentService()

    if inject_failure:
        # Inject failure on the payment service for saga demo
        payment.inject_failure("process_refund")

    return zendesk, order_db, shipping, payment


async def run_worker(config: OrderResolutionConfig, inject_failure: str | None = None):
    """Start the Temporal worker."""
    client = await Client.connect(config.temporal_address, namespace=config.temporal_namespace)
    zendesk, order_db, shipping, payment = create_mock_services(inject_failure)

    activities = OrderResolutionActivities(
        zendesk=zendesk, order_db=order_db, shipping=shipping, payment=payment
    )

    console.print("[bold green]Starting Order Resolution Worker[/bold green]")
    console.print(f"Task queue: {config.task_queue}")
    if inject_failure:
        console.print(f"[yellow]Failure injection enabled on: {inject_failure}[/yellow]")

    worker = Worker(
        client,
        task_queue=config.task_queue,
        workflows=[OrderResolutionWorkflow],
        activities=[
            activities.classify_and_extract,
            activities.investigate,
            activities.plan_resolution,
            activities.execute_step,
            activities.compensate_step,
            activities.verify_and_summarize,
        ],
    )

    console.print("[green]Worker started. Press Ctrl+C to stop.[/green]")
    await worker.run()


async def start_workflow(config: OrderResolutionConfig, ticket_id: str, workflow_id: str | None = None):
    """Start a new order resolution workflow."""
    client = await Client.connect(config.temporal_address, namespace=config.temporal_namespace)

    wf_id = workflow_id or f"order-resolution-{ticket_id}"

    console.print(f"[bold]Starting Order Resolution for ticket {ticket_id}[/bold]")
    console.print(f"Workflow ID: {wf_id}")

    handle = await client.start_workflow(
        OrderResolutionWorkflow.run,
        ticket_id,
        id=wf_id,
        task_queue=config.task_queue,
    )

    console.print(f"[green]Workflow started: {handle.id}[/green]")
    console.print("Use 'query' command to check progress, 'approve' to approve if needed.")
    return handle


async def send_approval(config: OrderResolutionConfig, workflow_id: str, approved: bool, notes: str = ""):
    """Send approval/rejection signal."""
    client = await Client.connect(config.temporal_address, namespace=config.temporal_namespace)
    handle = client.get_workflow_handle(workflow_id)

    decision = ApprovalDecision(approved=approved, reviewer="cli_user", notes=notes)
    await handle.signal(OrderResolutionWorkflow.approval_decision, decision)

    status = "approved" if approved else "rejected"
    console.print(f"[{'green' if approved else 'red'}]Resolution plan {status}[/{'green' if approved else 'red'}]")


async def query_workflow(config: OrderResolutionConfig, workflow_id: str, query_type: str):
    """Query workflow state."""
    client = await Client.connect(config.temporal_address, namespace=config.temporal_namespace)
    handle = client.get_workflow_handle(workflow_id)

    if query_type == "state":
        state = await handle.query(OrderResolutionWorkflow.get_workflow_state)
        console.print(f"[bold]Workflow State:[/bold] {state}")
    elif query_type == "plan":
        plan = await handle.query(OrderResolutionWorkflow.get_resolution_plan)
        if plan:
            display = PlanDisplay("Resolution Plan")
            for step in plan.steps:
                display.add_step(step.description, step.estimated_cost, step.requires_approval)
            console.print(display.render())
        else:
            console.print("[yellow]No plan generated yet[/yellow]")
    elif query_type == "progress":
        progress = await handle.query(OrderResolutionWorkflow.get_progress)
        # Display rich progress info
        console.print(f"[bold]State:[/bold] {progress.state.value}")
        console.print(f"[bold]Ticket:[/bold] {progress.ticket_id}")
        if progress.extracted_intent:
            console.print(f"[bold]Issues:[/bold] {len(progress.extracted_intent.issues)}")
            console.print(f"[bold]Sentiment:[/bold] {progress.extracted_intent.customer_sentiment}")
        if progress.plan:
            console.print(f"[bold]Plan Steps:[/bold] {len(progress.plan.steps)}")
            console.print(f"[bold]Total Cost:[/bold] ${progress.plan.total_estimated_cost:.2f}")
        if progress.completed_steps:
            console.print(f"[bold]Completed:[/bold] {len(progress.completed_steps)}/{len(progress.plan.steps) if progress.plan else '?'}")
        if progress.error_message:
            console.print(f"[red]Error: {progress.error_message}[/red]")
        if progress.customer_message:
            console.print(f"\n[bold]Customer Message:[/bold]\n{progress.customer_message}")


async def run_benchmark():
    """Run the resumability benchmark."""
    from .benchmark.compare import run_comparison
    await run_comparison()


def main():
    args = parse_args()
    config = OrderResolutionConfig()

    if args.command == "worker":
        asyncio.run(run_worker(config, inject_failure=getattr(args, 'inject_failure', None)))
    elif args.command == "start":
        asyncio.run(start_workflow(config, args.ticket_id, getattr(args, 'workflow_id', None)))
    elif args.command == "approve":
        asyncio.run(send_approval(config, args.workflow_id, approved=True, notes=getattr(args, 'notes', '')))
    elif args.command == "reject":
        asyncio.run(send_approval(config, args.workflow_id, approved=False, notes=getattr(args, 'notes', '')))
    elif args.command == "query":
        asyncio.run(query_workflow(config, args.workflow_id, args.type))
    elif args.command == "benchmark":
        asyncio.run(run_benchmark())


if __name__ == "__main__":
    main()
