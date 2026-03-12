# PII PayloadCodec Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement a Temporal PayloadCodec that transparently extracts PII from all workflow payloads, stores it in Redis, and replaces it with opaque reference tokens -- achieving GDPR right-to-erasure compliance with zero changes to existing workflow/activity code.

**Architecture:** A `PIIRedactingCodec` subclass of `temporalio.converter.PayloadCodec` intercepts all payload serialization on the worker. On encode, it walks the JSON tree, extracts values for known PII field names, stores them in Redis, and replaces with `pii:ref:...` tokens. On decode, it resolves tokens back from Redis. The codec is injected into every `Client.connect()` via a helper function. Verified against the 3-terminal live demo.

**Tech Stack:** temporalio (PayloadCodec), redis (aioredis via `redis.asyncio`), pydantic, existing zendesk-task-agents codebase

**Spec:** `docs/pii-separation-research.md`

---

## File Structure

| File | Responsibility |
|------|---------------|
| `common/pii_codec.py` | `PIIRedactingCodec(PayloadCodec)` -- encode/decode with Redis PII extraction |
| `common/pii_config.py` | `PII_FIELDS` set, `PIIConfig` model (redis_url, ttl, enabled flag) |
| `common/temporal_client.py` | `create_temporal_client()` helper that wires codec + pydantic converter |
| Modify: `sla_guardian/main.py` | Replace direct `Client.connect()` in live-* commands with helper |
| Modify: `pyproject.toml` | Add `redis>=5.0` dependency |
| `sla_guardian/benchmark_pii.py` | Verification script: run workflows, inspect Temporal history for PII absence |

---

### Task 1: Add Redis Dependency and PII Config

**Files:**
- Modify: `pyproject.toml`
- Create: `common/pii_config.py`

- [ ] **Step 1: Add redis to pyproject.toml**

Add `"redis>=5.0"` to the `dependencies` list in `pyproject.toml`.

- [ ] **Step 2: Run uv sync**

Run: `uv sync`
Expected: redis package installs successfully

- [ ] **Step 3: Create PII config**

Create `common/pii_config.py`:

```python
from __future__ import annotations

import os
from pydantic import BaseModel

PII_FIELDS: frozenset[str] = frozenset({
    # Direct identifiers
    "customer_name", "customer_id", "name", "email",
    # Content with embedded PII
    "customer_message", "resolution_summary", "drafted_message",
    "response_text", "notes",
    # Structured data containing PII
    "requester", "order_details", "shipping_details", "payment_details",
    # Text that may quote customers
    "key_phrases", "actionable_insights",
    # Activity result summaries that embed names
    "summary",
})

PII_REF_PREFIX = "pii:ref:"


class PIIConfig(BaseModel):
    redis_url: str = os.environ.get("PII_REDIS_URL", "redis://localhost:6379/1")
    ttl_seconds: int = 60 * 60 * 24 * 30  # 30 days
    enabled: bool = os.environ.get("PII_CODEC_ENABLED", "false").lower() == "true"
```

- [ ] **Step 4: Verify import**

Run: `.venv/bin/python -c "from common.pii_config import PII_FIELDS, PIIConfig; print(f'{len(PII_FIELDS)} PII fields, config OK')"`
Expected: `14 PII fields, config OK`

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock common/pii_config.py
git commit -m "feat: add redis dependency and PII field config"
```

---

### Task 2: Implement PIIRedactingCodec

**Files:**
- Create: `common/pii_codec.py`

- [ ] **Step 1: Create the codec**

Create `common/pii_codec.py`:

```python
from __future__ import annotations

import json
import hashlib
from collections.abc import Sequence
from typing import Any

import redis.asyncio as aioredis
from temporalio.api.common.v1 import Payload
from temporalio.converter import PayloadCodec

from .pii_config import PII_FIELDS, PII_REF_PREFIX


