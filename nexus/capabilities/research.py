"""
Tier 2 Research Projects — persistent investigation with deep evidence.
9 sources (Tier 1's 6 + source files + Tavily + deep Neptune), synthesized
into a research brief via Bedrock Sonnet. Stored as OverwatchResearchProject.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from nexus import overwatch_graph
from nexus.config import AWS_REGION, OPS_CHAT_MODEL_ID

logger = logging.getLogger(__name__)

_LABEL = "OverwatchResearchProject"
SONNET = OPS_CHAT_MODEL_ID
_FIELDS = ["project_id", "title", "description", "status", "evidence",
           "brief", "sources_checked", "confidence", "created_at",
           "updated_at", "completed_at"]
_RETURN_FIELDS = ", ".join(f"p.{f} AS {f}" for f in _FIELDS)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return f"research-{uuid.uuid4().hex[:12]}"


# --- CRUD --------------------------------------------------------------------


def create_project(title: str, description: str) -> dict[str, Any]:
    """Create a new research project in 'open' status."""
    if not (title and description):
        return {"error": "title and description are required"}
    now = _now_iso()
    node = {"project_id": _new_id(), "title": title, "description": description,
            "status": "open", "evidence": "[]", "brief": "",
            "sources_checked": "[]", "confidence": 0,
            "created_at": now, "updated_at": now}
    overwatch_graph._create_node(_LABEL, node)
    return node


def list_projects() -> list[dict[str, Any]]:
    """List all research projects, newest first."""
    if overwatch_graph.MODE != "production":
        rows = list(overwatch_graph._local_store.get(_LABEL, []))
    else:
        rows = overwatch_graph.query(
            f"MATCH (p:{_LABEL}) RETURN {_RETURN_FIELDS} ORDER BY p.created_at DESC")
    return sorted(rows, key=lambda r: r.get("created_at", ""), reverse=True)


def get_project(project_id: str) -> dict[str, Any] | None:
    if overwatch_graph.MODE != "production":
        for r in overwatch_graph._local_store.get(_LABEL, []):
            if r.get("project_id") == project_id:
                return dict(r)
        return None
    rows = overwatch_graph.query(
        f"MATCH (p:{_LABEL} {{project_id: $pid}}) RETURN {_RETURN_FIELDS}",
        {"pid": project_id})
    return rows[0] if rows else None


def archive_project(project_id: str) -> dict[str, Any]:
    _update(project_id, {"status": "archived", "updated_at": _now_iso()})
    return {"project_id": project_id, "status": "archived"}


def _update(project_id: str, patch: dict[str, Any]) -> None:
    """Patch a research project (local: dict mutation, prod: Cypher SET)."""
    if overwatch_graph.MODE != "production":
        for r in overwatch_graph._local_store.get(_LABEL, []):
            if r.get("project_id") == project_id:
                r.update(patch)
                return
        return
    sets = ", ".join(f"p.{k} = ${k}" for k in patch)
    overwatch_graph.query(
        f"MATCH (p:{_LABEL} {{project_id: $pid}}) SET {sets}",
        {"pid": project_id, **patch})


# --- Research execution ------------------------------------------------------


async def run_research(project_id: str) -> dict[str, Any]:
    """Gather evidence from all 9 sources and synthesize a research brief."""
    project = get_project(project_id)
    if not project:
        return {"error": "Project not found"}

    _update(project_id, {"status": "researching", "updated_at": _now_iso()})
    description = project.get("description", "")

    evidence = await _gather_all_evidence(description)
    brief = await _synthesize(project, evidence)

    _update(project_id, {
        "status": "complete",
        "evidence": json.dumps(evidence, default=str)[:50000],
        "brief": brief.get("brief", "")[:50000],
        "sources_checked": json.dumps(list(evidence.keys())),
        "confidence": int(brief.get("confidence", 0)),
        "completed_at": _now_iso(),
        "updated_at": _now_iso(),
    })
    return {"project_id": project_id, "status": "complete", "brief": brief.get("brief"),
            "confidence": brief.get("confidence", 0),
            "sources_checked": list(evidence.keys())}


async def _gather_all_evidence(description: str) -> dict[str, Any]:
    """Fire all 9 gatherers in parallel and collect results."""
    from nexus.capabilities import investigation as t1
    from nexus.capabilities import research_evidence as t2

    named = {
        "cloudwatch": t1._gather_cloudwatch(60),
        "ecs": t1._gather_ecs(),
        "neptune": t1._gather_neptune(),
        "github_ci": t1._gather_github_ci(),
        "synthetic": t1._gather_synthetic(),
        "platform_events": t1._gather_platform_events(),
        "source_files": t2.gather_source_files(description),
        "web_research": t2.gather_web_research(description),
        "neptune_deep": t2.gather_neptune_deep(description),
    }
    results = await asyncio.gather(*named.values(), return_exceptions=True)
    out: dict[str, Any] = {}
    for name, result in zip(named.keys(), results):
        if isinstance(result, Exception):
            out[name] = {"type": name, "error": f"{type(result).__name__}: {result}"}
        else:
            out[name] = result
    return out


_BRIEF_PROMPT = (
    "You are Overwatch. Research project: \"{title}\"\nDescription: {desc}\n\n"
    "Evidence from 9 sources:\n{ev}\n\n"
    "Write a markdown research brief with sections: "
    "## Current State (cite evidence), ## Root Cause Analysis, "
    "## Options (2-3 approaches: complexity, risk, what it unlocks), "
    "## Recommendation (be opinionated), ## Evidence Citations."
)


async def _synthesize(project, evidence) -> dict[str, Any]:
    """Bedrock Sonnet → research brief. Never raises."""
    try:
        import boto3
        ev_text = json.dumps(evidence, indent=2, default=str)[:15000]
        prompt = _BRIEF_PROMPT.format(
            title=project.get("title", ""),
            desc=project.get("description", ""), ev=ev_text)

        def _call():
            client = boto3.client("bedrock-runtime", region_name=AWS_REGION)
            resp = client.invoke_model(modelId=SONNET, body=json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 3000,
                "messages": [{"role": "user", "content": prompt}]}))
            body = json.loads(resp["body"].read())
            for block in body.get("content", []):
                if block.get("type") == "text":
                    return block.get("text", "")
            return ""

        text = (await asyncio.to_thread(_call)).strip()
        return {"brief": text, "confidence": 80 if text else 0}
    except Exception as exc:
        logger.exception("research synthesis failed")
        return {"brief": f"Synthesis failed: {exc}", "confidence": 0}


def format_for_report() -> str:
    """Compact research project summary for the diagnostic report."""
    projects = list_projects()
    if not projects:
        return "RESEARCH PROJECTS: none"
    active = sum(1 for p in projects if p.get("status") in ("open", "researching"))
    done = sum(1 for p in projects if p.get("status") == "complete")
    lines = [f"RESEARCH PROJECTS: {active} active, {done} complete"]
    for p in projects[:6]:
        lines.append(f"  [{p.get('status', '?')}] {(p.get('title') or '?')[:60]}")
    return "\n".join(lines)
