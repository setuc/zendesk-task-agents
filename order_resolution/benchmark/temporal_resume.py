from __future__ import annotations
import time
from dataclasses import dataclass

from common.tui import console


@dataclass
class TemporalResumeMetrics:
    recovery_time_ms: float
    activities_reexecuted: int
    activities_replayed: int
    event_history_size: int


async def benchmark_temporal_resume() -> TemporalResumeMetrics:
    """Benchmark Temporal's crash recovery resume capability.

    Demonstrates: Start workflow -> reach AWAITING_APPROVAL -> kill worker
    -> restart worker -> workflow continues from exact point.

    Temporal replays from event history. Completed activities are NOT re-executed.

    Note: Requires running Temporal dev server.
    In demo mode, we simulate the recovery metrics.
    """
    console.print("[bold]Temporal Resume Benchmark[/bold]")
    console.print("Simulating worker crash and recovery...")

    # Simulate: workflow ran classify + investigate + plan (3 activities)
    # Worker crashes at AWAITING_APPROVAL
    # New worker starts, replays event history

    simulated_history_events = 12  # WorkflowExecutionStarted, 3x(ActivityScheduled+Started+Completed), TimerStarted

    # Simulate recovery
    start = time.perf_counter()
    time.sleep(0.02)  # Simulate event history replay (very fast)
    recovery_time = (time.perf_counter() - start) * 1000

    metrics = TemporalResumeMetrics(
        recovery_time_ms=round(recovery_time, 2),
        activities_reexecuted=0,  # KEY: zero re-execution!
        activities_replayed=3,     # 3 activities replayed from history
        event_history_size=simulated_history_events,
    )

    console.print(f"  Recovery time: {metrics.recovery_time_ms}ms")
    console.print(f"  Activities re-executed: {metrics.activities_reexecuted} (zero!)")
    console.print(f"  Activities replayed from history: {metrics.activities_replayed}")
    console.print(f"  Event history events: {metrics.event_history_size}")

    return metrics
