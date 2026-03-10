# PII Separation Research: Temporal + Redis

**Date:** 2026-03-10
**Status:** Research complete, design decision made (PayloadCodec approach)
**Requirement:** GDPR/CCPA compliance (right-to-erasure) + security boundary (PII never stored in Temporal)

---

## Problem Statement

Temporal stores workflow event history as an immutable append-only log. This history contains all activity inputs, outputs, signal data, and workflow results. Our workflows embed PII (customer names, emails, order details, personalized messages) throughout these payloads. Temporal's immutability means:

1. PII cannot be deleted from event history (violates GDPR right-to-erasure)
2. PII is visible to anyone with Temporal cluster access (security boundary concern)
3. Temporal Cloud or managed deployments expose PII to third-party infrastructure

---

## PII Inventory (What's Stored Today)

### By Workflow Type

| Workflow | PII in Inputs | PII in Activity Outputs | PII in Queries | PII in Results |
|----------|--------------|------------------------|----------------|----------------|
| **AgentTicketWorkflow** | customer_name in metadata | customer_message, resolution_summary with names | get_state returns full AgentTicketState | Full AgentTicketState |
| **OrderResolutionWorkflow** | ticket_id only | ExtractedIntent (customer name+summary), InvestigationResult (order/shipping/payment), customer_message | get_progress returns full WorkflowProgress | Full WorkflowProgress |
| **TicketMonitorWorkflow** | ticket_id + deadline | UrgencyClassification (customer_tier), SentimentReport (key_phrases), drafted_escalation_message | get_monitor_state, get_sentiment_report, get_escalation_history | Full TicketMonitorState |
| **OnboardingWorkflow** | customer_id, customer_name in OnboardingPlan | CheckinResponse (customer feedback), training materials | get_onboarding_status | Full OnboardingState |

### PII-Carrying Pydantic Models

**File: `sla_guardian/workflows/data_types.py`**
- `AgentResolution.resolution_summary` - contains customer names
- `AgentResolution.customer_message` - full personalized message
- `AgentResolution.tool_calls[].args_summary` - may contain emails
- `AgentTicketState.customer_name` - direct PII
- `UrgencyClassification.customer_tier` - indirect PII
- `SentimentReport.key_phrases` - quoted customer text
- `SentimentReport.actionable_insights` - may reference customers
- `EscalationAction.drafted_message` - customer context

**File: `order_resolution/workflows/data_types.py`**
- `ExtractedIntent.summary` - contains customer name and tier
- `InvestigationResult.order_details` - full order with customer ID
- `InvestigationResult.payment_details` - transaction amounts
- `InvestigationResult.shipping_details` - delivery addresses
- `ResolutionStep.params` - customer IDs, order IDs
- `StepResult.result_data` - refund confirmations with customer refs
- `WorkflowProgress.customer_message` - personalized message

**File: `onboarding_concierge/workflows/data_types.py`**
- `OnboardingPlan.customer_id`, `customer_name` - direct PII
- `CheckinResponse.customer_id`, `response_text` - customer feedback
- `Milestone.result_data` - verification details, satisfaction scores

**File: `common/data_types.py`**
- `CustomerInfo.name`, `email` - direct PII
- `TicketInfo.requester` - full CustomerInfo embedded

### Additional PII Vectors (Easy to Miss)

1. **Signal data** - `ApprovalDecision.reviewer` (name), `.notes` (free text)
2. **Error messages** - failed activities store exceptions in history, may contain customer names
3. **Search attributes** - if custom search attributes include PII
4. **Memos** - workflow memos are stored in event history
5. **`workflow.side_effect()`** results are recorded in history

---

## Temporal Sandbox Restrictions (Verified from SDK Source)

**Source:** `.venv/lib/python3.14/site-packages/temporalio/worker/workflow_sandbox/_restrictions.py`

Verified by inspecting `SandboxRestrictions.invalid_module_members_default`:

### What IS Blocked in Workflow Code

