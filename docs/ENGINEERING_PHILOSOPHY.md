# Engineering Philosophy — Forgewing

**Status:** Distilled from Sprint 12-13 practice. April 23, 2026.
**Audience:** New engineers onboarding, investors assessing methodology.

---

## Core Conviction

Correctness over speed. Velocity within methodology, not instead of it.

We ship fast — Sprint 13 Day 3 deployed 6 phases, 3 mechanisms, 5 Lambdas, 3 Postgres migrations, and a UI overhaul in one session. But every piece was tested, verified, and committed with a clear audit trail. Speed came from methodology, not from skipping steps.

---

## Principles

### 1. Diagnose Before Fix

Never fix a bug you haven't diagnosed. Read the error. Read the code. Understand *why* it failed, not just *that* it failed. The fix that addresses the symptom instead of the cause creates a new bug downstream.

Example: Sprint 13 Day 2, the deploy cycle died silently. First diagnosis: `asyncio.CancelledError` is a `BaseException`, not `Exception`, on Python 3.9+. The `except Exception` handler never caught it. Fix targeted the root cause, not the symptom.

### 2. Read Files Before Writing Code

Never fabricate code from memory. Use grep, read the actual file, find the actual pattern. The codebase is the source of truth, not your mental model of it.

This prevents: wrong import paths, non-existent function names, stale API signatures, schema mismatches. Every one of these has caused a production failure when skipped.

### 3. The Ultra-Debugging Loop

For every bug: find → write synthetic test that fails → fix → verify synthetic passes → the bug cannot return. The synthetic test is the permanent guardrail.

Example: orphan nodes in Neptune. Found 9,388. Purged them. Then wrote `journey_orphan_zero_invariant` — a synthetic that fails if ANY orphan nodes exist. The purge was a one-time fix; the synthetic is permanent protection.

### 4. CFN Drift Hygiene

CloudFormation templates must match live AWS state. When you create a resource via CLI (because CFN failed, or for speed), immediately document the drift and close it by patching the template.

Sprint 13 Day 3 closed three separate CFN drifts:
- Phase 6 Lambdas missing Code blocks (added S3 references)
- Mechanism 2 template missing entirely (authored to match live)
- Mechanism 3 missing Neptune permissions (added inline policy)

### 5. File-Size Discipline

Production Python files: 200 lines max (CI-enforced as warning). This is architectural pressure toward small, focused modules. When a file approaches 200, extract a helper module rather than cramming.

Test files are exempt. The limit applies to production code only.

### 6. Graceful Fallback at Every Layer

No single failure should cascade. Every data read returns a sensible default on error. Every classifier call is wrapped in try/except. Every graph query returns `[]` on failure.

The prompt assembly pipeline demonstrates this: if Neptune is down, ARIA still generates a response (with "listen and learn" guidance instead of founder context). If tone markers fail, the prompt skips the emotional weather section. If summaries fail, the memory section is empty. ARIA always responds.

### 7. Lambda Packaging

Use `cp` not `touch` for Python `__init__.py` files. An empty `__init__.py` is valid Python but won't export module-level symbols that the handler imports. This caused a production ImportError on Mechanism 3's first invocation.

Verify after packaging: `unzip -l package.zip | grep __init__` — all sizes should be non-zero for submodules that re-export.

### 8. Surface-Area Minimization

Don't build endpoints you don't need yet. Every endpoint is attack surface, maintenance burden, and API contract. Build the library, test it, and only expose an HTTP surface when a consumer exists.

### 9. No Facades

Report success rate, not success count. "8 successes" means nothing without "out of how many?" Partial evidence is partial — never present it as complete.

---

## Operational Patterns

### One-Paste-One-Terminal

Ian's wrist injury means prompts are authored as complete Markdown files, pasted once. Each prompt is self-contained: context, guardrails, steps, success criteria, anti-goals. No back-and-forth clarification during execution.

This produces better engineering: the prompt author must think through edge cases *before* execution, not discover them midway.

### Parallel Terminals

Non-overlapping repos run in parallel terminals. Track A (nexus-platform) and Track B (aria-platform) execute simultaneously when their work is independent. Merge points are explicit.

### Verification Before Commit

Every commit follows: write code → run tests → verify line counts → check for regressions → commit. Never commit untested code. Never skip the full test suite.

---

## Cross-References

- `CANONICAL.md` — locked strategic decisions
- `docs/OVERWATCH.md` — the operational system these principles built
- `infra/lambdas/README.md` — packaging lesson documented at point of failure
