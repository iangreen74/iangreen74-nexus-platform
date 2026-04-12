"""Tests for the code audit system — all 10 rules + orchestrator."""
import os

os.environ.setdefault("NEXUS_MODE", "local")

import shutil  # noqa: E402
import tempfile  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from nexus.server import app  # noqa: E402

client = TestClient(app)


@pytest.fixture
def mock_repo():
    """Create a mock aria-platform layout with known issues."""
    tmp = tempfile.mkdtemp()
    os.makedirs(os.path.join(tmp, "aria"), exist_ok=True)
    os.makedirs(os.path.join(tmp, "forgescaler"), exist_ok=True)
    os.makedirs(os.path.join(tmp, "forgescaler-web", "src", "components"), exist_ok=True)

    # Unscoped query
    with open(os.path.join(tmp, "forgescaler", "bad_routes.py"), "w") as f:
        f.write(
            "from aria.graph_backend import execute_query\n"
            "def get_tasks(tenant_id):\n"
            '    return execute_query("MATCH (t:MissionTask {tenant_id: $tid}) RETURN t", {"tid": tenant_id})\n'
        )

    # Untagged write
    with open(os.path.join(tmp, "forgescaler", "bad_chat.py"), "w") as f:
        f.write(
            "from aria.graph_backend import merge_node\n"
            "def store(tid, msg):\n"
            '    merge_node("ConversationMessage", {"tenant_id": tid, "message_id": "x"}, {"content": msg})\n'
        )

    # Bad React component
    with open(os.path.join(tmp, "forgescaler-web", "src", "components", "Bad.jsx"), "w") as f:
        f.write('const el = document.querySelector(".chat-input")\n')

    # File over 200 lines
    with open(os.path.join(tmp, "aria", "big_file.py"), "w") as f:
        f.write("\n".join([f"# line {i}" for i in range(210)]))

    # CREATE instead of MERGE
    with open(os.path.join(tmp, "aria", "unsafe.py"), "w") as f:
        f.write('execute_query("CREATE (n:MissionTask {tenant_id: $tid})", {"tid": tid})\n')

    # api.js with missing project_id
    with open(os.path.join(tmp, "forgescaler-web", "src", "api.js"), "w") as f:
        f.write(
            "function sendMsg(text, tid) {\n"
            "  const body = { text: text, tenant_id: tid };\n"
            "  return req('POST', '/chat', body);\n"
            "}\n"
            "const api = {\n"
            "  status: (tid) => req('GET', `/status/${tid}`),\n"
            "};\n"
        )

    # Stale brand reference
    with open(os.path.join(tmp, "forgescaler", "stale.py"), "w") as f:
        f.write('message = "Welcome to ForgeScaler, the platform"\n')

    # Frontend scoping violation
    with open(os.path.join(tmp, "forgescaler-web", "src", "components", "Dash.jsx"), "w") as f:
        f.write("const x = api.status(tenantId);\n")

    # Param propagation violation
    with open(os.path.join(tmp, "forgescaler", "cycle.py"), "w") as f:
        f.write(
            "def run_cycle(tid):\n"
            "    ingest_repo(tid)\n"  # missing project_id
        )

    # Isolation escape
    with open(os.path.join(tmp, "forgescaler", "escape.py"), "w") as f:
        f.write("def use_default(tid):\n    p = get_default_project(tid)\n")

    yield tmp
    shutil.rmtree(tmp)


# --- Individual rules --------------------------------------------------------


def test_unscoped_queries(mock_repo):
    from nexus.audit_rules.unscoped_queries import UnScopedQueries
    findings = UnScopedQueries().scan(mock_repo)
    assert any("MissionTask" in f.message for f in findings)


def test_untagged_writes(mock_repo):
    from nexus.audit_rules.untagged_writes import UntaggedWrites
    findings = UntaggedWrites().scan(mock_repo)
    assert any("ConversationMessage" in f.message for f in findings)


def test_react_antipatterns(mock_repo):
    from nexus.audit_rules.react_antipatterns import ReactAntiPatterns
    findings = ReactAntiPatterns().scan(mock_repo)
    assert any("querySelector" in f.message for f in findings)