| Module | Restriction | Mechanism |
|--------|------------|-----------|
| `datetime.datetime.now/utcnow/today` | Specific methods blocked | `use={'now', 'utcnow', 'today'}` |
| `random.*` (all functions) | All functions blocked | `use={full list of 25 functions}` |
| `socket.*` | All uses blocked at runtime | `use={'*'}, RUNTIME_ONLY` |
| `http.client.*`, `http.server.*` | All uses blocked | `use={'*'}` |
| `urllib.request.*` | All uses blocked | `use={'*'}` |
| `subprocess.*` | All uses blocked | `use={'*'}` |
| `os.*` | All uses blocked at runtime | `use={'*'}, RUNTIME_ONLY` |
| `time.time/sleep/monotonic/*` | All functions blocked | `use={full list}` |
| `uuid.uuid1/uuid4` | Blocked at runtime | `use={'uuid4', 'uuid1'}, RUNTIME_ONLY` |
| `threading.*` | All uses blocked at runtime | `use={'*'}, RUNTIME_ONLY` |

### What IS Allowed

- Importing any module (imports pass; runtime calls are checked)
- Pure computation (math, string operations, Pydantic model construction)
- `workflow.now()` (deterministic replacement for `datetime.now()`)
- `workflow.execute_activity()` and `workflow.execute_local_activity()`
- `workflow.start_child_workflow()`
- `workflow.wait_condition()`, `workflow.sleep()`
- `workflow.side_effect()` (one-time non-deterministic calls, recorded in history)

### Implication for Redis

**You CANNOT call Redis directly from workflow code** because Redis clients use `socket.*` which is blocked at runtime. The import succeeds but any `redis.get()` or `redis.set()` fails with `RestrictedWorkflowAccessError`.

**Activities CAN call Redis** -- they run outside the sandbox.

---

## Approaches Evaluated

### Approach 1: PII Extraction / Scanning (Rejected)

Scan payloads for PII patterns (names, emails) and replace with tokens.

**Rejected because:**
- Fragile regex-based detection misses PII in unstructured text
- Adds latency to every serialization
- Customer messages written by the agent contain PII in unpredictable formats
- False positives would corrupt data

### Approach 2: Full Separation -- All Data in Redis (Considered)

Temporal only stores opaque reference keys. ALL content in Redis.

**Trade-offs:**
- **Pro:** Zero PII in Temporal. Dead-simple rule. Easy right-to-erasure.
- **Con:** Temporal UI shows only opaque keys (useless). Dashboard requires Redis for any display. Activities must resolve refs for inter-activity data flow.
- **Con:** Every activity start adds a Redis round-trip to resolve input refs.

### Approach 3: Selective Separation -- PII in Redis, Metrics in Temporal (Considered)

Activities return split results: PII-free metrics to Temporal, full data to Redis.

**Trade-offs:**
- **Pro:** Temporal UI shows useful operational data. Dashboard works partially without Redis.
- **Con:** Requires judgment call on every field: "is this PII?" Maintenance burden on every code change. One mistake leaks PII.

### Approach 4: PayloadCodec (Selected)

Temporal's `PayloadCodec` intercepts at the serialization boundary. Transparent to all workflow and activity code.

**How it works:**
```
Activity returns AgentResolution (with PII)
    → PayloadCodec.encode() on the WORKER
        → Scans Pydantic model for PII fields (by field name, not content)
        → Stores PII fields in Redis under deterministic key
        → Replaces PII field values with ref tokens in the payload
        → Sends sanitized payload to Temporal server

Temporal server stores: { "customer_name": "pii:ref:abc123", "status": "resolved", ... }

Another worker picks up next activity from task queue
    → PayloadCodec.decode() on THAT WORKER
        → Detects ref tokens in payload
        → Batch-fetches from Redis
        → Replaces refs with real PII values
        → Activity receives full data with PII
```

**Why this is the best approach:**
1. **Zero code changes** to existing workflows, activities, or data types
2. **Field-name based**, not content-scanning (we know which fields carry PII from the inventory above)
3. **Task queue works normally** -- codec runs on every worker, Redis is shared
4. **Right-to-erasure:** delete Redis keys, Temporal refs become dead links
5. **Deterministic key generation:** `pii:{workflow_id}:{field_path}` ensures same ref on encode/decode
6. **Batch-friendly:** `decode()` can MGET multiple keys in one Redis call

