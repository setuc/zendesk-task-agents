# Live 3-Terminal SLA Guardian Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a 3-terminal live demo where an injector feeds tickets, UC agents resolve/escalate them in real-time, and a dashboard shows everything updating live.

**Architecture:** Three independent processes communicate exclusively through Temporal. The injector starts `AgentTicketWorkflow` per ticket. The worker runs UC agents (real LLM or mock) with tools + sandbox + memory. The dashboard polls Temporal queries every 2s for live state.

**Tech Stack:** temporalio, rich (Live display), universal_computer (Agent, FunctionTool, UnixLocalSandboxClient, Memory), openai, pydantic

---

### Task 1: New Data Types for Agent Resolution

**Files:**
- Modify: `sla_guardian/workflows/data_types.py`

**Step 1: Add AgentTicketState and AgentResolution models**

Add these to the existing `data_types.py`:

```python
class AgentStatus(str, Enum):
    QUEUED = "queued"
    INVESTIGATING = "investigating"
    RESOLVING = "resolving"
    ESCALATING = "escalating"
    RESOLVED = "resolved"
    ESCALATED = "escalated"
    FAILED = "failed"

class ToolCall(BaseModel):
    """Record of a single tool call made by the agent."""
    tool_name: str
    args_summary: str = ""
    result_summary: str = ""
    duration_ms: float = 0.0

class MemoryHit(BaseModel):
    """A memory match from a previous ticket resolution."""
    matched_ticket_id: str = ""
    pattern: str = ""
    suggested_action: str = ""

class AgentResolution(BaseModel):
    """Result of an agent processing a ticket."""
    ticket_id: str
    status: AgentStatus = AgentStatus.QUEUED
    solvable: bool = False
    resolution_type: str = ""  # "refund", "replacement", "reset_link", "escalation", etc.
    resolution_summary: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    memory_hit: MemoryHit | None = None
    memory_written: str = ""  # What was saved to memory
    escalation_reason: str = ""
    escalation_team: str = ""
    customer_message: str = ""
    processing_time_ms: float = 0.0
    sandbox_commands: list[str] = Field(default_factory=list)  # Shell commands executed
    sandbox_output: str = ""  # Key sandbox output

class AgentTicketState(BaseModel):
    """Full state for the agent ticket workflow, queryable from dashboard."""
    ticket_id: str
    customer_name: str = ""
    customer_tier: str = "standard"
    priority: str = "normal"
    category: str = ""
    subject: str = ""
    agent_status: AgentStatus = AgentStatus.QUEUED
    resolution: AgentResolution | None = None
    started_at: str = ""
    completed_at: str = ""
```

**Step 2: Verify import works**

Run: `.venv/bin/python -c "from sla_guardian.workflows.data_types import AgentTicketState, AgentResolution, AgentStatus; print('OK')"`
Expected: OK

**Step 3: Commit**

```bash
git add sla_guardian/workflows/data_types.py
git commit -m "feat: add AgentTicketState and AgentResolution data types"
```

---

### Task 2: Agent Tools for Resolution

New FunctionTools that the UC agent will use to resolve tickets. These wrap the existing mock services.

**Files:**
- Create: `sla_guardian/tools/resolution_tools.py`

**Step 1: Create resolution tools**

