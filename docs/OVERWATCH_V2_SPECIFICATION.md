# OVERWATCH V2 — ARCHITECTURE & STRATEGIC SPECIFICATION

## Embedded Software Engineering Intelligence for VaultScaler

**Status:** Canonical. Approved by Ian Green, 2026-04-24.
**Supersedes:** all prior Overwatch capability planning.
**Purpose:** complete specification sufficient to begin and complete construction without further architectural decisions.
**Audience:** the engineer (human or AI) executing construction.

---

# PART I — STRATEGIC FOUNDATION

## 1. WHY THIS DOCUMENT EXISTS

VaultScaler is building Forgewing — a compounding intelligence platform whose moat is an operational-memory ontology that accrues per customer over years. The product itself is an AI engineering co-founder named ARIA. The company is one founder (Ian Green, CEO, 55%) and one co-founder (Ben, President, 45%) with no engineering hires. Engineering execution today happens through a chain of translation: Ian → Claude (chat) → Claude Code (local terminal) → local repo → AWS API surface → AWS ground truth.

That chain produces a structural failure mode. Each layer summarizes for the layer above. Information loses fidelity at every translation. On 2026-04-24, this chain produced four wrong framings in sequence about a single diagnostic question (whether the v2 deploy pipeline was healthy). Each framing was generated on top of an unverified premise. Each was retracted only after deeper inspection. The pattern is not a one-day failure; it is a structural property of multi-hop translation chains where each hop must summarize.

The architectural response is to dispose of the chain. Replace it with a single-hop interface: Ian → Overwatch V2 → AWS ground truth. Overwatch V2 is the embedded engineering intelligence that lives inside AWS, has direct SDK access to every service, has cross-account IAM to customer accounts, has its own graph database for self-knowledge, and has write-path tools for code generation, commits, deploys, and verification.

This document specifies that system completely. Reading it should be sufficient to build it.

## 2. THE STRATEGIC COMMITMENT

### 2.1 What was decided

On 2026-04-24 at approximately 14:00 local time, Ian and Claude made the following strategic commitment:

1. **Build Overwatch V2** as the primary engineering surface. The local Claude Code terminal chain becomes a fallback / emergency tool, not the default working interface.

2. **Continue the Forgewing frontend track in parallel.** The two tracks run simultaneously, not sequentially. Overwatch V2 construction does not pause frontend completion; frontend completion does not delay Overwatch V2 construction.

3. **Slip the design partner launch from June 9 to late June / early July**, accepting two-to-three weeks of calendar slip to gain a working surface that can be trusted.

These three commitments are canonical. They are not relitigated in ordinary work. Challenging any of them requires explicit founder-level unlock.

### 2.2 Why these commitments are correct

**The cost of not doing this is hidden but compounding.** The translation chain produces wrong framings at a measurable rate. On 2026-04-24, that rate was four per single critical question across five hours. If this is the steady-state rate, every critical engineering question costs a multiple of the time it would cost to answer through direct AWS access. Compounded over a six-week sprint, the cost is weeks of lost velocity and an unknown number of undetected wrong conclusions that ship to production.

**The architectural insight is that ground truth is one AWS API call away.** Overwatch already runs inside AWS. Overwatch already has direct SDK access. Overwatch already has 30+ capabilities, 512+ tests, a deploy pipeline, and cross-account IAM. The hard part is already built. What remains is wiring a reasoner with tool access to that infrastructure and giving it a chat surface.

**The compounding insight is the most important.** Overwatch V2 is not a tool that is built to enable Forgewing's construction. It IS Forgewing, pointed at our own engineering work as its first customer. Every capability we build into Overwatch V2 — code generation, deploy verification, ontology reasoning, conversational diagnosis, learned-pattern reuse — is a capability the customer-facing Forgewing product needs. They are the same product class. The work compounds. Ian's framing, recorded verbatim because it should not be lost in summary:

> "Isn't it a little bit like literally having a full software company working for you? It can work for us 24/7. And I think that we will surprise ourselves with the speed at which we can deliver this, because we're building on an existing product, aren't we? Overwatch is a product. And I think that the more powerful that we make this, in the end, what's going to happen is that Forgewing itself will be a better product that we build faster. So what we're unlocking here is the speed at which we can deliver Forgewing."

The two-week investment in Overwatch V2 is repaid by every subsequent week of development running through a working surface instead of a translation chain. By the time design partners are onboarded in late June / early July, Overwatch V2 has been the working surface for one month and has matured. The design partners interact with Forgewing; the Forgewing team interacts with Overwatch V2; the two products mature together.

### 2.3 What this is NOT

To prevent confusion in execution, several adjacent ideas are explicitly excluded from this commitment:

- **Not "an AI assistant for the Overwatch dashboard."** This would be a chatbot bolted onto a monitoring tool. Overwatch V2 is the engineering surface; the dashboard is one of its outputs.
- **Not "Claude Code running inside AWS."** Claude Code is an ephemeral session-bound terminal interface. Overwatch V2 is a persistent service with its own graph database, its own ontology, and its own learning from history.
- **Not "a faster diagnostic tool."** Diagnostics are a side effect. Overwatch V2 produces code, commits, deploys, and verification — not just diagnosis.
- **Not "a replacement for Anthropic's Claude API."** Overwatch V2's reasoner is built on Bedrock Sonnet 4.5 and Haiku 4.5, the same models Forgewing uses. The model is the same; the architecture, ontology, persona, and tool surface are what makes Overwatch V2 distinct.
- **Not "a productized version of the operator console."** Overwatch (V1, the current operator console at vaultscalerlabs.com — migrated 2026-04-25 from platform.vaultscaler.com) is internal-facing and reports-only. Overwatch V2 inherits that infrastructure but is fundamentally a different product class: it executes engineering work, not just observes it.

## 3. THE CONCEPTUAL MODEL

### 3.1 Overwatch V2 in one sentence

**A persistent embedded engineering intelligence that lives inside AWS, reasons over a graph database of its own work and the systems it manages, executes code generation and infrastructure operations through approval-gated tools, and learns from every task it performs.**

### 3.2 The five constitutive properties

Overwatch V2 is defined by five properties together. Removing any one produces a different system that does not solve the strategic problem.

**Property 1 — Embedded.** Overwatch V2 runs as a service inside AWS account 418295677815, with direct SDK access to AWS APIs and cross-account IAM to customer accounts. It does not call out to local terminals, does not synchronize with a local repo, does not depend on a developer's machine being online. It is always running, always able to act. Its uptime is the uptime of its ECS service.

**Property 2 — Memory-bearing.** Overwatch V2 has its own Neptune graph database, OverwatchGraph, separate from Forgewing's customer ontology. Every task it performs, every investigation it runs, every fix it proposes, every deploy it executes becomes an ontology object versioned in OverwatchGraph and append-logged to an eval corpus. Overwatch V2 reasons over its own history. It does not start every conversation from a blank context.

**Property 3 — Tool-equipped.** Overwatch V2's reasoner has a defined tool surface for reading and writing both code and infrastructure. Reading tools are unrestricted (file reads, AWS describes, Neptune queries). Writing tools (commits, deploys, infrastructure mutations) are gated behind explicit human approval. The tool surface is comprehensive enough that Overwatch V2 does not need to ask a human to "go run a command for me." If it cannot do a thing through its tools, that thing is out of its scope.

