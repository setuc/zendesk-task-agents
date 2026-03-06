from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

from temporalio.client import Client
from temporalio.worker import Worker

from .config import OrderResolutionConfig
from .workflows.workflow import OrderResolutionWorkflow
from .workflows.activities import OrderResolutionActivities
from .workflows.data_types import ApprovalDecision, WorkflowProgress, WorkflowState
from common.tui import console, WorkflowDashboard, PlanDisplay, ApprovalPrompt, BenchmarkReport
from .services.order_db_mock import MockOrderDBService
from .services.shipping_mock import MockShippingService
from .services.payment_mock import MockPaymentService
from common.services.zendesk_mock import MockZendeskService


# ======================================================================
# Fixture loading
# ======================================================================

_FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _load_ticket_fixtures() -> dict[str, dict]:
    """Load all ticket fixture files from the fixtures directory."""
    tickets: dict[str, dict] = {}
    for path in _FIXTURE_DIR.glob("sample_ticket*.json"):
        with open(path) as f:
            data = json.load(f)
        if isinstance(data, dict) and "id" in data:
            tickets[data["id"]] = data
    return tickets


# ======================================================================
# CLI argument parsing
# ======================================================================


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

    # demo command
    demo_parser = subparsers.add_parser(
        "demo",
        help="Run full end-to-end demo with rich TUI output",
    )
    demo_parser.add_argument(
        "--ticket",
        type=str,
        default="TKT-10042",
        help="Ticket ID to demo (default: TKT-10042)",
    )

    # demo-saga command
    demo_saga_parser = subparsers.add_parser(
        "demo-saga",
        help="Demo saga compensation (failure + rollback)",
    )
    demo_saga_parser.add_argument(
        "--ticket",
        type=str,
        default="TKT-10042",
        help="Ticket ID to demo (default: TKT-10042)",
    )

    return parser.parse_args()


# ======================================================================
# Service creation
# ======================================================================


def create_mock_services(
    inject_failure: str | None = None,
    *,
    inject_replacement_failure: bool = False,
) -> tuple[MockZendeskService, MockOrderDBService, MockShippingService, MockPaymentService]:
    """Create all mock services with optional failure injection."""
    zendesk = MockZendeskService()
    order_db = MockOrderDBService()
    shipping = MockShippingService()
    payment = MockPaymentService()

    # Load order-resolution ticket fixtures into the zendesk mock
    for tid, tdata in _load_ticket_fixtures().items():
        zendesk._tickets[tid] = tdata

    if inject_failure:
        payment.inject_failure("process_refund")

    if inject_replacement_failure:
        order_db.inject_failure("create_replacement_order")

    return zendesk, order_db, shipping, payment


# ======================================================================
# Worker
# ======================================================================


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


# ======================================================================
# Start / Approve / Query
# ======================================================================


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
        _print_progress(progress)


def _print_progress(progress: WorkflowProgress) -> None:
    """Pretty-print workflow progress."""
    console.print(f"[bold]State:[/bold] {progress.state.value}")
    console.print(f"[bold]Ticket:[/bold] {progress.ticket_id}")
    if progress.extracted_intent:
        console.print(f"[bold]Issues:[/bold] {len(progress.extracted_intent.issues)}")
        console.print(f"[bold]Sentiment:[/bold] {progress.extracted_intent.customer_sentiment}")
    if progress.plan:
        console.print(f"[bold]Plan Steps:[/bold] {len(progress.plan.steps)}")
        console.print(f"[bold]Total Cost:[/bold] ${progress.plan.total_estimated_cost:.2f}")
    if progress.completed_steps:
        console.print(
            f"[bold]Completed:[/bold] {len(progress.completed_steps)}"
            f"/{len(progress.plan.steps) if progress.plan else '?'}"
        )
    if progress.error_message:
        console.print(f"[red]Error: {progress.error_message}[/red]")
    if progress.customer_message:
        console.print(f"\n[bold]Customer Message:[/bold]\n{progress.customer_message}")