```python
from __future__ import annotations
from pydantic import BaseModel, ConfigDict, Field
from universal_computer.agents.tools import FunctionTool
from common.services.base import ZendeskService, OrderDBService, PaymentService, ShippingService


class ProcessRefundArgs(BaseModel):
    transaction_id: str = Field(description="Transaction ID to refund")
    amount: float = Field(description="Refund amount in dollars")
    reason: str = Field(description="Reason for the refund")

class ProcessRefundTool(FunctionTool[ProcessRefundArgs, dict]):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    tool_name = "process_refund"
    args_model = ProcessRefundArgs
    description = "Process a refund for a customer. Use when investigation confirms overcharge or damaged item."
    payment: PaymentService = Field(exclude=True)

    async def run(self, args: ProcessRefundArgs) -> dict:
        return await self.payment.process_refund(args.transaction_id, args.amount, args.reason)


class GetTransactionArgs(BaseModel):
    transaction_id: str = Field(description="Transaction ID to look up")

class GetTransactionTool(FunctionTool[GetTransactionArgs, dict]):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    tool_name = "get_transaction"
    args_model = GetTransactionArgs
    description = "Look up a payment transaction to verify charges and amounts."
    payment: PaymentService = Field(exclude=True)

    async def run(self, args: GetTransactionArgs) -> dict:
        return await self.payment.get_transaction(args.transaction_id)


class GetOrderArgs(BaseModel):
    order_id: str = Field(description="Order ID to look up")

class GetOrderTool(FunctionTool[GetOrderArgs, dict]):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    tool_name = "get_order_details"
    args_model = GetOrderArgs
    description = "Look up order details including items, prices, and delivery status."
    order_db: OrderDBService = Field(exclude=True)

    async def run(self, args: GetOrderArgs) -> dict:
        return await self.order_db.get_order(args.order_id)


class CheckEndpointArgs(BaseModel):
    url: str = Field(description="URL to check")
    method: str = Field(default="GET", description="HTTP method")

class CheckEndpointTool(FunctionTool[CheckEndpointArgs, dict]):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    tool_name = "check_endpoint"
    args_model = CheckEndpointArgs
    description = "Check if an API endpoint is responding. Use for technical tickets about API/webhook issues."
    # No external service needed -- mock implementation
    async def run(self, args: CheckEndpointArgs) -> dict:
        # Simulate endpoint check
        import random
        if "500" in args.url or "error" in args.url:
            return {"status": 500, "response_time_ms": 2500, "error": "Internal Server Error", "body": '{"error": "database connection pool exhausted"}'}
        return {"status": 200, "response_time_ms": 45, "body": "OK"}


class CreateEscalationArgs(BaseModel):
    ticket_id: str = Field(description="Ticket to escalate")
    team: str = Field(description="Team to escalate to: engineering, legal, management, billing")
    reason: str = Field(description="Detailed reason for escalation with findings")
    reproduction_steps: str = Field(default="", description="Steps to reproduce the issue (for technical tickets)")

class CreateEscalationTool(FunctionTool[CreateEscalationArgs, dict]):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    tool_name = "create_escalation"
    args_model = CreateEscalationArgs
    description = "Escalate a ticket to a specialized team when you cannot resolve it yourself. Include detailed findings and reproduction steps."
    zendesk: ZendeskService = Field(exclude=True)

    async def run(self, args: CreateEscalationArgs) -> dict:
        await self.zendesk.add_comment(args.ticket_id, f"ESCALATION to {args.team}: {args.reason}", public=False)
        await self.zendesk.update_ticket(args.ticket_id, {"priority": "urgent", "tags_add": [f"escalated_{args.team}"]})
        return {"escalated": True, "team": args.team, "ticket_id": args.ticket_id}


class SearchMemoryArgs(BaseModel):
    query: str = Field(description="What to search for in past resolutions, e.g. 'billing overcharge checkout' or 'API 500 webhook'")

class SearchMemoryTool(FunctionTool[SearchMemoryArgs, dict]):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    tool_name = "search_memory"
    args_model = SearchMemoryArgs
    description = "Search your memory of past ticket resolutions for similar issues. Returns matching patterns and suggested actions."
    memory_store: dict = Field(default_factory=dict, exclude=True)  # Shared mutable dict

    async def run(self, args: SearchMemoryArgs) -> dict:
        matches = []
        query_lower = args.query.lower()
        for key, entry in self.memory_store.items():
            if any(word in key.lower() for word in query_lower.split()):
                matches.append(entry)
        if matches:
            best = matches[0]
            return {"found": True, "matched_ticket": best.get("ticket_id", ""), "pattern": best.get("pattern", ""), "suggested_action": best.get("action", "")}
        return {"found": False}


class WriteMemoryArgs(BaseModel):
    ticket_id: str = Field(description="Ticket ID this learning is from")
    pattern: str = Field(description="Pattern identified, e.g. 'billing overcharge from checkout system v2.3 tax rounding'")
    action: str = Field(description="Resolution action that worked, e.g. 'process refund for difference between charged and listing price'")

class WriteMemoryTool(FunctionTool[WriteMemoryArgs, dict]):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    tool_name = "write_memory"
    args_model = WriteMemoryArgs
    description = "Save a resolution pattern to memory so you can resolve similar tickets faster in the future."
    memory_store: dict = Field(default_factory=dict, exclude=True)

    async def run(self, args: WriteMemoryArgs) -> dict:
        key = f"{args.ticket_id}:{args.pattern}"
        self.memory_store[key] = {"ticket_id": args.ticket_id, "pattern": args.pattern, "action": args.action}
        return {"saved": True, "key": key}
```

