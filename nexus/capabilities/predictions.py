"""
Predictions — forward-looking projections from current state + history.

Overwatch already knows what's wrong now. This module answers the
second question an operator actually cares about: "when does it clear,
or when does the next thing break?" The output is a list of
prediction dicts with a category, a human sentence, a confidence, and
the raw data the projection is built on (so a reader can sanity-check).

Three prediction categories today; the interface takes loosely-typed
input dicts so new categories can be slotted in without a schema
migration:

  - pipeline   : task completion ETA from per-tenant velocity
  - ci         : green-rate recovery ETA from the trend engine
  - infrastructure : disk-fill ETA from df snapshots (stub when missing)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("nexus.capabilities.predictions")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _fmt_eta(value: float, unit: str) -> str:
    if value is None:
        return "unknown"
    if value < 1:
        return f"<1 {unit}"
    return f"~{value:.0f} {unit}"


def _pipeline_prediction(tenant_data: dict[str, Any]) -> dict[str, Any] | None:
    """
    Estimate time-to-finish for a tenant with open PRs. Velocity is
    derived from completed_tasks_24h (or completed_tasks / days_active);
    caller supplies whichever it has.
    """
    if not isinstance(tenant_data, dict) or not tenant_data:
        return None
    open_prs = int(tenant_data.get("open_prs") or 0)
    velocity = float(tenant_data.get("velocity_per_day") or 0)
    name = tenant_data.get("tenant_name") or tenant_data.get("tenant_id") or "tenant"
    if open_prs <= 0 or velocity <= 0:
        return None
    eta_days = open_prs / velocity
    confidence = 0.85 if velocity >= 1 else 0.65
    return {
        "category": "pipeline",
        "prediction": (f"{name}: {open_prs} PRs open, at {velocity:.1f} PRs/day, "
                       f"all tasks complete in {_fmt_eta(eta_days, 'day')}"),
        "confidence": confidence,
        "data": {"tenant": name, "open_prs": open_prs,
                 "velocity_per_day": round(velocity, 2),
                 "eta_days": round(eta_days, 1)},
    }


def _ci_prediction(ci_data: dict[str, Any]) -> dict[str, Any] | None:
    """
    Derive a recovery ETA from the trend engine. The caller can either
    pass a pre-computed trend dict under 'trend' or raw fields under
    'current_rate' + 'improvement_per_hour' (used as fallback).
    """
    if not isinstance(ci_data, dict) or not ci_data:
        return None
    trend = ci_data.get("trend") or {}
    current = trend.get("current", ci_data.get("current_rate"))
    rate = trend.get("rate", ci_data.get("improvement_per_hour"))
    target = trend.get("target", 0.95)
    projected = trend.get("projected_threshold_time")
    direction = trend.get("direction")

    if current is None:
        return None
    current = float(current)
    rate = float(rate or 0)

    if direction == "improving" and projected:
        eta_label = projected[11:16] + " UTC"
        sentence = (f"CI green rate recovering at +{rate*100:.1f}%/hr, will "
                    f"cross {int(target*100)}% threshold at ~{eta_label}")
        confidence = 0.8
    elif direction == "degrading":
        sentence = (f"CI green rate degrading at {rate*100:.1f}%/hr — "
                    f"diverging from {int(target*100)}% target")
        confidence = 0.75
    elif current >= target:
        sentence = f"CI green rate at {current*100:.0f}% — above target, holding"
        confidence = 0.9
    else:
        sentence = (f"CI green rate {current*100:.0f}% below {int(target*100)}% "
                    f"target but stable; no ETA")
        confidence = 0.5

    return {
        "category": "ci", "prediction": sentence, "confidence": confidence,
        "data": {"current_rate": round(current, 3),
                 "improvement_per_hour": round(rate, 4),
                 "target": target, "eta": projected},
    }


def _infra_prediction(runner_data: dict[str, Any]) -> dict[str, Any] | None:
    """
    Linear projection of disk fill toward an 80% warn threshold. Skips
    cleanly when we don't have df telemetry (SSM disk output isn't wired
    yet — predictor degrades gracefully rather than inventing numbers).
    """
    if not isinstance(runner_data, dict) or not runner_data:
        return None
    current_pct = runner_data.get("disk_used_pct")
    growth = runner_data.get("disk_growth_per_day")
    runner = runner_data.get("runner_name") or "runner"
    if current_pct is None or growth is None:
        return None
    current_pct = float(current_pct)
    growth = float(growth)
    if growth <= 0 or current_pct >= 80:
        return None
    eta_days = (80 - current_pct) / growth
    return {
        "category": "infrastructure",
        "prediction": (f"{runner} disk: {current_pct:.0f}% used, 80% threshold "
                       f"estimated in {_fmt_eta(eta_days, 'days')} at "
                       f"{growth:.1f}%/day growth"),
        "confidence": 0.7,
        "data": {"runner": runner, "current_pct": round(current_pct, 1),
                 "growth_per_day": round(growth, 2),
                 "eta_days": round(eta_days, 1)},
    }


def generate_predictions(
    tenant_data: dict[str, Any] | list[dict[str, Any]] | None = None,
    ci_data: dict[str, Any] | None = None,
    runner_data: dict[str, Any] | list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """
    Produce the list of prediction dicts. Inputs are optional dicts (or
    lists for multi-tenant / multi-runner cases). Never raises —
    missing data yields fewer predictions, not an error.
    """
    out: list[dict[str, Any]] = []
    tenants = (tenant_data if isinstance(tenant_data, list)
               else [tenant_data] if tenant_data else [])
    for td in tenants:
        try:
            pred = _pipeline_prediction(td)
        except Exception:
            logger.exception("pipeline prediction failed")
            pred = None
        if pred:
            out.append(pred)

    try:
        ci_pred = _ci_prediction(ci_data or {})
    except Exception:
        logger.exception("ci prediction failed")
        ci_pred = None
    if ci_pred:
        out.append(ci_pred)

    runners = (runner_data if isinstance(runner_data, list)
               else [runner_data] if runner_data else [])
    for rd in runners:
        try:
            pred = _infra_prediction(rd)
        except Exception:
            logger.exception("infra prediction failed")
            pred = None
        if pred:
            out.append(pred)
    return out


def format_for_report(predictions: list[dict[str, Any]]) -> str:
    """Render predictions as a markdown list for the Goal diagnosis."""
    if not predictions:
        return "_No predictions — insufficient history or data._"
    lines = []
    for p in predictions:
        cat = p.get("category", "?").upper()
        conf = int((p.get("confidence") or 0) * 100)
        lines.append(f"- [{cat} · {conf}% conf] {p.get('prediction', '')}")
    return "\n".join(lines)
