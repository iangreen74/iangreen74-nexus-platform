"""Unit tests for the FixAgent. Runs in NEXUS_MODE=local; no AWS/GitHub calls."""
import json
import os

os.environ.setdefault("NEXUS_MODE", "local")

import pytest  # noqa: E402

from nexus.findings import Finding  # noqa: E402
from nexus.forge import fix_agent  # noqa: E402
from nexus.forge.fix_agent import FixAgent  # noqa: E402


@pytest.fixture(autouse=True)
def reset_rate_limit():
    fix_agent._recent_fixes.clear()
    yield
    fix_agent._recent_fixes.clear()


def _finding(**overrides) -> Finding:
    base = dict(
        summary="AttributeError on missing attr",
        severity="critical",
        category="code_fix",
        file="aria/intelligence/quality_gate.py",
        line=42,
    )
    base.update(overrides)
    return Finding(**base)


# --- can_fix ---------------------------------------------------------------
def test_can_fix_accepts_valid_finding():
    ok, reason = FixAgent().can_fix(_finding())
    assert ok, reason


def test_can_fix_rejects_missing_file():
    ok, reason = FixAgent().can_fix(_finding(file=None))
    assert not ok and reason == "missing_file_or_line"


def test_can_fix_rejects_missing_line():
    ok, reason = FixAgent().can_fix(_finding(line=None))
    assert not ok and reason == "missing_file_or_line"


def test_can_fix_rejects_unsupported_category():
    ok, reason = FixAgent().can_fix(_finding(category="config"))
    assert not ok and reason.startswith("unsupported_category")


def test_can_fix_rejects_out_of_scope_path():
    ok, reason = FixAgent().can_fix(_finding(file="nexus/server.py"))
    assert not ok and reason == "out_of_scope"


def test_can_fix_accepts_forgescaler_prefix():
    ok, _ = FixAgent().can_fix(_finding(file="forgescaler/api.py"))
    assert ok


# --- propose: happy path ---------------------------------------------------
def _fake_invoker_returning(fixed: str):
    def _invoke(prompt):
        return json.dumps({"fixed_file": fixed})
    return _invoke


def _fake_pr_opener(result):
    def _open(**kwargs):
        _open.calls.append(kwargs)
        return result
    _open.calls = []
    return _open


def test_propose_opens_pr_on_valid_fix():
    original = "def foo():\n    return bar\n"
    fixed = "def foo():\n    bar = 1\n    return bar\n"
    opener = _fake_pr_opener({"number": 7, "url": "https://gh/pr/7"})
    agent = FixAgent(
        invoker=_fake_invoker_returning(fixed),
        reader=lambda _p: original,
        pr_opener=opener,
    )
    result = agent.propose(_finding())
    assert result["status"] == "pr_opened"
    assert result["pr_number"] == 7
    assert opener.calls, "PR opener should have been called"
    change = opener.calls[0]["file_changes"][0]
    assert change.new_content == fixed
    assert opener.calls[0]["branch_name"].startswith("overwatch/fix-")


# --- propose: rejection paths ---------------------------------------------
def test_propose_skips_unfixable_finding():
    agent = FixAgent(
        invoker=lambda _: (_ for _ in ()).throw(AssertionError("should not call")),
        reader=lambda _p: "x",
        pr_opener=_fake_pr_opener({}),
    )
    result = agent.propose(_finding(category="config"))
    assert result["status"] == "skipped"
    assert result["reason"].startswith("unsupported_category")


def test_propose_rejects_no_change_output():
    original = "def foo():\n    pass\n"
    agent = FixAgent(
        invoker=_fake_invoker_returning(original),
        reader=lambda _p: original,
        pr_opener=_fake_pr_opener({"number": 1}),
    )
    result = agent.propose(_finding())
    assert result["status"] == "rejected"
    assert result["reason"] == "no_change"


def test_propose_rejects_syntax_error():
    agent = FixAgent(
        invoker=_fake_invoker_returning("def broken(:\n    pass\n"),
        reader=lambda _p: "def foo():\n    pass\n",
        pr_opener=_fake_pr_opener({"number": 1}),
    )
    result = agent.propose(_finding())
    assert result["status"] == "rejected"
    assert result["reason"].startswith("syntax_error")


def test_propose_rejects_oversized_fix():
    big = "\n".join([f"x{i} = {i}" for i in range(250)])
    agent = FixAgent(
        invoker=_fake_invoker_returning(big),
        reader=lambda _p: "x = 1\n",
        pr_opener=_fake_pr_opener({"number": 1}),
    )
    result = agent.propose(_finding())
    assert result["status"] == "rejected"
    assert result["reason"] == "exceeds_line_limit"


def test_propose_skips_oversized_source():
    huge = "\n".join([f"y{i} = {i}" for i in range(250)])
    agent = FixAgent(
        invoker=_fake_invoker_returning("ignored"),
        reader=lambda _p: huge,
        pr_opener=_fake_pr_opener({"number": 1}),
    )
    result = agent.propose(_finding())
    assert result["status"] == "skipped"
    assert result["reason"] == "source_too_long"


def test_propose_handles_unparseable_bedrock_output():
    agent = FixAgent(
        invoker=lambda _p: "this is not json at all",
        reader=lambda _p: "def foo():\n    pass\n",
        pr_opener=_fake_pr_opener({"number": 1}),
    )
    result = agent.propose(_finding())
    assert result["status"] == "no_fix"


def test_propose_reports_bedrock_exception():
    def _boom(_p):
        raise RuntimeError("throttled")

    agent = FixAgent(
        invoker=_boom,
        reader=lambda _p: "def foo():\n    pass\n",
        pr_opener=_fake_pr_opener({"number": 1}),
    )
    result = agent.propose(_finding())
    assert result["status"] == "failed"
    assert "throttled" in result["reason"]


def test_propose_reports_pr_open_failure():
    agent = FixAgent(
        invoker=_fake_invoker_returning("def foo():\n    return 1\n"),
        reader=lambda _p: "def foo():\n    pass\n",
        pr_opener=_fake_pr_opener({"error": "could_not_create_branch"}),
    )
    result = agent.propose(_finding())
    assert result["status"] == "failed"
    assert result["reason"] == "could_not_create_branch"


# --- rate limiting --------------------------------------------------------
def test_rate_limit_blocks_after_cap():
    fixed = "def foo():\n    return 1\n"
    agent = FixAgent(
        invoker=_fake_invoker_returning(fixed),
        reader=lambda _p: "def foo():\n    pass\n",
        pr_opener=_fake_pr_opener({"number": 1, "url": "u"}),
    )
    for _ in range(fix_agent.MAX_FIXES_PER_HOUR):
        assert agent.propose(_finding()).get("status") == "pr_opened"
    blocked = agent.propose(_finding())
    assert blocked["status"] == "rate_limited"
    assert blocked["reason"] == "hourly_cap"
