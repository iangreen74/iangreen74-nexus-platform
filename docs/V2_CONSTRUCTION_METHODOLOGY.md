# OVERWATCH V2 CONSTRUCTION METHODOLOGY

> **Status:** Lessons captured during Sprint 14 V2 build, starting 2026-04-24.
> **Audience:** Future Claude Code sessions executing V2 prompts.
> **Reference this doc** before starting any V2 construction prompt.

This document is a living capture of methodology lessons surfaced during
V2 construction. Each lesson has a date, a concrete failure mode, and a
prophylactic rule. New entries append; old entries do not change.

---

## Day 1 Lessons (2026-04-24, Track B foundation)

### L1 — Parallel CFN deploy ordering must respect IAM principal validation timing

**Failure mode:** The Day 1 prompt told us to deploy templates 01/03/04/05
in parallel after 06. KMS key policies (template 05) hard-validate that
all referenced principals exist at policy-set time. The mutation-role
trust policy (template 04) references the reasoner role's ARN as
principal — this surprisingly DOES work on initial creation because IAM
allows non-existent ARN principals during role creation. KMS does not.

**Actual safe order:** `06 -> (01 || 03) -> 04 -> 05 -> 02`

**Rule:** When provisioning multiple stacks where any stack's policy
references another stack's resource by name or ARN, validate at the AWS
service layer level whether existence is required at policy-set time.
KMS, S3 bucket policies, and SQS access policies validate at set time.
IAM trust policies generally do not. Default to serial when in doubt.

### L2 — IAM and EC2 description fields reject Unicode em-dash

**Failure mode:** IAM Role Description and EC2 Security Group
GroupDescription fields use a character-class regex roughly equivalent to
printable ASCII + Latin-1 supplement. Unicode em-dash (U+2014) is
outside this range and produces a stack failure.

**Affected fields (verified 2026-04-24):** IAM::Role.Description,
EC2::SecurityGroup.GroupDescription

**Fields that DO accept Unicode:** Stack-level Description:, YAML
comments, output descriptions, parameter descriptions.

**Rule:** Default to ASCII-only in any IAM or EC2 resource description
field when authoring CFN templates via Python write_text. Use ASCII
hyphen - not em-dash. If the source content has em-dashes, sed-replace
them before write.

### L3 — Failed-on-create stacks need delete-stack-and-wait before redeploy

**Failure mode:** When a stack reaches ROLLBACK_COMPLETE on initial
creation, CloudFormation does NOT allow update-stack from that state.
The stack must be deleted and re-created.

**Wall-clock cost:** ~2-3 minutes per failed stack (delete + wait +
redeploy).

**Rule:** When a CFN deploy fails on initial creation, the recovery
sequence is:

    aws cloudformation delete-stack --stack-name NAME
    aws cloudformation wait stack-delete-complete --stack-name NAME
    aws cloudformation deploy --template-file FIXED.yml --stack-name NAME

Don't attempt update-stack against ROLLBACK_COMPLETE; it returns an
unhelpful error.

### L4 — Two terminals sharing one working tree is a real branch hazard

**Failure mode:** Track A created a branch in ~/nexus-platform between
Track B's git checkout -b and Track B's git commit. Track B's HEAD
moved underneath it; the commit landed on Track A's branch. Recovery
required git update-ref and git push --force-with-lease. Cleanup was
clean only because Track A had not yet pushed to the remote.

**Rule:** For any V2 work where parallel tracks may operate on the same
repo, use git worktree add to give each track its own physical
directory:

    ~/nexus-platform/                 (main worktree)
    ~/nexus-platform-trackE/          (Track E)
    ~/nexus-platform-trackF/          (Track F)
    ~/nexus-platform-trackG/          (Track G)

Each worktree shares .git/ (so all branches are visible across all
worktrees) but maintains its own checked-out branch. They cannot stomp
each other's HEAD.

When firing parallel-track prompts, the prompt's pre-flight section must
verify it is operating in the correct worktree path, not assume
~/nexus-platform is its working tree.

### L5 — gh CLI auth: prefer stored auth over GITHUB_TOKEN env var