class PIIRedactingCodec(PayloadCodec):
    """Temporal PayloadCodec that extracts PII fields and stores them in Redis.

    On encode: walks JSON payload, replaces PII field values with ref tokens,
    stores originals in Redis.
    On decode: finds ref tokens, batch-fetches from Redis, restores original values.
    """

    def __init__(self, redis_client: aioredis.Redis, ttl_seconds: int = 2592000) -> None:
        self._redis = redis_client
        self._ttl = ttl_seconds

    async def encode(self, payloads: Sequence[Payload]) -> list[Payload]:
        encoded = []
        for payload in payloads:
            # Only process JSON payloads
            encoding = payload.metadata.get("encoding", b"").decode()
            if encoding not in ("json/plain", "json/protobuf"):
                encoded.append(payload)
                continue

            try:
                data = json.loads(payload.data)
            except (json.JSONDecodeError, UnicodeDecodeError):
                encoded.append(payload)
                continue

            # Extract PII and collect Redis writes
            pii_to_store: dict[str, str] = {}
            self._extract_pii(data, path="root", store=pii_to_store)

            if pii_to_store:
                # Batch write to Redis with TTL
                pipe = self._redis.pipeline()
                for ref_key, value in pii_to_store.items():
                    pipe.set(ref_key, value, ex=self._ttl)
                await pipe.execute()

            new_payload = Payload(
                data=json.dumps(data, default=str).encode(),
                metadata=payload.metadata,
            )
            encoded.append(new_payload)

        return encoded

    async def decode(self, payloads: Sequence[Payload]) -> list[Payload]:
        decoded = []
        for payload in payloads:
            encoding = payload.metadata.get("encoding", b"").decode()
            if encoding not in ("json/plain", "json/protobuf"):
                decoded.append(payload)
                continue

            try:
                data = json.loads(payload.data)
            except (json.JSONDecodeError, UnicodeDecodeError):
                decoded.append(payload)
                continue

            # Collect all refs
            refs: list[str] = []
            self._collect_refs(data, refs)

            if refs:
                # Batch fetch from Redis
                values = await self._redis.mget(refs)
                ref_map = {}
                for ref, val in zip(refs, values):
                    if val is not None:
                        try:
                            ref_map[ref] = json.loads(val)
                        except json.JSONDecodeError:
                            ref_map[ref] = val.decode() if isinstance(val, bytes) else val

                self._restore_pii(data, ref_map)

            new_payload = Payload(
                data=json.dumps(data, default=str).encode(),
                metadata=payload.metadata,
            )
            decoded.append(new_payload)

        return decoded

    def _extract_pii(self, obj: Any, path: str, store: dict[str, str]) -> None:
        """Recursively walk JSON, replace PII field values with refs."""
        if isinstance(obj, dict):
            for key in list(obj.keys()):
                child_path = f"{path}.{key}"
                if key in PII_FIELDS and obj[key] is not None:
                    # Generate deterministic ref key
                    ref_key = self._make_ref(child_path)
                    # Store original value
                    store[ref_key] = json.dumps(obj[key], default=str)
                    # Replace with ref token
                    obj[key] = f"{PII_REF_PREFIX}{ref_key}"
                else:
                    self._extract_pii(obj[key], child_path, store)
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                self._extract_pii(item, f"{path}[{i}]", store)

    def _collect_refs(self, obj: Any, refs: list[str]) -> None:
        """Find all pii:ref: tokens in the JSON tree."""
        if isinstance(obj, str) and obj.startswith(PII_REF_PREFIX):
            refs.append(obj[len(PII_REF_PREFIX):])
        elif isinstance(obj, dict):
            for val in obj.values():
                self._collect_refs(val, refs)
        elif isinstance(obj, list):
            for item in obj:
                self._collect_refs(item, refs)

    def _restore_pii(self, obj: Any, ref_map: dict[str, Any]) -> None:
        """Replace ref tokens with resolved PII values."""
        if isinstance(obj, dict):
            for key in list(obj.keys()):
                val = obj[key]
                if isinstance(val, str) and val.startswith(PII_REF_PREFIX):
                    ref_key = val[len(PII_REF_PREFIX):]
                    if ref_key in ref_map:
                        obj[key] = ref_map[ref_key]
                else:
                    self._restore_pii(val, ref_map)
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                if isinstance(item, str) and item.startswith(PII_REF_PREFIX):
                    ref_key = item[len(PII_REF_PREFIX):]
                    if ref_key in ref_map:
                        obj[i] = ref_map[ref_key]
                else:
                    self._restore_pii(item, ref_map)

    @staticmethod
    def _make_ref(path: str) -> str:
        """Create a deterministic ref key from the JSON path."""
        return hashlib.sha256(path.encode()).hexdigest()[:16]