**Property 4 — Conversation-native.** Ian's interface to Overwatch V2 is a chat. Not a dashboard with a chat sidebar — the chat is the primary surface. Conversations persist in OverwatchGraph. Every turn is part of a thread; threads are scoped to topics or projects; threads are searchable and reusable. The chat surface is not a thin frontend over a tool API; it is the working environment.

**Property 5 — Learning.** Overwatch V2 improves over time without explicit retraining. Every successful fix becomes a Pattern object that can be matched against future tasks. Every failure becomes evidence that informs hypothesis selection. Every conversation contributes to Overwatch V2's tone calibration with Ian. The system that exists in month three is materially more capable than the system that exists in week three, even with no new features shipped — because the ontology has matured.

### 3.3 The invariants

Three properties hold across every version, every capability, every interaction. These are the safety floor.

**Invariant A — Read is free, write is approved.** Overwatch V2 may read any AWS resource, any file, any database state without asking. It may not commit code, mutate infrastructure, or modify customer data without explicit Ian approval recorded in the conversation history. The approval is per-mutation, not session-wide. A general "yes go ahead" does not authorize a subsequent unrelated mutation.

**Invariant B — Every action is auditable.** Every read, every write, every reasoner step is logged to CloudWatch and committed to OverwatchGraph as an event. Reconstruction of any past state is possible from logs alone. There is no operation Overwatch V2 performs that is opaque to post-hoc inspection.

**Invariant C — Truth before framing.** Overwatch V2's reasoner is required to verify hypotheses against ground truth before producing strategic narratives or recommendations. The methodology failure of 2026-04-24 (compressing hedged findings into confident framings) is the failure mode this invariant exists to prevent. Operationally: hedged findings are surfaced explicitly to Ian, and the reasoner does not produce conclusion-language until verification has been performed.

---

# PART II — ARCHITECTURE

## 4. SYSTEM TOPOLOGY

### 4.1 High-level diagram

```
                    ┌──────────────────────────────────────────────┐
                    │              Ian Green (CEO)                 │
                    └──────────────────┬───────────────────────────┘
                                       │ chat (HTTPS)
                                       ▼
              ┌────────────────────────────────────────────────────┐
              │   vaultscalerlabs.com  (overwatch-v2-alb)          │
              └────────────────────────────┬───────────────────────┘
                                           │
                                           ▼
              ┌────────────────────────────────────────────────────┐
              │           ECS Cluster: overwatch-platform           │
              │                                                    │
              │   ┌───────────────────┐   ┌────────────────────┐  │
              │   │  aria-console     │   │   overwatch-v2-    │  │
              │   │  (existing UI +   │◄──┤   reasoner         │  │
              │   │  chat surface,    │   │   (new service)    │  │
              │   │  v2 extensions)   │   └─────────┬──────────┘  │
              │   └───────────────────┘             │             │
              │                                     │             │
              │   ┌───────────────────┐             │             │
              │   │  overwatch-       │◄────────────┤             │
              │   │  capabilities-v1  │  (existing) │             │
              │   │  (30+ caps)       │             │             │
              │   └───────────────────┘             │             │
              └─────────────────────────────────────┼─────────────┘
                                                    │
                                                    ▼
              ┌────────────────────────────────────────────────────┐
              │              Overwatch V2 Tool Surface              │
              │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────┐ │
              │  │ Read     │ │ Code     │ │ Deploy   │ │ Verify │ │
              │  │ Tools    │ │ Tools    │ │ Tools    │ │ Tools  │ │
              │  └─────┬────┘ └─────┬────┘ └─────┬────┘ └────┬───┘ │
              └────────┼────────────┼────────────┼───────────┼─────┘
                       │            │            │           │
              ┌────────┼────────────┼────────────┼───────────┼─────────────┐
              │        ▼            ▼            ▼           ▼             │
              │   AWS Direct    GitHub PAT   ECS/CFN/SFN   CloudWatch     │
              │   (SDK calls)   (writes)     (deploys)     (logs/metrics) │
              │                                                            │
              │              Bedrock Sonnet 4.5 / Haiku 4.5                │
              │                                                            │
              │   OverwatchGraph (Neptune)    OverwatchPostgres (RDS)     │
              │   ─ ontology objects          ─ versioned object writes   │
              │   ─ events                    ─ approval ledger           │
              │   ─ patterns                  ─ conversation persistence  │
              └────────────────────────────────────────────────────────────┘
```

### 4.2 Service boundaries

**aria-console (existing, extended):** the user-facing service. Hosts the React frontend. Currently serves the Overwatch V1 dashboard. Extended in V2 to host the conversation-native chat surface. Does not contain reasoning logic; all reasoning happens in overwatch-v2-reasoner.

**overwatch-v2-reasoner (new):** the brain. Bedrock-backed conversation loop with tool access. Accepts a user turn and a thread context, performs reasoning with tool calls, returns a turn response. Stateless between turns (state lives in OverwatchGraph and OverwatchPostgres). Deployed as a Fargate service.

**overwatch-capabilities-v1 (existing):** the 30+ existing capabilities (investigation, trend analysis, Neptune integrity scanning, cost monitoring, tenant health, CI self-healing, Bedrock metrics, etc.). Overwatch V2 calls these as a subset of its tool surface. They continue to be exercised autonomously on schedule for proactive monitoring.

**OverwatchGraph (new):** dedicated Neptune graph for Overwatch V2's self-knowledge. Distinct from Forgewing's customer ontology graph (`g-1xwjj34141`). Single tenant: `overwatch-prime`.

**OverwatchPostgres (new):** RDS Postgres instance for versioned object writes and approval ledger. Mirrors Forgewing's pattern (Neptune for graph, Postgres for versioning) so the same `service.propose_object()` discipline applies.

### 4.3 Why aria-console is the chat surface and not a new service

The cost of building a new frontend from scratch is several weeks of work that produces no compounding benefit. aria-console already serves at vaultscalerlabs.com (migrated 2026-04-25 from platform.vaultscaler.com), already has Cognito auth, already has a three-pane layout pattern, already has ECS deploy automation. Extending it to host a new chat tab is approximately a week of frontend work. Building a new service is approximately a month of frontend work plus the operational overhead of a second deploy pipeline.

The chat surface lives as a new tab or route in aria-console. The existing dashboard remains. Operators choose between "monitor mode" (the current dashboard) and "engineering mode" (the chat). They share auth, deployment, and infrastructure.

### 4.4 Why a separate reasoner service rather than reasoning inside aria-console

Three reasons:

1. **Independent scaling.** Reasoner workload is bursty and CPU-bound. Frontend serving is lightweight. Decoupling them allows the reasoner to scale to handle long-running tool chains without affecting frontend latency.
2. **Independent deploys.** Frontend changes ship daily. Reasoner changes are more sensitive (a broken reasoner is a broken engineering surface). Separating deploy paths reduces blast radius.
3. **Independent failure modes.** A frontend bug should not take down reasoning. A reasoner bug should not take down monitoring. They are distinct enough services to deserve distinct runtimes.

## 5. THE REASONER

### 5.1 Conceptual model

The reasoner is a function. Its signature is:

```
reason(thread_id, user_turn, system_state) → assistant_turn + side_effects
```

It is invoked once per user message. It loads the thread's history and relevant ontology context, calls Bedrock Sonnet 4.5 with that context plus the user turn, processes any tool calls the model makes, returns the final assistant turn. Side effects (tool calls, ontology writes, approval requests) are recorded in OverwatchGraph and OverwatchPostgres before the response is returned.

### 5.2 Bedrock model selection

