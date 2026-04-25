"""Tests for Overwatch V2 KMS approval-token module."""
import os
import threading
import time

os.environ.setdefault("NEXUS_MODE", "local")

import pytest

from nexus.overwatch_v2.auth import approval_tokens as at
from nexus.overwatch_v2.auth.approval_tokens import (
    KEY_ALIAS,
    ApprovalTokenClaims,
    VerifyResult,
    _reset_for_tests,
    hash_proposal,
    issue_token,
    verify_token,
)


@pytest.fixture(autouse=True)
def _clean():
    _reset_for_tests()
    yield
    _reset_for_tests()


PAYLOAD = {"action": "mutate_aws", "service": "cfn",
           "operation": "UpdateStack", "stack": "aria-platform"}


def test_issue_returns_three_part_jwt():
    tok = issue_token("p-1", PAYLOAD, "ian")
    assert tok.count(".") == 2
    h, c, s = tok.split(".")
    assert h and c and s


def test_issue_records_row_in_local_store():
    issue_token("p-1", PAYLOAD, "ian")
    assert len(at._local_token_store) == 1
    row = next(iter(at._local_token_store.values()))
    assert row["proposal_id"] == "p-1" and row["used"] is False


def test_verify_valid_token():
    tok = issue_token("p-1", PAYLOAD, "ian")
    r = verify_token(tok, "p-1", PAYLOAD)
    assert r.valid is True and r.reason is None
    assert r.claims and r.claims.proposal_id == "p-1"


def test_verify_expired_token():
    tok = issue_token("p-1", PAYLOAD, "ian", ttl_seconds=1)
    time.sleep(1.1)
    r = verify_token(tok, "p-1", PAYLOAD)
    assert r.valid is False and r.reason == "expired"


def test_verify_proposal_id_mismatch():
    tok = issue_token("p-1", PAYLOAD, "ian")
    r = verify_token(tok, "p-OTHER", PAYLOAD)
    assert r.valid is False and r.reason == "proposal_id_mismatch"


def test_verify_payload_hash_mismatch():
    tok = issue_token("p-1", PAYLOAD, "ian")
    other = dict(PAYLOAD); other["stack"] = "different"
    r = verify_token(tok, "p-1", other)
    assert r.valid is False and r.reason == "payload_hash_mismatch"


def test_token_is_single_use():
    tok = issue_token("p-1", PAYLOAD, "ian")
    r1 = verify_token(tok, "p-1", PAYLOAD)
    r2 = verify_token(tok, "p-1", PAYLOAD)
    assert r1.valid is True
    assert r2.valid is False and r2.reason == "already_used"


def test_concurrent_verify_only_one_succeeds():
    tok = issue_token("p-1", PAYLOAD, "ian")
    results: list[VerifyResult] = []
    barrier = threading.Barrier(8)

    def worker():
        barrier.wait()
        results.append(verify_token(tok, "p-1", PAYLOAD))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)
    wins = [r for r in results if r.valid]
    losses = [r for r in results if not r.valid]
    assert len(wins) == 1, f"expected 1 winner, got {len(wins)}"
    assert len(losses) == 7
    assert all(r.reason == "already_used" for r in losses)


def test_verify_malformed_token_rejected():
    r = verify_token("not.a.jwt.too.many.parts", "p-1", PAYLOAD)
    assert r.valid is False and r.reason == "malformed"


def test_verify_two_part_token_rejected():
    r = verify_token("only.twoparts", "p-1", PAYLOAD)
    assert r.valid is False and r.reason == "malformed"


def test_verify_bad_signature_rejected():
    tok = issue_token("p-1", PAYLOAD, "ian")
    h, c, _sig = tok.split(".")
    tampered = f"{h}.{c}.AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    r = verify_token(tampered, "p-1", PAYLOAD)
    assert r.valid is False and r.reason == "bad_signature"


def test_verify_tampered_claims_rejected():
    """If claim bytes change, the signature no longer covers them."""
    import base64, json
    tok = issue_token("p-1", PAYLOAD, "ian")
    h, c, sig = tok.split(".")
    pad = "=" * (-len(c) % 4)
    raw = json.loads(base64.urlsafe_b64decode(c + pad))
    raw["proposal_id"] = "p-EVIL"
    new_c = base64.urlsafe_b64encode(
        json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()
    ).rstrip(b"=").decode()
    r = verify_token(f"{h}.{new_c}.{sig}", "p-EVIL", PAYLOAD)
    assert r.valid is False and r.reason == "bad_signature"


def test_token_signed_with_different_secret_rejected(monkeypatch):
    """Tokens signed with one HMAC secret cannot be verified with another."""
    tok = issue_token("p-1", PAYLOAD, "ian")
    monkeypatch.setattr(at, "_LOCAL_TEST_SECRET", b"a-different-key")
    r = verify_token(tok, "p-1", PAYLOAD)
    assert r.valid is False and r.reason == "bad_signature"


def test_hash_proposal_is_canonical():
    a = {"x": 1, "y": 2}
    b = {"y": 2, "x": 1}
    assert hash_proposal(a) == hash_proposal(b)


def test_hash_proposal_distinguishes_payloads():
    assert hash_proposal({"a": 1}) != hash_proposal({"a": 2})


def test_claims_carry_kid_alias():
    """Token header must reference the KMS alias for key rotation safety."""
    import base64, json
    tok = issue_token("p-1", PAYLOAD, "ian")
    h = tok.split(".")[0]
    pad = "=" * (-len(h) % 4)
    header = json.loads(base64.urlsafe_b64decode(h + pad))
    assert header["alg"] == "HS256"
    assert header["kid"] == KEY_ALIAS


def test_default_ttl_is_300_seconds():
    tok = issue_token("p-1", PAYLOAD, "ian")
    row = next(iter(at._local_token_store.values()))
    assert row["expires_at"] - row["issued_at"] == 300


def test_custom_ttl_respected():
    issue_token("p-1", PAYLOAD, "ian", ttl_seconds=60)
    row = next(iter(at._local_token_store.values()))
    assert row["expires_at"] - row["issued_at"] == 60


def test_each_issue_gets_unique_jti():
    issue_token("p-1", PAYLOAD, "ian")
    issue_token("p-1", PAYLOAD, "ian")
    assert len(at._local_token_store) == 2


def test_verify_record_marks_used_at():
    tok = issue_token("p-1", PAYLOAD, "ian")
    verify_token(tok, "p-1", PAYLOAD)
    row = next(iter(at._local_token_store.values()))
    assert row["used"] is True
    assert "used_at" in row


def test_verify_returns_claims_even_on_bad_payload_hash():
    """Caller may want to inspect what proposal_id the token claimed."""
    tok = issue_token("p-1", PAYLOAD, "ian")
    r = verify_token(tok, "p-1", {"different": True})
    assert r.valid is False
    assert r.claims is not None and r.claims.proposal_id == "p-1"


def test_production_mode_calls_kms_not_local_secret():
    """Smoke: in production mode, _sign and _verify_sig route through boto3.
    We don't actually call AWS — we only assert the dispatch is mode-aware."""
    from unittest.mock import patch
    with patch.object(at, "MODE", "production"):
        with patch.object(at, "_kms") as kms_mock:
            kms_mock.return_value.generate_mac.return_value = {"Mac": b"FAKE"}
            sig = at._sign(b"test")
            assert sig == b"FAKE"
            kms_mock.return_value.generate_mac.assert_called_once()
