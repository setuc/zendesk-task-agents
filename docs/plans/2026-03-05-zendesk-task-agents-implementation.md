# Zendesk Task Agent Suite: Implementation Plan

## Phase 1: Shared Infrastructure

### Step 1.1: Service Protocols (`common/services/base.py`)
**What:** Define abstract Protocol classes that all mock/real services implement.
**Interfaces:**
- `ZendeskService`: `get_ticket()`, `update_ticket()`, `list_tickets()`, `add_comment()`
- `OrderDBService`: `get_order()`, `create_replacement_order()`
- `ShippingService`: `get_tracking()`, `create_return_label()`
- `PaymentService`: `process_refund()`, `reverse_refund()`, `get_transaction()`
- `SLARulesService`: `get_policy()`, `list_policies()`
- `EmailService`: `send_email()`
- `IntegrationTestService`: `test_endpoint()`, `run_diagnostics()`

**Test idea:** Import module, verify Protocol classes are importable and have correct method signatures via `typing.get_type_hints()`.

### Step 1.2: Mock Zendesk Service (`common/services/zendesk_mock.py`)
**What:** In-memory Zendesk service with configurable ticket data, failure injection.
**Test idea:** Create mock, add ticket, get ticket, update ticket, verify state changes. Test failure injection raises expected errors.

### Step 1.3: Service Registry (`common/services/service_registry.py`)
**What:** Factory function `create_services(config) -> dict[str, Any]` that returns mock or real service instances based on config.
**Test idea:** Call with default config, verify all returned services are mock instances.

### Step 1.4: Shared Zendesk Tools (`common/tools/zendesk_tools.py`)
**What:** FunctionTool subclasses following the UC pattern: `GetTicketTool`, `UpdateTicketTool`, `ListTicketsTool`, `AddCommentTool`.
**Test idea:** Instantiate tool with mock service, call `run()` directly with args model, verify correct service method called and return value.

### Step 1.5: Shared Data Types (`common/data_types.py`)
**What:** Pydantic models: `TicketInfo`, `CustomerInfo`, `TicketComment`, `TicketStatus` enum, `TicketPriority` enum.
**Test idea:** Instantiate models, verify serialization round-trips via `model_dump_json()` / `model_validate_json()`.

### Step 1.6: Agent Helpers (`common/agent_helpers.py`)
**What:** `run_agent_to_completion(task, prompt) -> str` helper that drives the Task event loop, collects text output, auto-executes non-approval tool calls.
**Test idea:** This is harder to unit test without a real LLM. Write a simple integration test that can be skipped without API keys. The function signature and error handling can be verified.

### Step 1.7: Rich TUI Components (`common/tui.py`)
**What:** Reusable Rich components: `WorkflowDashboard`, `StageProgress`, `ActivityTable`, `PlanDisplay`, `ApprovalPrompt`, `BenchmarkReport`.
**Test idea:** Instantiate each component, verify they render without errors using `rich.console.Console(file=StringIO())`. No visual verification needed initially.

---

## Phase 2: Example A - Order Resolution Agent

### Step 2.1: Data Types (`order_resolution/workflows/data_types.py`)
**Models:** `ExtractedIntent`, `ResolutionPlan`, `ResolutionStep`, `StepResult`, `ApprovalDecision`, `WorkflowState` (enum: CLASSIFYING, INVESTIGATING, PLANNING, AWAITING_APPROVAL, EXECUTING, COMPENSATING, VERIFYING, COMPLETED, FAILED).
**Test idea:** Serialization round-trip for each model.

### Step 2.2: Domain Services
- `order_resolution/services/order_db_mock.py` - MockOrderDBService with failure injection
- `order_resolution/services/shipping_mock.py` - MockShippingService
- `order_resolution/services/payment_mock.py` - MockPaymentService with `reverse_refund()` for saga

**Test idea:** Each mock: create instance, call methods, verify state. Payment mock: process_refund then reverse_refund, verify balance restored. Test failure injection triggers.

### Step 2.3: Domain Tools
- `order_resolution/tools/order_tools.py` - GetOrderTool, CreateReplacementOrderTool
- `order_resolution/tools/shipping_tools.py` - GetTrackingTool, CreateReturnLabelTool
- `order_resolution/tools/payment_tools.py` - ProcessRefundTool, GetTransactionTool

**Test idea:** Same pattern as Step 1.4 - instantiate with mock service, run directly.

### Step 2.4: Agent Factory + Instructions
- `order_resolution/agent_def.py` - `create_order_resolution_agent(services) -> Agent`
- `order_resolution/instructions.py` - System prompt for order resolution reasoning

**Test idea:** Call factory, verify returned Agent has correct tools, model, instructions set.

### Step 2.5: Temporal Activities (`order_resolution/workflows/activities.py`)
**Activities:** `classify_and_extract`, `investigate`, `plan_resolution`, `execute_step`, `compensate_step`, `verify_and_summarize`.
Each activity creates a UC agent, runs it in a sandbox, returns structured result.
**Test idea:** Can't easily test without LLM. Write the activity class with `@activity.defn`, verify it's importable. For integration testing, we'll test the full workflow.

