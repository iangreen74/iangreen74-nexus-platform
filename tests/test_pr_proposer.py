"""Tests for the Fix Advisor (formerly PR Proposer).

As of 2026-04-13 the PR Proposer no longer opens GitHub PRs. It reports
suggested fixes as OverwatchSuggestedFix nodes. These tests cover the
new report-only behavior: input validation, recording, suggestion list,
and the formatter.
"""
import os

os.environ.setdefault("NEXUS_MODE", "local")

from nexus import overwatch_graph  # noqa: E402
from nexus.capabilities import pr_proposer  # noqa: E402
from nexus.capabilities.registry import registry  # noqa: E402
from nexus.config import BLAST_SAFE  # noqa: E402


def _reset():
    for v in overwatch_graph._local_store.values():
        v.clear()


def test_propose_pr_requires_title_and_changes():
    _reset()
    assert pr_proposer.propose_pr().get("status") == "error"
    # missing title
    assert pr_proposer.propose_pr(
        title="", file_changes=[{"path": "a", "new_content": "x"}],
    ).get("status") == "error"
    # missing changes
    assert pr_proposer.propose_pr(
        title="t", file_changes=[],
    ).get("status") == "error"


def test_propose_pr_validates_file_change_shape():
    _reset()
    r = pr_proposer.propose_pr(
        title="t",
        file_changes=[{"path": "a"}],  # missing new_content
    )
    assert r["status"] == "error"
    assert "new_content" in r["error"]


def test_propose_pr_reports_without_github_call():
    """Happy path returns 'reported' — no GitHub API call is made."""
    _reset()
    r = pr_proposer.propose_pr(
        branch_name="overwatch/fix-test",
        title="test fix",
        reasoning="because",
        file_changes=[{"path": "docs/CHANGELOG.md", "new_content": "x\n"}],
        finding={"rule": "stale_references"},
    )
    assert r["status"] == "reported"
    assert r["title"] == "test fix"
    assert r["files_touched"] == ["docs/CHANGELOG.md"]
    # Crucially: no pr_url, no branch creation, no GitHub call
    assert "pr_url" not in r
    assert "pr_number" not in r


def test_propose_pr_records_suggested_fix_node():
    _reset()
    pr_proposer.propose_pr(
        branch_name="overwatch/fix-b",
        title="second fix",
        reasoning="also because",
        file_changes=[{"path": "README.md", "new_content": "y\n"}],
        finding={"rule": "unscoped_queries"},
        tenant_id="tenant-x",
    )
    rows = overwatch_graph._local_store.get("OverwatchSuggestedFix", [])
    assert len(rows) == 1
    assert rows[0]["title"] == "second fix"
    assert rows[0]["tenant_id"] == "tenant-x"
    assert rows[0]["finding_summary"] == "unscoped_queries"


def test_get_pending_suggestions_returns_recent_first():
    _reset()
    for i in range(3):
        pr_proposer.propose_pr(
            title=f"fix {i}",
            reasoning="r",
            file_changes=[{"path": "x.md", "new_content": str(i)}],
        )
    rows = pr_proposer.get_pending_suggestions()
    assert len(rows) == 3
    assert rows[0]["title"] == "fix 2"
    assert rows[-1]["title"] == "fix 0"


def test_backwards_compat_alias_works():
    """Old get_pending_proposals name still returns suggestions."""
    _reset()
    pr_proposer.propose_pr(
        title="x", reasoning="r",
        file_changes=[{"path": "x", "new_content": "x"}],
    )
    assert len(pr_proposer.get_pending_proposals()) == 1


def test_format_for_report_empty():
    _reset()
    assert pr_proposer.format_for_report() == "SUGGESTED FIXES: none"


def test_format_for_report_includes_title_and_finding():
    _reset()
    pr_proposer.propose_pr(
        title="fix z",
        reasoning="the z was off by one",
        file_changes=[{"path": "z.py", "new_content": "z"}],
        finding={"rule": "stale_references"},
    )
    text = pr_proposer.format_for_report()
    assert "SUGGESTED FIXES:" in text
    assert "fix z" in text
    assert "[stale_references]" in text
    # Must NOT reference a PR URL — this is report-only now
    assert "/pull/" not in text


def test_registered_as_safe_no_approval():
    cap = next((c for c in registry.list_all() if c.name == "propose_pr"), None)
    assert cap is not None
    assert cap.blast_radius == BLAST_SAFE
    assert cap.requires_approval is False


def test_diff_summary_includes_line_counts():
    """_compose_diff_summary shows file + line count without the full content."""
    summary = pr_proposer._compose_diff_summary([
        {"path": "a.py", "new_content": "line1\nline2\n"},
        {"path": "b.py", "new_content": "only one"},
    ])
    assert "a.py" in summary
    assert "b.py" in summary
    assert "lines" in summary
