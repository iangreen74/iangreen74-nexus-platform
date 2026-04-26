# Phase 0b — Cross-Source Log Index — Design Note

**Authored:** 2026-04-26
**Spec source:** `docs/OPERATIONAL_TRUTH_SUBSTRATE.md` line 145
**Original prompt:** "Track A — Phase 0b — Cross-Source Log Index"
**Override applied:** four adjustments per user reply (path correction, drop Track A prereq checks, reframe unblock language, dogfood anchor change).

## Spec deliverables vs. current main state

The spec lists five tools for Phase 0b:

| Spec tool | Current main state | Action |
|---|---|---|
| `read_cloudtrail(filter, time_range)` | not present | **NEW** |
| `read_alb_logs(filter, time_range)` | not present | **NEW** |
| `read_cloudwatch_logs(log_group, filter, time_range)` | already exists, `nexus/overwatch_v2/tools/read_tools/cloudwatch_logs.py`. Spec says "generalized CW Logs reader replacing the per-group whitelist" — **the existing tool has no whitelist** (accepts arbitrary `log_group`). | no change needed |
| `read_cloudwatch_metrics(...)` | already exists, `nexus/overwatch_v2/tools/read_tools/overwatch_metrics.py` | no change in scope of this PR |
| `query_correlated_events(timestamp, window_seconds, sources?)` | not present | **NEW** |

**Net new tools: 3.** Tool count delta: 15 → 18. Hits the prompt's 18-or-19 target.

## Naming

Spec uses `read_cloudtrail`, `read_alb_logs`. Original prompt suggested `read_cloudtrail_events`, `read_alb_access_logs`. **Substrate truth wins — follow spec.** Tool names: `read_cloudtrail`, `read_alb_logs`, `query_correlated_events`.

## IAM gaps to close

Spec footnote says: "Resolve sibling IAM gaps from PR #11 (Cost Explorer, expanded CloudWatch Logs scope, ALB access log read on S3)."

State today:
- ✅ `cloudtrail:LookupEvents` in `OverwatchV2ReasonerReadAccess` Sid `CloudTrailRead`
- ❌ No S3 read on `overwatch-v2-alb-logs-418295677815` — **MUST add for `read_alb_logs`**
- ❌ No `ce:GetCostAndUsage` — out of scope for these three tools, defer
- ✅ CloudWatch Logs scope — already broad enough (the tool is unrestricted, spec was wrong about the whitelist)

**Action:** add a single Sid `S3ReadAlbAccessLogs` to `infra/overwatch-v2/03-iam-reasoner-role.yml`, scoped to `arn:aws:s3:::overwatch-v2-alb-logs-418295677815/*` and `arn:aws:s3:::overwatch-v2-alb-logs-418295677815`.

## Audit pattern

Spec doesn't explicitly require an audit log group for operator-side reads. Cross-tenant audit (`/overwatch-v2/cross-tenant-audit`) exists for tenant-boundary detection — different concern.

The Track F registry already has `_emit_audit` that tries `from nexus.overwatch_v2.audit import emit_action_event` and silently no-ops if the module is missing. **The module is missing today.** Two paths:

- **A) Build `nexus/overwatch_v2/audit.py`** that writes to the registry's intended `overwatch_v2.action_events` sink. Generalizes audit for ALL tools, not just Phase 0b. Single audit path.
- **B) Per-tool inline audit calls** to a Phase-0b-specific `/overwatch-v2/operator-substrate-audit` log group. Boilerplate per tool.

**Choosing A.** Substrate honesty requires the registry's audit hook to actually work, not silently fail. Build `audit.py` to write to `/overwatch-v2/operator-substrate-audit` (90-day retention). All tools registered through the registry — including the 15 already shipped — start auditing immediately on this PR's deploy. This is the right structural fix.

CFN: new template `16-operator-substrate-audit-logs.yml`. Same pattern as `15-cross-tenant-audit-logs` (the parallel-track stack — note: that stack is named `overwatch-v2-cross-tenant-audit`, my Phase 0c was redundant).

