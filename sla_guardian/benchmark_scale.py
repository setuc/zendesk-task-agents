"""Standalone scale benchmark for AgentTicketWorkflow.

Starts an in-process worker, injects N workflows, waits for completion,
then measures storage, query performance, simulated token usage, and
throughput.  Produces a Rich report.

Usage:
    uv run python -m sla_guardian.benchmark_scale --tickets 1000 --seed 42
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)

from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Worker

from sla_guardian.config import SLAGuardianConfig
from sla_guardian.workflows.agent_ticket_workflow import AgentTicketWorkflow
from sla_guardian.workflows.agent_activities import AgentTicketActivities
from sla_guardian.workflows.data_types import (
    AgentResolution,
    AgentStatus,
    AgentTicketState,
    TokenMetrics,
)
from sla_guardian.fixtures.ticket_generator import generate_ticket_batch

from common.services.zendesk_mock import MockZendeskService
from order_resolution.services.order_db_mock import MockOrderDBService
from order_resolution.services.payment_mock import MockPaymentService
from order_resolution.services.shipping_mock import MockShippingService

console = Console()

# ---------------------------------------------------------------------------
# GPT-4o reference pricing
# ---------------------------------------------------------------------------
INPUT_PRICE_PER_M = 2.50  # $/1M tokens
OUTPUT_PRICE_PER_M = 10.00  # $/1M tokens


# ---------------------------------------------------------------------------
# Token estimation helpers
# ---------------------------------------------------------------------------

def _estimate_token_metrics(
    resolution: AgentResolution,
    ticket_description: str,
) -> TokenMetrics:
    """Compute simulated token usage from a completed AgentResolution.

    Heuristics modelled on what a real UC agent session would consume.
    """
    # --- Prompt (input) tokens ---
    system_prompt_tokens = 800
    content_tokens = int(len(ticket_description.split()) * 1.3)
    tool_input_tokens = len(resolution.tool_calls) * 100
    reasoning_tokens = 400  # base agent reasoning

    # Sandbox interaction adds extra context
    sandbox_tokens = 500 if resolution.sandbox_commands else 0

    # Memory hit means shorter reasoning path
    memory_bonus = -200 if resolution.memory_hit else 0

    prompt_tokens = max(
        0,
        system_prompt_tokens
        + content_tokens
        + tool_input_tokens
        + reasoning_tokens
        + sandbox_tokens
        + memory_bonus,
    )

    # --- Completion (output) tokens ---
    tool_output_tokens = len(resolution.tool_calls) * 200
    decision_tokens = 400  # agent reasoning output
    final_response_tokens = 300  # customer message generation

    completion_tokens = tool_output_tokens + decision_tokens + final_response_tokens

    total_tokens = prompt_tokens + completion_tokens
    estimated_cost = (
        prompt_tokens / 1_000_000 * INPUT_PRICE_PER_M
        + completion_tokens / 1_000_000 * OUTPUT_PRICE_PER_M
    )

    return TokenMetrics(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        estimated_cost_usd=round(estimated_cost, 6),
    )


# ---------------------------------------------------------------------------
# Category inference (mirrors main.py logic)
# ---------------------------------------------------------------------------

def _infer_category(tags: list[str]) -> str:
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


# ---------------------------------------------------------------------------
# Percentile helper
# ---------------------------------------------------------------------------

def _percentile(data: list[float], pct: float) -> float:
    if not data:
        return 0.0
    s = sorted(data)
    k = (len(s) - 1) * (pct / 100.0)
    f = int(k)
    c = f + 1
    if c >= len(s):
        return s[f]
    return s[f] + (k - f) * (s[c] - s[f])


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scale benchmark for AgentTicketWorkflow",
    )
    parser.add_argument(
        "--tickets",
        type=int,
        default=1000,
        help="Number of workflows to run (default: 1000)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed (default: 42)",
    )
    parser.add_argument(
        "--sla-offset",
        type=int,
        default=10,
        help="SLA offset in minutes (default: 10)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=50,
        help="Max concurrent workflow starts (default: 50)",
    )
    parser.add_argument(
        "--query-sample",
        type=int,
        default=100,
        help="Number of workflows to sample for query perf (default: 100)",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Storage measurement via `temporal workflow describe`
# ---------------------------------------------------------------------------

async def _measure_storage(
    client: Any,
    workflow_ids: list[str],
    concurrency: int,
) -> list[dict]:
    """Describe workflows via Python API and collect storage stats."""
    sem = asyncio.Semaphore(concurrency)
    results: list[dict] = []

    async def _describe(wf_id: str) -> None:
        async with sem:
            try:
                handle = client.get_workflow_handle(wf_id)
                desc = await handle.describe()
                raw = desc.raw_info
                results.append({
                    "historyLength": raw.history_length,
                    "historySizeBytes": raw.history_size_bytes,
                    "stateTransitionCount": raw.state_transition_count,
                })
            except Exception:
                pass

    await asyncio.gather(*[_describe(wid) for wid in workflow_ids])
    return results


# ---------------------------------------------------------------------------
# Main benchmark
# ---------------------------------------------------------------------------

async def run_benchmark(args: argparse.Namespace) -> None:
    config = SLAGuardianConfig()
    task_queue = "sla-guardian-benchmark"
    ticket_count = args.tickets
    seed = args.seed
    sla_offset = args.sla_offset
    concurrency = args.concurrency
    query_sample = min(args.query_sample, ticket_count)

    console.print()
    console.rule(
        f"[bold blue]Scale Benchmark: {ticket_count} Workflows[/bold blue]"
    )
    console.print()

    # ------------------------------------------------------------------ #
    # 1. Generate tickets                                                 #
    # ------------------------------------------------------------------ #
    console.print(
        f"[dim]Generating {ticket_count} tickets  seed={seed}  "
        f"SLA offset={sla_offset}min ...[/dim]"
    )
    tickets = generate_ticket_batch(
        ticket_count, seed=seed, sla_offset_minutes=sla_offset,
    )
    ticket_map: dict[str, dict] = {t["id"]: t for t in tickets}
    console.print(f"[green]Generated {len(tickets)} tickets.[/green]")

    # ------------------------------------------------------------------ #
    # 2. Set up mock services + worker                                    #
    # ------------------------------------------------------------------ #
    zendesk = MockZendeskService()
    now = datetime.now(timezone.utc)
    for t in tickets:
        ticket = dict(t)
        ticket["sla_deadline"] = (
            now + timedelta(minutes=sla_offset)
        ).isoformat()
        zendesk._tickets[ticket["id"]] = ticket

    activities = AgentTicketActivities(
        zendesk=zendesk,
        order_db=MockOrderDBService(),
        payment=MockPaymentService(),
        shipping=MockShippingService(),
        memory_store={},
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
        max_concurrent_activities=concurrency,
        max_concurrent_workflow_tasks=concurrency,
    )

    # Start worker in background
    worker_task = asyncio.create_task(worker.run())

    # ------------------------------------------------------------------ #
    # 3. Inject workflows                                                 #
    # ------------------------------------------------------------------ #
    console.print()
    console.print(
        f"[bold cyan]Injecting {ticket_count} workflows "
        f"(concurrency={concurrency}) ...[/bold cyan]"
    )

    sem = asyncio.Semaphore(concurrency)
    workflow_ids: list[str] = []
    inject_start = time.perf_counter()

    async def _start_one(ticket: dict) -> str:
        async with sem:
            ticket_id = ticket["id"]
            tags = ticket.get("tags", [])
            category = _infer_category(tags)
            metadata = {
                "customer_name": ticket.get("requester", {}).get("name", ""),
                "customer_tier": ticket.get("requester", {}).get(
                    "tier", "standard"
                ),
                "priority": ticket.get("priority", "normal"),
                "category": category,
                "subject": ticket.get("subject", ""),
            }
            wf_id = f"bench-agent-{ticket_id}"
            await client.start_workflow(
                AgentTicketWorkflow.run,
                args=[ticket_id, metadata],
                id=wf_id,
                task_queue=task_queue,
            )
            return wf_id

    start_results = await asyncio.gather(
        *[_start_one(t) for t in tickets],
        return_exceptions=True,
    )
    for r in start_results:
        if isinstance(r, str):
            workflow_ids.append(r)

    inject_end = time.perf_counter()
    inject_elapsed = inject_end - inject_start
    inject_rate = len(workflow_ids) / inject_elapsed if inject_elapsed > 0 else 0

    console.print(
        f"[green]Injected {len(workflow_ids)} workflows in "
        f"{inject_elapsed:.1f}s ({inject_rate:.0f} wf/s)[/green]"
    )

    if not workflow_ids:
        console.print("[bold red]No workflows started. Aborting.[/bold red]")
        worker_task.cancel()
        return

    # ------------------------------------------------------------------ #
    # 4. Wait for completion                                              #
    # ------------------------------------------------------------------ #
    console.print()
    console.print("[bold cyan]Waiting for workflows to complete ...[/bold cyan]")

    completion_start = time.perf_counter()
    completed_states: dict[str, AgentTicketState] = {}

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        ptask = progress.add_task("Completing", total=len(workflow_ids))

        while len(completed_states) < len(workflow_ids):
            batch_check: list[str] = [
                wid for wid in workflow_ids if wid not in completed_states
            ]
            check_sem = asyncio.Semaphore(concurrency)

            async def _check(wid: str) -> tuple[str, AgentTicketState | None]:
                async with check_sem:
                    try:
                        handle = client.get_workflow_handle(wid)
                        state: AgentTicketState = await handle.query(
                            AgentTicketWorkflow.get_state,
                        )
                        if state.agent_status in (
                            AgentStatus.RESOLVED,
                            AgentStatus.ESCALATED,
                            AgentStatus.FAILED,
                        ):
                            return wid, state
                    except Exception:
                        pass
                    return wid, None

            results = await asyncio.gather(
                *[_check(wid) for wid in batch_check]
            )
            for wid, state in results:
                if state is not None and wid not in completed_states:
                    completed_states[wid] = state
                    progress.update(ptask, completed=len(completed_states))

            if len(completed_states) < len(workflow_ids):
                await asyncio.sleep(0.5)

    completion_end = time.perf_counter()
    completion_elapsed = completion_end - completion_start
    total_elapsed = completion_end - inject_start
    processing_rate = (
        len(completed_states) / completion_elapsed
        if completion_elapsed > 0
        else 0
    )

    console.print(
        f"[green]All {len(completed_states)} workflows completed in "
        f"{completion_elapsed:.1f}s ({processing_rate:.0f} wf/s)[/green]"
    )

    # ------------------------------------------------------------------ #
    # 5. Token metrics (post-processing)                                  #
    # ------------------------------------------------------------------ #
    console.print()
    console.print("[dim]Computing token metrics ...[/dim]")

    all_token_metrics: list[TokenMetrics] = []
    category_tokens: dict[str, list[TokenMetrics]] = defaultdict(list)

    for wf_id, state in completed_states.items():
        resolution = state.resolution
        if resolution is None:
            continue

        ticket_id = resolution.ticket_id
        ticket_data = ticket_map.get(ticket_id, {})
        description = ticket_data.get("description", "")

        tm = _estimate_token_metrics(resolution, description)
        all_token_metrics.append(tm)

        tags = ticket_data.get("tags", [])
        category = _infer_category(tags)
        category_tokens[category].append(tm)

    total_prompt = sum(t.prompt_tokens for t in all_token_metrics)
    total_completion = sum(t.completion_tokens for t in all_token_metrics)
    total_all = sum(t.total_tokens for t in all_token_metrics)
    total_cost = sum(t.estimated_cost_usd for t in all_token_metrics)
    n_metrics = len(all_token_metrics) or 1

    avg_prompt = total_prompt / n_metrics
    avg_completion = total_completion / n_metrics
    avg_total = total_all / n_metrics
    avg_cost = total_cost / n_metrics

    # ------------------------------------------------------------------ #
    # 6. Query performance                                                #
    # ------------------------------------------------------------------ #
    console.print("[dim]Measuring query performance ...[/dim]")

    sample_ids = workflow_ids[:query_sample]

    # 6a. Single query
    single_id = workflow_ids[0]
    t0 = time.perf_counter()
    handle = client.get_workflow_handle(single_id)
    await handle.query(AgentTicketWorkflow.get_state)
    single_query_ms = (time.perf_counter() - t0) * 1000

    # 6b. N parallel queries (sample)
    query_sem = asyncio.Semaphore(concurrency)

    async def _query_one(wid: str) -> None:
        async with query_sem:
            h = client.get_workflow_handle(wid)
            await h.query(AgentTicketWorkflow.get_state)

    t0 = time.perf_counter()
    await asyncio.gather(*[_query_one(wid) for wid in sample_ids])
    batch_query_ms = (time.perf_counter() - t0) * 1000
    per_query_batch_ms = batch_query_ms / len(sample_ids) if sample_ids else 0

    # 6c. All N parallel queries
    t0 = time.perf_counter()
    await asyncio.gather(*[_query_one(wid) for wid in workflow_ids])
    all_query_ms = (time.perf_counter() - t0) * 1000
    per_query_all_ms = all_query_ms / len(workflow_ids) if workflow_ids else 0

    # 6d. list_workflows
    t0 = time.perf_counter()
    list_count = 0
    async for _ in client.list_workflows(
        query=f"TaskQueue='{task_queue}'"
    ):
        list_count += 1
    list_wf_ms = (time.perf_counter() - t0) * 1000

    # ------------------------------------------------------------------ #
    # 7. Storage metrics via temporal CLI                                  #
    # ------------------------------------------------------------------ #
    console.print(
        "[dim]Measuring storage via temporal workflow describe ...[/dim]"
    )

    storage_sample = workflow_ids[:min(200, len(workflow_ids))]
    described = await _measure_storage(client, storage_sample, concurrency)

    history_sizes: list[int] = []
    history_lengths: list[int] = []
    state_transitions: list[int] = []

    for info in described:
        hist_len = info.get("historyLength")
        hist_size = info.get("historySizeBytes")
        st_count = info.get("stateTransitionCount")

        if hist_size is not None:
            history_sizes.append(int(hist_size))
        if hist_len is not None:
            history_lengths.append(int(hist_len))
        if st_count is not None:
            state_transitions.append(int(st_count))

    # Extrapolate to full N
    if history_sizes:
        avg_size = statistics.mean(history_sizes)
        total_storage = avg_size * len(workflow_ids)
    else:
        avg_size = 0
        total_storage = 0

    # ------------------------------------------------------------------ #
    # 8. Shut down worker                                                 #
    # ------------------------------------------------------------------ #
    worker_task.cancel()
    try:
        await worker_task
    except asyncio.CancelledError:
        pass

    # ------------------------------------------------------------------ #
    # 9. Build report                                                     #
    # ------------------------------------------------------------------ #
    console.print()

    def _fmt_bytes(b: float) -> str:
        if b >= 1_000_000_000:
            return f"{b / 1_000_000_000:.1f} GB"
        if b >= 1_000_000:
            return f"{b / 1_000_000:.1f} MB"
        if b >= 1_000:
            return f"{b / 1_000:.1f} KB"
        return f"{b:.0f} B"

    # --- Throughput ---
    throughput_table = Table(
        show_header=False, box=None, padding=(0, 2), expand=True,
    )
    throughput_table.add_column("Metric", style="bold", ratio=2)
    throughput_table.add_column("Value", ratio=3)
    throughput_table.add_row(
        "Injection rate", f"{inject_rate:,.0f} workflows/sec"
    )
    throughput_table.add_row(
        "Processing rate", f"{processing_rate:,.0f} workflows/sec"
    )
    throughput_table.add_row("Total time", f"{total_elapsed:.1f}s")

    # --- Storage ---
    storage_table = Table(
        show_header=False, box=None, padding=(0, 2), expand=True,
    )
    storage_table.add_column("Metric", style="bold", ratio=2)
    storage_table.add_column("Value", ratio=3)

    if history_sizes:
        storage_table.add_row(
            "Total history (extrapolated)",
            f"{_fmt_bytes(total_storage)} ({len(workflow_ids)} workflows)",
        )
        storage_table.add_row(
            "Per workflow",
            f"avg={_fmt_bytes(avg_size)}  "
            f"p50={_fmt_bytes(_percentile(history_sizes, 50))}  "
            f"p95={_fmt_bytes(_percentile(history_sizes, 95))}  "
            f"p99={_fmt_bytes(_percentile(history_sizes, 99))}",
        )
    else:
        storage_table.add_row(
            "Total history",
            "[yellow]temporal CLI not available or describe failed[/yellow]",
        )

    if history_lengths:
        storage_table.add_row(
            "Events/workflow",
            f"avg={statistics.mean(history_lengths):.0f}  "
            f"min={min(history_lengths)}  max={max(history_lengths)}",
        )

    if history_sizes and history_lengths:
        avg_events = statistics.mean(history_lengths)
        overhead_est = avg_events * 45
        if avg_size > 0:
            payload_pct = max(0, (avg_size - overhead_est) / avg_size * 100)
            overhead_pct = 100 - payload_pct
            storage_table.add_row(
                "Payload ratio",
                f"{payload_pct:.0f}% your data / {overhead_pct:.0f}% Temporal overhead",
            )

    # --- Query perf ---
    query_table = Table(
        show_header=False, box=None, padding=(0, 2), expand=True,
    )
    query_table.add_column("Metric", style="bold", ratio=2)
    query_table.add_column("Value", ratio=3)
    query_table.add_row("Single query", f"{single_query_ms:.1f}ms")
    query_table.add_row(
        f"{len(sample_ids)} parallel queries",
        f"{batch_query_ms:.0f}ms ({per_query_batch_ms:.2f}ms/query)",
    )
    query_table.add_row(
        f"{len(workflow_ids)} parallel queries",
        f"{all_query_ms:.0f}ms ({per_query_all_ms:.2f}ms/query)",
    )
    query_table.add_row(
        "list_workflows",
        f"{list_wf_ms:.0f}ms for {list_count} workflows",
    )

    # --- Token usage ---
    token_table = Table(
        show_header=False, box=None, padding=(0, 2), expand=True,
    )
    token_table.add_column("Metric", style="bold", ratio=2)
    token_table.add_column("Value", ratio=3)
    prompt_pct = total_prompt / total_all * 100 if total_all else 0
    compl_pct = total_completion / total_all * 100 if total_all else 0
    token_table.add_row("Total tokens", f"{total_all:,.0f}")
    token_table.add_row("Avg per ticket", f"{avg_total:,.0f} tokens")
    token_table.add_row(
        "Prompt tokens", f"{total_prompt:,.0f} ({prompt_pct:.0f}%)"
    )
    token_table.add_row(
        "Completion tokens", f"{total_completion:,.0f} ({compl_pct:.0f}%)"
    )

    # --- Cost projection ---
    cost_table = Table(
        show_header=False, box=None, padding=(0, 2), expand=True,
    )
    cost_table.add_column("Metric", style="bold", ratio=2)
    cost_table.add_column("Value", ratio=3)
    cost_per_1k = avg_cost * 1000
    cost_per_10k_day = avg_cost * 10_000
    cost_per_10k_month = cost_per_10k_day * 30
    cost_table.add_row("Per ticket", f"${avg_cost:.4f}")
    cost_table.add_row("Per 1K tickets", f"${cost_per_1k:.2f}")
    cost_table.add_row(
        "Per 10K tickets/day",
        f"${cost_per_10k_day:.0f}/day = ${cost_per_10k_month:,.0f}/month",
    )

    # --- Token usage by category ---
    cat_table = Table(
        show_header=True, header_style="bold", box=None,
        padding=(0, 2), expand=True,
    )
    cat_table.add_column("Category", style="bold", ratio=2)
    cat_table.add_column("Count", justify="right", ratio=1)
    cat_table.add_column("Avg Tokens", justify="right", ratio=1)
    cat_table.add_column("Avg Cost", justify="right", ratio=1)

    for cat in sorted(category_tokens.keys()):
        cat_metrics = category_tokens[cat]
        cat_count = len(cat_metrics)
        cat_avg_tokens = (
            sum(m.total_tokens for m in cat_metrics) / cat_count
            if cat_count else 0
        )
        cat_avg_cost = (
            sum(m.estimated_cost_usd for m in cat_metrics) / cat_count
            if cat_count else 0
        )
        cat_table.add_row(
            cat,
            str(cat_count),
            f"{cat_avg_tokens:,.0f}",
            f"${cat_avg_cost:.4f}/ticket",
        )

    # --- Cluster sizing ---
    sizing_table = Table(
        show_header=False, box=None, padding=(0, 2), expand=True,
    )
    sizing_table.add_column("Scenario", style="bold", ratio=2)
    sizing_table.add_column("Projection", ratio=3)

    avg_size_for_sizing = avg_size if avg_size > 0 else 25_000  # fallback 25KB
    for daily, label in [
        (1_000, "1K tickets/day"),
        (10_000, "10K tickets/day"),
        (50_000, "50K tickets/day"),
    ]:
        retained = avg_size_for_sizing * daily * 7  # 7-day retention
        if retained < 500_000_000:
            rec = "single node sufficient"
        elif retained < 5_000_000_000:
            rec = "3-node cluster recommended"
        else:
            rec = "dedicated Cassandra cluster"
        sizing_table.add_row(
            label,
            f"{_fmt_bytes(retained)} retained (7d) -> {rec}",
        )

    # ------------------------------------------------------------------ #
    # Assemble final panel                                                #
    # ------------------------------------------------------------------ #
    grid = Table.grid(expand=True, padding=(0, 0))
    grid.add_column()

    grid.add_row(Text("THROUGHPUT", style="bold underline"))
    grid.add_row(throughput_table)
    grid.add_row(Text(""))

    grid.add_row(Text("TEMPORAL STORAGE", style="bold underline"))
    grid.add_row(storage_table)
    grid.add_row(Text(""))

    grid.add_row(Text("QUERY PERFORMANCE", style="bold underline"))
    grid.add_row(query_table)
    grid.add_row(Text(""))

    grid.add_row(Text("TOKEN USAGE (simulated)", style="bold underline"))
    grid.add_row(token_table)
    grid.add_row(Text(""))

    grid.add_row(
        Text("COST PROJECTION (GPT-4o pricing)", style="bold underline")
    )
    grid.add_row(cost_table)
    grid.add_row(Text(""))

    grid.add_row(Text("TOKEN USAGE BY CATEGORY", style="bold underline"))
    grid.add_row(cat_table)
    grid.add_row(Text(""))

    grid.add_row(
        Text("CLUSTER SIZING (with 7-day retention)", style="bold underline")
    )
    grid.add_row(sizing_table)

    report_panel = Panel(
        grid,
        title=f"[bold]Scale Benchmark: {ticket_count} Workflows[/bold]",
        border_style="bold cyan",
        padding=(1, 2),
    )

    console.print(report_panel)
    console.print()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    args = parse_args()
    asyncio.run(run_benchmark(args))


if __name__ == "__main__":
    main()