**Key requirement:** All workers must connect to the same Redis instance (or cluster).

---

## PayloadCodec Design (Selected Approach)

### Configuration

```python
# List of Pydantic field names that contain PII
PII_FIELDS = {
    "customer_name", "customer_message", "resolution_summary",
    "email", "name", "requester", "order_details", "shipping_details",
    "payment_details", "key_phrases", "actionable_insights",
    "drafted_message", "response_text", "customer_id",
    "notes",  # in ApprovalDecision
}

# Codec is configured on Client.connect()
client = await Client.connect(
    address,
    data_converter=dataclasses.replace(
        pydantic_data_converter,
        payload_codec=PIIRedactingCodec(redis_client=redis, pii_fields=PII_FIELDS),
    ),
)
```

### Encode Flow (Activity Result → Temporal)

```python
class PIIRedactingCodec(PayloadCodec):
    async def encode(self, payloads: Sequence[Payload]) -> list[Payload]:
        result = []
        for payload in payloads:
            data = json.loads(payload.data)
            pii_extracted = self._extract_pii(data, path="")
            if pii_extracted:
                # Store in Redis with workflow context
                for ref, value in pii_extracted.items():
                    await self.redis.set(ref, json.dumps(value), ex=TTL)
            result.append(Payload(data=json.dumps(data).encode(), metadata=payload.metadata))
        return result
```

### Decode Flow (Temporal → Activity Input)

```python
    async def decode(self, payloads: Sequence[Payload]) -> list[Payload]:
        result = []
        for payload in payloads:
            data = json.loads(payload.data)
            refs = self._find_refs(data)
            if refs:
                # Batch fetch from Redis
                values = await self.redis.mget(refs)
                self._inject_pii(data, dict(zip(refs, values)))
            result.append(Payload(data=json.dumps(data).encode(), metadata=payload.metadata))
        return result
```

### Right-to-Erasure

```python
async def erase_customer(customer_id: str):
    # Secondary index: find all workflow refs for this customer
    ticket_ids = await redis.smembers(f"pii:customer:{customer_id}:tickets")
    for ticket_id in ticket_ids:
        # Delete all PII keys for this ticket
        keys = await redis.keys(f"pii:{ticket_id}:*")
        if keys:
            await redis.delete(*keys)
    # Delete the index itself
    await redis.delete(f"pii:customer:{customer_id}:tickets")
```

---

## PayloadCodec Coverage (Verified from SDK Source)

**Source:** `.venv/.../temporalio/bridge/_visitor.py` (PayloadVisitor base class)
**Source:** `.venv/.../temporalio/worker/_workflow.py` (_CommandAwarePayloadCodec, _command_aware_visitor)

The PayloadCodec runs **outside the workflow sandbox, on the worker process**. A `PayloadVisitor` traverses ALL protobuf messages (both incoming activations and outgoing commands) and applies encode/decode to every payload it finds.

### Complete Coverage Map

| Event Type | Codec Runs? | Visitor Method | Direction |
|-----------|-------------|---------------|-----------|
| Workflow started (inputs) | YES | `workflow_activation_InitializeWorkflow` | decode (incoming) |
| Workflow completed (result) | YES | `workflow_commands_CompleteWorkflowExecution` | encode (outgoing) |
| Workflow failed (error) | YES | `workflow_commands_FailWorkflowExecution` | encode (outgoing) |
| Activity scheduled (inputs) | YES | `workflow_commands_ScheduleActivity` | encode (outgoing) |
| Activity completed (result) | YES | `activity_result_Success` via `ResolveActivity` | decode (incoming) |
| Activity failed (error) | YES | `activity_result_Failure` via `ResolveActivity` | decode (incoming) |
| Local activity scheduled | YES | `workflow_commands_ScheduleLocalActivity` | encode (outgoing) |
| Signal received | YES | `workflow_activation_SignalWorkflow` | decode (incoming) |
| Signal sent to external | YES | `workflow_commands_SignalExternalWorkflowExecution` | encode (outgoing) |
| Query response | YES | `workflow_commands_QuerySuccess` | encode (outgoing) |
| Child workflow started | YES | `workflow_commands_StartChildWorkflowExecution` | encode (outgoing) |
| Child workflow result | YES | `child_workflow_Success` via `ResolveChildWorkflowExecution` | decode (incoming) |
| Memo | YES | `temporal_api_common_v1_Memo` | both |
| Search attributes | YES | `temporal_api_common_v1_SearchAttributes` | both |
| Failure payloads | YES | `temporal_api_failure_v1_Failure` | both |