# ======================================================================
# Benchmark
# ======================================================================


async def run_benchmark():
    """Run the resumability benchmark."""
    from .benchmark.compare import run_comparison
    await run_comparison()


# ======================================================================
# Demo helpers
# ======================================================================

_DEMO_STAGES = [
    "Classify & Extract",
    "Investigate",
    "Plan Resolution",
    "Approve",
    "Execute Steps",
    "Verify & Summarize",
]


def _commentary(text: str) -> None:
    """Print educational commentary below the dashboard."""
    console.print(f"\n[dim italic]>> {text}[/dim italic]")


async def _poll_until_state(
    handle,
    target_states: set[str],
    *,
    timeout: float = 30.0,
    interval: float = 0.3,
) -> WorkflowProgress:
    """Poll the workflow until it reaches one of the target states."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        progress = await handle.query(OrderResolutionWorkflow.get_progress)
        if progress.state.value in target_states:
            return progress
        await asyncio.sleep(interval)
    # Return whatever state we have
    return await handle.query(OrderResolutionWorkflow.get_progress)


# ======================================================================
# demo command
# ======================================================================


async def run_demo(config: OrderResolutionConfig, ticket_id: str) -> None:
    """Run a full end-to-end demo with rich TUI output."""

    console.rule("[bold blue]Order Resolution Demo[/bold blue]")
    console.print()
    _commentary(
        "This demo shows a complete order resolution workflow powered by "
        "Temporal + Universal Computer agents. We will classify a customer "
        "ticket, investigate order data, plan a resolution, get approval, "
        "execute the plan, and generate a customer response."
    )
    console.print()

    # --- Load and display ticket ---
    tickets = _load_ticket_fixtures()
    ticket = tickets.get(ticket_id)
    if ticket:
        from rich.panel import Panel
        from rich.text import Text

        requester = ticket.get("requester", {})
        ticket_text = (
            f"[bold]Ticket:[/bold] {ticket.get('id', ticket_id)}\n"
            f"[bold]Subject:[/bold] {ticket.get('subject', 'N/A')}\n"
            f"[bold]Customer:[/bold] {requester.get('name', 'N/A')} "
            f"({requester.get('tier', 'standard')} tier)\n"
            f"[bold]Priority:[/bold] {ticket.get('priority', 'normal')}\n"
            f"\n{ticket.get('description', '')}"
        )
        console.print(Panel(
            ticket_text,
            title="[bold cyan]Incoming Ticket[/bold cyan]",
            border_style="cyan",
            padding=(1, 2),
        ))
        console.print()

    # --- Start worker in background ---
    _commentary("Starting Temporal worker in background...")
    client = await Client.connect(config.temporal_address, namespace=config.temporal_namespace)
    zendesk, order_db, shipping, payment = create_mock_services()

    activities = OrderResolutionActivities(
        zendesk=zendesk, order_db=order_db, shipping=shipping, payment=payment
    )

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

    # Run worker as background task
    worker_task = asyncio.create_task(worker.run())

    try:
        # Small delay to let the worker register
        await asyncio.sleep(0.5)

        # --- Start the dashboard ---
        dashboard = WorkflowDashboard(
            f"Order Resolution: {ticket_id}", _DEMO_STAGES
        )
        dashboard.start()

        # --- Start the workflow ---
        wf_id = f"demo-order-resolution-{ticket_id}-{int(time.time())}"
        handle = await client.start_workflow(
            OrderResolutionWorkflow.run,
            ticket_id,
            id=wf_id,
            task_queue=config.task_queue,
        )
        dashboard.set_detail(f"Workflow started: {wf_id}")

        # --- Stage 1: Classify ---
        t0 = time.monotonic()
        dashboard.update_stage("Classify & Extract", "running")
        act_idx = dashboard.add_activity("classify_and_extract")

        progress = await _poll_until_state(
            handle,
            {"investigating", "planning", "awaiting_approval", "executing", "verifying", "completed", "failed"},
        )
        elapsed = time.monotonic() - t0
        dashboard.update_activity(act_idx, "done", duration=elapsed)
        dashboard.update_stage("Classify & Extract", "done")

        if progress.extracted_intent:
            intent = progress.extracted_intent
            dashboard.set_detail(
                f"Found {len(intent.issues)} issue(s) | "
                f"Sentiment: {intent.customer_sentiment} | "
                f"Urgency: {intent.urgency}"
            )

        # --- Stage 2: Investigate ---
        t0 = time.monotonic()
        dashboard.update_stage("Investigate", "running")
        act_idx = dashboard.add_activity("investigate")

        progress = await _poll_until_state(
            handle,
            {"planning", "awaiting_approval", "executing", "verifying", "completed", "failed"},
        )
        elapsed = time.monotonic() - t0
        dashboard.update_activity(act_idx, "done", duration=elapsed)
        dashboard.update_stage("Investigate", "done")

        if progress.investigation:
            inv = progress.investigation
            dashboard.set_detail(
                f"Findings: {len(inv.findings)} | "
                f"Discrepancies: {len(inv.discrepancies)}"
            )

        # --- Stage 3: Plan ---
        t0 = time.monotonic()
        dashboard.update_stage("Plan Resolution", "running")
        act_idx = dashboard.add_activity("plan_resolution")

        progress = await _poll_until_state(
            handle,
            {"awaiting_approval", "executing", "verifying", "completed", "failed"},
        )
        elapsed = time.monotonic() - t0
        dashboard.update_activity(act_idx, "done", duration=elapsed)
        dashboard.update_stage("Plan Resolution", "done")

        if progress.plan:
            dashboard.set_detail(
                f"Plan: {len(progress.plan.steps)} steps | "
                f"Cost: ${progress.plan.total_estimated_cost:.2f} | "
                f"Approval: {'REQUIRED' if progress.plan.requires_human_approval else 'auto'}"
            )

        # Stop dashboard temporarily to show the plan
        dashboard.stop()
        console.print()

        # --- Display investigation findings ---
        if progress.investigation:
            inv = progress.investigation
            from rich.panel import Panel

            findings_text = ""
            if inv.findings:
                findings_text += "[bold]Findings:[/bold]\n"
                for f in inv.findings:
                    findings_text += f"  [green]+[/green] {f}\n"
            if inv.discrepancies:
                findings_text += "\n[bold]Discrepancies:[/bold]\n"
                for d in inv.discrepancies:
                    findings_text += f"  [red]![/red] {d}\n"

            console.print(Panel(
                findings_text.strip(),
                title="[bold yellow]Investigation Results[/bold yellow]",
                border_style="yellow",
                padding=(1, 2),
            ))
            console.print()

        # --- Display the plan ---
        if progress.plan:
            plan_display = PlanDisplay("Resolution Plan")
            for step in progress.plan.steps:
                plan_display.add_step(
                    step.description,
                    step.estimated_cost,
                    step.requires_approval,
                )
            console.print(plan_display.render())
            console.print()

            # Show reasoning
            if progress.plan.reasoning:
                from rich.panel import Panel
                console.print(Panel(
                    progress.plan.reasoning,
                    title="[bold]Agent Reasoning[/bold]",
                    border_style="bright_blue",
                    padding=(1, 2),
                ))
                console.print()

        # --- Stage 4: Approve ---
        if progress.state == WorkflowState.AWAITING_APPROVAL:
            _commentary(
                "The plan requires human approval (cost exceeds $50 threshold). "
                "Auto-approving for demo purposes..."
            )
            console.print()

            # Auto-approve
            decision = ApprovalDecision(
                approved=True,
                reviewer="demo_auto_approver",
                notes="Auto-approved by demo runner",
            )
            await handle.signal(OrderResolutionWorkflow.approval_decision, decision)
            console.print("[bold green]Plan auto-approved.[/bold green]")
            console.print()
        else:
            _commentary("Plan within auto-approval threshold -- no human approval needed.")
            console.print()

        # Resume dashboard for execution
        dashboard = WorkflowDashboard(
            f"Order Resolution: {ticket_id} -- Executing", _DEMO_STAGES
        )
        # Mark completed stages
        dashboard.update_stage("Classify & Extract", "done")
        dashboard.update_stage("Investigate", "done")
        dashboard.update_stage("Plan Resolution", "done")
        dashboard.update_stage("Approve", "done")
        dashboard.update_stage("Execute Steps", "running")
        dashboard.start()

        # --- Stage 5: Execute ---
        if progress.plan:
            num_steps = len(progress.plan.steps)
            step_act_indices: list[int] = []
            for i, step in enumerate(progress.plan.steps):
                idx = dashboard.add_activity(
                    f"execute: {step.action} ({step.step_id})"
                )
                step_act_indices.append(idx)

        # Poll execution
        t0 = time.monotonic()
        last_completed = 0
        progress = await _poll_until_state(
            handle,
            {"verifying", "completed", "failed", "compensating"},
            timeout=60.0,
            interval=0.2,
        )
        elapsed = time.monotonic() - t0

        # Mark all step activities
        if progress.plan:
            for i in range(len(progress.plan.steps)):
                if i < len(progress.completed_steps):
                    step_result = progress.completed_steps[i]
                    details = step_result.result_data.get("summary", "")
                    if len(details) > 60:
                        details = details[:57] + "..."
                    dashboard.update_activity(
                        step_act_indices[i],
                        "done" if step_result.success else "failed",
                        details=details,
                    )
                else:
                    dashboard.update_activity(step_act_indices[i], "skipped")

        dashboard.update_stage("Execute Steps", "done")
        dashboard.set_detail(
            f"Executed {len(progress.completed_steps)} step(s) in {elapsed:.2f}s"
        )

        # --- Stage 6: Verify ---
        dashboard.update_stage("Verify & Summarize", "running")
        act_idx = dashboard.add_activity("verify_and_summarize")

        progress = await _poll_until_state(
            handle,
            {"completed", "failed"},
            timeout=30.0,
        )
        dashboard.update_activity(act_idx, "done")
        dashboard.update_stage("Verify & Summarize", "done")
        dashboard.set_detail("Workflow complete!")

        dashboard.stop()
        console.print()

        # --- Final result ---
        if progress.customer_message:
            from rich.panel import Panel
            console.print(Panel(
                progress.customer_message,
                title="[bold green]Customer Response Message[/bold green]",
                border_style="green",
                padding=(1, 2),
            ))
            console.print()

        # --- Summary ---
        console.rule("[bold blue]Demo Complete[/bold blue]")
        console.print()

        if progress.extracted_intent:
            console.print(f"[bold]Issues identified:[/bold] {len(progress.extracted_intent.issues)}")
        if progress.plan:
            console.print(f"[bold]Resolution steps:[/bold] {len(progress.plan.steps)}")
            console.print(f"[bold]Total cost:[/bold] ${progress.plan.total_estimated_cost:.2f}")
        console.print(f"[bold]Steps executed:[/bold] {len(progress.completed_steps)}")
        console.print(f"[bold]Final state:[/bold] {progress.state.value}")
        console.print()

        if progress.extracted_intent:
            _commentary(
                f"AI Summary: {progress.extracted_intent.summary}"
            )
            console.print()

    finally:
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass


# ======================================================================
# demo-saga command
# ======================================================================


async def run_demo_saga(config: OrderResolutionConfig, ticket_id: str) -> None:
    """Demo saga compensation: inject a failure and show rollback."""

    console.rule("[bold red]Saga Compensation Demo[/bold red]")
    console.print()
    _commentary(
        "This demo shows Temporal's saga compensation pattern. We inject a "
        "failure on the replacement step AFTER a refund has already been "
        "processed. The workflow will detect the failure and automatically "
        "roll back (compensate) all previously completed steps in reverse "
        "order -- reversing the refund."
    )
    console.print()

    # --- Start worker with failure injection ---
    client = await Client.connect(config.temporal_address, namespace=config.temporal_namespace)
    zendesk, order_db, shipping, payment = create_mock_services(
        inject_replacement_failure=True,
    )

    activities = OrderResolutionActivities(
        zendesk=zendesk, order_db=order_db, shipping=shipping, payment=payment
    )

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

    worker_task = asyncio.create_task(worker.run())

    try:
        await asyncio.sleep(0.5)

        from rich.panel import Panel

        console.print(Panel(
            "[bold red]FAILURE INJECTION ACTIVE[/bold red]\n\n"
            "The order DB service will fail on create_replacement_order.\n"
            "This simulates a downstream service outage that occurs\n"
            "AFTER some steps have already completed (refunds processed).\n\n"
            "The saga pattern ensures we don't leave the system in an\n"
            "inconsistent state -- all completed steps will be reversed.",
            title="[bold]Fault Injection[/bold]",
            border_style="red",
            padding=(1, 2),
        ))
        console.print()

        # --- Start the dashboard ---
        saga_stages = [
            "Classify & Extract",
            "Investigate",
            "Plan Resolution",
            "Approve",
            "Execute Steps",
            "FAILURE DETECTED",
            "Saga Compensation",
        ]
        dashboard = WorkflowDashboard(
            f"Saga Demo: {ticket_id}", saga_stages
        )
        dashboard.start()

        # --- Start the workflow ---
        wf_id = f"demo-saga-{ticket_id}-{int(time.time())}"
        handle = await client.start_workflow(
            OrderResolutionWorkflow.run,
            ticket_id,
            id=wf_id,
            task_queue=config.task_queue,
        )
        dashboard.set_detail(f"Workflow started: {wf_id}")

        # --- Classify ---
        dashboard.update_stage("Classify & Extract", "running")
        act_idx = dashboard.add_activity("classify_and_extract")
        progress = await _poll_until_state(
            handle, {"investigating", "planning", "awaiting_approval", "executing", "verifying", "completed", "failed", "compensating"}
        )
        dashboard.update_activity(act_idx, "done")
        dashboard.update_stage("Classify & Extract", "done")

        # --- Investigate ---
        dashboard.update_stage("Investigate", "running")
        act_idx = dashboard.add_activity("investigate")
        progress = await _poll_until_state(
            handle, {"planning", "awaiting_approval", "executing", "verifying", "completed", "failed", "compensating"}
        )
        dashboard.update_activity(act_idx, "done")
        dashboard.update_stage("Investigate", "done")

        # --- Plan ---
        dashboard.update_stage("Plan Resolution", "running")
        act_idx = dashboard.add_activity("plan_resolution")
        progress = await _poll_until_state(
            handle, {"awaiting_approval", "executing", "verifying", "completed", "failed", "compensating"}
        )
        dashboard.update_activity(act_idx, "done")
        dashboard.update_stage("Plan Resolution", "done")

        # --- Approve (auto) ---
        if progress.state == WorkflowState.AWAITING_APPROVAL:
            dashboard.update_stage("Approve", "running")
            decision = ApprovalDecision(
                approved=True,
                reviewer="saga_demo_auto",
                notes="Auto-approved for saga demo",
            )
            await handle.signal(OrderResolutionWorkflow.approval_decision, decision)
            dashboard.update_stage("Approve", "done")
            dashboard.set_detail("Auto-approved. Now executing steps...")
        else:
            dashboard.update_stage("Approve", "skipped")

        # --- Execute (will fail) ---
        dashboard.update_stage("Execute Steps", "running")

        # Track individual steps
        if progress.plan:
            step_indices: list[int] = []
            for step in progress.plan.steps:
                idx = dashboard.add_activity(f"execute: {step.action} ({step.step_id})")
                step_indices.append(idx)

        # Wait for failure or completion
        progress = await _poll_until_state(
            handle,
            {"compensating", "failed", "completed", "verifying"},
            timeout=60.0,
            interval=0.2,
        )

        # Mark steps based on results
        if progress.plan:
            for i, step in enumerate(progress.plan.steps):
                if i < len(progress.completed_steps):
                    sr = progress.completed_steps[i]
                    dashboard.update_activity(
                        step_indices[i],
                        "done" if sr.success else "failed",
                        details=sr.result_data.get("summary", sr.error_message or "")[:60],
                    )
                elif progress.error_message and step.step_id in (progress.error_message or ""):
                    dashboard.update_activity(
                        step_indices[i], "failed",
                        details="INJECTED FAILURE",
                    )
                else:
                    dashboard.update_activity(step_indices[i], "skipped")

        dashboard.update_stage("Execute Steps", "failed")

        if progress.state.value in ("compensating", "failed"):
            dashboard.update_stage("FAILURE DETECTED", "failed")
            dashboard.set_detail(
                f"FAILURE: {progress.error_message or 'Step execution failed'}"
            )

            # --- Saga compensation ---
            dashboard.update_stage("Saga Compensation", "running")

            # Add compensation activities in reverse
            comp_indices: list[int] = []
            for sr in reversed(progress.completed_steps):
                if sr.compensation_data:
                    idx = dashboard.add_activity(
                        f"compensate: {sr.compensation_data.get('action', 'rollback')} "
                        f"({sr.step_id})"
                    )
                    comp_indices.append(idx)

            # Wait for the final state
            final_progress = await _poll_until_state(
                handle,
                {"failed", "completed"},
                timeout=30.0,
            )

            # Mark compensation as done
            for idx in comp_indices:
                dashboard.update_activity(idx, "done", details="Rolled back successfully")

            dashboard.update_stage("Saga Compensation", "done")
            dashboard.set_detail("All completed steps have been reversed.")
        else:
            dashboard.set_detail("Workflow completed (no failure triggered)")

        dashboard.stop()
        console.print()

        # --- Show summary ---
        final_progress = await handle.query(OrderResolutionWorkflow.get_progress)

        console.print(Panel(
            (
                f"[bold]Final State:[/bold] {final_progress.state.value}\n"
                f"[bold]Error:[/bold] {final_progress.error_message or 'None'}\n"
                f"[bold]Steps Completed Before Failure:[/bold] {len(final_progress.completed_steps)}\n"
                f"[bold]Steps Compensated:[/bold] "
                f"{sum(1 for s in final_progress.completed_steps if s.compensation_data)}\n"
                f"\n"
                f"[bold yellow]What happened:[/bold yellow]\n"
                f"1. The workflow executed steps sequentially.\n"
                f"2. After processing refund(s), the replacement step failed\n"
                f"   due to the injected service outage.\n"
                f"3. The saga pattern kicked in, reversing all completed\n"
                f"   steps in reverse order.\n"
                f"4. Refund(s) were reversed (money returned to company).\n"
                f"5. The system is back to a consistent state -- no partial\n"
                f"   resolution was left in place."
            ),
            title="[bold red]Saga Compensation Summary[/bold red]",
            border_style="red",
            padding=(1, 2),
        ))
        console.print()

        console.rule("[bold red]Saga Demo Complete[/bold red]")
        console.print()
        _commentary(
            "This demonstrates why durable execution matters: without Temporal, "
            "a crash or failure mid-workflow would leave refunds processed but "
            "no replacement shipped -- an inconsistent state. The saga pattern "
            "ensures all-or-nothing semantics."
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
    elif args.command == "demo":
        asyncio.run(run_demo(config, args.ticket))
    elif args.command == "demo-saga":
        asyncio.run(run_demo_saga(config, args.ticket))


if __name__ == "__main__":
    main()