**Step 2: Verify tools import**

Run: `.venv/bin/python -c "from sla_guardian.tools.resolution_tools import ProcessRefundTool, SearchMemoryTool, WriteMemoryTool; print('OK')"`
Expected: OK

**Step 3: Commit**

```bash
git add sla_guardian/tools/resolution_tools.py
git commit -m "feat: add resolution tools for UC agent (refund, order, endpoint, escalation, memory)"
```

---

### Task 3: Agent Ticket Workflow

A new Temporal workflow that runs a UC agent per ticket. This replaces the old TicketMonitorWorkflow for the live demo.

**Files:**
- Create: `sla_guardian/workflows/agent_ticket_workflow.py`
- Create: `sla_guardian/workflows/agent_activities.py`

**Step 1: Create the agent activities**

The activities are where UC agent calls happen. Two activities:
- `run_agent_on_ticket`: Creates a UC agent, gives it the ticket, returns AgentResolution
- `run_mock_agent_on_ticket`: Smart mock logic (no LLM), same return type

```python
# sla_guardian/workflows/agent_activities.py
from __future__ import annotations

import time
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from temporalio import activity

from .data_types import (
    AgentResolution,
    AgentStatus,
    MemoryHit,
    ToolCall,
)
from common.services.base import ZendeskService, OrderDBService, PaymentService, ShippingService


@dataclass
class AgentTicketActivities:
    zendesk: ZendeskService
    order_db: OrderDBService
    payment: PaymentService
    shipping: ShippingService
    memory_store: dict = field(default_factory=dict)
    use_real_agent: bool = False

    @activity.defn
    async def process_ticket(self, ticket_id: str) -> AgentResolution:
        """Process a ticket using either real UC agent or smart mock."""
        if self.use_real_agent:
            return await self._process_with_uc_agent(ticket_id)
        return await self._process_with_mock(ticket_id)

    async def _process_with_mock(self, ticket_id: str) -> AgentResolution:
        """Smart mock agent that simulates tool chains."""
        start = time.monotonic()
        tool_calls: list[ToolCall] = []
        sandbox_commands: list[str] = []
        sandbox_output = ""

        try:
            ticket = await self.zendesk.get_ticket(ticket_id)
        except KeyError:
            return AgentResolution(ticket_id=ticket_id, status=AgentStatus.FAILED, resolution_summary="Ticket not found")

        description = ticket.get("description", "").lower()
        subject = ticket.get("subject", "").lower()
        tags = ticket.get("tags", [])
        customer_name = ticket.get("requester", {}).get("name", "Customer")
        text = f"{subject} {description}"

        tool_calls.append(ToolCall(tool_name="get_ticket", args_summary=ticket_id, result_summary=f"Subject: {ticket.get('subject', '')[:50]}", duration_ms=5))

        # Search memory
        memory_hit = None
        query_words = []
        if "billing" in text or "overcharge" in text or "charge" in text:
            query_words = ["billing", "overcharge"]
        elif "api" in text or "500" in text or "webhook" in text:
            query_words = ["api", "endpoint", "500"]
        elif "wrong" in text or "incorrect" in text:
            query_words = ["wrong", "item", "fulfillment"]

        if query_words:
            query = " ".join(query_words)
            tool_calls.append(ToolCall(tool_name="search_memory", args_summary=query, duration_ms=2))
            for key, entry in self.memory_store.items():
                if any(w in key.lower() for w in query_words):
                    memory_hit = MemoryHit(matched_ticket_id=entry["ticket_id"], pattern=entry["pattern"], suggested_action=entry["action"])
                    tool_calls[-1].result_summary = f"MATCH: {entry['pattern'][:60]}"
                    break
            if not memory_hit:
                tool_calls[-1].result_summary = "No matches found"

        # Determine if solvable and resolve
        # --- Billing / overcharge ---
        if any(kw in text for kw in ["overcharge", "billing discrepancy", "duplicate charge", "charged more", "incorrect amount"]):
            order_match = re.search(r"(ORD-\d+)", ticket.get("description", ""))
            if order_match:
                oid = order_match.group(1)
                try:
                    order = await self.order_db.get_order(oid)
                    tool_calls.append(ToolCall(tool_name="get_order_details", args_summary=oid, result_summary=f"Total: ${order.get('charged_total', 0)}", duration_ms=8))
                except KeyError:
                    pass

            txn_id = f"TXN-{order_match.group(1)}" if order_match else ""
            if txn_id:
                try:
                    txn = await self.payment.get_transaction(txn_id)
                    tool_calls.append(ToolCall(tool_name="get_transaction", args_summary=txn_id, result_summary=f"Amount: ${txn.get('amount', 0)}", duration_ms=6))
                except KeyError:
                    pass

            # Process refund
            amounts = re.findall(r"\$(\d+\.?\d*)", ticket.get("description", ""))
            refund_amount = 0.0
            if len(amounts) >= 2:
                try:
                    refund_amount = abs(float(amounts[0]) - float(amounts[1]))
                except ValueError:
                    refund_amount = 20.0
            if refund_amount <= 0:
                refund_amount = 20.0

            tool_calls.append(ToolCall(tool_name="process_refund", args_summary=f"${refund_amount:.2f}", result_summary="Refund processed", duration_ms=15))
            tool_calls.append(ToolCall(tool_name="update_ticket", args_summary=f"{ticket_id} -> solved", result_summary="Ticket closed", duration_ms=5))

            # Write memory
            pattern = "billing overcharge - checkout pricing discrepancy"
            self.memory_store[f"{ticket_id}:billing"] = {"ticket_id": ticket_id, "pattern": pattern, "action": f"refund ${refund_amount:.2f}"}
            tool_calls.append(ToolCall(tool_name="write_memory", args_summary=pattern[:40], result_summary="Saved", duration_ms=2))

            elapsed = (time.monotonic() - start) * 1000
            return AgentResolution(
                ticket_id=ticket_id, status=AgentStatus.RESOLVED, solvable=True,
                resolution_type="refund", resolution_summary=f"Refund ${refund_amount:.2f} processed for billing overcharge",
                tool_calls=tool_calls, memory_hit=memory_hit, memory_written=pattern,
                customer_message=f"Hi {customer_name.split()[0]}, we've issued a refund of ${refund_amount:.2f}. It will appear in 3-5 business days.",
                processing_time_ms=elapsed,
            )

        # --- Wrong item ---
        if any(kw in text for kw in ["wrong item", "wrong color", "incorrect item", "not what i ordered"]):
            tool_calls.append(ToolCall(tool_name="get_order_details", args_summary="order lookup", result_summary="Item mismatch confirmed", duration_ms=10))
            tool_calls.append(ToolCall(tool_name="create_replacement", args_summary="replacement order", result_summary="Replacement created", duration_ms=12))
            tool_calls.append(ToolCall(tool_name="update_ticket", args_summary=f"{ticket_id} -> solved", result_summary="Ticket closed", duration_ms=5))

            pattern = "wrong item - fulfillment error"
            self.memory_store[f"{ticket_id}:fulfillment"] = {"ticket_id": ticket_id, "pattern": pattern, "action": "create replacement order"}
            tool_calls.append(ToolCall(tool_name="write_memory", args_summary=pattern, result_summary="Saved", duration_ms=2))

            elapsed = (time.monotonic() - start) * 1000
            return AgentResolution(
                ticket_id=ticket_id, status=AgentStatus.RESOLVED, solvable=True,
                resolution_type="replacement", resolution_summary="Replacement order created for wrong item",
                tool_calls=tool_calls, memory_hit=memory_hit, memory_written=pattern,
                customer_message=f"Hi {customer_name.split()[0]}, we've shipped a replacement. You'll receive it in 3-5 business days.",
                processing_time_ms=(time.monotonic() - start) * 1000,
            )

        # --- Password / login ---
        if any(kw in text for kw in ["password", "login", "locked out", "cannot access", "can't log in"]):
            tool_calls.append(ToolCall(tool_name="update_ticket", args_summary=f"{ticket_id} -> solved", result_summary="Reset link sent", duration_ms=5))
            elapsed = (time.monotonic() - start) * 1000
            return AgentResolution(
                ticket_id=ticket_id, status=AgentStatus.RESOLVED, solvable=True,
                resolution_type="password_reset", resolution_summary="Password reset link sent to customer",
                tool_calls=tool_calls, memory_hit=memory_hit,
                customer_message=f"Hi {customer_name.split()[0]}, we've sent a password reset link to your email.",
                processing_time_ms=elapsed,
            )

        # --- Technical / API / webhook (use sandbox) ---
        if any(kw in text for kw in ["api", "500", "webhook", "endpoint", "integration", "timeout"]):
            endpoint_url = "https://api.customer.example.com/webhook"
            url_match = re.search(r"(https?://\S+)", ticket.get("description", ""))
            if url_match:
                endpoint_url = url_match.group(1)

            tool_calls.append(ToolCall(tool_name="check_endpoint", args_summary=endpoint_url[:40], result_summary="HTTP 500", duration_ms=2500))

            # Sandbox execution
            cmd = f"curl -s -o /dev/null -w '%{{http_code}} %{{time_total}}' {endpoint_url}"
            sandbox_commands.append(cmd)
            sandbox_output = "500 2.345\nResponse: {\"error\": \"database connection pool exhausted\"}"
            tool_calls.append(ToolCall(tool_name="run_diagnostic_script", args_summary=f"curl {endpoint_url[:30]}...", result_summary="500 - DB pool exhausted", duration_ms=3000))

            # Escalate
            tool_calls.append(ToolCall(tool_name="create_escalation", args_summary="engineering", result_summary="Escalated with diagnostics", duration_ms=10))

            elapsed = (time.monotonic() - start) * 1000
            return AgentResolution(
                ticket_id=ticket_id, status=AgentStatus.ESCALATED, solvable=False,
                resolution_type="escalation", resolution_summary=f"Escalated to engineering: {endpoint_url} returning 500",
                tool_calls=tool_calls, memory_hit=memory_hit,
                escalation_reason=f"API endpoint {endpoint_url} returning 500 errors. Diagnostic shows database connection pool exhausted.",
                escalation_team="engineering",
                sandbox_commands=sandbox_commands, sandbox_output=sandbox_output,
                processing_time_ms=elapsed,
            )

        # --- Production outage / crisis ---
        if any(kw in text for kw in ["production", "outage", "down", "critical", "data loss", "security"]):
            tool_calls.append(ToolCall(tool_name="create_escalation", args_summary="engineering/management", result_summary="Critical escalation", duration_ms=8))
            elapsed = (time.monotonic() - start) * 1000
            return AgentResolution(
                ticket_id=ticket_id, status=AgentStatus.ESCALATED, solvable=False,
                resolution_type="escalation", resolution_summary="Critical issue escalated to engineering and management",
                tool_calls=tool_calls, memory_hit=memory_hit,
                escalation_reason="Production-impacting issue requiring immediate engineering attention",
                escalation_team="engineering + management",
                processing_time_ms=elapsed,
            )

        # --- Legal / escalation request ---
        if any(kw in text for kw in ["legal", "lawyer", "manager", "supervisor", "escalat", "cancel"]):
            tool_calls.append(ToolCall(tool_name="create_escalation", args_summary="management", result_summary="Escalated per request", duration_ms=8))
            elapsed = (time.monotonic() - start) * 1000
            return AgentResolution(
                ticket_id=ticket_id, status=AgentStatus.ESCALATED, solvable=False,
                resolution_type="escalation", resolution_summary="Customer requesting management attention",
                tool_calls=tool_calls, memory_hit=memory_hit,
                escalation_reason="Customer explicitly requested escalation to management",
                escalation_team="management",
                processing_time_ms=elapsed,
            )

        # --- Feature request (defer) ---
        if any(kw in text for kw in ["feature", "suggestion", "would be nice", "request"]):
            tool_calls.append(ToolCall(tool_name="update_ticket", args_summary=f"{ticket_id} -> pending", result_summary="Tagged as feature request", duration_ms=5))
            elapsed = (time.monotonic() - start) * 1000
            return AgentResolution(
                ticket_id=ticket_id, status=AgentStatus.ESCALATED, solvable=False,
                resolution_type="deferred", resolution_summary="Feature request forwarded to product team",
                tool_calls=tool_calls, memory_hit=memory_hit,
                escalation_reason="Feature request - forwarded to product backlog",
                escalation_team="product",
                processing_time_ms=elapsed,
            )

        # --- Default: investigate and escalate ---
        tool_calls.append(ToolCall(tool_name="create_escalation", args_summary="support-l2", result_summary="Needs further investigation", duration_ms=8))
        elapsed = (time.monotonic() - start) * 1000
        return AgentResolution(
            ticket_id=ticket_id, status=AgentStatus.ESCALATED, solvable=False,
            resolution_type="escalation", resolution_summary="Requires further investigation by senior support",
            tool_calls=tool_calls, memory_hit=memory_hit,
            escalation_reason="Could not determine resolution from available information",
            escalation_team="support-l2",
            processing_time_ms=elapsed,
        )

    async def _process_with_uc_agent(self, ticket_id: str) -> AgentResolution:
        """Process ticket with a real UC agent (requires OPENAI_API_KEY)."""
        # TODO: Implement with actual UC Agent.start() + FunctionTools
        # For now, fall back to mock
        activity.logger.info(f"Real UC agent mode for {ticket_id} (falling back to mock for now)")
        return await self._process_with_mock(ticket_id)
```

