# Zendesk Task Agent Suite

**Universal Computer + Temporal** examples for intelligent, durable customer support workflows.

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- [Temporal CLI](https://docs.temporal.io/cli) (`temporal server start-dev`)

## Quick Start

```bash
# Install dependencies
uv sync

# Start Temporal dev server (separate terminal)
temporal server start-dev

# Run the interactive demo launcher
uv run python demo.py
```

## Examples

### Order Resolution Agent

Multi-issue ticket handling with saga compensation, human-in-the-loop approval, and AI investigation.

```bash
uv run python -m order_resolution.main demo          # Full end-to-end demo
uv run python -m order_resolution.main demo-saga     # Saga compensation demo
uv run python -m order_resolution.main benchmark     # UC vs Temporal resume comparison
```

### SLA Guardian Orchestrator

Always-on SLA monitoring with durable timers, sentiment analysis, and intelligent escalation.

```bash
uv run python -m sla_guardian.main demo              # Full monitoring demo
uv run python -m sla_guardian.main simulate           # Fast simulation mode
```

### Onboarding Concierge

Multi-day enterprise onboarding with integration testing, training delivery, and milestone check-ins.

```bash
uv run python -m onboarding_concierge.main demo      # Full onboarding demo
uv run python -m onboarding_concierge.main simulate-day  # Fast-forward timeline
```

## Architecture

```
zendesk-task-agents/
├── common/                    # Shared infrastructure
│   ├── services/              # Service protocols + mock implementations
│   ├── tools/                 # Shared Zendesk FunctionTools
│   ├── tui.py                 # Rich TUI components
│   └── data_types.py          # Shared Pydantic models
├── order_resolution/          # Example A: Order Resolution
│   ├── workflows/             # Temporal workflow + activities
│   ├── services/              # Order, shipping, payment mocks
│   ├── tools/                 # Domain FunctionTools
│   └── benchmark/             # UC vs Temporal resume comparison
├── sla_guardian/              # Example B: SLA Guardian
│   ├── workflows/             # Guardian cron + per-ticket monitor
│   ├── services/              # SLA rules mock
│   └── tools/                 # Analysis FunctionTools
├── onboarding_concierge/      # Example C: Onboarding
│   ├── workflows/             # Multi-day orchestrator + integration test child
│   ├── services/              # Integration test + email mocks
│   ├── tools/                 # Diagnostic + report FunctionTools
│   └── docker/                # Integration test harness
└── demo.py                    # Unified demo launcher
```

## Why UC + Temporal Together

| UC Alone | Temporal Alone | UC + Temporal |
|----------|---------------|---------------|
| Smart agent, no failure recovery | Bulletproof workflows, dumb activities | Intelligent AND reliable |
| Can investigate, can't coordinate rollback | Can chain with retry, can't understand context | Agent reasons + saga compensates |
| Memory helps one agent, no orchestration | Workflows independent, no intelligence | Memory feeds workflow decisions |
