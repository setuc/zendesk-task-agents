from __future__ import annotations

import time
from typing import Any

from rich.align import Align
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

console = Console()


# ---------------------------------------------------------------------------
# Colour / icon helpers
# ---------------------------------------------------------------------------

_STATUS_STYLE: dict[str, str] = {
    "pending": "dim",
    "running": "bold yellow",
    "done": "bold green",
    "failed": "bold red",
    "skipped": "dim strike",
}

_STATUS_ICON: dict[str, str] = {
    "pending": "[ ]",
    "running": "[*]",
    "done": "[+]",
    "failed": "[X]",
    "skipped": "[-]",
}


# ---------------------------------------------------------------------------
# StageProgress
# ---------------------------------------------------------------------------


class StageProgress:
    """Shows workflow stages with status indicators."""

    def __init__(self, stages: list[str]) -> None:
        self.stages: dict[str, str] = {name: "pending" for name in stages}

    def update(self, stage: str, status: str) -> None:
        self.stages[stage] = status

    def render(self) -> Table:
        table = Table(
            show_header=True,
            header_style="bold cyan",
            border_style="bright_blue",
            expand=True,
            padding=(0, 1),
        )
        table.add_column("Stage", ratio=3)
        table.add_column("Status", justify="center", ratio=1)

        for name, status in self.stages.items():
            icon = _STATUS_ICON.get(status, "?")
            style = _STATUS_STYLE.get(status, "")
            table.add_row(
                Text(name, style=style),
                Text(icon, style=style),
            )
        return table


# ---------------------------------------------------------------------------
# ActivityTable
# ---------------------------------------------------------------------------


class ActivityTable:
    """Live-updating table of activity executions."""

    def __init__(self) -> None:
        self.activities: list[dict[str, Any]] = []

    def add_activity(self, name: str) -> int:
        """Register a new activity row and return its index."""
        entry: dict[str, Any] = {
            "name": name,
            "status": "running",
            "start": time.monotonic(),
            "duration": None,
            "details": "",
        }
        self.activities.append(entry)
        return len(self.activities) - 1

    def update_activity(
        self,
        index: int,
        status: str,
        duration: float | None = None,
        details: str = "",
    ) -> None:
        act = self.activities[index]
        act["status"] = status
        if duration is not None:
            act["duration"] = duration
        elif status in ("done", "failed", "skipped"):
            act["duration"] = time.monotonic() - act["start"]
        if details:
            act["details"] = details

    def render(self) -> Table:
        table = Table(
            show_header=True,
            header_style="bold magenta",
            border_style="bright_blue",
            expand=True,
            padding=(0, 1),
        )
        table.add_column("Activity", ratio=3)
        table.add_column("Status", justify="center", ratio=1)
        table.add_column("Duration", justify="right", ratio=1)
        table.add_column("Details", ratio=3)

        for act in self.activities:
            status = act["status"]
            icon = _STATUS_ICON.get(status, "?")
            style = _STATUS_STYLE.get(status, "")

            dur_str = ""
            if act["duration"] is not None:
                dur_str = f"{act['duration']:.2f}s"
            elif status == "running":
                dur_str = f"{time.monotonic() - act['start']:.1f}s"

            table.add_row(
                Text(act["name"], style=style),
                Text(icon, style=style),
                Text(dur_str, style=style),
                Text(act["details"], style=style, overflow="ellipsis"),
            )
        return table


# ---------------------------------------------------------------------------
# PlanDisplay
# ---------------------------------------------------------------------------


class PlanDisplay:
    """Formatted display of a resolution plan with costs and approval flags."""

    def __init__(self, plan_name: str) -> None:
        self.plan_name = plan_name
        self.steps: list[dict[str, Any]] = []

    def add_step(
        self,
        description: str,
        cost: float = 0.0,
        needs_approval: bool = False,
    ) -> None:
        self.steps.append(
            {
                "description": description,
                "cost": cost,
                "needs_approval": needs_approval,
            }
        )

    @property
    def total_cost(self) -> float:
        return sum(s["cost"] for s in self.steps)

    def render(self) -> Panel:
        table = Table(
            show_header=True,
            header_style="bold white",
            border_style="bright_blue",
            expand=True,
            padding=(0, 1),
        )
        table.add_column("#", justify="right", width=4)
        table.add_column("Step", ratio=5)
        table.add_column("Cost", justify="right", ratio=1)
        table.add_column("Approval", justify="center", ratio=1)

        for idx, step in enumerate(self.steps, 1):
            cost_str = f"${step['cost']:.2f}" if step["cost"] else "-"
            approval_str = Text("REQUIRED", style="bold red") if step["needs_approval"] else Text("-", style="dim")
            table.add_row(
                str(idx),
                step["description"],
                cost_str,
                approval_str,
            )

        # Footer with total
        table.add_section()
        table.add_row(
            "",
            Text("Total", style="bold"),
            Text(f"${self.total_cost:.2f}", style="bold cyan"),
            "",
        )

        return Panel(
            table,
            title=f"[bold cyan]Plan: {self.plan_name}[/]",
            border_style="bright_blue",
            padding=(1, 1),
        )