## Correlation primitive — Option A (explicit tool)

Spec lists `query_correlated_events(timestamp, window_seconds, sources?)`. Original prompt's Option A.

Implementation: takes a center timestamp + window, fans out to the read-* tools across selected sources, returns a flat time-sorted array of unified-shape records:

```python
{
  "source": "cloudtrail" | "alb" | "cloudwatch_logs",
  "timestamp": "2026-04-26T13:30:12Z",
  "summary": "<one-line description>",
  "principal": "<who or null>",
  "resource": "<arn or null>",
  "raw": {...},          # source-specific payload
  "locator": {...},      # how to re-fetch this record
}
```

`sources` param defaults to all three. `window_seconds` capped at 600 (10 min). Hard cap 500 events total returned.

## Bounds and safety caps

Same conventions as existing tools:
- Time-range default: last 60 min for read tools, ±30s for correlator
- Time-range cap: 24h for read tools (matches `cloudwatch_logs`), 10 min for correlator
- Result cap: 500 events per call (CloudTrail), 1000 lines (ALB), 500 (correlator total)
- Truncation envelope: `{events: [...], truncated: true|false, ...}`

## ALB log parser

ALB access log format is space-separated, ~30 fields, documented in AWS docs. Will implement a small parser that returns:
```
{type, timestamp, alb, client_ip, target_ip, request_processing_time, target_processing_time,
 response_processing_time, elb_status_code, target_status_code, received_bytes, sent_bytes,
 request, user_agent, ssl_cipher, ssl_protocol, target_group_arn, trace_id, domain_name,
 chosen_cert_arn, matched_rule_priority, request_creation_time, actions_executed,
 redirect_url, error_reason, target_port_list, target_status_code_list, classification, ...}
```

Will skip GZIP decompress + line parse for any object that's >5MB (defense against pathological logs).

## Files to touch

```
infra/overwatch-v2/03-iam-reasoner-role.yml        (+ S3ReadAlbAccessLogs Sid)
infra/overwatch-v2/16-operator-substrate-audit-logs.yml   (NEW, ~25 lines)
nexus/overwatch_v2/audit.py                        (NEW, ~50 lines)
nexus/overwatch_v2/tools/read_tools/read_cloudtrail.py    (NEW, ~150 lines)
nexus/overwatch_v2/tools/read_tools/read_alb_logs.py      (NEW, ~190 lines)
nexus/overwatch_v2/tools/read_tools/query_correlated_events.py  (NEW, ~150 lines)
nexus/overwatch_v2/tools/read_tools/_registration.py      (+ 3 register lines)
tests/test_overwatch_v2_phase_0b_cloudtrail.py
tests/test_overwatch_v2_phase_0b_alb_logs.py
tests/test_overwatch_v2_phase_0b_correlate.py
tests/test_overwatch_v2_phase_0b_audit.py
tests/test_overwatch_v2_phase_0b_dogfood.py        (integration — Phase 0b's own deploy timeline)
docs/PHASE_0B_DESIGN.md                            (this doc, committed)
```

Existing brittle tool-count tests will need bumping (15 → 18) — same pattern as Phase 0c hit.

## Hard stops still in force

- spec missing → already verified ✅
- spec implies infrastructure we don't have → none for these three tools, only S3 read which exists
- ALB access logs not in S3 → 135 fresh objects ✅
- CloudTrail disabled → IsLogging:true ✅
- dogfood test fails → do not merge

## Dogfood test (override Step 7 anchor)

After this PR's `aws ecs update-service` rolls out, ask Echo:

> "What happened across all systems in the 15 minutes following Phase 0b's own deploy? Show CloudTrail + ALB + container logs correlated by time."

Expected: Echo invokes `read_cloudtrail`, `read_alb_logs`, `read_cloudwatch_logs`, then composes `query_correlated_events` over the three results. Returns a unified timeline showing the deploy's CloudTrail events + ALB target health changes + container startup logs, evidence-cited. Audit log shows the four tool calls.

If passes: Phase 0b is operationally proven on its own birth.