**Failure mode:** A misconfigured GITHUB_TOKEN environment variable
(set to an invalid value somewhere upstream of Claude Code's shell) takes
precedence over gh auth's stored credentials. The PR-creation step in
multiple prompts hit auth failures until unset GITHUB_TOKEN was run.

**Rule:** Every V2 prompt that uses gh for PR creation, branch
operations, or workflow dispatch begins with:

    unset GITHUB_TOKEN
    unset GH_TOKEN
    gh auth status

This forces gh to fall back to its stored credentials, which are
configured correctly.

---

## Methodology principles (added throughout V2 build)

### P1 — Trust ground truth over spec content (added 2026-04-24)

Specs drift from reality between authoring and execution. Three drifts in
Track A alone (file path, logo handler location, doc location) caught by
Claude Code's file-read methodology, all would have been silent failures
without it.

**Rule:** Every prompt section that says "the spec specifies X" gets
paired with a view or grep step that verifies X actually exists at
the spec's claimed location. Halt and report on mismatch; do not paper
over by assuming the spec was right.

### P2 — Destructive operations need runtime live-state intersection (added 2026-04-24)

The Track D dry-run caught a secrets-filter bug that would have soft-
deleted four production secrets, plus a path-embedded-name bug that would
have deleted four production log groups. The bugs survived three review
passes (Track C report, prompt authoring, the driver's own
classification).

**Rule:** Any destructive operation includes a runtime check that
re-fetches live AWS state and intersects with the planned action set
just before execution. Compile-time protection lists are necessary but
not sufficient. The intersection eliminates entire classes of "stuff
created between discovery and execution" bugs.

### P3 — Hedges from Claude Code findings are load-bearing (added 2026-04-24)

Multiple times in this sprint, Claude Code reported findings with
explicit caveats ("I'd need to verify X before I can confirm Y"), and
the surrounding chat compressed those caveats into confident framings.
Each compression produced a wrong conclusion.

**Rule:** When Claude Code surfaces a hedge, the hedge is the finding.
The reasoner does not produce conclusion-language until verification has
been performed. This is operationalized in the V2 reasoner persona as
Invariant C ("truth before framing") from the V2 spec section 3.3.

---

*This document appends. New lessons land below as they surface.*

---

### L44 — Parallel-agent collisions on shared infrastructure (added 2026-04-26)

**Pattern.** When two Claude Code sessions are working from a stale shared
inventory, they may both pick up the same "next work" item and execute it
in parallel. The second agent's deploy can silently overwrite IAM/CFN/
secrets the first agent's deploy registered, leaving live AWS state
inconsistent with main.

**Observed instances (Sprint 14 Day 1).**
- PR #29 / PR #30 — cross-tenant read primitive (Phase 1 / "Phase 0c"),
  built in parallel from the same Phase-1 prompt seed.
- PR #32 / PR #34 — Phase 0b cross-source log correlation, same pattern.

**Common signature.**
- Both PRs implement the same spec from the same prompt seed.
- One merges first; the other catches it post-deploy.
- The losing agent self-closes as duplicate.
- IAM/CFN drift between losing-agent's deploy and merged-agent's deploy
  requires manual reconciliation. In PR #34's case, the losing agent
  had deployed `AlbAccessLogsS3Read` (both buckets) before discovering
  PR #32 had merged with `S3ReadAlbAccessLogs` (V2 bucket only); live
  state had to be reconciled by re-deploying main's IAM template.

**Defense — proposed CI assertion.** Pre-commit hook or workflow step
that asserts live AWS IAM matches the policies declared in the merging
branch. Catches drift in the deploy workflow before it lands rather
than at the next-deploy. Implementation candidate for Day 3.

**Operator discipline complement.** Strategic chat surfaces every "next
work" item with a clear claim — "Track A is firing Phase 0b in worktree
X" — so parallel sessions know what's already in flight before they
pick up a similar item from inventory.

> **Numbering note (2026-04-26).** L6–L43 were recorded only in commit
> message bodies and the auto-memory store, not appended to this file.
> L44 lands here as the first entry to break that drift; future lessons
> should land in this doc at the time they're committed elsewhere.