**Primary reasoning: Sonnet 4.5.** The model used for tool calling, code generation, conversation, and strategic reasoning. Same model Forgewing uses for ARIA's conversational responses.

**Extraction and synthesis: Haiku 4.5.** Used for narrower tasks where Sonnet's reasoning is overkill: extracting structured fields from logs, summarizing conversation history into rolling memory, generating titles for new threads, classifying user turns by intent. Same model Forgewing uses for classifier extraction.

**No other providers.** This is canonical to Forgewing and inherited by Overwatch V2. Provider-agnosticism is at the abstraction layer; all production traffic flows through Bedrock.

### 5.3 Prompt assembly

Modeled directly on Forgewing's `aria/prompt_assembly.py`. The prompt assembled for each reasoning turn is constructed from seven sources, in priority order:

1. **Persona** (never trimmed) — the Overwatch V2 ARIA persona, defined in §6.4.
2. **Founder context** — Ian's role, recent priorities, communication preferences, currently active projects.
3. **Rolling memory** — Haiku-summarized rolling context of the last N turns of this thread.
4. **Tone calibration** — observed conversational style markers, applied to ensure assistant turns match the working register Ian uses.
5. **Ontology grounding** — relevant ontology objects from OverwatchGraph, retrieved via similarity search and explicit reference resolution.
6. **Tool results** — outputs from any tools invoked during this turn.
7. **Conversation history** — prior turns of this thread, possibly trimmed to fit token budget.

Token budget: 10,000 tokens for the assembled prompt. Persona is never trimmed. Other sources are trimmed in reverse priority when the budget is exceeded.

### 5.4 The tool surface

Defined as a fixed set of tools the reasoner may call. Categorized into Read, Code, Deploy, Verify, and Meta.

#### Read tools (no approval required)

```
read_file(repo: str, path: str, ref: str = "HEAD") → str
  Read a file from a repository at a given ref.
  repos: "aria-platform" | "iangreen74-nexus-platform"

grep_repo(repo: str, pattern: str, path_glob: str = "**") → list[Match]
  Search a repository for a regex pattern, returning matches with paths and line numbers.

list_directory(repo: str, path: str, ref: str = "HEAD") → list[FileEntry]
  Enumerate files in a directory.

query_aws(service: str, operation: str, params: dict) → dict
  Perform a read-only AWS SDK call. Allowed operations are restricted to Get*, Describe*, List*, Lookup*.
  Service whitelist: cloudformation, ecs, ec2, elbv2, iam, lambda, logs, s3, sfn, sqs, sns, ssm,
                     secretsmanager, kms, codebuild, ecr, cognito, route53, cloudwatch, cloudtrail,
                     servicequotas, neptune, rds, bedrock-runtime.

read_cloudwatch_logs(group: str, start: datetime, end: datetime, filter: str = None) → list[LogEvent]
  Structured log query against a CloudWatch log group.

query_neptune(graph: "forgewing" | "overwatch", gremlin: str) → list[dict]
  Read-only Gremlin query against either Forgewing's or Overwatch's graph.

query_postgres(database: "forgewing" | "overwatch", query: str) → list[dict]
  Read-only SQL query.

read_secret(name: str) → str
  Read a Secrets Manager secret. Audited; appears in CloudTrail.

run_bash_sandbox(cmd: str, timeout_seconds: int = 30) → BashResult
  Execute a command in an ephemeral container with no AWS credentials and no network access
  except to pypi/npm/crates. Used for safe experimentation: parsing JSON, running test scripts,
  validating syntax. Cannot mutate any persistent state.
```

#### Code tools (approval required)

```
propose_commit(repo: str, branch: str, files: list[FileChange], message: str) → CommitProposal
  Stage a commit. Returns a CommitProposal with a unique ID, a unified diff, the commit message,
  and a rollback plan. Does NOT push.

execute_commit(proposal_id: str, approval_token: str) → CommitResult
  Execute a previously proposed commit. Requires an approval_token issued by Ian against this
  specific proposal_id. The approval_token is recorded in OverwatchGraph and is single-use.

create_pull_request(repo: str, branch: str, title: str, body: str) → PRResult
  Open a PR from a branch to main. Approval-gated.
```

#### Deploy tools (approval required)

```
trigger_deploy(repo: str) → DeployResult
  Trigger the deploy workflow. For aria-platform this dispatches the Deploy ARIA Platform
  workflow; for nexus-platform this is automatic on push to main, so this tool is effectively
  trigger_workflow_dispatch.

mutate_aws(service: str, operation: str, params: dict, approval_token: str) → dict
  Perform a mutating AWS SDK call. Allowed operations: Create*, Update*, Put*, Delete* (where
  Delete is whitelisted by service). Each call requires an approval_token tied to the specific
  mutation parameters.
```

#### Verify tools (no approval required)

```
verify_deploy(deploy_id: str, timeout_seconds: int = 600) → DeployVerification
  Poll a deploy until it reaches a terminal state. Reads SFN execution output, ECS service
  status, ALB target health, CloudWatch logs, CFN stack events. Returns a structured verdict.

probe_endpoint(url: str, expected_status: int = 200, timeout: int = 10) → ProbeResult
  HTTP probe against a deployed endpoint.

run_tests(repo: str, test_path: str, env: dict = None) → TestResult
  Execute a test suite in a sandbox container with the repo checked out.
```

#### Meta tools (no approval required)

```
search_ontology(query: str, types: list[str] = None, limit: int = 10) → list[OntologyObject]
  Semantic search against OverwatchGraph for relevant ontology objects.

record_pattern(name: str, signature: dict, fix: str, evidence: list[str]) → PatternObject
  Record a learned pattern to OverwatchGraph for future reuse. Not approval-gated because
  patterns are knowledge, not mutations. Used after a successful fix to memorialize the pattern.

ask_user(question: str, options: list[str] = None) → UserResponse
  Pose a question to Ian and pause execution until response. Used when the reasoner has
  ambiguity it cannot resolve from context.

emit_audit(event_type: str, payload: dict) → None
  Record an audit event to OverwatchGraph and CloudWatch. Used for high-importance moments
  (approval issued, mutation executed, pattern learned, threshold breached).
```

### 5.5 Approval flow

A mutation tool (code or deploy) returns a `*Proposal` object. The frontend renders this as an approval card with diff, rationale, affected systems, and rollback plan. Ian clicks Approve or Reject. On approve, the frontend issues an approval_token (a JWT signed by an HSM-backed key, scoped to the proposal_id and expiring in 5 minutes). The reasoner then calls the corresponding `execute_*` tool with the approval_token. The execute tool verifies the token, performs the mutation, records the action in OverwatchGraph, and returns a result.

Tokens are single-use. Tokens are tied to specific proposals. A token issued for a CFN UpdateStack cannot be used for an UpdateStack against a different stack.

### 5.6 Failure modes and recovery

**Reasoner crash mid-tool-call.** The thread's last successful state is recoverable from OverwatchGraph events. The reasoner is restarted from the last checkpoint. Idempotent tools are retried; non-idempotent tools (mutations) are not retried automatically — Ian sees an error and decides whether to retry.

**Tool timeout.** Tools have explicit timeouts. On timeout, the reasoner receives a TimeoutError and decides next action. For long-running operations (deploys), the reasoner uses async-poll mode rather than blocking.

**Ambiguous input.** The reasoner uses `ask_user()` rather than guessing. The cost of asking is one round-trip; the cost of guessing wrong is the methodology failure of 2026-04-24.

