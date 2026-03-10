# Temporal + UC Agent: Scale Benchmark Results

**Date:** 2026-03-10
**Workload:** 1,000 AgentTicketWorkflows (mock agent, single activity per ticket)
**Infrastructure:** Temporal dev server (single node, localhost), Python 3.14, macOS

---

## Throughput

| Metric | Value |
|--------|-------|
| Injection rate | 846 workflows/sec |
| Processing rate | 173 workflows/sec |
| Total time (1000 tickets) | 7.0s |

---

## Temporal Storage Per Workflow

| Metric | Value |
|--------|-------|
| Avg per workflow | **6.1 KB** |
| p50 | 6.2 KB |
| p95 | 6.9 KB |
| p99 | 6.9 KB |
| Events per workflow | 11 |
| Payload ratio | 92% application data / 8% Temporal overhead |
| Total for 1,000 workflows | 6.1 MB |

Each workflow has 11 events: WorkflowExecutionStarted, 2x WorkflowTaskScheduled/Started/Completed, ActivityTaskScheduled/Started/Completed, WorkflowExecutionCompleted.

---

## Query Performance

| Operation | Latency |
|-----------|---------|
| Single workflow query | 3.9 ms |
| 100 parallel queries | 224 ms (2.24 ms/query) |
| 1,000 parallel queries | 2,979 ms (2.98 ms/query) |
| list_workflows (3,100 total) | 244 ms |

Live dashboard polling 1,000 workflows is feasible at 3-5s refresh intervals.

---

## Simulated Token Usage (GPT-4o Pricing)

| Metric | Value |
|--------|-------|
| Avg tokens per ticket | **3,297** |
| Prompt tokens (% of total) | 1,782,225 (54%) |
| Completion tokens (% of total) | 1,514,600 (46%) |
| **Cost per ticket** | **$0.020** |
| Cost per 1K tickets | $19.60 |
| Cost per 10K tickets/day | $196/day ($5,880/month) |

Token simulation includes: system prompt (800), ticket content (word count * 1.3), tool calls (100 in + 200 out each), sandbox diagnostics (+500 for technical), memory lookup discount (-200), agent reasoning (400), final response (300).

### By Ticket Category

| Category | Count | Avg Tokens | Avg Cost | Notes |
|----------|-------|-----------|----------|-------|
| technical | 151 | 3,585 | $0.020 | Sandbox diagnostics add ~500 tokens |
| billing | 135 | 3,425 | $0.022 | Transaction lookup + refund reasoning |
| general | 55 | 3,534 | $0.020 | Unstructured tickets need more reasoning |
| crisis | 316 | 3,253 | $0.019 | Quick escalation path |
| shipping | 127 | 3,211 | $0.020 | Order + tracking lookup |
| feature | 65 | 3,273 | $0.019 | Quick triage to product |
| account | 151 | 2,982 | $0.018 | Simplest (password reset) |

---

## Cluster Sizing (7-Day Retention)

Based on measured 6.1 KB/workflow (mock agent). With real UC agent payloads (~15 KB/workflow), multiply storage by 2.5x.

| Daily Volume | Storage (mock) | Storage (real agent) | Recommendation |
|-------------|----------------|---------------------|----------------|
| 1K tickets/day | 43 MB | 105 MB | Single node, 1 GB memory |
| 10K tickets/day | 430 MB | 1.1 GB | Single node, 4 GB memory |
| 50K tickets/day | 2.1 GB | 5.3 GB | 3-node cluster |
| 100K tickets/day | 4.3 GB | 10.5 GB | 5-node cluster, dedicated persistence |

**Cost at scale (10K tickets/day, real agent):**
- Temporal storage: ~1.1 GB/week retained
- LLM cost: $196/day = $5,880/month
- Infrastructure: 3-node Temporal cluster ~$500-1,500/month (self-hosted) or Temporal Cloud pricing

---

## Key Takeaways

1. **Storage is cheap.** At 6 KB per workflow, even 50K tickets/day only needs 2 GB retained. The real cost driver is LLM tokens, not Temporal storage.

2. **Overhead scales with activity count, not ticket complexity.** Each activity adds ~1.7 KB of Temporal overhead (3 events). A single-activity workflow (AgentTicketWorkflow) has 8% overhead. An 8-activity workflow (OrderResolution) has 54% overhead. Fewer, larger activities are more storage-efficient.

3. **Query performance is good for live dashboards.** Single query: 4 ms. 1,000 parallel queries: 3s. A dashboard polling every 5s can monitor 1,000+ tickets in real-time.

4. **Token cost dominates.** At $0.02/ticket, LLM costs are ~100x the Temporal infrastructure cost. Optimizing prompts and using memory to skip redundant investigation is the highest-ROI optimization.

5. **Memory reduces token usage.** Tickets matching a previously resolved pattern skip investigation steps, saving ~500-800 tokens ($0.003-0.005/ticket). At 10K tickets/day with 30% memory hit rate, that's ~$15-50/day saved.

---

## How to Reproduce

```bash
cd zendesk-task-agents
temporal server start-dev
uv run python -m sla_guardian.benchmark_scale --tickets 1000 --seed 42 --sla-offset 10
```
