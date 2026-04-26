"""Phase 1 dispatch-time approval gate + mutation-audit emit helpers.

Sits between registry.dispatch() and the existing Track F approval-token
verifier (nexus.overwatch_v2.auth.approval_tokens.verify_token). Keeps
the registry small and makes the gate logic independently testable.

For one-shot mutation tools (no upstream proposal payload), the gate
synthesizes an ephemeral proposal:

    proposal_id      = f"tool:{tool_name}"
    proposal_payload = {"tool_name": tool_name, "params": parameters}

The operator UI must issue a token with the same proposal_id +
proposal_payload shape; verify_token rebuilds the canonical-json hash
and compares. Mismatch => leakage => rejected.

Per the spec proposal-id model (V2 SPECIFICATION §5.4), this stays
forward-compatible with multi-step proposals (propose_commit ->
execute_commit) — those will pass real proposal_ids instead of the
synthesized `tool:` form.
"""
from __future__ import annotations

from typing import Any, Optional

from nexus.overwatch_v2.auth.approval_tokens import verify_token
from nexus.overwatch_v2.mutation_audit import (
    OUTCOME_REJECTED_BAD_TOKEN, OUTCOME_REJECTED_NO_TOKEN,
    OUTCOME_SUCCESS, OUTCOME_TOOL_ERROR, emit_mutation_event,
)


def _proposal_id(tool_name: str) -> str:
    return f"tool:{tool_name}"


def _proposal_payload(tool_name: str, parameters: dict[str, Any]) -> dict[str, Any]:
    return {"tool_name": tool_name, "params": parameters}


def token_id_prefix(token: Optional[str]) -> Optional[str]:
    """Truncated, audit-safe token-id prefix (full token is sensitive)."""
    return None if not token else (token[:24] + "…")


def precheck(
    tool_name: str, parameters: dict[str, Any],
    approval_token: Optional[str], actor: str,
) -> tuple[bool, Optional[str], Optional[str]]:
    """Verify the gate. Emits mutation-audit on either failure mode.

    Returns (ok, fail_reason_or_None, token_id_prefix_or_None).
    Caller (dispatch) raises ApprovalRequired on ok=False.
    """
    prefix = token_id_prefix(approval_token)
    if not approval_token:
        try:
            emit_mutation_event(
                tool_name=tool_name, parameters=parameters, actor=actor,
                outcome=OUTCOME_REJECTED_NO_TOKEN, token_id_prefix=None,
            )
        except Exception:
            pass  # audit failure is non-fatal; do not mask the original
        return False, "no token supplied", None
    vr = verify_token(
        approval_token, _proposal_id(tool_name),
        _proposal_payload(tool_name, parameters),
    )
    if not vr.valid:
        try:
            emit_mutation_event(
                tool_name=tool_name, parameters=parameters, actor=actor,
                outcome=OUTCOME_REJECTED_BAD_TOKEN, token_id_prefix=prefix,
                error=vr.reason,
            )
        except Exception:
            pass
        return False, vr.reason, prefix
    return True, None, prefix


def emit_outcome(
    tool_name: str, parameters: dict[str, Any], actor: str, ok: bool,
    error: Optional[str], duration_ms: int,
    token_id_prefix: Optional[str],
) -> None:
    """Post-handler audit. Success or tool_error per outcome."""
    try:
        emit_mutation_event(
            tool_name=tool_name, parameters=parameters, actor=actor,
            outcome=OUTCOME_SUCCESS if ok else OUTCOME_TOOL_ERROR,
            token_id_prefix=token_id_prefix, error=error,
            duration_ms=duration_ms,
        )
    except Exception:
        pass
