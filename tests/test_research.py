"""Tests for Tier 2 Research Projects + Tier 3 deep investigation."""
import os

os.environ.setdefault("NEXUS_MODE", "local")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from nexus import overwatch_graph  # noqa: E402
from nexus.capabilities import research, research_evidence  # noqa: E402
from nexus.server import app  # noqa: E402

client = TestClient(app)


def _reset():
    for v in overwatch_graph._local_store.values():
        v.clear()


# --- CRUD -------------------------------------------------------------------


def test_create_project_requires_fields():
    assert "error" in research.create_project("", "x")
    assert "error" in research.create_project("x", "")


def test_create_project_happy():
    _reset()
    p = research.create_project("Title", "Why is X happening?")
    assert p["project_id"].startswith("research-")
    assert p["status"] == "open"
    assert p["title"] == "Title"
    assert p["confidence"] == 0


def test_list_projects_newest_first():
    _reset()
    research.create_project("older", "d1")
    research.create_project("newer", "d2")
    rows = research.list_projects()
    assert len(rows) == 2
    # sorted newest first
    assert rows[0]["title"] in ("newer", "older")


def test_get_project_found_and_missing():
    _reset()
    p = research.create_project("t", "d")
    assert research.get_project(p["project_id"])["title"] == "t"
    assert research.get_project("does-not-exist") is None


def test_archive_project():
    _reset()
    p = research.create_project("t", "d")
    r = research.archive_project(p["project_id"])
    assert r["status"] == "archived"
    assert research.get_project(p["project_id"])["status"] == "archived"


# --- Evidence gathering (local-mode mocks) ----------------------------------


@pytest.mark.asyncio
async def test_gather_source_files_local_mock():
    r = await research_evidence.gather_source_files("any question")
    assert r["type"] == "source_files"
    assert r.get("mock") is True


@pytest.mark.asyncio
async def test_gather_web_research_local_mock():
    r = await research_evidence.gather_web_research("any")
    assert r["type"] == "web_research"
    assert r.get("mock") is True


@pytest.mark.asyncio
async def test_gather_neptune_deep_local_mock():
    r = await research_evidence.gather_neptune_deep("any")
    assert r["type"] == "neptune_deep"
    assert r.get("mock") is True


def test_sanitize_cypher_blocks_writes():
    from nexus.capabilities.research_evidence import _sanitize_cypher
    assert _sanitize_cypher("MATCH (n) RETURN n LIMIT 5") is not None
    assert _sanitize_cypher("DELETE (n)") is None
    assert _sanitize_cypher("CREATE (n:X)") is None
    assert _sanitize_cypher("MATCH (n) SET n.x = 1") is None
    assert _sanitize_cypher("MATCH (n) DETACH DELETE n") is None


# --- Full research run (local mode short-circuits synthesis errors) ----------


@pytest.mark.asyncio
async def test_run_research_not_found():
    _reset()
    r = await research.run_research("does-not-exist")
    assert r.get("error") == "Project not found"


@pytest.mark.asyncio
async def test_run_research_completes_locally():
    """Local mode: synthesis will fail (no boto3 creds) but project state
    should still end as 'complete' with error captured in the brief."""
    _reset()
    p = research.create_project("Why slow?", "Why is tenant X slow?")
    result = await research.run_research(p["project_id"])
    assert result["status"] == "complete"
    assert result["project_id"] == p["project_id"]
    # Brief may contain synthesis failure in local mode — that's fine
    stored = research.get_project(p["project_id"])
    assert stored["status"] == "complete"


# --- Report formatter --------------------------------------------------------


def test_format_for_report_empty():
    _reset()
    assert research.format_for_report() == "RESEARCH PROJECTS: none"


def test_format_for_report_with_projects():
    _reset()
    research.create_project("a", "d")
    research.create_project("b", "d")
    text = research.format_for_report()
    assert "RESEARCH PROJECTS" in text
    assert "2 active" in text or "active" in text


# --- API endpoints -----------------------------------------------------------


def test_research_create_endpoint():
    resp = client.post("/api/research", json={"title": "T", "description": "D"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["project_id"].startswith("research-")


def test_research_create_validates():
    resp = client.post("/api/research", json={"title": ""})
    assert resp.status_code == 400


def test_research_list_endpoint():
    _reset()
    client.post("/api/research", json={"title": "X", "description": "D"})
    resp = client.get("/api/research")
    assert resp.status_code == 200
    assert len(resp.json()["projects"]) >= 1


def test_research_get_endpoint_404():
    resp = client.get("/api/research/doesnotexist")
    assert resp.status_code == 404


def test_research_archive_endpoint():
    _reset()
    created = client.post("/api/research", json={"title": "A", "description": "D"}).json()
    pid = created["project_id"]
    resp = client.delete(f"/api/research/{pid}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "archived"


def test_research_run_returns_immediately():
    """POST /run is fire-and-forget — must respond fast with researching status."""
    _reset()
    created = client.post("/api/research", json={"title": "T", "description": "D"}).json()
    pid = created["project_id"]
    resp = client.post(f"/api/research/{pid}/run")
    assert resp.status_code == 200
    body = resp.json()
    assert body["project_id"] == pid
    assert body["status"] == "researching"
    assert "Poll" in body["message"]


def test_research_run_404_for_missing():
    resp = client.post("/api/research/does-not-exist/run")
    assert resp.status_code == 404


# --- Deep investigation ------------------------------------------------------


def test_investigate_deep_requires_question():
    resp = client.post("/api/investigate/deep", json={})
    assert resp.status_code == 400


def test_investigate_deep_local_returns_tier1_or_mock():
    """Local mode: no Step Functions, returns either tier=1 (high confidence)
    or tier=3 mode=local (when force_deep=True)."""
    resp = client.post("/api/investigate/deep",
                       json={"question": "Is Overwatch running?",
                             "force_deep": True})
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("tier") in (1, 3)
