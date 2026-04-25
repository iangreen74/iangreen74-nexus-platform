# RFC: Conversation Data Model

**Status:** Open. Decision needed by Ian.
**Trigger:** aria-platform PR #68 (v6.1 Phase 3 left-pane completion).
**Date raised:** 2026-04-24.

## Context

Phase 3 frontend remediation surfaced that the spec's assumption about
ConversationMessage's data model was wrong.

`FORGEWING_FRONTEND_REMEDIATION.md` §20 stated that ConversationMessages
are grouped by `conversation_id` in Postgres, added in migration 003
(`classifier_proposals`).

File-read against the current codebase shows the actual model:

- ConversationMessages are **Neptune nodes**, not Postgres rows.
- Schema: `tenant_id`, `message_id`, `role`, `content`, `phase`,
  `project_id`. **No `conversation_id` field.**
- `get_conversation_history(tenant_id, project_id)` returns a **flat
  list** of all messages for a `(tenant, project)` pair — no thread
  grouping concept exists.
- Write path: `_store_message()` in
  `aria/remote_engineer/conversation.py`, called from
  `forgescaler/conversation_routes.py` and a few other code paths in
  `aria/remote_engineer/`.

Two of the v6.1 left-pane elements (Element 2: + New Chat, Element 5:
Recents) were specified against the assumed multi-thread model. With
no `conversation_id` to key on, both elements can't be implemented as
specified. This RFC surfaces the question for decision before that
work is restarted.

## The product question

**Does v6.1 support multiple conversation threads per project?**

### Option A — Yes, multi-thread (original spec intent)

Each project has many conversations. + New Chat starts a fresh thread.
Recents lists threads ordered by last_active_at. The founder switches
between threads freely. Ontology aggregates objects across all threads
of a project.

**Implementation cost:** 200–400 LOC across:
- Neptune schema: pick representation (property on
  ConversationMessage vs. parent `Conversation` node + edges)
- Write paths: thread `conversation_id` through every `_store_message`
  call site
- Read API: new `GET /api/conversations` endpoint querying distinct
  conversations per `(tenant, project)`
- Frontend state: active-conversation that survives reload (URL param
  + localStorage)
- Migration: backfill every existing ConversationMessage row to a
  synthetic "default" conversation per project, so existing tenants'
  history doesn't appear empty after the schema change

**Calendar:** 2–3 days of focused work across two repos.

### Option B — No, single-thread per project

Each project has exactly one rolling conversation. + New Chat becomes
"Reset" or is removed. Recents shows the last few projects (which
ProjectHome already does at the page level).

**Implementation cost:** ~10 LOC to remove or relabel the buttons.

**Calendar:** half a day.

### Option C — Defer the decision (interim launch state)

Ship Phase 3's 4 clean elements now (logout/settings, projects
dropdown, search tooltip, logo navigation). Interim-tooltip the two
deferred elements: + New Chat is disabled with "New Chat coming soon"
hover tooltip; Recents placeholder text becomes "Coming soon". Decide
A vs B post-launch with design-partner feedback.

**Implementation cost:** ~10 LOC, identical pattern to the search
tooltip in Element 4.

## Recommendation

Option C is shipped as the interim state in aria-platform PR #68
(`feat/v6-1-phase-3-completion`). + New Chat and Recents render
"Coming soon" tooltips, not dead clicks. **This is interim, not
final** — Ian decides A vs B post-launch with design-partner
feedback.

If A wins, the tooltips are replaced by the working multi-thread UI.
If B wins, the affordances are removed or relabeled.

The reasoning for not deciding A vs B now: multi-thread per project is
a real UX commitment. Locking it in before any design partner has used
the product means committing to a 200–400 LOC investment on a guess.
The tooltip cost (~10 LOC) is cheap enough to absorb the option value
of waiting.

## What hangs on this

- **Frontend** (aria-platform): Elements 2 and 5 of
  `FORGEWING_FRONTEND_REMEDIATION.md`. PR #68 ships interim
  tooltips; final state pending this decision.
- **Backend** (aria-platform): whether to introduce `conversation_id`
  on Neptune ConversationMessage nodes, and the migration story for
  existing data on `forge-1dba4143ca24ed1f` and any other tenant with
  history.
- **Ontology** (nexus-platform): whether ConversationTurn nodes link
  to a `Conversation` parent or directly to `Project`. Current
  ontology emit logic (`publish_conversation_turn` in
  `aria/ontology_events.py`) assumes project-scoping; multi-thread
  would add a thread layer between project and turn.
- **ARIA** (nexus-platform): whether the returning-chat opener
  (Element 6 — already deferred from PR #68 pending Ian-authored
  persona prose) keys off conversation-level state or
  project-level state. Affects `nexus/aria/prompt_assembly.py` and
  the persona snippet in `nexus/aria/persona.md`.

## What changes if Ian picks A

Three-PR follow-up sequence:

1. **Backend (aria-platform):** introduce `conversation_id` on
   ConversationMessage nodes. Update every `_store_message` call site
   to pass it. Backfill script for existing rows. Schema doc update.
2. **Backend (aria-platform):** new `GET /api/conversations?tenant_id=&project_id=&limit=10`
   endpoint. Returns `[{id, title, last_active_at, turn_count}]`.
   Title = first user message of conversation, truncated to 50 chars.
3. **Frontend (aria-platform):** wire + New Chat (Element 2) and
   Recents (Element 5) per the original spec intent, replacing the
   interim tooltips. Active-conversation state in URL + localStorage.

Estimated calendar: 2–3 days of focused work.

## What changes if Ian picks B

One-PR follow-up:

1. **Frontend (aria-platform):** replace + New Chat with "Reset" (or
   remove the button entirely); replace Recents placeholder with a
   project-level recent list (or remove the section).

Estimated calendar: half a day.

## What changes if Ian picks C as the long-term answer (i.e. ship as is)

Nothing additional — interim state from PR #68 becomes the durable
state. The "Coming soon" tooltips would eventually feel stale; some
follow-up to either reword them ("Single conversation per project")
or hide the affordances entirely would be appropriate within a few
months.

## Decision tracker

- [ ] Ian's call: A / B / C-as-final
- [ ] Date decided: ____
- [ ] Follow-up PR(s) opened: ____