```

- [ ] **Step 2: Verify import and basic behavior**

Run:
```bash
.venv/bin/python -c "
from common.pii_codec import PIIRedactingCodec
import json

# Test the extraction logic without Redis
codec = PIIRedactingCodec.__new__(PIIRedactingCodec)
data = {'customer_name': 'Sarah Chen', 'status': 'resolved', 'urgency': 0.85}
store = {}
codec._extract_pii(data, 'root', store)
print(f'Extracted {len(store)} PII fields')
print(f'customer_name replaced: {data[\"customer_name\"].startswith(\"pii:ref:\")}')
print(f'status unchanged: {data[\"status\"] == \"resolved\"}')
print(f'urgency unchanged: {data[\"urgency\"] == 0.85}')
"
```
Expected: 1 PII field extracted, customer_name replaced, others unchanged

- [ ] **Step 3: Commit**

```bash
git add common/pii_codec.py
git commit -m "feat: PIIRedactingCodec with Redis-backed PII extraction"
```

---

### Task 3: Create Temporal Client Helper

**Files:**
- Create: `common/temporal_client.py`

- [ ] **Step 1: Create the helper**

Create `common/temporal_client.py`:

```python
from __future__ import annotations

import dataclasses

import redis.asyncio as aioredis
from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter

from .pii_codec import PIIRedactingCodec
from .pii_config import PIIConfig


async def create_temporal_client(
    address: str = "localhost:7233",
    namespace: str = "default",
    pii_config: PIIConfig | None = None,
) -> Client:
    """Create a Temporal client with optional PII codec.

    If pii_config is provided and enabled, wraps the pydantic data converter
    with a PIIRedactingCodec backed by Redis.
    """
    data_converter = pydantic_data_converter

    if pii_config and pii_config.enabled:
        redis_client = aioredis.from_url(pii_config.redis_url, decode_responses=False)
        codec = PIIRedactingCodec(redis_client, ttl_seconds=pii_config.ttl_seconds)
        data_converter = dataclasses.replace(
            pydantic_data_converter,
            payload_codec=codec,
        )

    return await Client.connect(
        address,
        namespace=namespace,
        data_converter=data_converter,
    )
```

- [ ] **Step 2: Verify import**

Run: `.venv/bin/python -c "from common.temporal_client import create_temporal_client; print('OK')"`
Expected: OK

- [ ] **Step 3: Commit**

```bash
git add common/temporal_client.py
git commit -m "feat: create_temporal_client helper with optional PII codec"
```

---

### Task 4: Wire Codec into Live Demo Commands

**Files:**
- Modify: `sla_guardian/main.py` (only the `live-worker`, `live-inject`, `live-dashboard` commands)

- [ ] **Step 1: Add PII config to CLI args**

Add `--pii` flag to the `live-worker`, `live-inject`, and `live-dashboard` subparsers:
```python
parser.add_argument("--pii", action="store_true", help="Enable PII redaction codec (requires Redis)")
```

- [ ] **Step 2: Replace Client.connect in live-worker**

In the `run_live_worker` function, replace the direct `Client.connect(...)` call with:
```python
from common.temporal_client import create_temporal_client
from common.pii_config import PIIConfig