**Step 2: Create the workflow**

```python
# sla_guardian/workflows/agent_ticket_workflow.py
from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from .agent_activities import AgentTicketActivities
    from .data_types import AgentResolution, AgentStatus, AgentTicketState


@workflow.defn
class AgentTicketWorkflow:
    """Per-ticket workflow: runs a UC agent to investigate and resolve/escalate."""

    def __init__(self) -> None:
        self._state = AgentTicketState(ticket_id="")

    @workflow.query
    def get_state(self) -> AgentTicketState:
        return self._state

    @workflow.run
    async def run(self, ticket_id: str, ticket_metadata: dict | None = None) -> AgentTicketState:
        self._state.ticket_id = ticket_id
        if ticket_metadata:
            self._state.customer_name = ticket_metadata.get("customer_name", "")
            self._state.customer_tier = ticket_metadata.get("tier", "standard")
            self._state.priority = ticket_metadata.get("priority", "normal")
            self._state.category = ticket_metadata.get("category", "")
            self._state.subject = ticket_metadata.get("subject", "")
        self._state.started_at = str(workflow.now())
        self._state.agent_status = AgentStatus.INVESTIGATING

        resolution: AgentResolution = await workflow.execute_activity_method(
            AgentTicketActivities.process_ticket,
            ticket_id,
            start_to_close_timeout=timedelta(seconds=300),
            retry_policy=RetryPolicy(maximum_attempts=2),
        )

        self._state.resolution = resolution
        self._state.agent_status = resolution.status
        self._state.completed_at = str(workflow.now())
        return self._state
```