### Step 2.6: Temporal Workflow (`order_resolution/workflows/workflow.py`)
**Workflow:** `OrderResolutionWorkflow` with signals (approval_decision, cancel), queries (get_state, get_plan, get_progress).
**Test idea:** Use Temporal's test environment (`temporalio.testing.WorkflowEnvironment`) to:
1. Run workflow with mock activities that return canned responses
2. Verify signal handling (send approval signal, verify workflow continues)
3. Verify query responses at each stage
4. Verify saga compensation on step failure

### Step 2.7: Config + CLI (`order_resolution/config.py`, `order_resolution/main.py`)
**Commands:** `worker`, `start`, `approve`, `query`, `benchmark`
**Test idea:** Verify CLI parses arguments correctly. Integration test: start dev server, run workflow end-to-end.

### Step 2.8: Fixtures
Sample ticket, orders, shipping data as JSON files.
**Test idea:** Validate each fixture loads and parses correctly.

### Step 2.9: Benchmark (`order_resolution/benchmark/`)
UC native resume vs Temporal resume comparison.
**Test idea:** Benchmark scripts run without error and produce comparison output.

---

## Phase 3: Example B - SLA Guardian (after Phase 2 complete)

### Step 3.1-3.6: Follow same pattern as Phase 2
- SLA-specific data types, services, tools
- Guardian cron workflow (continue-as-new) + per-ticket monitor (durable timers)
- Key test: Temporal test environment with time-skipping to verify durable timers fire correctly

---

## Phase 4: Example C - Onboarding Concierge (after Phase 3 complete)

### Step 4.1-4.7: Follow same pattern
- Multi-day workflow with continue-as-new
- Integration test child workflow (Docker sandbox)
- Key test: Simulate multi-day progression with Temporal time-skipping

---

## Testing Strategy

### Unit Tests (no external deps)
- All Pydantic model serialization round-trips
- All mock service CRUD operations
- All FunctionTool.run() with mock services
- CLI argument parsing
- TUI component rendering

### Integration Tests (require Temporal dev server)
- Workflow execution with mock activities
- Signal/query handling
- Saga compensation
- Durable timer behavior (time-skipping)
- Continue-as-new lifecycle

### E2E Tests (require LLM API + Temporal)
- Full workflow: ticket -> agent reasoning -> resolution
- Skippable via pytest marker `@pytest.mark.e2e`

## File Creation Order (Phase 1+2)

```
1.  examples/zendesk_task_agents/__init__.py
2.  examples/zendesk_task_agents/common/__init__.py
3.  examples/zendesk_task_agents/common/data_types.py
4.  examples/zendesk_task_agents/common/services/__init__.py
5.  examples/zendesk_task_agents/common/services/base.py
6.  examples/zendesk_task_agents/common/services/zendesk_mock.py
7.  examples/zendesk_task_agents/common/services/service_registry.py
8.  examples/zendesk_task_agents/common/tools/__init__.py
9.  examples/zendesk_task_agents/common/tools/zendesk_tools.py
10. examples/zendesk_task_agents/common/agent_helpers.py
11. examples/zendesk_task_agents/common/tui.py
12. examples/zendesk_task_agents/order_resolution/__init__.py
13. examples/zendesk_task_agents/order_resolution/workflows/__init__.py
14. examples/zendesk_task_agents/order_resolution/workflows/data_types.py
15. examples/zendesk_task_agents/order_resolution/services/__init__.py
16. examples/zendesk_task_agents/order_resolution/services/order_db_mock.py
17. examples/zendesk_task_agents/order_resolution/services/shipping_mock.py
18. examples/zendesk_task_agents/order_resolution/services/payment_mock.py
19. examples/zendesk_task_agents/order_resolution/tools/__init__.py
20. examples/zendesk_task_agents/order_resolution/tools/order_tools.py
21. examples/zendesk_task_agents/order_resolution/tools/shipping_tools.py
22. examples/zendesk_task_agents/order_resolution/tools/payment_tools.py
23. examples/zendesk_task_agents/order_resolution/instructions.py
24. examples/zendesk_task_agents/order_resolution/agent_def.py
25. examples/zendesk_task_agents/order_resolution/config.py
26. examples/zendesk_task_agents/order_resolution/workflows/activities.py
27. examples/zendesk_task_agents/order_resolution/workflows/workflow.py
28. examples/zendesk_task_agents/order_resolution/main.py
29. examples/zendesk_task_agents/order_resolution/fixtures/sample_ticket.json
30. examples/zendesk_task_agents/order_resolution/fixtures/sample_orders.json
31. examples/zendesk_task_agents/order_resolution/fixtures/sample_shipping.json
32. examples/zendesk_task_agents/order_resolution/benchmark/__init__.py
33. examples/zendesk_task_agents/order_resolution/benchmark/uc_native_resume.py
34. examples/zendesk_task_agents/order_resolution/benchmark/temporal_resume.py
35. examples/zendesk_task_agents/order_resolution/benchmark/compare.py
```
