# Operational Truth Substrate

**Authored:** 2026-04-25
**Status:** CANONICAL
**Supersedes:** the report-first sequencing implied by `OVERWATCH_V2_REPORTS_ARCHITECTURE.md`. That document is preserved as the Phase 2 detail spec (the report catalog itself is unchanged); this document defines the substrate the catalog sits on.
**Aligns with:** `OVERWATCH_V2_SPECIFICATION.md` Invariant C ("Truth before framing"). This spec is the operational mechanism by which Invariant C becomes structurally enforceable rather than a behavioural rule.

---

## 1. Philosophy

> **Operational truth is the architectural primitive.** When the operator asks any question about the system, the answer must be grounded in evidence drawn from all available sources, correlated, and presented with citations. "I think" is unacceptable. "Here is what the data shows, here are the events that support this claim" is the standard.

Two quiet failure modes destroy operator trust in tools that reason about systems. The first is hallucination: confidently asserting things that are not in any data source. The second, more insidious, is partial truth: confidently asserting things based on the small subset of sources the tool happened to read, while the contradicting evidence sits unread in another source the tool could have queried but didn't.

Invariant C of the V2 spec ("hedged findings are surfaced explicitly; the reasoner does not produce conclusion-language until verification has been performed") addresses these as a behavioural rule. This document addresses them structurally: by ensuring every source is reachable through a tool, by making cross-source correlation a primitive operation, and by representing operational facts as a queryable graph rather than a stream of disjoint logs to grep.

### 1.1 The April 25 incident as worked example

On 2026-04-25, in a single working session, four separate diagnoses were made confidently and turned out to be wrong. Each traced to a specific category of data source that was not read before the diagnosis was offered:

| Wrong diagnosis | Source not read |
|---|---|
| "PR #11 IAM swap is complete" — confidently stated after a CloudTrail probe showed the new role active. | Production daemon logs, which were emitting 14+ `AccessDeniedException`/sec on `forgescaler/api` because the new role's `SecretsReadOverwatchNamespaceOnly` policy didn't cover that secret. The CloudTrail probe verified *the role swap*, not *the role's adequacy*. |
| "Pipeline-truth backend works; the failure must be elsewhere" — after grepping the routes file and confirming it was mounted in `server.py`. | The tool client's `DEFAULT_BASE_URL`. The backend was perfect; the client was calling `:8001` while the server bound `:9001`. The diagnosis read the server, not the client. |
| "aria-console is CLEAN — only operator traffic, vanity alias situation" — after enumerating ALB rules and target groups. | The aria-console FastAPI source code (no Host-header branching) AND the actual HTTP response bodies from both vanity domains. Either probe alone would have either confirmed CLEAN or escalated to COUPLED; neither was run before the verdict. |
| "Safe to destroy aria-platform-alb" (the implicit framing of an early Track-8 prompt) — based on memory that the ALB was V1-era. | The current ALB rules, which still routed `api.forgescaler.com` and `staging-api.forgescaler.com` to dedicated customer target groups. Destroying the ALB would have taken down the Forgewing customer API. Caught by the explicit "Step 1 audit before destruction" gate, not by intuition. |

Each was correctable in minutes once the missed source was read. Each was preventable in the first place if the question had been routed through a tool surface that mandates cross-source enumeration before producing a verdict. **This is what the substrate exists to make structurally impossible.**

### 1.2 Strategic implication

Capabilities built into Overwatch are exportable to Forgewing customer tenants. Every tool Echo gains for reading and correlating its own operational data is, with the same architecture and a different IAM scope, a tool a customer founder will eventually have for reading and correlating *their* operational data. The Operational Truth Substrate is therefore not a private convenience for the operator — it is the proving ground for Forgewing's eventual customer-facing capability ceiling. What works for Echo with one operator will work for ten thousand founders with the same architecture.

Practically: a customer founder asking "why did my deploy fail this morning?" should get the same kind of evidence-cited multi-source synthesis Echo gives Ian. The substrate makes this transferable.

---

## 2. The Three Layers

### Layer 1 — Raw data sources

Every source Echo must be able to read. The substrate is not "complete" until every source in this table has a tool with the matching IAM scope and a tested call path.

