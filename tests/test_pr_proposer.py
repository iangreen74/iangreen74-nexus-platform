"""Tests for the PR Proposer capability.

The real PR-opening path hits GitHub, so these tests cover the
wrapper's behavior: input validation, draft enforcement, OverwatchProposedPR
recording, and the report formatter. Local mode's aria_repo.create_fix_pr
returns a mock result, so we exercise the full pipeline end-to-end
without network calls.
"""
import os

os.environ.setdefault("NEXUS_MODE", "local")

from nexus import overwatch_graph  # noqa: E402
from nexus.capabilities import pr_proposer  # noqa: E402
from nexus.capabilities.registry import registry  # noqa: E402
from nexus.config import BLAST_DANGEROUS  # noqa: E402


def _reset():
    overwatch_graph.reset_local_store()


def test_propose_pr_requires_branch_and_title_and_changes():
    _reset()
    assert pr_proposer.propose_pr().get("status") == "error"
    assert pr_proposer.propose_pr(
        branch_name="b", title="", file_changes=[{"path": "a", "new_content": "x"}],
    ).get("status") == "error"
    assert pr_proposer.propose_pr(
        branch_name="", title="t", file_changes=[{"path": "a", "new_content": "x"}],
    ).get("status") == "error"
    assert pr_proposer.propose_pr(
        branch_name="b", title="t", file_changes=[],
    ).get("status") == "error"


def test_propose_pr_validates_file_change_shape():
    _reset()
    r = pr_proposer.propose_pr(
        branch_name="b", title="t",
        file_changes=[{"path": "a"}],  # missing new_content
    )
    assert r["status"] == "error"
    assert "new_content" in r["error"]


def test_propose_pr_in_local_mode_returns_mock():
    _reset()
    r = pr_proposer.propose_pr(
        branch_name="overwatch/fix-test",
        title="test fix",
        reasoning="because",
        file_changes=[{"path": "docs/CHANGELOG.md", "new_content": "x\n"}],
        finding={"rule": "stale_references"},
    )
    assert r["status"] == "mock"
    assert r["draft"] is True
    assert r["pr_url"].endswith("/pull/MOCK")
    assert r["files_changed"] == ["docs/CHANGELOG.md"]


def test_propose_pr_records_overwatch_proposed_pr_node():
    _reset()
    pr_proposer.propose_pr(
        branch_name="overwatch/fix-b",
        title="second fix",
        reasoning="also because",
        file_changes=[{"path": "README.md", "new_content": "y\n"}],
        finding={"rule": "unscoped_queries"},
        tenant_id="tenant-x",
    )
    rows = overwatch_graph._local_store.get("OverwatchProposedPR", [])
    assert len(rows) == 1
    assert rows[0]["title"] == "second fix"
    assert rows[0]["tenant_id"] == "tenant-x"
    assert rows[0]["finding_summary"] == "unscoped_queries"
    assert rows[0]["draft"] is True


def test_get_pending_proposals_returns_recent_first():
    _reset()
    for i in range(3):
        pr_proposer.propose_pr(
            branch_name=f"overwatch/fix-{i}", title=f"fix {i}",
            reasoning="r",
            file_changes=[{"path": "x.md", "new_content": str(i)}],
        )
    rows = pr_proposer.get_pending_proposals()
    assert len(rows) == 3
    # sorted newest-first by created_at
    assert rows[0]["title"] == "fix 2"
    assert rows[-1]["title"] == "fix 0"


def test_format_for_report_empty():
    _reset()
    assert pr_proposer.format_for_report() == "PENDING PULL REQUESTS: none"


def test_format_for_report_includes_title_url_and_finding():
    _reset()
    pr_proposer.propose_pr(
        branch_name="overwatch/fix-z", title="fix z",
        reasoning="the z was off by one",
        file_changes=[{"path": "z.py", "new_content": "z"}],
        finding={"rule": "stale_references"},
    )
    text = pr_proposer.format_for_report()
    assert "PENDING PULL REQUESTS:" in text
    assert "fix z" in text
    assert "[stale_references]" in text
    assert "/pull/MOCK" in text


def test_registered_as_dangerous_with_approval():
    cap = next((c for c in registry.list_all() if c.name == "propose_pr"), None)
    assert cap is not None
    assert cap.blast_radius == BLAST_DANGEROUS
    assert cap.requires_approval is True


def test_pr_body_contains_draft_warning_and_finding():
    body = pr_proposer._compose_pr_body(
        "the reason",
        {"rule": "unscoped_queries", "file": "x.py"},
        ["x.py"],
    )
    assert "Draft" in body
    assert "human must mark this ready for review" in body
    assert "unscoped_queries" in body
    assert "`x.py`" in body


def test_create_fix_pr_defaults_to_draft_mock():
    """create_fix_pr in local mode echoes the draft flag."""
    from nexus.forge.aria_repo import FileChange, create_fix_pr

    r = create_fix_pr(
        branch_name="b", file_changes=[FileChange(path="a", new_content="x")],
        title="t", body="b",
    )
    assert r["draft"] is True
