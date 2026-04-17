"""Tests for the Learning Intelligence Report."""
import os

os.environ.setdefault("NEXUS_MODE", "local")

from fastapi.testclient import TestClient  # noqa: E402

from nexus.intelligence import capability_matrix as cm  # noqa: E402
from nexus.intelligence import learning_report  # noqa: E402
from nexus.intelligence import pattern_fingerprint as pf  # noqa: E402
from nexus.intelligence import report_sections as s  # noqa: E402
from nexus.intelligence import report_sections_ext as sx  # noqa: E402
from nexus.server import app  # noqa: E402

client = TestClient(app)


def test_capability_matrix_has_all_statuses():
    counts = cm.status_counts()
    assert counts.get("proven", 0) >= 1
    assert counts.get("architected", 0) >= 3
    assert counts.get("roadmap", 0) >= 10


def test_capability_matrix_renders():
    md = cm.render_matrix()
    assert "| Capability |" in md
    assert "proven" in md
    assert "roadmap" in md


def test_fingerprint_stable():
    pr = {"builds": ["a", "b"], "exposes": {"x": "int"}}
    stack = {"language": "python", "framework": "fastapi"}
    fp1 = pf.compute_pr_fingerprint(pr, stack)
    fp2 = pf.compute_pr_fingerprint(pr, stack)
    assert fp1["fingerprint"] == fp2["fingerprint"]
    assert len(fp1["fingerprint"]) == 20


def test_fingerprint_differs_on_builds():
    stack = {"language": "python"}
    fp1 = pf.compute_pr_fingerprint({"builds": ["a"]}, stack)
    fp2 = pf.compute_pr_fingerprint({"builds": ["b"]}, stack)
    assert fp1["fingerprint"] != fp2["fingerprint"]


def test_fingerprint_has_slug():
    pr = {"builds": ["user auth"]}
    stack = {"language": "python", "framework": "flask"}
    fp = pf.compute_pr_fingerprint(pr, stack)
    assert "python" in fp["slug"]
    assert "flask" in fp["slug"]


def test_report_generates_without_crashing():
    md = learning_report.generate_report()
    assert "# Learning Intelligence Report" in md
    assert "## 1. Executive Summary" in md
    assert "## 8. Anomalies" in md


def test_report_sections_all_render():
    md = learning_report.generate_report()
    for i in range(1, 9):
        assert f"## {i}." in md, f"Section {i} missing"


def test_section_1_returns_string():
    result = s.section_1_executive_summary()
    assert "Executive Summary" in result


def test_section_4_returns_string():
    result = s.section_4_failure_taxonomy()
    assert "Failure Mode" in result


def test_section_8_returns_string():
    result = sx.section_8_anomalies()
    assert "Anomalies" in result


def test_api_endpoint():
    resp = client.get("/api/learning-report")
    assert resp.status_code == 200
    assert "Learning Intelligence Report" in resp.text
    assert resp.headers["content-type"].startswith("text/markdown")


def test_api_endpoint_has_all_sections():
    md = client.get("/api/learning-report").text
    for i in range(1, 9):
        assert f"## {i}." in md, f"Section {i} missing from API response"
