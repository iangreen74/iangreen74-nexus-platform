"""KMS-backed approval-token issue/verify for V2 mutations (spec §9.3).

JWT HS256, MAC'd by KMS HMAC_256 key alias/overwatch-v2-approval-token
(boto3 generate_mac/verify_mac — not asymmetric sign/verify). Claims:
proposal_id, proposal_hash, issued_at, expires_at, issuer, jti.

Single-use: atomic UPDATE … WHERE used=false RETURNING against the
`approval_tokens` table (Track E migration 010, schema-aligned to code by
Phase 1.5 migration 013). Concurrent verifies race; one wins. Hash binding:
re-hash payload at verify so a token cannot replay across different proposals.
"""
from __future__ import annotations

import base64
import hashlib
import hmac as _hmac
import json
import logging
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from nexus.config import MODE

log = logging.getLogger("nexus.overwatch_v2.auth")

KEY_ALIAS = "alias/overwatch-v2-approval-token"
DEFAULT_TTL_SECONDS = 300
_LOCAL_TEST_SECRET = b"overwatch-v2-local-test-only-do-not-use-in-prod"

_local_token_store: dict[str, dict[str, Any]] = {}
_local_lock = threading.Lock()


@dataclass
class ApprovalTokenClaims:
    proposal_id: str
    proposal_hash: str
    issued_at: int
    expires_at: int
    issuer: str
    jti: str  # token_id, uuid hex — primary key in approval_tokens table


@dataclass
class VerifyResult:
    valid: bool
    claims: Optional[ApprovalTokenClaims] = None
    reason: Optional[str] = None


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _b64u_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _canon(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      default=str)


def hash_proposal(proposal_payload: dict) -> str:
    return hashlib.sha256(_canon(proposal_payload).encode("utf-8")).hexdigest()


def _kms():
    import boto3
    return boto3.client("kms", region_name=os.environ.get("AWS_REGION", "us-east-1"))


def _sign(message: bytes) -> bytes:
    """Production: KMS GenerateMac. Local: hmac stdlib."""
    if MODE != "production":
        return _hmac.new(_LOCAL_TEST_SECRET, message, hashlib.sha256).digest()
    return _kms().generate_mac(
        KeyId=KEY_ALIAS, Message=message, MacAlgorithm="HMAC_SHA_256",
    )["Mac"]


def _verify_sig(message: bytes, mac: bytes) -> bool:
    if MODE != "production":
        expected = _hmac.new(_LOCAL_TEST_SECRET, message, hashlib.sha256).digest()
        return _hmac.compare_digest(expected, mac)
    # TODO(phase-2): STS-assume into overwatch-v2-mutation-role before
    # verify_mac to restore Phase 0/1's two-actor separation of duties.
    # Currently single-actor; key resource policy aligned with this in
    # Phase 1.5.3 (PR #43) — see docs/CANONICAL.md search key:
    # KmsHmacApprovalToken_SeparationOfDuties.
    try:
        return bool(_kms().verify_mac(
            KeyId=KEY_ALIAS, Message=message,
            MacAlgorithm="HMAC_SHA_256", Mac=mac,
        ).get("MacValid"))
    except Exception:
        log.exception("verify_mac failed")
        return False


def _record_issue(claims: ApprovalTokenClaims) -> None:
    if MODE != "production":
        with _local_lock:
            _local_token_store[claims.jti] = {**asdict(claims), "used": False}
        return
    from nexus.overwatch_v2.db import get_conn
    # No schema prefix on `approval_tokens` (Phase 1.5 decision, see db.py
    # docstring). Migrations create the table in `public`; the earlier
    # `overwatch_v2.approval_tokens` reference was authorial accident.
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO approval_tokens (token_id, proposal_id, "
            "proposal_hash, issued_at, expires_at, issuer, used) VALUES "
            "(%s, %s, %s, to_timestamp(%s), to_timestamp(%s), %s, false)",
            (claims.jti, claims.proposal_id, claims.proposal_hash,
             claims.issued_at, claims.expires_at, claims.issuer),
        )


def _consume(token_id: str) -> bool:
    """Atomically flip used. True iff this caller won the race."""
    if MODE != "production":
        with _local_lock:
            row = _local_token_store.get(token_id)
            if row is None or row.get("used"):
                return False
            row["used"] = True
            row["used_at"] = int(time.time())
            return True
    try:
        from nexus.overwatch_v2.db import get_conn
        # See _record_issue: schema prefix dropped per Phase 1.5 db.py docstring.
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE approval_tokens SET used=true, "
                "used_at=now() WHERE token_id=%s AND used=false RETURNING token_id",
                (token_id,),
            )
            return cur.fetchone() is not None
    except Exception:
        log.exception("approval_tokens consume failed for %s", token_id)
        return False


def issue_token(
    proposal_id: str,
    proposal_payload: dict,
    issuer: str,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> str:
    now = int(datetime.now(timezone.utc).timestamp())
    claims = ApprovalTokenClaims(
        proposal_id=proposal_id,
        proposal_hash=hash_proposal(proposal_payload),
        issued_at=now,
        expires_at=now + ttl_seconds,
        issuer=issuer,
        jti=uuid.uuid4().hex,
    )
    h_b64 = _b64u(_canon({"alg": "HS256", "typ": "JWT", "kid": KEY_ALIAS}).encode())
    c_b64 = _b64u(_canon(asdict(claims)).encode())
    sig = _sign(f"{h_b64}.{c_b64}".encode("ascii"))
    _record_issue(claims)
    return f"{h_b64}.{c_b64}.{_b64u(sig)}"


def verify_token(
    token: str,
    expected_proposal_id: str,
    expected_proposal_payload: dict,
) -> VerifyResult:
    parts = token.split(".")
    if len(parts) != 3:
        return VerifyResult(False, reason="malformed")
    h_b64, c_b64, s_b64 = parts
    try:
        claims_dict = json.loads(_b64u_decode(c_b64))
        sig = _b64u_decode(s_b64)
    except Exception:
        return VerifyResult(False, reason="malformed")
    if not _verify_sig(f"{h_b64}.{c_b64}".encode("ascii"), sig):
        return VerifyResult(False, reason="bad_signature")
    try:
        claims = ApprovalTokenClaims(**claims_dict)
    except TypeError:
        return VerifyResult(False, reason="bad_claims")
    now = int(datetime.now(timezone.utc).timestamp())
    if now >= claims.expires_at:
        return VerifyResult(False, claims=claims, reason="expired")
    if claims.proposal_id != expected_proposal_id:
        return VerifyResult(False, claims=claims, reason="proposal_id_mismatch")
    if claims.proposal_hash != hash_proposal(expected_proposal_payload):
        return VerifyResult(False, claims=claims, reason="payload_hash_mismatch")
    if not _consume(claims.jti):
        return VerifyResult(False, claims=claims, reason="already_used")
    return VerifyResult(True, claims=claims)


def _reset_for_tests() -> None:
    with _local_lock:
        _local_token_store.clear()
