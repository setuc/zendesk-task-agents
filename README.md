# Zendesk Task Agent Suite

**Universal Computer + Temporal** examples for intelligent, durable customer support workflows.

Three production-style examples demonstrating why you need both technologies together:
AI agents that reason (UC) orchestrated by indestructible workflows (Temporal).

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- [Temporal CLI](https://docs.temporal.io/cli)

## Quick Start

```bash
# Install dependencies
uv sync

# Start Temporal dev server (keep this running in its own terminal)
temporal server start-dev

# Run the interactive demo launcher
uv run python demo.py
```

The Temporal Web UI is available at **http://localhost:8233** -- open it to see workflows, event histories, and timers.

---

## Example A: Order Resolution Agent

Multi-issue ticket handling with saga compensation, human-in-the-loop approval, and AI-powered investigation.

**What it demonstrates:**
- UC agent reads a ticket, extracts 3 issues (wrong item, damaged item, overcharge) and 2 intents (replacement, refund)
- Cross-references order DB, shipping tracker, and payment system to verify claims
- Generates a resolution plan with cost analysis and per-step reasoning
- Pauses for human approval when cost exceeds $50 threshold (survives restarts)
- Executes refund, replacement, and return label steps sequentially
- On failure: saga compensation reverses all completed steps in reverse order
- Writes a personalized customer response with confirmation numbers

```bash
# Full end-to-end demo (single command, runs everything)
uv run python -m order_resolution.main demo

# Saga compensation demo (injects failure, shows rollback)
uv run python -m order_resolution.main demo-saga

# UC vs Temporal resumability benchmark
uv run python -m order_resolution.main benchmark
```

**Manual operation** (for exploring individual commands):
```bash
# Terminal 1: Start the worker
uv run python -m order_resolution.main worker

# Terminal 2: Start a workflow, query it, approve it
uv run python -m order_resolution.main start TKT-10042
uv run python -m order_resolution.main query order-resolution-TKT-10042 --type progress
uv run python -m order_resolution.main approve order-resolution-TKT-10042
```

---

## Example B: SLA Guardian Orchestrator

Always-on SLA monitoring with durable timers, sentiment analysis, and intelligent escalation through L1 -> L2 -> L3 -> Manager tiers.

**What it demonstrates:**
- Guardian workflow scans all open tickets on a schedule (continue-as-new for infinite lifecycle)
- Per-ticket child workflows classify urgency and analyze sentiment across all comments
- Durable timers fire before SLA deadlines -- even if the server restarts at 2am, the 3am timer still fires
- Escalation messages are context-aware (not templates): L1->L2 is informational, L3->Manager includes executive summary with business impact and churn risk
- Override signals cancel escalations; resolution signals close monitoring

```bash
# Full monitoring demo (5 tickets, fast timers)
uv run python -m sla_guardian.main demo
```

### Stress Test: 100+ Concurrent Tickets

The stress test generates 100+ realistic random tickets and processes them through Temporal as concurrent child workflows. This demonstrates UC's parsing capability at scale (every ticket is unique) and Temporal's ability to orchestrate 100+ independent workflows with durable timers.

**Run in two terminals:**

```bash
# Terminal 1: Start the stress worker (pre-loads generated tickets)
uv run python -m sla_guardian.main stress-worker --tickets 100

# Terminal 2: Run the stress demo (injects tickets in waves, shows live dashboard)
uv run python -m sla_guardian.main demo-stress --tickets 100 --waves 5 --wave-delay 10
```

Both commands use the same random seed (`--seed 42`) so the worker and controller generate identical tickets without shared state.

**Stress test options:**
```
--tickets N        Total tickets to generate (default: 100)
--waves N          Number of injection waves (default: 5)
--wave-delay N     Seconds between waves (default: 10)
--seed N           Random seed for deterministic generation (default: 42)
--sla-offset N     SLA deadline offset in minutes (default: 3)
```

**What you'll see:**
- Live dashboard showing all tickets with urgency scores, sentiment, SLA status, escalation tiers
- Wave-by-wave injection with progress tracking
- Final summary with urgency/sentiment distributions, SLA breach counts, top 5 highest-urgency tickets

**Typical results (100 tickets):**
| Metric | Value |
|--------|-------|
| Processing time | ~45s |
| Throughput | ~2 tickets/sec |
| Total escalation events | ~150+ |
| Concurrent child workflows | 100 |

Open **http://localhost:8233** to see all 100 workflows in Temporal's Web UI.

---

## Example C: Onboarding Concierge

Multi-day enterprise onboarding with Docker integration testing, milestone timers, and customer check-in signals.

**What it demonstrates:**
- 14-day onboarding plan with 5 milestones (days 1, 3, 5, 10, 14)
- Account verification checklist (7 checks: billing, API keys, admin users, etc.)
- Integration testing via child workflow (tests customer's webhook endpoint, runs diagnostics on failure)
- Personalized training guide generation with customer-specific details
- Durable timers between milestones (days apart in production, seconds in demo)
- Customer response signals pause the workflow until acknowledgement
- Continue-as-new bounds event history for multi-week workflows

```bash
# Full onboarding demo (fast timeline: 5s per simulated day)
uv run python -m onboarding_concierge.main demo
```

**Manual operation:**
```bash
# Terminal 1: Start the worker
uv run python -m onboarding_concierge.main worker

# Terminal 2: Start onboarding, check status, send responses
uv run python -m onboarding_concierge.main onboard CUST-ENT-001
uv run python -m onboarding_concierge.main status onboarding-CUST-ENT-001
uv run python -m onboarding_concierge.main checkin-response onboarding-CUST-ENT-001 --milestone M3 --text "Looks great!"
```

---

## Architecture

```
zendesk-task-agents/
├── common/                         # Shared infrastructure
│   ├── services/
│   │   ├── base.py                 # Service protocols (ZendeskService, OrderDB, etc.)
│   │   ├── zendesk_mock.py         # In-memory Zendesk mock with failure injection
│   │   └── service_registry.py     # Factory for mock/real services
│   ├── tools/
│   │   └── zendesk_tools.py        # GetTicket, UpdateTicket, AddComment FunctionTools
│   ├── tui.py                      # Rich TUI: WorkflowDashboard, StageProgress, etc.
│   ├── data_types.py               # Shared Pydantic models
│   └── agent_helpers.py            # run_agent_to_completion() utility
│
├── order_resolution/               # Example A
│   ├── workflows/
│   │   ├── workflow.py             # OrderResolutionWorkflow (signals, queries, saga)
│   │   ├── activities.py           # 6 activities with rich mock logic
│   │   └── data_types.py           # ExtractedIntent, ResolutionPlan, StepResult
│   ├── services/                   # OrderDB, Shipping, Payment mocks
│   ├── tools/                      # Order, Shipping, Payment FunctionTools
│   ├── benchmark/                  # UC vs Temporal resume comparison
│   └── fixtures/                   # Sample tickets (simple, standard, complex)
│
├── sla_guardian/                    # Example B
│   ├── workflows/
│   │   ├── guardian_workflow.py     # Cron scanner with continue-as-new
│   │   ├── ticket_monitor_workflow.py  # Per-ticket SLA tracker with durable timers
│   │   └── activities.py           # Urgency, sentiment, escalation activities
│   ├── fixtures/
│   │   ├── ticket_generator.py     # 100+ ticket generator (28 templates, 9 categories)
│   │   └── sample_open_tickets.json
│   └── tools/                      # AnalyzeConversation, ClassifyUrgency tools
│
├── onboarding_concierge/           # Example C
│   ├── workflows/
│   │   ├── onboarding_workflow.py  # Multi-day orchestrator with continue-as-new
│   │   ├── integration_test_workflow.py  # Child workflow for endpoint testing
│   │   └── activities.py           # Verify, test, train, checkin, report
│   ├── docker/                     # Integration test harness
│   └── fixtures/                   # Customer, integration config, milestones
│
└── demo.py                         # Unified interactive demo launcher
```

## Why UC + Temporal Together

| | UC Alone | Temporal Alone | UC + Temporal |
|---|----------|---------------|---------------|
| **Intelligence** | Agent reads tickets, reasons about issues, writes personalized responses | Activities are dumb function calls | Agent reasons **inside** durable workflows |
| **Reliability** | Smart but chaotic -- no failure recovery, no rollback | Bulletproof but static -- can't adapt to unusual cases | Intelligent investigation + guaranteed saga compensation |
| **Coordination** | Can investigate one ticket, can't orchestrate multi-step resolution | Can chain API calls, can't understand ticket context | Agent plans + Temporal executes each step with retry |
| **Persistence** | Can serialize state, but requires explicit save | Automatic -- survives any crash via event history replay | Kill the worker mid-resolution, restart, zero lost work |
| **Scale** | One agent at a time | Task queues distribute across workers | 100+ concurrent intelligent agents, all fault-tolerant |
| **Long-running** | Agent sessions have timeouts | Continue-as-new runs for months/years | 2-week onboarding with durable timers between milestones |
| **Human-in-loop** | No built-in approval gates | Signals pause/resume workflows indefinitely | Agent generates plan, human approves, workflow continues |

## Tech Stack

- **[Universal Computer](https://github.com/OpenAI-Early-Access/universal_computer)** - AI agent framework with sandbox execution, FunctionTools, and memory
- **[Temporal](https://temporal.io)** - Durable execution platform for fault-tolerant workflows
- **[Rich](https://github.com/Textualize/rich)** - Terminal UI with live dashboards, tables, and panels
- **[Pydantic](https://docs.pydantic.dev)** - Data validation and serialization for all models
