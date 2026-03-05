from __future__ import annotations

from ...common.tui import console, BenchmarkReport
from .uc_native_resume import benchmark_uc_resume
from .temporal_resume import benchmark_temporal_resume


async def run_comparison():
    """Run both benchmarks and display side-by-side comparison."""
    console.print("\n[bold cyan]═══ Resumability Benchmark: UC vs Temporal ═══[/bold cyan]\n")

    uc_metrics = await benchmark_uc_resume()
    console.print()
    temporal_metrics = await benchmark_temporal_resume()
    console.print()

    report = BenchmarkReport("Resumability Comparison: UC vs Temporal")

    report.add_metric(
        "Resume Type",
        "Application-level (serialize task state)",
        "Infrastructure-level (event history replay)",
    )
    report.add_metric(
        "Recovery Time",
        f"{uc_metrics.resume_time_ms:.1f}ms",
        f"{temporal_metrics.recovery_time_ms:.1f}ms",
    )
    report.add_metric(
        "State Size",
        f"{uc_metrics.serialized_size_bytes:,} bytes",
        f"{temporal_metrics.event_history_size} events",
    )
    report.add_metric(
        "Work Re-executed",
        "None (full state preserved)",
        f"None ({temporal_metrics.activities_replayed} replayed, 0 re-run)",
    )
    report.add_metric(
        "Survives Process Death",
        "No (requires explicit save)",
        "Yes (automatic, durable)",
    )
    report.add_metric(
        "Context Preserved",
        f"{uc_metrics.context_items_preserved} conversation items",
        "Full workflow state + history",
    )
    report.add_metric(
        "Best For",
        "Mid-task pause/resume within session",
        "Crash recovery, long-running workflows",
    )

    report.print()

    console.print("\n[bold]Key Insight:[/bold] UC resume is application-level (serialize agent state),")
    console.print("while Temporal resume is infrastructure-level (replay from event history).")
    console.print("Combined: intelligent agents that survive any failure.\n")
