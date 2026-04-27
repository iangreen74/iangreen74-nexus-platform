# External State Inventory

Search key: **`UncodifiedExternalState`** (per `docs/CANONICAL.md` Phase 2
debt register).

This runbook tracks every piece of state our systems depend on that
lives **outside** this repo's IaC. The 8th substrate-truth save is
always in something we forgot to look at — this file is how we shrink
that blind-spot surface deliberately rather than waiting for the next
silent failure.

Every PR that introduces a new external integration must add an entry
here. Every entry must answer:

- **Where** the state actually lives
- **Who** can change it (vendor UI, AWS console, registrar, etc.)
- **What** breaks if it drifts, and what the failure mode looks like
- **How** to verify it currently matches our assumptions
- **When** it was last verified

Entries live forever. Resolved drifts are not deleted — they're
annotated with the resolving PR so the lesson sticks.

---

## CloudTrail `account-security-trail` S3 sink

- **Discovered:** 2026-04-26 (Phase 0b.5 substrate-truth check on
  Sprint 15 Day 2)
- **Status:** RESOLVED 2026-04-26 — bucket recreated under CFN stack
  `account-security-trail-bucket` via `infra/account-security-trail-bucket.yaml`
- **External state location (pre-fix):** the trail's `S3BucketName`
  pointed at `account-security-cloudtrail-dc5f419b`, a bucket that had
  been deleted out from under the trail's reference. The bucket was
  not codified anywhere — no CFN stack owned it; no Terraform module
  owned it; no repo file referenced its name. Likely created via bare
  CLI at some point in pre-history.
- **External state location (post-fix):** bucket and bucket policy
  codified in `infra/account-security-trail-bucket.yaml`, owned by the
  CFN stack `account-security-trail-bucket`. The trail itself
  (`account-security-trail`) is **still uncodified** — recreating it
  via CFN was deliberately deferred to keep this PR's blast radius
  bounded. Trail-as-CFN is the structural follow-up below.
- **Failure mode (silent):** trail keeps `IsLogging=true` and reports
  `LatestDeliveryError=NoSuchBucket` on
  `cloudtrail get-trail-status`. CloudWatch metrics and any alarm
  watching `LatestDeliveryError` would have caught this — none
  existed. Recent CloudTrail history within the 90-day LookupEvents
  window remained queryable throughout the incident; only S3-archived
  long-tail history was affected.
- **Permanent gap (unrecoverable):** ~4 months of audit history,
  2025-09-27 → ~2026-01-26, falls outside both the broken S3 sink
  and the LookupEvents window. Cannot be recovered.
- **Verification (2026-04-27 02:25 UTC):** `LatestDeliveryError=null`,
  `LatestDelivery=2026-04-27T02:25:17Z` — first successful delivery
  in ~7 months.
- **Lesson:** CloudTrail trails MUST be CFN-managed. Bare-CLI trail
  creation produces this exact failure mode silently. The trail's
  configuration is intact for 7 months while the destination bucket
  vanishes — neither AWS nor any default alarm tells you.
- **Structural follow-ups (Sprint 16+ candidates):**
  1. Codify `account-security-trail` itself in CFN — currently the
     trail and its CW Logs / KMS / multi-region settings are still
     uncodified. Bucket alone is half the picture.
  2. Add a substrate check to the dogfood-runner that asserts every
     CloudTrail trail in the account has a successful S3 delivery
     within the last 24h. Would have caught this 7 months earlier.
  3. CloudWatch alarm on `CloudTrailDeliveryErrors` metric (one per
     trail) so the operator hears about S3 sink failures within
     minutes, not a quarterly substrate audit.