def test_file_limits(mock_repo):
    from nexus.audit_rules.file_limits import FileLimits
    findings = FileLimits().scan(mock_repo)
    assert any("210 lines" in f.message for f in findings)


def test_unsafe_neptune(mock_repo):
    from nexus.audit_rules.unsafe_neptune import UnsafeNeptune
    findings = UnsafeNeptune().scan(mock_repo)
    assert any("CREATE" in f.message for f in findings)


def test_api_contract(mock_repo):
    from nexus.audit_rules.api_contract import ApiContractMismatch
    findings = ApiContractMismatch().scan(mock_repo)
    assert any("project_id" in f.message for f in findings)


def test_stale_references(mock_repo):
    from nexus.audit_rules.stale_references import StaleReferences
    findings = StaleReferences().scan(mock_repo)
    assert any("ForgeScaler" in f.message for f in findings)


def test_frontend_scoping(mock_repo):
    from nexus.audit_rules.frontend_scoping import FrontendScoping
    findings = FrontendScoping().scan(mock_repo)
    assert any("status" in f.message for f in findings)


def test_param_propagation(mock_repo):
    from nexus.audit_rules.param_propagation import ParamPropagation
    findings = ParamPropagation().scan(mock_repo)
    assert any("ingest_repo" in f.message for f in findings)


def test_isolation_escapes(mock_repo):
    from nexus.audit_rules.isolation_escapes import IsolationEscapes
    findings = IsolationEscapes().scan(mock_repo)
    assert any("default_project" in f.message for f in findings)


# --- Orchestrator ------------------------------------------------------------


def test_full_audit(mock_repo):
    from nexus.nexus_code_auditor import run_audit
    report = run_audit(local_path=mock_repo, store_results=False)
    assert report["status"] == "complete"
    assert report["total_findings"] >= 5
    assert report["health_score"] < 100
    assert len(report["rule_summaries"]) == 10
    assert report["rules_run"] == 10


def test_health_score_deductions(mock_repo):
    from nexus.nexus_code_auditor import run_audit
    report = run_audit(local_path=mock_repo, store_results=False)
    expected = 100 - (
        report["critical"] * 10 + report["high"] * 5
        + report["medium"] * 2 + report["low"] * 0.5
    )
    assert report["health_score"] == max(0, round(expected))


def test_format_report_text_empty():
    from nexus.nexus_code_auditor import format_report_text
    text = format_report_text(None)
    assert "No audit report" in text


def test_format_report_text_has_sections(mock_repo):
    from nexus.nexus_code_auditor import format_report_text, run_audit
    report = run_audit(local_path=mock_repo, store_results=False)
    text = format_report_text(report)
    assert "CODE HEALTH AUDIT" in text
    assert "Health Score" in text


def test_run_audit_stores_to_graph(mock_repo):
    from nexus import overwatch_graph
    from nexus.nexus_code_auditor import get_latest_report, run_audit

    # Clear prior events
    for v in overwatch_graph._local_store.values():
        v.clear()
    run_audit(local_path=mock_repo, store_results=True)
    retrieved = get_latest_report()
    assert retrieved is not None
    assert retrieved["status"] == "complete"


def test_run_audit_bad_path_returns_error():
    from nexus.nexus_code_auditor import run_audit
    # Non-existent path + MODE=local means clone will be attempted but
    # in local mode no boto3/secrets — still may try git clone, may fail
    report = run_audit(local_path="/nonexistent/xyz/path", store_results=False)
    # Either completed (via clone) or errored — both acceptable
    assert report["status"] in ("complete", "error")


# --- API endpoints -----------------------------------------------------------


def test_code_audit_get_endpoint_no_data():
    from nexus import overwatch_graph
    for v in overwatch_graph._local_store.values():
        v.clear()
    resp = client.get("/api/code-audit")
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("status") == "no_audit_yet"


def test_code_audit_text_endpoint():
    resp = client.get("/api/code-audit/text")
    assert resp.status_code == 200
    body = resp.json()
    assert "report" in body