# ---------------------------------------------------------------------------
# ApprovalPrompt
# ---------------------------------------------------------------------------


class ApprovalPrompt:
    """Styled Y/N prompt with plan summary."""

    @staticmethod
    def ask(plan_summary: str, total_cost: float, threshold: float) -> bool:
        over = total_cost > threshold
        cost_style = "bold red" if over else "bold green"

        console.print()
        console.rule("[bold yellow]Approval Required[/]")
        console.print()
        console.print(
            Panel(
                Text(plan_summary),
                title="[bold]Plan Summary[/]",
                border_style="yellow",
                padding=(1, 2),
            )
        )
        console.print()

        cost_text = Text.assemble(
            ("Estimated cost: ", "bold"),
            (f"${total_cost:.2f}", cost_style),
            ("  |  Threshold: ", "dim"),
            (f"${threshold:.2f}", "dim"),
        )
        console.print(Align.center(cost_text))

        if over:
            console.print(
                Align.center(
                    Text("Cost exceeds auto-approval threshold.", style="bold red")
                )
            )

        console.print()
        answer = console.input("[bold yellow]Approve? (y/n): [/]").strip().lower()
        approved = answer in ("y", "yes")

        if approved:
            console.print("[bold green]Approved.[/]")
        else:
            console.print("[bold red]Rejected.[/]")

        console.print()
        return approved


# ---------------------------------------------------------------------------
# WorkflowDashboard
# ---------------------------------------------------------------------------


class WorkflowDashboard:
    """Live layout combining header, progress stages, activity log, and detail panel."""

    def __init__(self, title: str, stages: list[str]) -> None:
        self.title = title
        self.stage_progress = StageProgress(stages)
        self.activity_table = ActivityTable()
        self.detail_text: str = ""
        self.live: Live | None = None

    def _build_layout(self) -> Layout:
        layout = Layout()

        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="body"),
            Layout(name="footer", size=5),
        )

        layout["body"].split_row(
            Layout(name="stages", ratio=1),
            Layout(name="activities", ratio=2),
        )

        # Header
        header_text = Text(self.title, style="bold white on blue", justify="center")
        layout["header"].update(Panel(header_text, border_style="blue"))

        # Stages
        layout["stages"].update(
            Panel(
                self.stage_progress.render(),
                title="[bold cyan]Stages[/]",
                border_style="bright_blue",
            )
        )

        # Activities
        layout["activities"].update(
            Panel(
                self.activity_table.render(),
                title="[bold magenta]Activities[/]",
                border_style="bright_blue",
            )
        )

        # Footer / detail
        layout["footer"].update(
            Panel(
                Text(self.detail_text, style="italic"),
                title="[bold]Details[/]",
                border_style="bright_blue",
            )
        )

        return layout

    def start(self) -> None:
        """Begin live rendering."""
        self.live = Live(
            self._build_layout(),
            console=console,
            refresh_per_second=4,
            screen=False,
        )
        self.live.start()

    def stop(self) -> None:
        """Stop live rendering."""
        if self.live is not None:
            self.live.stop()
            self.live = None

    def _refresh(self) -> None:
        if self.live is not None:
            self.live.update(self._build_layout())

    def update_stage(self, stage: str, status: str) -> None:
        self.stage_progress.update(stage, status)
        self._refresh()

    def add_activity(self, name: str) -> int:
        idx = self.activity_table.add_activity(name)
        self._refresh()
        return idx

    def update_activity(self, index: int, status: str, **kwargs: Any) -> None:
        self.activity_table.update_activity(index, status, **kwargs)
        self._refresh()

    def set_detail(self, text: str) -> None:
        self.detail_text = text
        self._refresh()


# ---------------------------------------------------------------------------
# BenchmarkReport
# ---------------------------------------------------------------------------


class BenchmarkReport:
    """Side-by-side comparison table for UC vs Temporal resume benchmarks."""

    def __init__(self, title: str = "Resumability Benchmark") -> None:
        self.title = title
        self.metrics: list[dict[str, str]] = []

    def add_metric(self, name: str, uc_value: str, temporal_value: str) -> None:
        self.metrics.append(
            {"name": name, "uc_value": uc_value, "temporal_value": temporal_value}
        )

    def render(self) -> Panel:
        table = Table(
            show_header=True,
            header_style="bold white",
            border_style="bright_blue",
            expand=True,
            padding=(0, 2),
        )
        table.add_column("Metric", ratio=3, style="bold")
        table.add_column("Universal Computer", justify="center", ratio=2, style="cyan")
        table.add_column("Temporal", justify="center", ratio=2, style="yellow")

        for m in self.metrics:
            table.add_row(m["name"], m["uc_value"], m["temporal_value"])

        return Panel(
            table,
            title=f"[bold cyan]{self.title}[/]",
            border_style="bright_blue",
            padding=(1, 1),
        )

    def print(self) -> None:
        console.print(self.render())
