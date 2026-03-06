# Live 3-Terminal SLA Guardian with UC Agent

## Overview

Redesign the SLA Guardian stress demo into a 3-terminal live experience that demonstrates Universal Computer's unique capabilities (tools + sandbox + memory) orchestrated by Temporal at scale.

## Architecture

```
Terminal 1: DASHBOARD          Terminal 2: AGENT WORKER         Terminal 3: INJECTOR
(polls Temporal queries)       (UC agents via task queue)        (starts workflows)

+--Live Ticket Grid----------+ +--Agent Activity Log----------+ +--Injection Log---------+
| TKT-30001 STD NORM RESOLVED | | [A-01] TKT-30042 billing..  | | Wave 1: 20 tickets     |
| TKT-30002 ENT URG  ESCALATE | |   > get_ticket > refund $45  | |   TKT-30001 (solvable) |
| TKT-30003 PRE HIGH WORKING  | |   > RESOLVED (2.1s)          | |   TKT-30002 (unsolvable)|
| ...rows appear as injected..| | [A-02] TKT-30043 API 500s.. | |   ...                  |
| Stats: 42/100 resolved      | |   > curl sandbox > ESCALATED | | Wave 2: 20 tickets...  |
+-----------------------------+ +-----------------------------+ +------------------------+
           ^                              ^                              |
           |                              |                              |
           +-------- Temporal Server (localhost:7233) <------------------+
```

All communication through Temporal. No shared files, no Redis.

## UC Agent per Ticket

Each ticket gets a real UC agent session with:

### Tools (9)
- `get_ticket` - Read ticket details from Zendesk
- `get_order` - Query order database
- `get_transaction` - Query payment system
- `process_refund` - Execute a refund
- `create_replacement` - Create replacement order
- `check_endpoint` - HTTP check on a URL
- `run_diagnostic_script` - Execute shell commands in UC sandbox
- `update_ticket` - Update ticket status/fields
- `search_memory` - Query memory for similar past tickets

### Sandbox Execution
For technical tickets (API 500s, webhook failures), the agent writes and executes shell commands in UC's UnixLocalSandboxClient:
- curl commands to test failing endpoints
- jq to parse response bodies
- diagnostic scripts to check connectivity

### Memory Plugin
- Before investigating: agent searches memory for similar tickets
- After resolving: agent writes what it learned (root cause, resolution pattern)
- Over time: agent recognizes patterns and resolves faster

### Agent Flow
```
1. Read ticket (get_ticket)
2. Search memory for similar past tickets (search_memory)
3. IF memory match -> apply known pattern (faster resolution)
4. ELSE -> investigate using relevant tools:
   - Billing: get_transaction, compare amounts
   - Technical: check_endpoint, run_diagnostic_script (sandbox)
   - Shipping: get_order, get tracking info
5. Decide: RESOLVE or ESCALATE
6. RESOLVE: execute actions (refund, replacement, etc.)
7. ESCALATE: write detailed note with findings
8. Write to memory: what was learned
9. Update ticket status
```

## Ticket Mix

~60% solvable, ~40% unsolvable:

Solvable (agent resolves autonomously):
- Billing overcharges -> query transaction, process refund
- Wrong items -> query order, create replacement + return label
- Password resets -> send reset link
- Duplicate charges -> reverse duplicate
- Simple shipping delays -> provide tracking + ETA

Unsolvable (agent escalates with detailed notes):
- Production API outages -> needs engineering team
- Data loss claims -> needs manager approval
- Legal threats -> needs legal team
- Complex multi-system failures -> needs cross-team investigation
- Feature requests -> deferred to product team

## Terminal Details

### T1: Dashboard (`python -m sla_guardian.main live-dashboard`)
- Rich Live display, refreshes every 2s
- Table columns: Ticket | Customer | Tier | Priority | Category | Agent Status | Resolution | Time
- Agent Status: QUEUED -> INVESTIGATING -> RESOLVING/ESCALATING -> DONE
- Color coding: green=resolved, yellow=working, red=escalated, dim=queued
- Stats bar: Total | Resolved | Escalated | Working | Queued | Avg Resolution Time
- Polls Temporal workflow queries for state

### T2: Agent Worker (`python -m sla_guardian.main live-worker`)
- Runs UC agents via Temporal task queue
- Parallel condensed log showing multiple agents simultaneously
- Each line: `[A-NN] TKT-XXXXX category > tool_call > tool_call > RESULT (time)`
- For sandbox executions: shows the command and key output
- For memory hits: shows what was recalled
- `--mock` flag for smart mock logic (no API key needed)

### T3: Injector (`python -m sla_guardian.main live-inject --tickets 100 --rate 2`)
- Generates tickets using existing ticket_generator.py
- Injects at configurable rate (default: 1 ticket every 2 seconds)
- Shows each injected ticket with solvable/unsolvable tag
- Commentary: "This billing overcharge should be auto-resolved by the agent"
- Commentary: "This production outage requires engineering -- agent should escalate"

## Demo "Wow Moments"

1. Agent resolves billing ticket: queries payment -> finds discrepancy -> processes refund -> writes customer message
2. Agent gets webhook ticket: writes curl command -> executes in sandbox -> sees 500 response -> includes diagnostics in escalation
3. Memory kicks in: "Based on past ticket TKT-30015, this matches a checkout tax rounding issue" -> resolves faster
4. Unsolvable ticket: agent investigates, determines needs engineering, writes detailed reproduction steps
5. Kill worker mid-reasoning, restart -> Temporal replays, agent resumes exactly where it left off

## Commands

```bash
# Start Temporal
temporal server start-dev

# Terminal 1: Live dashboard
uv run python -m sla_guardian.main live-dashboard

# Terminal 2: Agent worker (real LLM)
uv run python -m sla_guardian.main live-worker
# Or mock mode (no API key):
uv run python -m sla_guardian.main live-worker --mock

# Terminal 3: Inject tickets
uv run python -m sla_guardian.main live-inject --tickets 100 --rate 2
```

## Dependencies
- Existing: temporalio, rich, pydantic, openai
- UC: universal_computer (Agent, FunctionTool, UnixLocalSandboxClient, Memory plugin)
- OPENAI_API_KEY for real LLM mode