**Step 3: Verify imports**

Run: `.venv/bin/python -c "from sla_guardian.workflows.agent_activities import AgentTicketActivities; from sla_guardian.workflows.agent_ticket_workflow import AgentTicketWorkflow; print('OK')"`
Expected: OK (may warn about workflow sandbox but should import)

**Step 4: Commit**

```bash
git add sla_guardian/workflows/agent_activities.py sla_guardian/workflows/agent_ticket_workflow.py
git commit -m "feat: AgentTicketWorkflow with UC agent activities (mock + real modes)"
```

---

### Task 4: Terminal 3 -- Injector (`live-inject`)

**Files:**
- Modify: `sla_guardian/main.py` (add `live-inject` subcommand)

**Step 1: Add the injector command**

Append to `parse_args()` a new subparser `live-inject` with args: `--tickets`, `--rate`, `--seed`, `--sla-offset`.

The injector:
1. Generates tickets with `generate_ticket_batch()`
2. Connects to Temporal
3. Starts one `AgentTicketWorkflow` per ticket at `--rate` tickets/second
4. Prints each injection with commentary about solvable/unsolvable
5. Uses Rich console with color coding

**Step 2: Test injector standalone**

Run injector with 5 tickets, verify workflows appear in Temporal:
```bash
.venv/bin/python -m sla_guardian.main live-inject --tickets 5 --rate 1 --seed 42
temporal workflow list --query "TaskQueue='sla-guardian-live'"
```
Expected: 5 workflows listed

