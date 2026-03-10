# Benchmark Code Walkthrough

How the scale benchmark works, end to end.

---

## Architecture

```
benchmark_scale.py (single process)
    |
    |-- 1. generate_ticket_batch(1000)     # Create 1000 realistic tickets
    |
    |-- 2. Start Temporal Worker           # In-process, same event loop
    |       |-- AgentTicketWorkflow         # 1 workflow definition
    |       |-- process_ticket activity     # 1 activity, runs mock agent
    |
    |-- 3. Inject workflows concurrently   # asyncio.gather + semaphore(50)
    |       |-- client.start_workflow()     # For each of 1000 tickets
    |
    |-- 4. Poll for completion             # Query each workflow until done
    |
    |-- 5. Measure storage                 # handle.describe().raw_info
    |-- 6. Measure query performance       # Time single + parallel queries
    |-- 7. Compute token metrics           # Post-process from AgentResolution
    |-- 8. Render Rich report              # Tables + panels
```

Everything runs in a single Python process. The Temporal Worker and the
benchmark orchestrator share the same asyncio event loop via
`asyncio.create_task(worker.run())`.

---

## Key Components

### 1. Ticket Generation

```python
tickets = generate_ticket_batch(1000, seed=42, sla_offset_minutes=10)
```

`ticket_generator.py` uses a seeded `random.Random` instance so every run
with the same seed produces identical tickets. 28 templates across 9
categories (billing, shipping, technical, crisis, account, feature, etc.).
Each ticket has a realistic description, comment thread, customer profile,
and tags.

### 2. The Workflow: `AgentTicketWorkflow`

The workflow is intentionally minimal -- it exists to give Temporal something
to orchestrate and persist:

```python
@workflow.defn
class AgentTicketWorkflow:
    @workflow.query
    def get_state(self) -> AgentTicketState:     # Dashboard polls this
        return self._state

    @workflow.run
    async def run(self, ticket_id, metadata):
        self._state.agent_status = AgentStatus.INVESTIGATING

        resolution = await workflow.execute_activity_method(
            AgentTicketActivities.process_ticket,   # <-- all logic here
            ticket_id,
            start_to_close_timeout=timedelta(seconds=120),
        )

        self._state.resolution = resolution
        self._state.agent_status = resolution.status
        return self._state
```

- `@workflow.query` exposes `get_state` so the dashboard can poll live status
  without waiting for completion.
- `workflow.now()` instead of `datetime.now()` -- Temporal's sandbox prohibits
  non-deterministic calls inside workflows.
- The workflow's only job is to dispatch the `process_ticket` activity and
  record the result. All intelligence lives in the activity.

### 3. The Activity: `process_ticket`

This is where the "agent" runs. In mock mode, it simulates what a real UC
agent would do by pattern-matching ticket content and building a realistic
tool-call chain:

```python
@activity.defn
async def process_ticket(self, ticket_id: str) -> AgentResolution:
    ticket = await self.zendesk.get_ticket(ticket_id)  # Read from mock

    # 1. Search memory for similar past tickets
    # 2. Pattern-match ticket content to resolution path
    # 3. Build tool_calls list simulating agent reasoning
    # 4. Write to memory for future tickets
    # 5. Return AgentResolution with status, tool chain, cost
```

Each resolution path builds a `tool_calls` list that mirrors what a real
agent session would produce:

| Category | Tool Chain | Result |
|----------|-----------|--------|
| Billing | get_ticket > get_transaction > process_refund > write_memory | RESOLVED |
| Technical | get_ticket > check_endpoint > run_diagnostic (sandbox) > create_escalation | ESCALATED |
| Account | get_ticket > check_account > send_password_reset | RESOLVED |
| Crisis | get_ticket > create_escalation (engineering + management) | ESCALATED |

### 4. Injection (Concurrent Workflow Starts)

