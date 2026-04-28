# Mechanism 1 — inline classifier

## What it does

Reads a single conversation turn (founder ↔ ARIA), runs three Haiku
prompts against it (one per ontology type — Feature / Decision /
Hypothesis), and produces zero or more `ProposalCandidate` rows that
land in `classifier_proposals` Postgres pending founder disposition.

The classifier is invoked from the `ontology-conversation-classifier`
Lambda (`infra/lambdas/ontology_conversation_classifier/handler.py`)
on every `conversation_turn` EventBridge event with `role=user`.

## Per-type extraction (post-PR-B Bug 4 rigorous fix)

The ontology service requires different fields per type (see
`nexus/ontology/schema.py`'s `REQUIRED_TYPE_FIELDS`). The classifier
extracts the schema-required fields for each type via dedicated Haiku
prompts.

### Feature
- **Required by schema**: `name`, `description`, `project_id`
- **Extracted by prompt**: `title` → `name`, `summary` → `description`
  (the renames happen in `aria-platform/aria/proposals/payload.py`'s
  `build_ontology_payload`); `project_id` is event-detail metadata.
- **Status**: works (has worked since `feature.txt` was written; the
  rename coincidence carried it).

### Decision (NEW in PR-B)
- **Required by schema**: `name`, `context`, `choice_made`, `reasoning`,
  `decided_at`, `decided_by`
- **Extracted by prompt**: title (→name), summary, reasoning,
  choice_made, decided_at, decided_by, plus optional
  alternatives_considered.
- **Defaults applied at classifier layer**:
  - `decided_at` → current UTC ISO timestamp when Haiku omits (within
    seconds of message-publish, sufficient for proposal-time capture)
  - `decided_by` → `"founder"` when Haiku omits (default for first-
    person commitments; explicit role/name when attributed)
- **alternatives_considered**: emitted as comma-separated string by
  Haiku, or `null` when no alternatives were named in the turn.

### Hypothesis (NEW end-to-end in PR-B — was completely broken)
- **Required by schema**: `statement`, `why_believed`,
  `how_will_be_tested`
- **Extracted by prompt**: title, summary, reasoning, statement,
  why_believed, how_will_be_tested.
- **Defaults**: `how_will_be_tested` is the most-likely-omitted field;
  the prompt instructs Haiku to suggest a reasonable falsifiability
  test based on the statement when the founder didn't propose one.
  System-suggested tests are flagged in the summary so the founder
  knows it's a suggestion.

## Code map

| Concern | File |
|---|---|
| Haiku prompts (one per type) | `nexus/mechanism1/prompts/{decision,feature,hypothesis}.txt` |
| `ProposalCandidate` dataclass + `extract()` | `nexus/mechanism1/classifier.py` |
| `enqueue_proposal` + `list_pending` + `_fetch_candidate` (persistence) | `nexus/mechanism1/proposals.py` |
| `dispose` (accept/edit/reject) + ontology-service call | `nexus/mechanism1/disposition.py` |
| HTTP routes (`/api/classifier/*`) | `nexus/mechanism1/api.py` |
| Tone classification (separate concern) | `nexus/mechanism1/tone.py`, `tone_store.py` |
| Lambda entry point | `infra/lambdas/ontology_conversation_classifier/handler.py` |
| Postgres schema | migrations 003, 012, 014, 016 |

`proposals.py` and `disposition.py` were split in PR-B (Bug 4 rigorous
fix) when the per-type column expansion would have pushed the combined
file over the 200-line CI invariant. The boundary is "persistence vs
state-mutation": persistence stays in `proposals.py`; the ontology-side
state-mutation path lives in `disposition.py`.

## ProposalCandidate dataclass shape

```
candidate_id, tenant_id, project_id, object_type,
title, summary, reasoning, confidence, source_turn_id,
context,                                                # migration 014
choice_made, decided_at, decided_by, alternatives_considered,  # 016 — Decision
statement, why_believed, how_will_be_tested,                   # 016 — Hypothesis
```

Per-type fields are `None` for non-matching `object_type` rows. A
Hypothesis row has `choice_made = decided_at = decided_by = None`; a
Decision row has `statement = why_believed = how_will_be_tested = None`.
Postgres column constraints are nullable per migration 016.

## Length caps (Postgres + payload bounding)

`nexus/mechanism1/classifier.py` `_FIELD_CAPS`:

| Field | Cap (chars) |
|---|---|
| context | 1000 (migration 014 / `CONTEXT_MAX_CHARS`) |
| choice_made | 1000 |
| alternatives_considered | 1000 |
| statement | 2000 |
| why_believed | 2000 |
| how_will_be_tested | 2000 |
| title | 200 (existing) |
| summary | 2000 (existing) |
| reasoning | 1000 (existing) |

Caps are upper bounds — typical values are far below. The cap exists so
a runaway Haiku response can't bloat Postgres or the ontology payload.

## Lambda redeploy required

Classifier code changes (prompt rewrites, dataclass additions, INSERT
path updates) take effect in production **only after the
`ontology-conversation-classifier` Lambda redeploys.** The Lambda has
no automated CI/CD path today — manual `aws lambda
update-function-code` is the only mechanism.

After PR-B merges to `main`:
- `:latest` ECR image rebuilds with new code
- Operator runs:
  ```
  cd /tmp/build && cp -r ~/nexus-platform/nexus . && \
    cp ~/nexus-platform/infra/lambdas/ontology_conversation_classifier/handler.py . && \
    zip -r /tmp/lambda.zip handler.py nexus/ && \
    aws lambda update-function-code \
      --function-name ontology-conversation-classifier \
      --zip-file fileb:///tmp/lambda.zip --region us-east-1
  ```
- Verify `LastModified` and `CodeSha256` change

PR-D in the Bug 4 rigorous fix sequence closes this manual-redeploy
gap by adding a Lambda-deploy job to `.github/workflows/deploy.yml`.

## Real-Haiku validation

Prompt iteration in PR-B was based on prompt-engineering principles
(field-by-field guidance, worked examples, default rules) — not on
observed Haiku output, because Bedrock invocations from the dev
workstation are blocked by IAM (the role with Bedrock perms is the
Lambda's role, reachable only from the Lambda).

Real-Haiku validation happens at Lambda smoke time post-deploy:

1. Send a Decision-shaped message via metanym; verify
   `classifier_proposals` row has all 6 required Decision fields
   populated (non-NULL `choice_made`, `decided_at`, `decided_by`).
2. Send a Hypothesis-shaped message; verify all 3 required Hypothesis
   fields populated.
3. Click Accept; verify ontology-service POST returns 200 (not 400).

If any required field extracts unreliably in production, iterate on
the prompt. The prompt files are not code — they're operator-tunable
configuration.

## Refs

- `migrations/014_classifier_proposals_context.sql` — context column
- `migrations/016_classifier_proposals_bug4_columns.sql` — Decision /
  Hypothesis columns added in PR-A
- `nexus/ontology/schema.py` — per-type `REQUIRED_TYPE_FIELDS`
- `aria-platform/aria/proposals/payload.py` — outgoing-payload
  assembly (PR-C will branch this per type)
- `/tmp/bug4_rigorous_findings.md` — substrate read that motivated PR-B