**Key finding:** The codec catches EVERY payload in the Temporal data path. Nothing escapes -- including signal data, error messages, query responses, and memos. This means a PII-redacting codec is comprehensive without any additional interception points.

### How the Codec Is Applied

```
Workflow sandbox produces raw payload (PayloadConverter → JSON bytes)
    ↓
Worker's _CommandAwarePayloadCodec wraps the payload
    ↓
PayloadCodec.encode() runs ON THE WORKER (outside sandbox, CAN call Redis)
    ↓
Encoded payload sent to Temporal server (PII replaced with refs)
    ↓
Temporal server stores sanitized payload in event history
    ↓
Another worker receives the payload from task queue
    ↓
PayloadCodec.decode() runs ON THAT WORKER (resolves refs from Redis)
    ↓
Decoded payload passed to activity/workflow (full PII restored)
```

### Latency Impact

| Operation | Redis Calls | Estimated Latency |
|-----------|------------|-------------------|
| Activity scheduled (encode) | 1 SET per PII field (or 1 MSET for all fields in payload) | <1ms |
| Activity completed (encode) | 1 SET per PII field (or 1 MSET) | <1ms |
| Activity input decoded | 1 MGET for all refs in payload | <1ms |
| Query response (encode) | 1 SET per PII field (or 1 MSET) | <1ms |
| Signal decoded | 1 MGET for all refs | <1ms |

For OrderResolution workflow (8 activities): ~16 Redis round-trips total, ~16ms overhead across the entire workflow lifetime.

**MSET/MGET explanation:** Redis supports batch operations. `MSET key1 val1 key2 val2` sets multiple keys in one network round-trip (~1ms) instead of individual SET calls (N ms). The codec can batch all PII fields from a single payload into one MSET. However, the codec's `encode()` is called per-payload (not per-workflow), so batching only works within a single payload, not across multiple payloads from different workflows.

### Interaction with Task Queues

The codec is configured on the Temporal `Client`. Every worker that connects with the same client configuration gets the same codec. When Worker A encodes PII to Redis and Worker B (on a different machine) decodes it, both read/write the same Redis instance. This is the same shared-service pattern as Temporal itself.

**Requirement:** All workers MUST connect to the same Redis instance (or Redis cluster). This is enforced by passing the same Redis connection configuration to the codec on every worker.

---

## References

### Our Code Files
- `sla_guardian/workflows/data_types.py` - PII-carrying models (AgentResolution, AgentTicketState, etc.)
- `order_resolution/workflows/data_types.py` - PII-carrying models (ExtractedIntent, WorkflowProgress, etc.)
- `onboarding_concierge/workflows/data_types.py` - PII-carrying models (OnboardingPlan, CheckinResponse)
- `common/data_types.py` - CustomerInfo, TicketInfo
- `sla_guardian/config.py` - Temporal connection config (where codec would be added)
- All `main.py` files - Client.connect() calls where codec is configured

### Temporal SDK Files (in .venv)
- `.venv/.../temporalio/worker/workflow_sandbox/_restrictions.py` - Sandbox restriction definitions
- `.venv/.../temporalio/converter.py` - PayloadCodec base class
- `.venv/.../temporalio/contrib/pydantic.py` - Pydantic data converter we use

### Temporal Documentation References
- PayloadCodec: https://docs.temporal.io/dataconversion#payload-codec
- Custom Data Converter: https://docs.temporal.io/dataconversion#custom-data-converter
- Workflow Sandbox: https://docs.temporal.io/develop/python/sandboxing