| Source | What it tells us | Status today | Phase |
|---|---|---|---|
| Source code (both repos) | What the system *is supposed to* do | Partial — `read_repo_file` exists for nexus-platform; aria-platform reachable through GitHub App since PR #18 | 0a |
| Git history | When code changed and why | Reachable via `gh` and `git log` from CCR sandbox; not yet a first-class tool | 0a |
| CloudTrail | Every AWS API action, by principal, with parameters | Manual `aws` CLI today; no tool wrapper | 0b |
| CloudWatch logs | Application + container stdout/stderr from every service | `read_cloudwatch_logs` tool exists but only for whitelisted log groups | 0b |
| CloudWatch metrics | CPU, memory, latency, errors as time series | `read_cloudwatch_metrics` exists; underused by reasoner | 0b |
| ALB access logs | Per-request edge detail (host, path, status, latency, cookie names hashed) | Available in S3 but no tool reads them | 0b |
| VPC flow logs | Network connectivity at the packet level | Enabled on V2 VPCs; no tool surface | 0b |
| ECS task state | Service health, deployment progress, task IDs | Wired (`query_aws` covers `ecs:Describe*`) | done |
| Step Functions execution history | Per-execution input/output/state-by-state | Wired via `query_pipeline_truth` (Track G) | done |
| Postgres state | OverwatchPostgres + customer ontology | Wired (`query_postgres`) | done |
| Neptune state | OverwatchGraph + Forgewing graph | Wired (`query_neptune`) | done |
| GitHub Actions logs | Customer CI runs and ours | Partial via `read_github` tool; no tool for run-log streaming yet | 0b |
| Customer tenant logs/state | Per-tenant deploy progress, Conversations, MissionTasks | Manual cross-account assume-role; no per-tenant tool | 0c |
| Bedrock invocation logs | Echo's own reasoner steps | CloudWatch but no enriched accessor | 0b |
| Cost Explorer | Spend per service, anomaly attribution | `ce:GetCostAndUsage` denied for reasoner role today (sibling gap from PR #11) | 0b |

The single biggest insight when assembling this table: **everything Echo could need is already in some AWS or Git surface.** Nothing has to be built or buffered or synthesized at this layer. Layer 1 is purely a wiring exercise — exposing the existing sources through a tool surface with the right IAM scope.

### Layer 2 — Synthesis primitives

The operations Echo performs over Layer 1 data. These are *primitives*: composable building blocks the reasoner uses to construct evidence-grounded answers.

**Cross-source correlation.** Given a timestamp T and a window W, return every event from every Layer 1 source within `[T-W, T+W]`. Example: `query_correlated_events(timestamp="2026-04-25T19:46:33Z", window_seconds=60)` returns the CloudTrail AccessDenied, the aria-console log line, the corresponding ALB request, and the ECS task health change in one structured response. Implementation: a parallel fan-out across Layer 1 tool calls with a normalizer that produces uniform `{source, timestamp, principal, resource, payload, citation}` records.

**Temporal drill-back.** Given a resource R and a time T, reconstruct the state of R at T. For an ECS service this is "what task definition was running", for an ALB it's "what listener rules were attached", for an IAM role it's "what policies, what trust". Implementation: CloudTrail event replay against a baseline + diffing engine. Cheap for stateful resources (CloudFormation `describe-stack-events` covers most), expensive for ephemeral (in-memory app state at T is unrecoverable; the substrate must surface "this is unknowable, the only available signal is the next periodic checkpoint" rather than fabricate).

**Causal chain reconstruction.** Given a target event E, find the most plausible chain of preceding events that explains it. Constrained breadth-first traversal of the Operational Graph (Layer 3) along `CAUSED` and `PRECEDED` edges, capped at a max-lookback time. Returns ranked chains with confidence scores per link. Example: "what caused the 14:39:35 ECS task failure?" returns the deploy event at 14:38:59 with the role policy change at 14:38:30 as direct evidence, ranked above the unrelated cron run at 14:35:00.

**Evidence enumeration.** Every claim Echo makes must be accompanied by one or more `{source, locator}` pairs. `locator` is whatever lets a human re-fetch the evidence: a CloudTrail eventId, a CloudWatch log stream + timestamp, a file path + line range, a git SHA + path, a Neptune node ID. The reasoner is structurally prevented from emitting conclusion-language without at least one citation.

**Confidence calibration.** Every Echo answer carries an explicit confidence band: `verified` (evidence directly observed), `inferred` (multi-step deduction from observed evidence), `hypothesis` (consistent with evidence but not entailed by it), `unknown` (no evidence either way). A `verified` answer with one citation is stronger than a `hypothesis` with ten. The UI surfaces these bands distinctly.

### Layer 3 — Operational Graph

This is the load-bearing innovation and the one piece of this spec that is genuinely research-territory.

Today's `OverwatchGraph` (Property 2 of the V2 spec) stores founder-level abstractions: `Decision`, `Hypothesis`, `Feature`, `BriefEntry`, `Pattern`. The Operational Graph extends the same primitive: every operational fact becomes a node, every causal/temporal/evidential relationship becomes an edge. They share the same Neptune database; the new node types live alongside the existing ones with their own labels.

#### Node types

| Label | Purpose |
|---|---|
| `DeployEvent` | One CFN/ECS/Lambda deployment attempt, status, commit, who triggered |
| `CloudTrailEvent` | One AWS API action, principal, resource, parameters digest |
| `ALBRequest` | One edge request bucket — host, path, status, cookie-name hash, latency |
| `CloudWatchAlarm` | One alarm state transition |
| `EchoInvestigation` | One operator question + the evidence chain Echo cited + the conclusion |
| `CodeChange` | One commit — SHA, files touched, diff summary, author, timestamp |
| `ConfigDrift` | One detected divergence between expected (CFN, source) and actual (live AWS) |
| `IngestRun` | One Forgewing pipeline run end-to-end |
| `PolicyDecision` | One Cognito / IAM policy evaluation — allow/deny, principal, action, resource |
| `Pattern` | An operational pattern Echo has learned (e.g., "PR-11-style namespace gap") |

Enumerable: ~20 in the canonical first cut; new node types added as new sources come online. Schema additions are migrations, not breaking changes (additive labels).

#### Edge types

| Edge | Meaning |
|---|---|
| `CAUSED` | A directly caused B; the edge carries the evidence locator that establishes causation |
| `PRECEDED` | A occurred before B within a defined causal window; weaker than CAUSED |
| `EVIDENCES` | Data point A supports claim B |
| `CONTRADICTS` | Data point A contradicts claim B; surfaced loudly to operator |
| `TRIGGERED_BY` | Operational event triggered by code change (links `DeployEvent` → `CodeChange`) |
| `INVESTIGATED_BY` | Investigation chain — Echo's own reasoning history |
| `MATCHED_PATTERN` | A given fact matches a known `Pattern`; carries similarity score |

#### Why this is innovative

Operational diagnostics in most production systems falls into one of two patterns. (a) Ad-hoc grep-through-logs by a senior engineer who knows where to look. The knowledge of *where to look* is not durable; when that engineer leaves, it goes with them. (b) Pre-built dashboards that answer the questions the dashboard author anticipated, and nothing else.

The Operational Graph turns the diagnostic operation into a graph traversal. *"Find the shortest path from this 5xx response back through `CAUSED` edges to a `CodeChange`"* becomes a tractable Cypher query rather than 90 minutes of cross-referencing CloudWatch with `git log` with `aws ce`. Cross-tenant patterns become traversals over the same graph: *"find all `DeployEvent` nodes with a `MATCHED_PATTERN` edge to `Pattern{kind: 'namespace_gap'}` across all tenants in the last 30 days"* surfaces a fleet-wide signal that no per-tenant dashboard would catch.

This idea has academic precedent — distributed systems provenance research (Pivot Tracing, Whodunit, Pip) explored similar graph-of-events models. None have shipped as an integrated developer-tools product. **This is open ground.**

The substrate is incremental. We do not need every node type and every edge type before the graph is useful. The graph is useful as soon as the first source ingests into it — a graph of just `CloudTrailEvent` nodes with `PRECEDED` edges is already strictly better than CloudTrail's native console for causal reasoning.

---

## 3. Build Sequence

Six phases in dependency order. The 0-series phases are the substrate; phases 1+ build capability on top. Total to V1 parity + scale-out + research-grade observability: **~30 hours of focused capability work**.

### Phase 0a — Codebase Index (~5 hours)

The system must be able to read its own source. Both repos.

**Tools delivered:**
- `read_repo_file(repo, path, ref?)` — returns file contents + git provenance metadata
- `search_codebase(query, repo?, glob?)` — full-text + symbol search across both repos
- `read_git_diff(commit_sha, file?)` — what changed in a commit
- `read_git_log(repo, path?, since?, author?)` — recent commits as structured records

**Acceptance:**
Echo answers "what does function X do, and when was it last modified?" with `file:line` citations for the implementation and a git SHA for the modification. Tested against five sample queries spanning both repos.

**Dependencies:** GitHub App auth (PR #18, landed). No new infra.

### Phase 0b — Cross-Source Log Index (~6 hours)

Every AWS observability surface becomes a tool.

**Tools delivered:**
- `read_cloudtrail(filter, time_range)` — structured CloudTrail lookup with helpers for common filter patterns
- `read_alb_logs(filter, time_range)` — reads S3-stored ALB access logs, parses, returns structured records
- `read_cloudwatch_logs(log_group, filter, time_range)` — generalized CW Logs reader replacing the per-group whitelist
- `read_cloudwatch_metrics(namespace, metric, time_range, dimensions)` — already exists, extend coverage
- `query_correlated_events(timestamp, window_seconds, sources?)` — fan-out across the above

**Acceptance:**
Echo answers "what happened across all systems between 14:00 and 14:30 today?" with one structured response covering CloudTrail + CloudWatch logs + ALB requests + metric anomalies, evidence-cited per row.

**Dependencies:** Resolve sibling IAM gaps from PR #11 (Cost Explorer, expanded CloudWatch Logs scope, ALB access log read on S3). Each is a small named policy in the style of `SecretsReadForgescalerApi`.

### Phase 0c — Cross-Tenant Read (~5 hours)

Echo can read from customer tenant resources without operator intervention.

**Tools delivered:**
- `read_customer_tenant_state(tenant_id)` — Tenant node + active Project + recent IngestRuns
- `read_customer_pipeline(tenant_id, time_range?)` — per-tenant pipeline state and history
- `read_customer_ontology(tenant_id, types?)` — slice of the customer's MissionBriefs / Decisions / Features
- `read_aria_conversations(tenant_id, since?)` — recent ARIA conversation history with the tenant

**Infra:** per-tenant `forgescaler-readonly-<tenant_id>` IAM role with cross-account trust to `overwatch-v2-reasoner-role`. Provisioned via a CFN template that takes the tenant list as a parameter. Tools assume the tenant-specific role via `sts:AssumeRole` per call; the role lifetime is the call duration.

**Acceptance:**
Echo answers "state of tenant X right now" with the Tenant + Project + last IngestRun + last Conversation in one structured response, sourced from cross-account reads, evidence-cited.

**Dependencies:** Phase 0b for the log-tool layer.

### Phase 0d — Operational Graph + Correlation (~6 hours)

The substrate goes from "tools to read sources" to "tools to traverse a graph of correlated facts".

**Schema work:**
- Postgres tables: `operational_nodes`, `operational_edges`, indexed on `(label, timestamp)` and `(source_node_id, edge_type)`
- Neptune projection: nightly batch job that materializes the high-traversal subset into the graph for fast traversal queries
- Migrations: additive label registration for each node type defined in §2 Layer 3

**Ingestion pipeline:**
- Layer 1 source events stream into `operational_nodes` via lightweight per-source ingestors. Initial coverage: `CloudTrailEvent`, `DeployEvent`, `EchoInvestigation`, `CodeChange`. Other node types added as their source ingests.
- A separate correlator pass over each new batch creates `PRECEDED` edges within the configured causal window. `CAUSED` edges are written by Echo's investigations (not auto-derived) — causation is a claim with evidence, not an inference.

**Tools delivered:**
- `traverse_operational_graph(start_node, edge_types, max_depth)` — Cypher traversal over the graph
- `find_causal_chain(target_event, max_lookback)` — ranked chains as described in §2 Layer 2
- `record_investigation(question, evidence, conclusion)` — write-side, creates an `EchoInvestigation` node and `INVESTIGATED_BY` edges

**Acceptance:**
Given a target event, `find_causal_chain` returns at least one ranked chain with all links evidence-cited and a confidence band. Tested against three reconstruction scenarios drawn from the April 25 incident.

**Dependencies:** Phases 0a/0b/0c for the source data; the graph is empty until they ingest.

### Phase 1 — Fleet Sweep Service (~3 hours)

Cron-driven Lambda runs hourly. Iterates all tenants. Calls Layer 1 capture tools. Writes structured records to Postgres `tenant_state_snapshots`. Emits classifier events for state changes (driving the Critical Findings report and the Operational Graph's `ConfigDrift` nodes).

Detail in `OVERWATCH_V2_REPORTS_ARCHITECTURE.md` Phase 1; this prompt does not change the design, only positions it as Phase 1 of the substrate-first sequence.

### Phase 2 — Reports API + UI (~6 hours)

Twelve reports per `OVERWATCH_V2_REPORTS_ARCHITECTURE.md`. The catalog is unchanged — this spec changes only that the reports now operate *on the substrate* (Layer 1+2+3 tools) rather than directly against AWS. This makes reports cheap to add: a new report is a new Cypher / Postgres query plus a UI card, not a new ingestion pipeline.

### Phase 3 — V1 Diagnose Parity (~4 hours)

Three-tier investigation: pattern match → multi-source correlation → causal-chain traversal. The `Diagnose this tenant` button. Detail in the reports doc.

### Phase 4 — Pattern-Based Action (~6 hours)

Cross-tenant pattern recognition over the Operational Graph. Recommended Actions Queue. Pattern Action Plans report. The `MATCHED_PATTERN` edge type from §2 Layer 3 is the substrate primitive that makes this tractable.

### Phase 5 — Mutation Tools (~separate sprint day)

KMS-gated. Day 4 work as previously planned. Approval-token issue/verify already shipped (PR #5 / Track F).

---

## 4. The Twelve Reports

The full catalog is preserved verbatim in [`OVERWATCH_V2_REPORTS_ARCHITECTURE.md`](OVERWATCH_V2_REPORTS_ARCHITECTURE.md). That document is now positioned as the **Phase 2 detail spec** of this substrate document — the report catalog itself is unchanged; only its place in the build sequence shifts (the reports now sit on top of the substrate's Layer 1+2+3 tools rather than reading AWS directly).

When in conflict, this document supersedes. The reports doc's three-layer "Capture → Reports → Synthesis" decomposition becomes a subset of this document's Layer 1+2+3 + Phase 1+2: the reports doc's "Capture layer" is this doc's Layer 1 + Phase 1; "Reports layer" is Phase 2; "Synthesis layer" is the Echo reasoner using Layer 2 primitives over Layer 3.

---

## 5. Strategic Implications

### 5.1 Forgewing transfer

Each capability built into Overwatch is exportable to a Forgewing customer tenant with the same architecture and a customer-scoped IAM boundary. Specifically:

- **Layer 1 tools** become customer-facing `read_*` tools scoped to the customer's own AWS account. A founder asking "what's running in my account?" gets the same `read_cloudtrail`/`read_cloudwatch_logs` surface Echo gets.
- **Layer 2 primitives** are AWS-account-shape-agnostic; the same correlation, drill-back, and causal-chain operations work over any customer's data.
- **Layer 3 graph** becomes per-tenant. Each customer ontology gains an Operational subgraph. Cross-tenant patterns at the Overwatch level become per-tenant patterns at the customer level — same primitive, narrower scope.

This collapses the Forgewing roadmap question "what should we build for customers next?" into a much more tractable form: **whatever capability we build for Echo to manage Forgewing, the customers will inherit eventually.** Overwatch becomes the proving ground for Forgewing's customer-facing capability ceiling. Capability investment compounds: a tool built once serves both tenants of users.

### 5.2 Competitive positioning

Most developer-tools companies build dashboards. Some build observability platforms (Datadog, Honeycomb, Signoz) — these are sophisticated query-and-visualize layers over time-series and logs, but the underlying model is "queries against indexed events". A few research systems have explored provenance-graph models for distributed systems (Pivot Tracing, Whodunit, Pip), but none have shipped as an integrated developer-tools product capability.

The Operational Graph is genuine differentiation if executed. The hard part is not the graph schema or the Neptune queries — those are tractable. The hard part is the discipline of every operational fact going through a tool that ingests it into the graph, and every Echo answer carrying citations back to the graph. Discipline cannot be retrofitted; it has to be the substrate from the start.

### 5.3 Falsifiability

The thesis is testable. By **Q1 2027**, either:

- **(a)** an operator can answer any operational question — *"why did tenant X's deploy fail at 14:30?"*, *"what changed in the platform's IAM in the last week?"*, *"which patterns are most active this month?"* — in **under 60 seconds with evidence**, or
- **(b)** they cannot.

The April 25 incident is the explicit baseline. If we still have hours-long diagnostic mysteries six months from now with this substrate built, the thesis is wrong and we should retire it. The success criterion is durable, observable, and adversarial in the right way.

---

## Appendix — Methodology lessons

Three lessons consolidated by this spec:

- **L37** *(prior, recorded in V1→V2 transition):* V2 must preserve V1 functionality before adding V2-only features. This spec operationalizes that as Phase 3 (V1 Diagnose parity) being a substrate dependency, not an afterthought.
- **L38** *(prior, from `OVERWATCH_V2_REPORTS_ARCHITECTURE.md`):* Reports are substrate; conversational AI is layer. This spec extends: reports themselves are a layer on top of a deeper substrate (Layer 1+2+3).
- **L39** *(new):* **Operational truth is an architectural primitive, not a feature.** Every Echo answer must cite evidence. The substrate exists so this is structurally enforceable rather than an aspiration. The April 25 incident is the worked example that motivates the lesson; the lesson exists to prevent its recurrence.

---

## Appendix — Phase 0e instances

### OperatorFeature: Ontology Capture Loop

The first canonical OperatorFeature instance, encoding the ontology
capture loop's health surface. Six Sprint 15 Day 3-4 substrate-truth
catches ship as either HealthSignals or EvidenceQueries:

- HealthSignal `capture_loop_accepted_24h` — Bug 4 closure proxy: count
  of accepted Decision/Hypothesis rows in 24h with all type-required
  fields populated. RED at 0.
- HealthSignal `source_turn_id_linkage_pct_24h` — Loop 2/3 substrate
  quality. Today's smoke surfaced 100% NULL.
- HealthSignal `pending_stale_rate_pct_24h` — UI workflow stall +
  CrossServiceWriteAtomicity orphan visibility.
- HealthSignal `extraction_quality_pct_24h` — silent classifier
  degradation (Bedrock regression, prompt overflow, Lambda staleness).
- EvidenceQuery `Postgres proposal counts by type and status (24h)` —
  drift surface (Postgres side).
- EvidenceQuery `Neptune ontology node counts (cumulative, by type)` —
  drift surface (Neptune side); compare with Postgres for IAM-regression
  detection of the same shape as 2026-04-27.
- EvidenceQuery `Recent classifier proposals (4h, all tenants)` —
  near-realtime workflow visibility.
- EvidenceQuery `Neptune Decisions and Hypotheses with required fields
  (recent)` — orphan / atomicity-debt visibility.

Two concerns defer pending new evidence kinds: schema round-trip
integrity (file/git read) and Lambda staleness vs. nexus/mechanism1/
(AWS-API + git correlation). Both are follow-ups to this PR; the
deferred enum values are documented in the instance's module
docstring.

The falsifiability statement requires at least one fully-populated
Decision or Hypothesis per 24h with all required fields populated by
the classifier. Sweep automation (scheduled execution writing
FeatureReports) deferred to a follow-up so signal definitions can be
refined against real production data before automating.

Consumed today via `read_holograph(feature_id="ontology_capture_loop")`
from any Echo-using surface; before this OperatorFeature, the same
diagnostic state surfaced over hours of manual investigation across
classifier_proposals SQL, Neptune Cypher, and CloudWatch logs.