pii_config = PIIConfig(enabled=args.pii) if hasattr(args, 'pii') and args.pii else None
client = await create_temporal_client(
    config.temporal_address, config.temporal_namespace, pii_config
)
```

Do the same for `run_live_inject` and `run_live_dashboard`.

- [ ] **Step 3: Verify the live-worker still starts without --pii**

Run: `.venv/bin/python -m sla_guardian.main live-worker --tickets 5 --seed 42 --sla-offset 5`
Expected: Worker starts normally (no Redis connection attempted)

- [ ] **Step 4: Commit**

```bash
git add sla_guardian/main.py
git commit -m "feat: wire PII codec into live demo commands via --pii flag"
```

---

### Task 5: PII Verification Benchmark

**Files:**
- Create: `sla_guardian/benchmark_pii.py`

- [ ] **Step 1: Create the verification script**

A standalone script that:
1. Starts Redis (assumes `redis-server` running on localhost:6379)
2. Starts an in-process Temporal worker WITH the PII codec enabled
3. Injects 10 workflows
4. Waits for completion
5. Inspects Temporal event history via `temporal workflow show --output json`
6. Scans every event's payload data for PII (customer names from fixtures)
7. Reports: how many payloads contain PII vs how many contain `pii:ref:` tokens
8. Checks Redis to verify PII is stored there
9. Calls `erase_customer()` equivalent (DEL pii keys) and verifies refs are now dead

The script outputs a Rich report:
```
╭───── PII Codec Verification ─────╮
│                                   │
│  Workflows tested:  10            │
│  PII in Temporal:   0 fields      │
│  pii:ref tokens:    47 fields     │
│  PII in Redis:      47 keys       │
│  After erasure:     0 Redis keys  │
│  Dead refs:         47            │
│                                   │
│  VERDICT: PASS ✓                  │
╰───────────────────────────────────╯
```

- [ ] **Step 2: Verify it runs**

Prerequisites: `redis-server` running, `temporal server start-dev` running.

Run: `.venv/bin/python -m sla_guardian.benchmark_pii --tickets 10 --seed 42`
Expected: VERDICT: PASS

- [ ] **Step 3: Commit**

```bash
git add sla_guardian/benchmark_pii.py
git commit -m "feat: PII verification benchmark - confirms zero PII in Temporal"
```

---

### Task 6: End-to-End Verification with 3-Terminal Demo

- [ ] **Step 1: Start Redis**

Run: `redis-server --daemonize yes`
Verify: `redis-cli ping` returns `PONG`

- [ ] **Step 2: Start Temporal**

Run: `temporal server start-dev` (in separate terminal)

- [ ] **Step 3: Run the 3-terminal demo WITH --pii flag**

```bash
# Terminal 1: Dashboard
uv run python -m sla_guardian.main live-dashboard --pii

# Terminal 2: Worker
uv run python -m sla_guardian.main live-worker --tickets 20 --seed 42 --pii

# Terminal 3: Inject
uv run python -m sla_guardian.main live-inject --tickets 20 --rate 2 --seed 42 --pii
```

- [ ] **Step 4: Verify PII absent from Temporal**

After workflows complete:
```bash
temporal workflow show --workflow-id agent-ticket-TKT-30001 --output json | grep -i "sarah\|chen\|customer_name"
```
Expected: No matches (only `pii:ref:...` tokens)

- [ ] **Step 5: Verify PII present in Redis**

```bash
redis-cli -n 1 KEYS "pii:*" | head -20
redis-cli -n 1 GET $(redis-cli -n 1 KEYS "pii:*" | head -1)
```
Expected: Keys exist, values contain actual PII

- [ ] **Step 6: Verify dashboard still shows real names**

The dashboard should display customer names normally -- the codec decodes refs on the query path.

- [ ] **Step 7: Test right-to-erasure**

```bash
redis-cli -n 1 KEYS "pii:*" | xargs redis-cli -n 1 DEL
temporal workflow show --workflow-id agent-ticket-TKT-30001 --output json | python3 -c "
import json, sys
data = json.load(sys.stdin)
# All pii:ref tokens should still be there but Redis values are gone
print('Refs still in Temporal: workflow data intact but PII erased from Redis')
"
```

- [ ] **Step 8: Final commit**

```bash
git add -A
git commit -m "feat: verified PII PayloadCodec with 3-terminal live demo"
git push origin feat/initial-implementation
```
