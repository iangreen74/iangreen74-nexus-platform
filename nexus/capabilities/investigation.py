"""
Tier 1 Investigation — natural-language question → parallel evidence
gathering → Bedrock synthesis → structured diagnosis.

Most evidence sources reuse existing functions (deploy drift, synthetic
suite, tenant health, open incidents, CI monitor) wrapped in
asyncio.to_thread for parallel execution. Only CloudWatch log filtering
is genuinely new.

Per-gatherer try/except: one source going down can't kill the
investigation. Bedrock failures degrade to "raw evidence shown" rather
than crashing the endpoint.

logs:FilterLogEvents is NOT in aria-ecs-task-role today — the
cloudwatch gatherer will return AccessDeniedException-as-evidence in
production. That's the honest signal; the synthesizer just sees less
data. Add the IAM permission to enable it.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from nexus.capabilities.bedrock_utils import parse_bedrock_json, parse_bedrock_json_array
from nexus.config import AWS_REGION, MODE, OPS_CHAT_MODEL_ID

logger = logging.getLogger(__name__)

# Architecture preamble for the synthesizer. Without this, Bedrock keeps
# recommending fixes for decisions we've already made (separate clusters,
# IAM permissions already added) and flagging intentional design as drift.
# Short-term workaround; long-term this should be learned from diagnosis
# history via the pattern-learning tier.
_KNOWN_CONTEXT = (
    "Known architecture (do NOT flag any of these as issues):\n"
    "- aria-console runs in the overwatch-platform ECS cluster — intentionally "
    "separate from customer services in aria-platform.\n"
    "- aria-console uses the nexus-platform ECR image — intentionally a "
    "different repo from forgescaler/aria-daemon.\n"
    "- ECS image 'drift' between forgescaler, aria-daemon, and aria-console is "
    "expected: each ships independently from its own Dockerfile.\n"
    "- logs:FilterLogEvents permission was added to aria-ecs-task-role on "
    "2026-04-13; older CloudWatch evidence may still show AccessDeniedException "
    "that is already resolved.\n"
    "- aria-platform cluster and overwatch-platform cluster are both expected "
    "to exist and be ACTIVE.\n"
    "- A tenant status='pending' with deployment.provisioned=False is NORMAL "
    "during the Build phase — it means the tenant hasn't deployed to AWS yet. "
    "Not a deployment failure.\n"
    "- A MissionTask in status='pending' that is waiting behind another task "
    "in status='in_review' (PR awaiting merge) is NORMAL pipeline behavior, "
    "not a stuck task.\n"
    "- forge-1dba4143ca24ed1f (Ian's test project) at stage=complete with no "
    "deployments is expected — it's a test/demo tenant.\n"
    "- Projects at stage=ingesting or ingestion_pending for less than 15 "
    "minutes are normal (pipeline in progress). Only flag stuck after that.\n"
    "- Connect-mode projects typically take 2-5 minutes to complete "
    "ingestion on a typical repo.\n"
    "- SFS (Start-from-Scratch) projects typically take 1-3 minutes to "
    "scaffold and ingest.\n"
    "- Brief isolation was definitively fixed on 2026-04-14 (86b1ac8): all 4 "
    "data inputs to _synthesize (BriefEntry, task list, AnalysisReport, "
    "PredictedTask/OmniscientInsight) are project-scoped via scoped_filter. "
    "If project_separation or brief_project_isolation synthetics fail AFTER "
    "that date, it is a NEW leak, not the historical one.\n"
    "- Orphan BriefEntry/MissionBrief nodes with NULL project_id were purged "
    "for forge-1dba4143ca24ed1f on 2026-04-14. A 'missing project_id' "
    "finding for this tenant after that date indicates a regression in the "
    "write path.\n"
    "- Task 17 on forge-6b3550bef6c41d1b is waiting for Ben to merge PR #17 "
    "on GitHub — NORMAL pipeline behavior (task behind an in_review PR), "
    "not stuck. Do not recommend manual intervention.\n"
    "- Intent Haiku JSON parse errors were fixed (f791107) via "
    "parse_bedrock_json. 'Extra data' errors in CloudWatch after that "
    "deploy indicate a new model response format issue.\n"
    "- Neptune orphan-node query was rewritten as per-label OPTIONAL MATCH "
    "queries (2923034). Neptune Analytics openCypher does NOT support "
    "NOT EXISTS subqueries or label disjunction in WHERE clauses.\n"
    "- NEXUS Platform CI green rate is recovering from historical failures. "
    "The 100-run window still includes pre-fix runs; rate should climb "
    "above 95% within 24 hours as new passing runs accumulate.\n"
    "- Daemon heartbeat is now independent of cycle completion (42dc476). "
    "DaemonCycle nodes are written at cycle START, so a stale heartbeat "
    "(>5min) genuinely indicates a stuck daemon, not a slow cycle.\n"
)

CLASSIFIER_MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
SYNTHESIZER_MODEL_ID = OPS_CHAT_MODEL_ID  # claude-sonnet-4-6
# Per-gatherer budget. A top-level wait_for on the whole gather cancels
# completed-but-unreported siblings when any one runs long, which erases
# evidence for the report. Per-gatherer timeout preserves partial results.
PER_GATHERER_TIMEOUT_SEC = 30
GATHER_TIMEOUT_SEC = 45  # hard ceiling (ALB idle is 60s); fail-safe only


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- Evidence gatherers (wrap existing capabilities) -------------------------


_TRACEBACK_FRAME = re.compile(
    r'File "(?P<file>[^"]+\.py)", line (?P<line>\d+)'
    r'(?:, in (?P<func>[A-Za-z_][A-Za-z0-9_]*))?'
)

_LOGGER_LINE = re.compile(
    r'^(?P<logger>[\w.]+)\s+(?P<level>ERROR|CRITICAL|WARNING)\s*[-:]\s*(?P<msg>.+)$',
    re.MULTILINE,
)


def _parse_traceback(message: str) -> dict[str, Any] | None:
    """
    Extract `file`, `line`, `function`, and short `exc` summary from a
    log message containing a Python traceback. Picks the LAST (deepest)
    frame — that's the actual failure site. Returns None when no
    traceback-shaped frame is present.
    """
    frames = list(_TRACEBACK_FRAME.finditer(message or ""))
    if not frames:
        return None
    last = frames[-1]
    file_path = last.group("file")
    if file_path.startswith("/app/"):
        file_path = file_path[len("/app/"):]
    lines = [ln for ln in (message or "").splitlines() if ln.strip()]
    exc = lines[-1].strip() if lines else ""
    return {
        "file": file_path,
        "line": int(last.group("line")),
        "function": last.group("func") or "",
        "exc": exc[:160],
    }


def _logger_summary(message: str) -> str:
    """Pull the first logger/level/message match — gives a clean 1-liner."""
    m = _LOGGER_LINE.search(message or "")
    if not m:
        return (message or "").splitlines()[0][:200]
    return f"{m.group('logger')} {m.group('level')} - {m.group('msg').strip()}"[:200]


def _fetch_context(client, log_group: str, ts_ms: int, window_ms: int = 5000,
                    limit: int = 10) -> list[dict[str, Any]]:
    """Surrounding log events around ts_ms — stitches split tracebacks."""
    if not ts_ms:
        return []
    try:
        resp = client.filter_log_events(
            logGroupName=log_group,
            startTime=max(0, ts_ms - window_ms),
            endTime=ts_ms + window_ms,
            limit=limit,
        )
        return [{"timestamp": ev.get("timestamp"),
                 "message": (ev.get("message") or "")[:400]}
                for ev in resp.get("events", [])]
    except Exception:
        return []


async def _gather_cloudwatch(timeframe_minutes: int = 30) -> dict[str, Any]:
    if MODE != "production":
        return {"type": "cloudwatch", "mock": True, "entries": []}

    def _pull():
        from nexus import aws_client
        client = aws_client._client("logs")
        end = int(datetime.now(timezone.utc).timestamp() * 1000)
        start = end - (timeframe_minutes * 60 * 1000)
        out: list[dict[str, Any]] = []
        for log_group in ("/ecs/forgescaler", "/ecs/forgescaler-staging",
                           "/aria/daemon", "/aria/console", "/aria/agents"):
            try:
                resp = client.filter_log_events(
                    logGroupName=log_group, startTime=start, endTime=end,
                    filterPattern="ERROR", limit=20,
                )
                for ev in resp.get("events", []):
                    raw = ev.get("message") or ""
                    ts_ms = ev.get("timestamp") or 0
                    entry: dict[str, Any] = {
                        "source": log_group,
                        "timestamp": ts_ms,
                        "message": raw[:1200],
                        "summary": _logger_summary(raw),
                    }
                    frame = _parse_traceback(raw)
                    if frame:
                        entry.update(frame)
                    else:
                        ctx = _fetch_context(client, log_group, ts_ms)
                        if ctx:
                            entry["context"] = ctx
                            combined = "\n".join(c["message"] for c in ctx)
                            frame = _parse_traceback(combined)
                            if frame:
                                entry.update(frame)
                    out.append(entry)
            except Exception as exc:
                out.append({"source": log_group, "error": str(exc)[:200]})
        return out

    entries = await asyncio.to_thread(_pull)
    return {"type": "cloudwatch", "count": len(entries), "entries": entries[:30]}


async def _gather_ecs() -> dict[str, Any]:
    from nexus.capabilities.ci_cd_gates import check_deploy_drift
    return {"type": "ecs", **await asyncio.to_thread(check_deploy_drift)}


async def _gather_neptune() -> dict[str, Any]:
    def _pull():
        from nexus import overwatch_graph
        from nexus.sensors import tenant_health
        tenants = tenant_health.check_all_tenants() or []
        incidents = overwatch_graph.get_open_incidents() or []
        return {
            "tenant_count": len(tenants),
            "tenants": [
                {"id": t.get("tenant_id"),
                 "stage": (t.get("context") or {}).get("mission_stage"),
                 "overall_status": t.get("overall_status"),
                 "deploy_stuck": t.get("deploy_stuck", False)}
                for t in tenants
            ],
            "open_incidents": len(incidents),
            "incident_summary": [
                {"source": i.get("source"), "type": i.get("type"),
                 "root_cause": i.get("root_cause", "")[:200]}
                for i in incidents[:10]
            ],
        }
    return {"type": "neptune", **await asyncio.to_thread(_pull)}


async def _gather_github_ci() -> dict[str, Any]:
    def _pull():
        from nexus.sensors import ci_monitor
        return ci_monitor.check_ci() or {}
    data = await asyncio.to_thread(_pull)
    return {
        "type": "github_ci",
        "green_rate_24h": data.get("green_rate_24h"),
        "run_count": data.get("run_count"),
        "failing_workflows": data.get("failing_workflows", []),
        "last_run_status": data.get("last_run_status"),
        "repos_checked": data.get("repos_checked", []),
    }


async def _gather_synthetic() -> dict[str, Any]:
    from nexus.capabilities.ci_cd_gates import run_synthetic_suite
    return {"type": "synthetic", **await asyncio.to_thread(run_synthetic_suite, "investigation", "")}


async def _gather_platform_events() -> dict[str, Any]:
    def _pull():
        from nexus import overwatch_graph
        from nexus.reasoning.executor import get_all_active_chains
        events = overwatch_graph.get_recent_events(limit=30) or []
        chains = get_all_active_chains() or {}
        return {
            "recent_events": [
                {"event_type": e.get("event_type"), "service": e.get("service"),
                 "severity": e.get("severity"), "created_at": e.get("created_at")}
                for e in events[:15]
            ],
            "active_heal_chains": [
                {"source": k, "chain": v.get("chain"), "step": v.get("step")}
                for k, v in chains.items()
            ],
        }
    return {"type": "platform_events", **await asyncio.to_thread(_pull)}


_GATHERERS: dict[str, Any] = {
    "cloudwatch": _gather_cloudwatch,
    "ecs": _gather_ecs,
    "neptune": _gather_neptune,
    "github_ci": _gather_github_ci,
    "synthetic": _gather_synthetic,
    "platform_events": _gather_platform_events,
}


# --- Bedrock --------------------------------------------------------------


def _invoke_bedrock(model_id: str, prompt: str, max_tokens: int = 1500) -> str:
    """Synchronous Bedrock invocation; matches ops_chat.py pattern."""
    import boto3
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
    for block in body.get("content", []):
        if block.get("type") == "text":
            return block.get("text", "")
    return ""


def _strip_fences(text: str) -> str:
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
    if text.endswith("```"):
        text = text.rsplit("```", 1)[0]
    return text.strip()


async def _classify(question: str) -> list[str]:
    """Pick relevant evidence sources. Falls back to ALL on failure."""
    all_sources = list(_GATHERERS.keys())
    if MODE != "production":
        return all_sources
    prompt = (
        "Forgewing platform investigation router. Given the question below, "
        "return ONLY a JSON array of evidence source names that should be "
        "queried (no explanation, no markdown).\n\n"
        f"Available sources: {all_sources}\n\n"
        f"Question: {question}"
    )
    try:
        text = await asyncio.to_thread(_invoke_bedrock, CLASSIFIER_MODEL_ID, prompt, 200)
        parsed = parse_bedrock_json_array(text, fallback=[])
        if isinstance(parsed, list) and parsed:
            picked = [s for s in parsed if s in _GATHERERS]
            return picked or all_sources
    except Exception as exc:
        logger.warning("classifier failed, using all sources: %s", exc)
    return all_sources


async def _synthesize(question: str, evidence: dict[str, Any]) -> dict[str, Any]:
    """Sonnet synthesizes evidence into a structured diagnosis."""
    if MODE != "production":
        return {
            "root_cause": "Local mode — no Bedrock call",
            "explanation": "Investigation ran end-to-end in local mode; review evidence.",
            "confidence": 0, "severity": "unknown",
            "recommended_actions": [], "evidence_used": list(evidence.keys()),
            "evidence_gaps": [],
        }
    evidence_text = json.dumps(evidence, indent=2, default=str)[:14000]
    prompt = (
        "You are Overwatch, the autonomous platform engineer for Forgewing.\n\n"
        f"{_KNOWN_CONTEXT}\n"
        f'Operator question: "{question}"\n\n'
        f"Evidence gathered:\n{evidence_text}\n\n"
        "Return ONLY valid JSON matching this shape:\n"
        '{"root_cause": "one sentence", '
        '"explanation": "2-3 paragraphs citing evidence", '
        '"confidence": 0-100, '
        '"severity": "critical|high|medium|low", '
        '"recommended_actions": [{"action": "what", "priority": "immediate|soon|later", '
        '"type": "code_fix|config_change|data_fix|restart|investigate_further"}], '
        '"evidence_used": ["source names"], "evidence_gaps": ["what we could not determine"]}'
    )
    fallback = {
        "root_cause": "Bedrock synthesis produced unparseable output — see raw evidence",
        "explanation": "Synthesizer returned text that could not be parsed as JSON; "
                       "raw evidence is included below.",
        "confidence": 0, "severity": "unknown",
        "recommended_actions": [
            {"action": "Review raw evidence below", "priority": "immediate",
             "type": "investigate_further"}],
        "evidence_used": list(evidence.keys()),
        "evidence_gaps": ["synthesis (JSON parse failed)"],
    }
    try:
        text = await asyncio.to_thread(_invoke_bedrock, SYNTHESIZER_MODEL_ID, prompt, 12000)
        parsed = parse_bedrock_json(text, fallback=fallback)
        # parse_bedrock_json returns the fallback (with extra error/raw keys)
        # if it couldn't parse — that's fine, surface it as-is.
        return parsed
    except Exception as exc:
        logger.warning("synthesizer failed: %s", exc)
        out = dict(fallback)
        out["root_cause"] = f"Synthesis failed: {type(exc).__name__}"
        out["explanation"] = f"Evidence gathered but Bedrock synthesis raised: {str(exc)[:200]}"
        out["evidence_gaps"] = ["all (synthesis failed)"]
        return out


# --- Context injection for synthesis -----------------------------------------


def _inject_session_context(evidence: dict[str, Any]) -> None:
    """
    Enrich the evidence dict with forward-looking + session-level signal
    right before Bedrock synthesis. Each source is best-effort — a
    missing module or failed call leaves the key absent rather than
    breaking investigation.
    """
    # Keys are underscore-prefixed so they flow into the synthesis prompt
    # but don't appear in the classifier's sources_returned list (which
    # filters out _-prefixed keys). Keeps classifier contract intact.
    try:
        from nexus.capabilities.session_context import gather_session_context
        evidence["_recent_commits"] = gather_session_context(hours=24)
    except Exception:
        logger.debug("session_context unavailable", exc_info=True)
    try:
        from nexus.capabilities.sprint_context import get_status
        evidence["_sprint_context"] = get_status()
    except Exception:
        logger.debug("sprint_context unavailable", exc_info=True)
    try:
        from nexus.capabilities.trend_analysis import compute_trend
        from nexus.sensors import ci_monitor
        ci = ci_monitor.check_ci() or {}
        rate = ci.get("green_rate_24h")
        if rate is not None:
            evidence["_trend_ci_green_rate"] = compute_trend(
                "ci_green_rate", float(rate))
    except Exception:
        logger.debug("trend_analysis unavailable", exc_info=True)
    try:
        from nexus.capabilities.predictions import generate_predictions
        preds = generate_predictions(
            ci_data={"trend": evidence.get("_trend_ci_green_rate", {})})
        if preds:
            evidence["_predictions"] = preds
    except Exception:
        logger.debug("predictions unavailable", exc_info=True)


# --- Orchestrator ------------------------------------------------------------


async def investigate(question: str, timeframe_minutes: int = 30) -> dict[str, Any]:
    """Full investigation pipeline. Never raises."""
    started = datetime.now(timezone.utc)
    question = (question or "").strip()
    if not question:
        return {"error": "question is required", "tier": 1}

    sources = await _classify(question)

    async def _bounded(name: str, coro: Any) -> tuple[str, Any]:
        """Wrap each gatherer so its timeout only kills itself, not siblings."""
        try:
            result = await asyncio.wait_for(coro, timeout=PER_GATHERER_TIMEOUT_SEC)
            return name, result
        except asyncio.TimeoutError:
            return name, {"type": name,
                          "error": f"gatherer timed out after {PER_GATHERER_TIMEOUT_SEC}s"}
        except Exception as exc:
            return name, {"type": name,
                          "error": f"{type(exc).__name__}: {str(exc)[:200]}"}

    coros = []
    for s in sources:
        fn = _GATHERERS.get(s)
        if not fn:
            continue
        inner = fn(timeframe_minutes) if s == "cloudwatch" else fn()
        coros.append(_bounded(s, inner))

    evidence: dict[str, Any] = {}
    if coros:
        try:
            results = await asyncio.wait_for(
                asyncio.gather(*coros, return_exceptions=False),
                timeout=GATHER_TIMEOUT_SEC,
            )
            for name, result in results:
                evidence[name] = result
        except asyncio.TimeoutError:
            evidence["_timeout"] = {
                "error": f"overall gather exceeded {GATHER_TIMEOUT_SEC}s "
                         f"(per-gatherer budget {PER_GATHERER_TIMEOUT_SEC}s)"
            }

    _inject_session_context(evidence)
    diagnosis = await _synthesize(question, evidence)
    return {
        "question": question, "tier": 1,
        "sources_requested": sources,
        "sources_returned": [k for k in evidence if not k.startswith("_")],
        "diagnosis": diagnosis, "evidence": evidence,
        "duration_seconds": round((datetime.now(timezone.utc) - started).total_seconds(), 1),
        "classifier_model": CLASSIFIER_MODEL_ID,
        "synthesizer_model": SYNTHESIZER_MODEL_ID,
        "timestamp": _now_iso(),
    }