**Tool output exceeds context.** Large outputs (long CloudWatch logs, big files) are truncated with a summary marker. The reasoner sees the truncation explicitly and can re-call with a narrower scope if needed.

## 6. THE GRAPH (OverwatchGraph)

### 6.1 Why a graph at all

Overwatch V2's reasoner needs context that goes beyond the current conversation. It needs to know that a similar bug was fixed two weeks ago. It needs to know that an investigation last sprint produced a pattern that applies to today's question. It needs to know which CFN resources have failed before, and how. A graph database is the natural representation of this knowledge because the relationships are dense: investigations point to evidence, evidence points to patterns, patterns point to fixes, fixes point to the resources they touched, resources point to the deploys that created them.

Equally important: building OverwatchGraph the same way Forgewing's ontology is built means we are dogfooding our own product on ourselves. Every architectural choice in OverwatchGraph is a choice that will ultimately apply to a Forgewing customer's ontology. We learn the schema's strengths and weaknesses from our own use.

### 6.2 Provisioning

Dedicated Neptune Analytics graph in account 418295677815, us-east-1. Graph identifier to be allocated; reference name `overwatch-graph`.

Single tenant: `overwatch-prime`. Every node in OverwatchGraph carries `tenant_id = "overwatch-prime"`.

Backed by an OverwatchPostgres RDS instance for versioned object storage (mirrors Forgewing's dual-write pattern). Postgres holds the canonical version history; Neptune holds the queryable graph projection.

### 6.3 Schema

The schema is intentionally close to Forgewing's customer ontology to maximize transfer of patterns between the two systems. Adapted for engineering work rather than startup operational decisions.

#### Top-level node types

```
EngineeringTask
  A discrete unit of engineering work. The atomic granularity of "what is Overwatch V2 doing?"
  Properties:
    - id (uuid)
    - tenant_id (always "overwatch-prime")
    - title (str)
    - description (str)
    - status (proposed | in_progress | completed | failed | abandoned)
    - priority (p0 | p1 | p2 | p3)
    - created_at, completed_at (datetime)
    - thread_id (which conversation produced this task)

Investigation
  A diagnostic activity. Either standalone (Ian asks "why is X broken?") or attached to a Task.
  Properties:
    - id, tenant_id
    - hypothesis (str)
    - methodology (str — how we'll test the hypothesis)
    - tools_used (list[str])
    - duration_seconds (int)
    - verdict (confirmed | refuted | inconclusive)
    - confidence (float 0-1)

Hypothesis
  A specific testable claim. Investigations may produce or refute multiple hypotheses.
  Properties:
    - id, tenant_id
    - claim (str)
    - status (untested | confirmed | refuted)
    - evidence_for (list[Evidence])
    - evidence_against (list[Evidence])

Evidence
  A specific factual observation. Always sourced from a tool call.
  Properties:
    - id, tenant_id
    - source (tool name + parameters)
    - observation (str — what was observed)
    - timestamp (when the observation was made)
    - raw (json — the full tool output for audit)

Decision
  A choice made by Ian (or the reasoner with Ian's approval) that has consequences.
  Properties:
    - id, tenant_id
    - question (str)
    - options_considered (list[str])
    - chosen (str)
    - rationale (str)
    - reversibility (reversible | one_way)

FixAttempt
  A code or infrastructure change made to resolve a problem.
  Properties:
    - id, tenant_id
    - task_id (parent EngineeringTask)
    - description (str — what was changed)
    - commits (list[git_sha])
    - mutations (list[aws_resource_id])
    - outcome (succeeded | failed | partial)

DeployEvent
  An execution of the deploy pipeline.
  Properties:
    - id, tenant_id
    - repo (str)
    - sfn_execution_arn (str — for v2)
    - status (started | succeeded | failed | rolled_back)
    - duration_seconds
    - resources_created / resources_failed (list)
    - sfn_output (json — the full output payload, never just status)

Pattern
  A learned recurring shape. Comes into being when a Fix succeeds in a way that
  matches a previously-observed Failure shape.
  Properties:
    - id, tenant_id
    - name (str — short identifier like "cfn-iam-resource-mismatch")
    - signature (dict — what to match against)
    - fix (str — how to resolve)
    - evidence (list[Evidence] — historical instances)
    - confidence (float — increases each time the pattern is reused successfully)

Failure
  An unsuccessful outcome. Distinct from FixAttempt.failed because Failures can be
  observational (a deploy failed, regardless of whether we tried to fix it).
  Properties:
    - id, tenant_id
    - what (str)
    - root_cause (str | null — null until investigation completes)
    - blast_radius (list[resource])
    - resolution (FixAttempt id | null)

Success
  An unambiguous positive outcome. We record these specifically because the Forgewing
  thesis is that compounding requires recognizing what worked, not just what failed.
  Properties:
    - id, tenant_id
    - what (str)
    - method (FixAttempt id | str description)
    - reusability (str — circumstances under which this success can be replicated)

CapabilityState
  The current state of one of Overwatch V2's capabilities. Used to track what
  Overwatch V2 itself can do, and how its abilities have evolved.
  Properties:
    - id, tenant_id
    - capability_name (str)
    - autonomy_level (L1 reactive | L2 guided | L3 proactive | L4 anticipatory | L5 invisible)
    - last_exercised (datetime)
    - success_rate_30d (float)

Conversation
  A thread of turns between Ian and Overwatch V2.
  Properties:
    - id, tenant_id
    - title (str — Haiku-generated)
    - started_at, last_active_at (datetime)
    - turn_count (int)
    - status (active | archived)
    - tags (list[str])

ConversationTurn
  A single message in a conversation.
  Properties:
    - id, tenant_id
    - conversation_id
    - role (user | assistant | tool)
    - content (str)
    - tool_calls (list[ToolCall])
    - timestamp (datetime)
```

#### Edge types

```
INVESTIGATES        EngineeringTask → Investigation
PRODUCES            Investigation → Hypothesis
SUPPORTS            Evidence → Hypothesis
CONTRADICTS         Evidence → Hypothesis
RESOLVED_BY         Failure → FixAttempt
CAUSED_BY           Failure → resource_id
TARGETS             FixAttempt → resource_id
COMMITS             FixAttempt → git_sha
DEPLOYED_VIA        FixAttempt → DeployEvent
LEARNED_FROM        Pattern → list[Failure]
APPLIES_TO          Pattern → resource_pattern (regex or shape)
RESULTED_IN         FixAttempt → Success | Failure
DECIDED             Conversation → Decision
TURNED_INTO         Conversation → list[EngineeringTask]
EXERCISES           EngineeringTask → CapabilityState (records which capability was used)
```

#### Versioning

Every node version is written to OverwatchPostgres before being materialized in Neptune. The Postgres write is the source of truth; the Neptune projection is for queryability. This is identical to Forgewing's pattern — proven in production since Sprint 13.

### 6.4 ARIA persona for Overwatch V2

The persona for Overwatch V2's chat is distinct from Forgewing's customer-facing ARIA. The customer-facing ARIA is "an AI engineering co-founder for non-technical founders" — warm, explanatory, vision-engaging. Overwatch V2's ARIA is for an experienced founder/CTO doing engineering work — direct, precise, evidence-first, comfortable with technical density.

Properties of the Overwatch V2 ARIA persona:

- **Identifies as a colleague, not a tool.** Says "let me check the SFN execution output" rather than "I will perform an SFN execution output query."
- **Surfaces hedges as hedges.** Never compresses uncertain findings into confident framings. The methodology rule from 2026-04-24 is built into the persona.
- **Reports raw observations before interpretations.** "The CFN stack rolled back at 01:13 UTC. The first failed resource was the ALB with reason ServiceLimitExceeded. My read of this is..." not "The ALB quota was exceeded, so..."
- **Asks rather than guesses.** When ambiguity is material, calls `ask_user()` rather than picking one interpretation and proceeding.
- **Names methodology errors when noticed.** If Ian's request rests on an unverified premise, the persona surfaces that explicitly: "Before I act on this, I want to verify that X is actually true — your message implies it but we haven't checked."
- **Aware of its own history.** References past investigations, past patterns, past decisions when relevant. Does not pretend each conversation starts from zero.
- **Acknowledges its operational role explicitly.** Knows it is the engineering surface for VaultScaler, knows the strategic context, knows the launch timeline.

The persona content lives in `nexus/aria_v2/persona.md`, mirroring the path pattern of Forgewing's `aria/persona.md`. Authoring is Ian's responsibility (as canonical product decisions are). The reasoner loads the persona at every prompt assembly.

## 7. THE FRONTEND (chat surface)

### 7.1 Layout

A new route in aria-console: `/engineering` (or `/v2`, name to be confirmed). Three-pane layout consistent with v6.1 conventions:

- **Left pane:** conversation list. Threads grouped by status (active, recent, archived). Search. New conversation button.
- **Center pane:** the current thread. Message stream with role-based styling (user, assistant, tool result, approval card). Composer at bottom.
- **Right pane:** ops context. When a conversation is grounded in a specific task, deploy, or investigation, the right pane shows the live state of that target (e.g., the SFN execution status, the CFN stack events, the test results). When no specific target, shows recent activity across Overwatch V2's autonomous capabilities.

### 7.2 Approval cards

The most consequential UI element. When the reasoner returns a CommitProposal or a mutate_aws proposal, the frontend renders an approval card inline in the message stream:

```
┌────────────────────────────────────────────────────────────────┐
│ APPROVAL REQUESTED                                              │
│                                                                 │
│ Action: Commit to aria-platform branch fix/v2-iam-scope         │
│ Files: 1 changed, +4 / -1                                       │
│                                                                 │
│ infrastructure/forgescaler-customer-deploy-role.yml             │
│   - Resource: ["...stack/forgescaler-*", "...stack/ForgeScaler-*"] │
│   + Resource: ["...stack/forgescaler-*", "...stack/ForgeScaler-*",│
│   +            "...stack/forge-*"]                              │
│                                                                 │
│ Rationale:                                                      │
│ v2 pipeline names stacks forge-<tenant>-<project>. Current IAM  │
│ scope blocks this naming pattern. This change adds the v2       │
│ pattern while preserving v1 patterns.                           │
│                                                                 │
│ Affected: all customer-class tenants on next role apply         │
│                                                                 │
│ Rollback: revert the commit; existing roles retain old policy   │
│ until next apply                                                │
│                                                                 │
│              [Reject]            [Approve]                      │
└────────────────────────────────────────────────────────────────┘
```

Approval cards are persisted as ConversationTurn objects with `role = "tool"` and `tool_calls = [{ kind: "approval_request", ... }]`. The decision (approve/reject) is itself a ConversationTurn. This makes every mutation auditable.

### 7.3 Streaming

Reasoner responses stream as they generate. Tool calls render in the message stream as collapsed cards that expand on click to show the tool name, parameters, and result. This gives Ian visibility into the reasoner's chain-of-thought in near real time, without forcing him to read every detail.

### 7.4 Persistence

Every conversation is persisted to OverwatchGraph via the Conversation and ConversationTurn types. Conversation history is loaded server-side when a thread is opened; the frontend never stores conversation state in localStorage or sessionStorage (per Forgewing's invariant).

## 8. INTEGRATION WITH EXISTING OVERWATCH (V1)

### 8.1 What stays

All 30+ existing Overwatch capabilities continue to run. They are not retired or replaced. They are exercised on schedule for proactive monitoring just as today: CI self-healing, trend analysis, Neptune integrity scanner, AWS cost monitoring, tenant onboarding health, Bedrock metrics, the 3-tier investigation engine, the Diagnose buttons on the dashboard.

The aria-console UI continues to host the existing dashboard. The dashboard becomes one tab among several. The "engineering" tab (V2 chat) is the new addition.

### 8.2 What changes

**The chat surface is new.** A new route, new components, new backend service.

**The capabilities expose tool interfaces.** Existing capabilities are wrapped (not rewritten) so the V2 reasoner can call them as tools. For example, the existing 3-tier investigation engine becomes available as `run_investigation(target, tier)` — the reasoner can call it directly rather than asking Ian to click a Diagnose button.

**OverwatchGraph is provisioned.** A net-new Neptune graph and Postgres database. The existing Overwatch capabilities continue to write to their existing storage; OverwatchGraph is additive.

### 8.3 The migration story

There is no big-bang migration. The V1 dashboard continues to work. The V2 chat starts with a small set of tools and grows. Ian uses both during the transition, gradually moving more work to the chat as confidence builds. By month two, the chat is the default and the dashboard is reference.

### 8.4 The naming distinction

To prevent confusion between V1 and V2 Overwatch:

- **Overwatch (no version)** refers to the overall operator console, including both V1 dashboard and V2 chat.
- **Overwatch V1** specifically refers to the dashboard + autonomous capabilities + investigation engine that exists today.
- **Overwatch V2** specifically refers to the conversation-native engineering interface being built.
- **OverwatchGraph** is the V2-specific graph database.

## 9. SECURITY MODEL

### 9.1 Authentication

Existing Cognito user pool `us-east-1_3dzaO4Dzl`. Same SSO Ian uses for the V1 dashboard. No separate auth surface. MFA enforced (existing requirement).

### 9.2 Authorization

Two roles: `overwatch_operator` (Ian) and `overwatch_observer` (read-only, for any future audit access). The reasoner itself runs under an IAM role `overwatch-v2-reasoner-role` with the AWS permissions needed for its read tools. Mutations go through a separate role `overwatch-v2-mutation-role` that is only assumable when an approval token has been validated.

### 9.3 Approval token

JWT signed by a key in AWS KMS. Claims: `proposal_id`, `proposal_hash` (SHA-256 of the proposal payload), `issued_at`, `expires_at`, `issuer = ian_user_id`. Verification: signature valid, not expired, proposal_hash matches, single-use (consumed tokens are blocked in DynamoDB with a TTL).

### 9.4 Audit

CloudTrail logs every AWS API call. CloudWatch logs every reasoner turn including tool calls and parameters. OverwatchGraph holds the structural audit (every action becomes a node). Three independent audit trails. Reconstruction is possible from any one.

### 9.5 Blast radius limits

The reasoner cannot:

- Modify resources outside us-east-1
- Modify resources in other AWS accounts (no cross-account write trust)
- Create IAM users (only roles)
- Modify the `overwatch-v2-mutation-role` itself (lockout protection)
- Disable CloudTrail
- Delete OverwatchGraph or OverwatchPostgres

These are hard limits enforced at the IAM policy level, not at the reasoner level. A compromised reasoner cannot bypass them.

### 9.6 Secrets handling

Secrets are read on-demand from AWS Secrets Manager via the `read_secret` tool. They are not cached in OverwatchGraph or in the reasoner's memory beyond the duration of the turn. Logged secret reads are audit-tracked but the secret values themselves never appear in logs or in OverwatchGraph.

## 10. THE LEARNING LOOP

### 10.1 What "learning" means here

Three concrete mechanisms produce improvement over time:

**Pattern accretion.** Every successful FixAttempt is examined for reuse potential. If the fix matched a recurring shape (e.g., "CFN role policy missing Resource scope for new naming pattern"), a Pattern node is created. Future investigations check existing Patterns first via `search_ontology()` and reuse known fixes.

**Tone calibration.** Every conversation contributes observations to a tone-marker corpus: when does Ian want long versus short responses, when does he want tables versus prose, when is humor appropriate, when is gravity required. The reasoner reads these markers in prompt assembly and matches.

**Capability autonomy graduation.** Capabilities track their success rate. When a capability has run autonomously for N exercises with success rate above threshold, it graduates one autonomy level (L2 guided → L3 proactive → L4 anticipatory). This is the same autonomy ladder concept already present in OVERWATCH.md for V1.

### 10.2 What learning specifically does NOT mean

**Not retraining.** No fine-tuning of Bedrock models. The model is fixed at Sonnet 4.5 / Haiku 4.5. Learning happens at the ontology and prompt-assembly layers, not at the model weights.

**Not unsupervised.** Patterns are recorded explicitly via `record_pattern()`. They do not appear automatically. The reasoner proposes patterns when it notices recurrence; Ian approves or rejects pattern recording.

**Not autonomous mutation.** No matter how confident a Pattern is, applying it still requires per-mutation approval. Confidence may make Ian's approval faster (he sees a high-confidence pattern and approves quickly), but the approval is still required.

## 11. OBSERVABILITY (for Overwatch V2 itself)

A meta-observability requirement: Overwatch V2 must be observable from the outside. This is the lesson from 2026-04-24 — a working surface that is itself a black box reproduces the failure mode it was built to solve.

### 11.1 Health metrics (CloudWatch dashboards)

- Reasoner request latency p50/p95/p99
- Tool call latency by tool name p50/p95/p99
- Token consumption per turn (Sonnet, Haiku separately)
- Approval round-trip time (proposal → decision)
- Mutation success/failure rate by tool
- Error rate by tool name

### 11.2 Structural metrics (OverwatchGraph queries)

- EngineeringTasks completed per day
- Investigations started vs completed (queue depth)
- Patterns recorded per week
- Capability autonomy distribution (L1 / L2 / L3 / L4 / L5 counts)
- Conversation thread depth distribution

### 11.3 Truth-first pipeline view

The single most important observability surface, designed explicitly to prevent today's failure class. A dashboard view that integrates:

- SFN execution status AND output payload (not just top-level status)
- ECS task status with container exit codes
- CloudFormation stack states with first failed resource event
- IAM AssumeRole successes and failures (CloudTrail-sourced)
- Regional quota utilization for ALB, EIP, ECS service, Lambda concurrency, etc.

Renders as a single page with collapsible sections per active deploy. Color-coded by terminal-status interpretation: green = succeeded with resources created, red = SUCCEEDED-but-stub-terminated, amber = in-progress, dark-red = failed with ground-truth root cause displayed.

This view is the explicit antidote to the "SFN status: SUCCEEDED but no deploy happened" pattern of 2026-04-24.

---

# PART III — CONSTRUCTION PLAN

## 12. BUILD SEQUENCE

Two weeks. Day-by-day. Each day produces a deliverable that is testable on its own.

### Week 1: Foundation

**Day 1 — Architecture review and provisioning prep.**
- Ian reads this document, identifies any remaining concerns, raises them
- The construction plan is committed to `docs/OVERWATCH_V2_CONSTRUCTION.md`
- Branch `overwatch-v2/foundation` opened in `iangreen74-nexus-platform`
- No code yet

**Day 2 — Provisioning.**
- Neptune graph `overwatch-graph` provisioned in us-east-1
- Postgres RDS instance provisioned for OverwatchPostgres
- IAM roles `overwatch-v2-reasoner-role` and `overwatch-v2-mutation-role` created
- KMS key for approval token signing
- Secrets Manager entries for the github PAT used by code tools
- Terraform/CFN for the above committed to nexus-platform

**Day 3 — OverwatchGraph schema and service layer.**
- `nexus/overwatch_v2/ontology/schema.py` — node and edge type definitions
- `nexus/overwatch_v2/ontology/service.py` — `propose_object()`, `record_event()`, dual-write to Postgres + Neptune
- Migration scripts to create the Postgres tables
- 50+ unit tests covering schema validation and service operations
- First test that creates a Conversation, adds ConversationTurn objects, queries them back

**Day 4 — Tool layer foundation.**
- `nexus/overwatch_v2/tools/registry.py` — tool registration, parameter validation, audit emission
- Read tools fully implemented: `read_file`, `grep_repo`, `list_directory`, `query_aws`, `read_cloudwatch_logs`, `query_neptune`, `query_postgres`, `read_secret`
- `run_bash_sandbox` with Fargate-based ephemeral container execution
- Each tool has 5+ unit tests including failure cases

**Day 5 — Tool layer mutation surface.**
- `propose_commit` with diff generation, GitHub PAT integration
- `execute_commit` with approval token validation
- `create_pull_request` and `trigger_deploy`
- `mutate_aws` with whitelisted operation list
- KMS-backed approval token issuance and verification module
- Integration tests for the full proposal → approval → execution flow against a sandbox tenant

**Day 6 — Truth-first pipeline observability dashboard.**
- New route in aria-console: `/engineering/pipeline-truth`
- React component reading from new nexus-platform endpoint `/api/overwatch/pipeline-truth`
- Backend endpoint that pulls SFN execution output, ECS task statuses, CFN stack events, ALB target health, regional quota utilization for the latest N deploys
- Color-coded status logic that distinguishes SUCCEEDED-with-deploy from SUCCEEDED-stub-terminated
- Tested against the 96 historical SFN executions (must correctly classify all 96 as stub-terminations)

**Day 7 — Foundation complete. Demo and review.**
- All foundation work merged to main
- Demo: Ian opens the pipeline-truth view and verifies it accurately reflects current production state
- Demo: Ian uses a Postman or shell client to invoke `read_file`, `query_aws`, and `propose_commit`+`execute_commit` directly against the tool layer
- No reasoner yet, no chat yet, but the substrate is testable end to end

### Week 2: Reasoner and chat

**Day 8 — Reasoner skeleton.**
- `nexus/overwatch_v2/reasoner/loop.py` — Bedrock Sonnet 4.5 conversation loop with tool calling
- `nexus/overwatch_v2/reasoner/prompt_assembly.py` — modeled on Forgewing's `aria/prompt_assembly.py`, with the seven-source priority order
- Persona file `nexus/aria_v2/persona.md` written by Ian (deliverable: a draft persona document for review)
- First end-to-end test: a hardcoded user turn ("read the file aria-platform/forgescaler/main.py and tell me how many lines it has") flows through the reasoner, calls `read_file`, returns a response

**Day 9 — Reasoner full integration.**
- Conversation persistence: every turn writes to OverwatchGraph
- Rolling memory: Haiku-backed summarization of long threads
- Tone calibration: stub implementation that just records markers (does not yet condition responses)
- Ontology grounding: `search_ontology()` integrated into prompt assembly
- 20+ integration tests covering multi-turn conversations, tool failures, ambiguity handling

**Day 10 — Chat surface — backend.**
- New endpoint set in aria-console / aria-platform: POST `/api/overwatch/v2/threads`, POST `/api/overwatch/v2/threads/{id}/turns`, GET `/api/overwatch/v2/threads/{id}`
- Server-sent events (SSE) for streaming reasoner responses
- Approval card endpoints: POST `/api/overwatch/v2/proposals/{id}/approve`, `/reject`
- Auth wired to Cognito with role check for `overwatch_operator`

**Day 11 — Chat surface — frontend.**
- New route `/engineering` in aria-console
- Three-pane layout (conversation list, current thread, ops context)
- Streaming message renderer with tool call cards
- Approval card component with diff display, rationale, Approve/Reject buttons
- Composer with multi-line support and command shortcuts

**Day 12 — Capability wrapping.**
- Existing 30+ Overwatch V1 capabilities wrapped as tools the V2 reasoner can call
- Specifically: `run_investigation(target, tier)`, `query_capability_state(name)`, `get_recent_alerts(filters)`, etc.
- Documentation generated from tool registry — Ian can see the full tool surface as a reference

**Day 13 — End-to-end real engineering task.**
- Ian opens the chat. First real task: "Fix the v2 IAM scope bug — see the SPRINT 14 DAY 2 HANDOVER for context."
- Overwatch V2 investigates (reads the role policy, reads the v2 stack naming code, confirms the gap), proposes a fix, gets approval, executes the commit, triggers deploy, verifies via probe
- The first real fix goes through Overwatch V2 end to end
- The conversation, the investigation, the FixAttempt, the DeployEvent, the resulting Success are all recorded in OverwatchGraph

**Day 14 — Retrospective and transition.**
- Ian reviews two weeks of construction
- Decision: Overwatch V2 becomes default for engineering work; Claude Code chain becomes fallback
- Documentation updates committed: CANONICAL.md, ENGINEERING_PHILOSOPHY.md, SPRINT_14_RELEASE_PLAYBOOK.md all reflect Overwatch V2 as the primary engineering surface
- Construction sprint complete; transition begins

## 13. PARALLEL TRACK: FORGEWING FRONTEND

Throughout these two weeks, the Forgewing frontend track continues. Specifically:

**Phase 3 left-pane completion bundle** (one PR, ~150 LOC, ships through current Claude Code chain — last major work to do so):

1. Logout wiring in `ConversationNav.jsx` v6-pane-left__bottom
2. Projects state wiring (3-LOC fix in `Mission.jsx`)
3. "+ New Chat" wired (Option A, multiple chats per project)
4. Search "coming soon" tooltip
5. Recents real list
6. ARIA returning-chat opener variant

**Subsequent frontend work** queues for Overwatch V2 to take over once it's operational. The Phase 3 bundle is the final piece executed through the old chain.

**Why this works in parallel:** the frontend work touches the aria-platform repo and the v6.1 components. The Overwatch V2 work touches the nexus-platform repo and brand-new modules. Zero file overlap. Zero risk of merge conflicts. The two tracks proceed independently.

## 14. ACCEPTANCE CRITERIA

Overwatch V2 is considered operational when ALL of the following are true:

1. **A real engineering task has been completed end-to-end through the chat.** Investigation → proposal → approval → execution → verification, all in one conversation, recorded in OverwatchGraph.

2. **The pipeline-truth dashboard view correctly classifies historical executions.** Validated against the 96 historical v2 SFN executions — all correctly identified as stub-terminations.

3. **Approval flow works end-to-end with KMS-signed tokens.** Token issued by frontend on Ian's approval, validated by backend, single-use enforced, audit recorded.

4. **The reasoner has all tools wired and tested.** All read tools, all code tools, all deploy tools, all verify tools, all meta tools — each with at least one passing test.

5. **OverwatchGraph contains real data.** At least 10 EngineeringTasks, 20 Investigations, 5 Patterns, 100+ ConversationTurns from real use, not seed data.

6. **Existing Overwatch V1 capabilities are reachable as tools.** The reasoner can call `run_investigation()` and receive structured results.

7. **Documentation is updated.** CANONICAL.md reflects Overwatch V2 as the primary engineering surface. OVERWATCH.md updated to describe V2 architecture. ENGINEERING_PHILOSOPHY.md updated with the new working pattern.

8. **Ian uses Overwatch V2 for one full day of work without using Claude Code.** This is the practical acceptance test. If a full day of engineering work flows through the chat without Ian needing to fall back to Claude Code, V2 is operational.

## 15. KNOWN RISKS

**Risk 1 — Reasoner quality is insufficient.** Sonnet 4.5 with tool calls is the same setup Forgewing uses for ARIA's customer-facing conversations. It works there. It should work here. But "works there for non-technical founders" and "works here for an experienced founder doing engineering work" are different distributions. If the reasoner produces low-quality engineering output, the mitigation is improving the persona, the prompt assembly, and the tool design — not abandoning the architecture.

**Risk 2 — Approval flow becomes a bottleneck.** If every mutation requires human approval, throughput is gated by Ian's clicking speed. Mitigation: high-confidence Patterns surface as "auto-approve unless rejected within N seconds" candidates (still recorded as approvals, just with a different default). This is a future enhancement; in V2's first month, every approval is explicit.

**Risk 3 — OverwatchGraph schema needs evolution.** The schema specified here is informed by Forgewing's customer ontology, but engineering work may expose gaps. Mitigation: additive schema changes ship freely (new node types, new optional fields, new edge types). Required field additions need migration. Breaking changes need explicit version bump. Same rules as Forgewing's ontology evolution — proven pattern.

**Risk 4 — Construction takes longer than two weeks.** Two weeks is an estimate, not a contract. If construction extends, the launch date adjusts further. The decision criterion: a delivered Overwatch V2 in three weeks is preferable to a half-delivered one in two weeks.

**Risk 5 — The first real task exposes integration gaps.** The Day 13 end-to-end task may surface issues with how tools compose, how approval flows interact with multi-step investigations, or how OverwatchGraph queries scale. Mitigation: build buffer into Day 14 for fix-up work before retrospective.

**Risk 6 — Confusion between V1 and V2 in operations.** During the transition period, operators may not know which surface to use. Mitigation: explicit naming ("V1 dashboard" vs "V2 chat"), explicit migration guidance in OVERWATCH.md, and a default landing page that surfaces both clearly.

## 16. WHAT IS EXPLICITLY OUT OF SCOPE FOR THIS BUILD

To prevent scope creep during construction, the following items are excluded from the two-week sprint. They may be valuable; they are not necessary for V2 to be operational.

- **Multi-operator support.** V2 is built for Ian. Future operator support (engineering hires, Ben as occasional user) is post-launch.
- **Mobile experience.** Desktop only.
- **Voice input.** Text only.
- **Forgewing-customer-facing version.** The architectural patterns transfer, but exposing Overwatch V2 capabilities to customers is a separate product decision.
- **Cross-cohort pattern sharing.** Patterns learned by Overwatch V2 stay in Overwatch V2's tenant. Pattern sharing to customer ontologies is a deliberate future capability.
- **Real-time collaboration.** Two operators editing the same thread simultaneously is not supported.
- **Backwards-compatibility with Claude Code session export.** Past Claude Code sessions are not imported into OverwatchGraph. Overwatch V2 starts with a clean ontology and grows.

---

# PART IV — REFERENCE

## 17. INHERITED CONTEXT

This section consolidates technical context the construction team needs that is not specific to Overwatch V2 but is required to operate within VaultScaler's existing infrastructure.

### 17.1 Repositories

- `iangreen74/aria-platform` — Forgewing product (customer-facing). Local clone at `~/aria-platform`.
- `iangreen74/iangreen74-nexus-platform` — Overwatch (operator console + V2). Local clone at `~/nexus-platform`.

The local nexus-platform clone does NOT include the `iangreen74/` prefix in its directory name. Always reference as `~/nexus-platform`.

### 17.2 AWS context

- Account: 418295677815
- Region: us-east-1 only (SCP-enforced)
- Forgewing Neptune graph: `g-1xwjj34141`
- ECS clusters: `aria-platform` (Forgewing product), `overwatch-platform` (Overwatch and where V2 runs)
- ECR for nexus-platform: `nexus-platform`
- aria-console service domain: `vaultscalerlabs.com` (migrated 2026-04-25 from `platform.vaultscaler.com`; predecessor RETIRED)
- Cognito user pool: `us-east-1_3dzaO4Dzl`
- Bedrock models in use: Anthropic Claude Sonnet 4.5, Anthropic Claude Haiku 4.5

### 17.3 Tenants

- Ian's tenant: `forge-1dba4143ca24ed1f`. Project `proj-c268128467ed4605` is blog1.
- Ben's tenant: `forge-6b3550bef6c41d1b`. Project is sinkboard.
- Overwatch V2 tenant: `overwatch-prime`. New, single-tenant for OverwatchGraph.

### 17.4 File-size invariant

All Python files in production code must be ≤ 200 lines, CI-enforced. Files at or near the limit require helper module extraction rather than further additions. Specifically constrained today: `forgescaler/main.py`, `forgescaler/daemon_actions.py`, `forgescaler/daemon.py`, `forgescaler/daemon_helpers.py`, `forgescaler/accretion_context.py`.

This invariant applies to Overwatch V2 modules from day one. Plan module decomposition accordingly.

### 17.5 Deploy invariants

- Production deploys to aria-platform main require explicit Ian verbal confirmation before triggering empty commits or workflow_dispatch.
- nexus-platform deploys auto-trigger on push to main via `deploy.yml`. Standard flow: invariant-checks → test → ECR build/push → ECS force-new-deployment → wait services-stable → /health verify. Approximately 4-5 minutes end to end. Includes circuit breaker with auto-rollback.
- Overwatch V2 follows nexus-platform's auto-deploy pattern.

### 17.6 Boundaries

- `aria-console` ECS service is owned by Overwatch. Never `aws ecs execute-command` into aria-console from an aria-platform engineering context.
- `aria-platform` ECS service is owned by Forgewing product. Overwatch V2 may read from it but does not directly mutate it without going through standard deploy paths.
- Customer accounts are accessible only via cross-account IAM role assumption, with the audit trail preserved.

### 17.7 What was learned about v2 pipeline state on 2026-04-24

(Preserved here so Overwatch V2's first task has full context.)

The v2 deploy pipeline (`forgewing-deploy-v2` Step Functions state machine) currently has two parallel bugs:

**Bug 1 — Customer IAM scope mismatch.** Customer-class tenants assume IAM role `forgescaler-role-<tenantid>`. That role's policy in `aria-platform/infrastructure/forgescaler-customer-deploy-role.yml` allows `cloudformation:*` actions but scopes them to Resource patterns `forgescaler-*` and `ForgeScaler-*`. The v2 pipeline names stacks `forge-<tenant>-<project>` (no "scaler" suffix). The Resource scope does not match, so every customer v2 deploy fails AccessDenied at CreateStack.

Multiple resource patterns in the same template have the same naming-mismatch:
- CodeBuild: `project/forgescaler-*` (v2 uses `forgewing-build-*`)
- ECR: `repository/forgescaler-*` (v2 uses `forgewing/<projectid>`)
- Lambda: `function:forgescaler-*`
- DynamoDB: `table/forgescaler-*`
- SecretsManager: `secret:forgescaler/*`

CreateStack fails first because it is the first IAM-gated call. Fixing only CreateStack will surface the next AccessDenied on whatever resource v2 touches next.

**Bug 2 — Dogfood ALB quota under concurrency.** Dogfood deploys all share `dogfood-shared-vpc`. Every v2 stack creates its own ALB. Concurrent burst creation can exceed regional ALB quota (currently 50, no per-VPC quota, current static usage 19/50, no quota requests ever filed for L-53DA6B97). On 2026-04-22 01:12 UTC, a burst of 30 concurrent dogfood deploys hit ServiceLimitExceeded.

**Bug 3 — Diagnostic gaps.** `aria/remote_engineer/deployment_v2/tasks/create_stack.py:_poll_stack` swallows CFN failed-resource details, reporting `"Stack failed: DELETE_COMPLETE: no reason"` instead of fetching `describe-stack-events`. The runner.py swallows AWS errors under "stub termination" in the SFN failure flow. The dogfood sensor stage-matcher misses `*_failed` patterns like `stack_creation_failed`.

**Of 96 SFN executions reported as SUCCEEDED, all are stub-terminations** via `TerminateWithFailure` — SFN ran cleanly to a failure terminator. No deploy actually completed. Blog1 deployed via v1, not v2. v2 has zero customer successes.

When Overwatch V2 takes its first engineering task, this bug set is the canonical example of what the V2 chat is built to handle.

## 18. THE METHODOLOGY ERROR THAT MUST NOT REPEAT

(Preserved as institutional memory because it is the reason this document exists.)

On 2026-04-24, a single diagnostic question — "is the v2 deploy pipeline healthy?" — produced four wrong framings in sequence over five hours. Each framing was generated on top of an unverified premise. Each was retracted only after Claude Code performed deeper inspection and self-corrected its own earlier reports.

The pattern: Claude Code reports with caveats and hedges. Claude (the chat instance) extracts a surface conclusion. Claude builds strategic framing on the unverified premise. Claude Code self-corrects its own work after deeper investigation. Claude's strategic framing retroactively falls apart. The cycle repeats.

The pattern is not a one-day failure. It is a structural property of multi-hop translation chains where each layer must summarize. Overwatch V2 exists to dispose of the chain.

The methodology rule, recorded for the V2 reasoner's persona: when a finding is hedged, the hedge is load-bearing. Hedges are not formalities. The reasoner does not produce conclusion-language until verification has been performed. If verification cannot be performed, the reasoner says so and asks Ian whether to investigate further or proceed under stated uncertainty.

This rule is built into the Overwatch V2 ARIA persona (§6.4) and is the operational embodiment of Invariant C from §3.3.

## 19. AUTHORITY AND ESCALATION

- **Ian Green (CEO, 55%)** — sole authority for product, UX, scope, architecture, and engineering decisions. Approves all mutations performed by Overwatch V2. Owns this document.
- **Ben (President, 45%)** — referenced for commercial positioning and design partner relationships. Does not approve engineering mutations.
- **Claude / Overwatch V2** — proposes, executes, verifies. Does not approve own mutations.

Architectural changes to this document require Ian's explicit unlock and produce a versioned revision (v1.0 → v1.1, etc.) with the change log.

---

*End of specification.*

*This document is the canonical architecture and strategic spec for Overwatch V2. A fresh session should be able to read this document and have everything needed to begin construction. If a question arises that this document does not answer, the question itself is feedback that the document needs revision; raise it with Ian and update this document before proceeding.*