**Step 3: Commit**

```bash
git add sla_guardian/main.py
git commit -m "feat: live-inject command for ticket injection"
```

---

### Task 5: Terminal 2 -- Agent Worker (`live-worker`)

**Files:**
- Modify: `sla_guardian/main.py` (add `live-worker` subcommand)

**Step 1: Add the worker command**

Worker that:
1. Creates mock services + loads generated tickets (same seed as injector)
2. Creates `AgentTicketActivities` instance
3. Starts Temporal worker with `AgentTicketWorkflow` + activities
4. Subscribes to activity completions and prints condensed agent log lines
5. Uses Rich console: `[A-NN] TKT-XXXXX category > tool > tool > RESULT (time)`

The worker should print each agent's tool chain as it completes, showing:
- Memory searches and hits
- Tool calls in sequence
- Sandbox commands (for technical tickets)
- Resolution or escalation result

**Step 2: Test worker processes a ticket**

Run worker, then inject 1 ticket:
```bash
# Terminal 1
.venv/bin/python -m sla_guardian.main live-worker --seed 42

# Terminal 2
.venv/bin/python -m sla_guardian.main live-inject --tickets 1 --seed 42
```
Expected: Worker shows agent processing the ticket with tool calls

**Step 3: Commit**

```bash
git add sla_guardian/main.py
git commit -m "feat: live-worker command with condensed agent activity log"
```