```python
sem = asyncio.Semaphore(50)  # Max 50 concurrent starts

async def _start_one(ticket):
    async with sem:
        await client.start_workflow(
            AgentTicketWorkflow.run,
            args=[ticket_id, metadata],
            id=f"bench-agent-{ticket_id}",
            task_queue="sla-guardian-benchmark",
        )

await asyncio.gather(*[_start_one(t) for t in tickets])
```

All 1000 workflows are started concurrently (throttled to 50 at a time by the
semaphore). The Temporal server queues them and the in-process Worker picks
them up from the task queue.

### 5. Completion Polling

```python
while len(completed) < total:
    for wf_id in remaining:
        state = await handle.query(AgentTicketWorkflow.get_state)
        if state.agent_status in (RESOLVED, ESCALATED, FAILED):
            completed[wf_id] = state
    await asyncio.sleep(0.5)
```

Polls every 500ms. Uses `@workflow.query` which reads state without
blocking the workflow -- this is the same mechanism the live dashboard uses.

### 6. Storage Measurement

```python
handle = client.get_workflow_handle(wf_id)
desc = await handle.describe()
raw = desc.raw_info

history_length = raw.history_length       # Number of events
history_size_bytes = raw.history_size_bytes  # Total bytes
state_transition_count = raw.state_transition_count
```

Uses the Temporal Python SDK's `describe()` API (not the CLI) to get storage
metrics. `history_size_bytes` is Temporal's canonical measure of how much
storage a workflow's event history consumes.

### 7. Token Estimation

Post-processes the `AgentResolution` from each completed workflow:

```python
def _estimate_token_metrics(resolution, ticket_description):
    # Prompt tokens (input to the LLM):
    system_prompt = 800         # Fixed system prompt
    content = words * 1.3       # Ticket content (word-to-token ratio)
    tool_inputs = tools * 100   # Each tool call has ~100 token input
    reasoning = 400             # Base agent reasoning
    sandbox = 500 if sandbox_commands else 0
    memory_discount = -200 if memory_hit else 0

    # Completion tokens (output from the LLM):
    tool_outputs = tools * 200  # Each tool call has ~200 token output
    decision = 400              # Agent's resolution reasoning
    response = 300              # Customer-facing message

    cost = (prompt / 1M * $2.50) + (completion / 1M * $10.00)
```

This models what a real GPT-4o agent session would cost. The memory discount
(-200 tokens) simulates the agent skipping investigation steps when it
recognizes a known pattern.

---

## What Each Measured Metric Means

| Metric | What It Measures | Why It Matters |
|--------|-----------------|---------------|
| **Injection rate** (wf/s) | How fast we can start workflows via Temporal client | Determines max ticket ingestion throughput |
| **Processing rate** (wf/s) | How fast the worker completes workflows | Determines how many workers you need at scale |
| **History size** (bytes/wf) | Temporal's event history per workflow | Drives storage capacity planning |
| **Events/workflow** | Number of Temporal events per workflow | Each event = ~500B overhead; fewer activities = less overhead |
| **Payload ratio** | Your data vs Temporal metadata | Tells you whether to optimize payloads or reduce event count |
| **Single query latency** | Time to query one workflow's state | Base latency for dashboard interactions |
| **Parallel query latency** | Per-query cost at concurrency | Determines dashboard refresh rate at scale |
| **list_workflows** | Time to enumerate all workflows | Used by dashboard to discover new tickets |
| **Tokens/ticket** | Simulated LLM token consumption | Drives LLM cost projections |
| **Cost/ticket** | LLM cost at GPT-4o rates | The actual dollar cost per ticket resolution |

---

## How to Run

```bash
# Start Temporal dev server (separate terminal)
temporal server start-dev

# Run benchmark (default: 1000 tickets)
uv run python -m sla_guardian.benchmark_scale --tickets 1000 --seed 42

# Quick test (100 tickets)
uv run python -m sla_guardian.benchmark_scale --tickets 100

# High concurrency test
uv run python -m sla_guardian.benchmark_scale --tickets 5000 --concurrency 100
```
