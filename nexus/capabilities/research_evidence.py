"""
Tier 2 evidence gatherers — source files, web research, deep Neptune.
Each returns {type, ...} or {type, error}. Never raises.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import Any

from nexus.capabilities.bedrock_utils import parse_bedrock_json_array
from nexus.config import AWS_REGION, MODE

logger = logging.getLogger(__name__)

HAIKU = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
_BLOCKED = ("DELETE", "DROP", "REMOVE", "DETACH", "CREATE", "MERGE", "SET ",
            "CALL db.", "PERIODIC")


def _sanitize_cypher(q: str) -> str | None:
    up = q.strip().upper()
    if not up.startswith("MATCH"):
        return None
    for bad in _BLOCKED:
        if bad in up:
            return None
    return q


async def _invoke_bedrock(model_id: str, prompt: str, max_tokens: int = 500) -> str:
    """Thin wrapper — synchronous Bedrock in a thread. Returns text or ''."""
    import boto3

    def _call():
        client = boto3.client("bedrock-runtime", region_name=AWS_REGION)
        resp = client.invoke_model(
            modelId=model_id,
            body=json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": prompt}],
            }),
        )
        body = json.loads(resp["body"].read())
        blocks = body.get("content") or []
        for block in blocks:
            if block.get("type") == "text":
                return block.get("text", "")
        return ""

    return await asyncio.to_thread(_call)


# --- Source files via GitHub API ---------------------------------------------


async def gather_source_files(description: str) -> dict[str, Any]:
    """Read 3–5 relevant source files from aria-platform via GitHub."""
    if MODE != "production":
        return {"type": "source_files", "mock": True, "files_read": []}
    try:
        prompt = (
            "Given this research question about the Forgewing platform, which "
            "source files should we read? The platform has: aria/ (backend), "
            "forgescaler/ (API routes), forgescaler-web/src/ (React frontend). "
            f'Question: "{description}"\n'
            "Return ONLY a JSON array of file paths, max 5. "
            'Example: ["aria/remote_engineer/cycle.py"]'
        )
        text = await _invoke_bedrock(HAIKU, prompt, max_tokens=300)
        paths = parse_bedrock_json_array(text, fallback=[])[:5]
        if not paths:
            return {"type": "source_files", "error": "No file list generated",
                    "raw": (text or "")[:200]}
        return await _fetch_github_files(paths)
    except Exception as exc:
        return {"type": "source_files", "error": f"{type(exc).__name__}: {exc}"}


async def _fetch_github_files(paths: list[str]) -> dict[str, Any]:
    import httpx

    from nexus.aws_client import _client

    token = ""
    try:
        sm = _client("secretsmanager")
        raw = sm.get_secret_value(SecretId="github-token")["SecretString"].strip()
        token = json.loads(raw).get("token", raw) if raw.startswith("{") else raw
    except Exception:
        token = ""

    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"token {token}"
    contents: dict[str, str] = {}
    async with httpx.AsyncClient(timeout=10) as c:
        for path in paths:
            try:
                r = await c.get(
                    f"https://api.github.com/repos/iangreen74/aria-platform/contents/{path}",
                    headers=headers,
                )
                if r.status_code == 200:
                    raw = r.json().get("content") or ""
                    contents[path] = base64.b64decode(raw).decode(
                        "utf-8", errors="replace")[:3000]
                else:
                    contents[path] = f"(HTTP {r.status_code})"
            except Exception as exc:
                contents[path] = f"Error: {exc}"
    return {"type": "source_files", "files_read": list(contents.keys()),
            "contents": contents}


# --- Tavily web research -----------------------------------------------------


async def gather_web_research(description: str) -> dict[str, Any]:
    if MODE != "production":
        return {"type": "web_research", "mock": True, "results": []}
    try:
        import httpx

        from nexus.aws_client import _client

        sm = _client("secretsmanager")
        raw = sm.get_secret_value(SecretId="forgescaler/research-api")["SecretString"]
        parsed = json.loads(raw) if raw.startswith("{") else {"api_key": raw.strip()}
        api_key = parsed.get("api_key", "")
        if not api_key:
            return {"type": "web_research", "error": "No Tavily API key configured"}
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post("https://api.tavily.com/search", json={
                "api_key": api_key, "query": description,
                "max_results": 5, "search_depth": "advanced",
            })
        if r.status_code != 200:
            return {"type": "web_research",
                    "error": f"Tavily returned {r.status_code}"}
        results = r.json().get("results", [])
        return {"type": "web_research", "results": [
            {"title": x.get("title"), "url": x.get("url"),
             "content": (x.get("content") or "")[:500]}
            for x in results
        ]}
    except Exception as exc:
        return {"type": "web_research", "error": f"{type(exc).__name__}: {exc}"}


# --- Deep Neptune queries -----------------------------------------------------


async def gather_neptune_deep(description: str) -> dict[str, Any]:
    if MODE != "production":
        return {"type": "neptune_deep", "mock": True, "queries_run": 0}
    try:
        prompt = (
            "Generate 2-3 read-only openCypher queries (MATCH only, no writes) "
            "to investigate this question about the Forgewing platform. "
            "Available labels: Tenant, MissionTask, RepoFile, MissionBrief, "
            "Project, OverwatchIncident, ConversationMessage, DeploymentProgress, "
            "BriefEntry.\n"
            f'Question: "{description}"\n'
            "Return ONLY a JSON array of Cypher strings. Start each with MATCH. "
            "Use aggregations (count, avg), sort, LIMIT 20."
        )
        text = await _invoke_bedrock(HAIKU, prompt, max_tokens=600)
        queries = parse_bedrock_json_array(text, fallback=[])[:3]
        if not queries:
            return {"type": "neptune_deep", "error": "no queries generated",
                    "raw": (text or "")[:200]}

        from nexus import neptune_client

        results: dict[str, Any] = {}
        for q in queries:
            safe = _sanitize_cypher(q)
            if not safe:
                results[q] = "BLOCKED: write or unsafe operation"
                continue
            try:
                rows = neptune_client.query(safe)
                results[q] = rows[:20]
            except Exception as exc:
                results[q] = f"Error: {exc}"
        return {"type": "neptune_deep", "queries_run": len(results), "results": results}
    except Exception as exc:
        return {"type": "neptune_deep", "error": f"{type(exc).__name__}: {exc}"}