---

### Task 6: Terminal 1 -- Dashboard (`live-dashboard`)

**Files:**
- Modify: `sla_guardian/main.py` (add `live-dashboard` subcommand)

**Step 1: Add the dashboard command**

Dashboard that:
1. Connects to Temporal
2. Uses Rich Live display, refreshes every 2s
3. Polls all `AgentTicketWorkflow` workflows by listing them
4. Queries each workflow's state via `get_state` query
5. Renders a live table with columns: Ticket | Customer | Tier | Priority | Category | Agent Status | Resolution | Tool Count | Memory | Time
6. Stats bar at bottom: Total | Resolved | Escalated | Working | Queued | Avg Time
7. Color coding: green=resolved, yellow=investigating, red=escalated, cyan=resolving, dim=queued

**Step 2: Test full 3-terminal flow**

```bash
# Terminal 1
.venv/bin/python -m sla_guardian.main live-dashboard

# Terminal 2
.venv/bin/python -m sla_guardian.main live-worker --seed 42 --tickets 20

# Terminal 3
.venv/bin/python -m sla_guardian.main live-inject --tickets 20 --rate 2 --seed 42
```
Expected: Dashboard shows rows appearing and updating live as agents process tickets

**Step 3: Commit**

```bash
git add sla_guardian/main.py
git commit -m "feat: live-dashboard with real-time ticket grid and stats"
```

---

### Task 7: End-to-End Verification

**Step 1: Run full 100-ticket demo**

```bash
# Terminal 1: Dashboard
.venv/bin/python -m sla_guardian.main live-dashboard

# Terminal 2: Worker
.venv/bin/python -m sla_guardian.main live-worker --seed 42 --tickets 100

# Terminal 3: Injector
.venv/bin/python -m sla_guardian.main live-inject --tickets 100 --rate 3 --seed 42
```

Verify:
- Dashboard shows rows appearing as injector sends tickets
- Worker shows parallel agent activity with tool chains
- Memory hits increase over time (later tickets resolve faster)
- Mix of RESOLVED and ESCALATED statuses
- Sandbox commands visible for technical tickets
- No crashes, no missing tickets

**Step 2: Test kill-and-restart**

While 100-ticket demo is running:
1. Kill the worker (Ctrl+C)
2. Wait 5 seconds
3. Restart: `.venv/bin/python -m sla_guardian.main live-worker --seed 42 --tickets 100`
4. Verify: queued tickets resume processing, dashboard updates

**Step 3: Final commit**

```bash
git add -A
git commit -m "feat: verified 3-terminal live SLA Guardian demo"
git push origin feat/initial-implementation
```
