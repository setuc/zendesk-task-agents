#!/usr/bin/env python3
"""Unified demo launcher for the Zendesk Task Agent Suite.

Usage:
    python demo.py                    # Interactive menu
    python demo.py order              # Order Resolution demo
    python demo.py order-saga         # Saga compensation demo
    python demo.py sla                # SLA Guardian demo
    python demo.py onboarding         # Onboarding Concierge demo
    python demo.py benchmark          # Resumability benchmark
    python demo.py all                # Run all demos sequentially

Requires: temporal server start-dev (Temporal dev server running)
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()

BANNER = r"""
 ______              _           _      _____         _
|___  /             | |         | |    |_   _|       | |
   / / ___ _ __   __| | ___  ___| | __   | | __ _ ___| | __
  / / / _ \ '_ \ / _` |/ _ \/ __| |/ /   | |/ _` / __| |/ /
 / /_|  __/ | | | (_| |  __/\__ \   <    | | (_| \__ \   <
/_____\___|_| |_|\__,_|\___||___/_|\_\   \_/\__,_|___/_|\_\

     _                    _     ____        _ _
    / \   __ _  ___ _ __ | |_  / ___| _   _(_) |_ ___
   / _ \ / _` |/ _ \ '_ \| __| \___ \| | | | | __/ _ \
  / ___ \ (_| |  __/ | | | |_   ___) | |_| | | ||  __/
 /_/   \_\__, |\___|_| |_|\__| |____/ \__,_|_|\__\___|
         |___/
"""

DEMOS = {
    "order": {
        "title": "Order Resolution Agent",
        "description": (
            "Multi-issue ticket handling with saga compensation, "
            "human-in-the-loop approval, and AI-powered investigation."
        ),
        "highlights": [
            "UC agent reads ticket, extracts 3 issues + 2 intents",
            "Cross-references order DB, shipping, payment systems",
            "Generates resolution plan with cost analysis",
            "Human approval gate (auto-approved in demo)",
            "Executes refund, replacement, return label steps",
            "Writes personalized customer response",
        ],
    },
    "order-saga": {
        "title": "Saga Compensation Demo",
        "description": (
            "Shows what happens when a step fails mid-execution: "
            "all completed steps are automatically reversed."
        ),
        "highlights": [
            "Injects failure on replacement order creation",
            "Refund processes successfully first",
            "Replacement step fails (simulated outage)",
            "Saga pattern kicks in automatically",
            "Refund is reversed (money returned to company)",
            "System returns to consistent state",
        ],
    },
    "sla": {
        "title": "SLA Guardian Orchestrator",
        "description": (
            "Always-on monitoring with durable timers, intelligent "
            "escalation drafting, and infinite lifecycle."
        ),
        "highlights": [
            "Monitors 5 tickets with different priorities/tiers",
            "AI classifies true urgency beyond priority tags",
            "Sentiment analysis tracks frustration trajectory",
            "Durable timers fire even after server restarts",
            "Context-aware escalation messages (not templates)",
            "Override signals cancel/redirect escalations",
        ],
    },
    "onboarding": {
        "title": "Multi-Day Onboarding Concierge",
        "description": (
            "2-week enterprise onboarding with Docker integration testing, "
            "milestone timers, and customer check-in signals."
        ),
        "highlights": [
            "Account verification checklist",
            "Integration testing with diagnostics on failure",
            "Personalized training guide generation",
            "Milestone check-ins with progress reports",
            "Customer response signals at any time",
            "Continue-as-new for multi-week lifecycle",
        ],
    },
    "benchmark": {
        "title": "Resumability Benchmark",
        "description": (
            "Side-by-side comparison of UC application-level resume "
            "vs Temporal infrastructure-level resume."
        ),
        "highlights": [
            "UC: serialize/deserialize task state",
            "Temporal: event history replay after crash",
            "Zero re-execution in both cases",
            "Different strengths for different scenarios",
        ],
    },
}


def show_menu() -> str | None:
    """Display interactive demo selection menu."""
    console.print(BANNER, style="bold cyan")
    console.print(
        "[bold]Universal Computer + Temporal[/bold]  |  "
        "[dim]Intelligent AND Reliable Customer Support[/dim]\n"
    )

    table = Table(
        title="Available Demos",
        show_header=True,
        header_style="bold magenta",
        padding=(0, 2),
    )
    table.add_column("#", style="bold", width=4)
    table.add_column("Command", style="cyan", width=14)
    table.add_column("Demo", style="bold", width=32)
    table.add_column("Description", style="dim")

    for i, (key, info) in enumerate(DEMOS.items(), 1):
        table.add_row(str(i), key, info["title"], info["description"])

    table.add_row(
        str(len(DEMOS) + 1), "all", "Run All Demos", "Sequentially run every demo"
    )
    console.print(table)
    console.print()

    try:
        choice = console.input("[bold]Select demo (number or name): [/bold]").strip()
    except (EOFError, KeyboardInterrupt):
        return None

    if not choice:
        return None

    # Accept number or name
    if choice.isdigit():
        idx = int(choice) - 1
        keys = list(DEMOS.keys()) + ["all"]
        if 0 <= idx < len(keys):
            return keys[idx]
    elif choice.lower() in DEMOS or choice.lower() == "all":
        return choice.lower()

    console.print(f"[red]Unknown selection: {choice}[/red]")
    return None


def show_demo_intro(key: str) -> None:
    """Display intro panel for a demo."""
    info = DEMOS.get(key, {})
    if not info:
        return

    highlights = "\n".join(f"  [green]*[/green] {h}" for h in info.get("highlights", []))
    console.print(Panel(
        f"[bold]{info['title']}[/bold]\n\n"
        f"{info['description']}\n\n"
        f"[bold]Key moments:[/bold]\n{highlights}",
        title=f"[bold cyan]Demo: {key}[/bold cyan]",
        border_style="cyan",
        padding=(1, 2),
    ))
    console.print()


async def run_demo(key: str) -> None:
    """Run a specific demo."""
    show_demo_intro(key)

    if key == "order":
        from order_resolution.config import OrderResolutionConfig
        from order_resolution.main import run_demo as _run_order_demo
        await _run_order_demo(OrderResolutionConfig(), "TKT-10042")

    elif key == "order-saga":
        from order_resolution.config import OrderResolutionConfig
        from order_resolution.main import run_demo_saga
        await run_demo_saga(OrderResolutionConfig(), "TKT-10042")

    elif key == "sla":
        from sla_guardian.config import SLAGuardianConfig
        from sla_guardian.main import run_demo as _run_sla_demo
        await _run_sla_demo(SLAGuardianConfig())

    elif key == "onboarding":
        from onboarding_concierge.config import OnboardingConfig
        from onboarding_concierge.main import run_demo as _run_onboarding_demo
        await _run_onboarding_demo(OnboardingConfig())

    elif key == "benchmark":
        from order_resolution.benchmark.compare import run_comparison
        await run_comparison()


async def run_all() -> None:
    """Run all demos sequentially."""
    console.rule("[bold cyan]Running All Demos[/bold cyan]")
    console.print()

    for key in DEMOS:
        try:
            await run_demo(key)
        except Exception as e:
            console.print(f"[red]Demo '{key}' failed: {e}[/red]")
        console.print()
        console.rule()
        console.print()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Zendesk Task Agent Suite - Demo Launcher",
        epilog="Requires: temporal server start-dev",
    )
    parser.add_argument(
        "demo",
        nargs="?",
        choices=list(DEMOS.keys()) + ["all"],
        help="Demo to run (omit for interactive menu)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.demo:
        key = args.demo
    else:
        key = show_menu()
        if key is None:
            console.print("[dim]No demo selected. Goodbye![/dim]")
            sys.exit(0)

    console.print()

    if key == "all":
        asyncio.run(run_all())
    else:
        asyncio.run(run_demo(key))


if __name__ == "__main__":
    main()
