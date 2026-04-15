"""
AWS Cost Monitor — daily spend + monthly projection + credits runway.

Reads Cost Explorer. Goal reports get a "Cost" section so an operator
sees spend trends the same place they see CI health. Requires
`ce:GetCostAndUsage` on the task IAM role; if the role can't read Cost
Explorer the sensor returns a structured error rather than breaking
diagnosis.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from nexus.config import MODE

logger = logging.getLogger("nexus.capabilities.cost_monitor")

# Pure placeholder: there's no API for "credits remaining". Operator can
# override via env or a small admin route later; for now we expose the
# constant so the projection math is transparent.
DEFAULT_CREDITS_REMAINING = 4038.00


def _ce_client():
    from nexus.aws_client import _client
    return _client("ce")


def _date(d: Any) -> str:
    return d.isoformat() if hasattr(d, "isoformat") else str(d)


def _parse_amount(group: dict[str, Any]) -> float:
    metrics = group.get("Metrics") or {}
    blended = metrics.get("UnblendedCost") or {}
    try:
        return float(blended.get("Amount") or 0)
    except (TypeError, ValueError):
        return 0.0


def _day_total(ce: Any, day: Any) -> float:
    """Sum UnblendedCost for a single date by calling Cost Explorer."""
    next_day = day + timedelta(days=1)
    resp = ce.get_cost_and_usage(
        TimePeriod={"Start": _date(day), "End": _date(next_day)},
        Granularity="DAILY",
        Metrics=["UnblendedCost"],
    ) or {}
    total = 0.0
    for period in resp.get("ResultsByTime", []) or []:
        total += _parse_amount(period)
    return round(total, 2)


def _mtd_with_services(ce: Any, start: Any, end: Any) -> tuple[float, list[dict[str, Any]]]:
    resp = ce.get_cost_and_usage(
        TimePeriod={"Start": _date(start), "End": _date(end)},
        Granularity="MONTHLY",
        Metrics=["UnblendedCost"],
        GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}],
    ) or {}
    total = 0.0
    per_service: list[dict[str, Any]] = []
    for period in resp.get("ResultsByTime", []) or []:
        for grp in period.get("Groups", []) or []:
            amt = _parse_amount(grp)
            total += amt
            name = (grp.get("Keys") or ["?"])[0]
            per_service.append({"service": name, "amount": round(amt, 2)})
    per_service.sort(key=lambda r: r["amount"], reverse=True)
    return round(total, 2), per_service[:8]


def _mock_summary() -> dict[str, Any]:
    return {
        "today": 31.42, "yesterday": 28.76, "month_to_date": 412.33,
        "projected_monthly": 962.00,
        "credits_remaining": DEFAULT_CREDITS_REMAINING,
        "burn_rate_per_day": 32.10,
        "credits_runway_days": 125,
        "top_services": [
            {"service": "Amazon EC2", "amount": 18.50},
            {"service": "Amazon Neptune", "amount": 5.20},
            {"service": "Amazon ECS", "amount": 3.10},
        ],
        "mock": True,
    }


def get_daily_spend() -> dict[str, Any]:
    """Daily/MTD/projection snapshot. Never raises — errors surface structured."""
    if MODE != "production":
        return _mock_summary()
    try:
        ce = _ce_client()
    except Exception as exc:
        return {"error": f"ce client unavailable: {type(exc).__name__}"}

    today = datetime.now(timezone.utc).date()
    yesterday = today - timedelta(days=1)
    month_start = today.replace(day=1)
    try:
        today_spend = _day_total(ce, today)
        yesterday_spend = _day_total(ce, yesterday)
        mtd, top_services = _mtd_with_services(ce, month_start, today + timedelta(days=1))
    except Exception as exc:
        logger.exception("cost_monitor CE call failed")
        return {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}

    days_elapsed = max(1, (today - month_start).days + 1)
    burn_rate = round(mtd / days_elapsed, 2) if days_elapsed else 0.0
    days_in_month = 30
    projected_monthly = round(burn_rate * days_in_month, 2)
    credits = DEFAULT_CREDITS_REMAINING
    runway_days = int(credits / burn_rate) if burn_rate > 0 else None

    return {
        "today": today_spend,
        "yesterday": yesterday_spend,
        "month_to_date": mtd,
        "projected_monthly": projected_monthly,
        "burn_rate_per_day": burn_rate,
        "credits_remaining": credits,
        "credits_runway_days": runway_days,
        "top_services": top_services,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


def format_for_report(summary: dict[str, Any] | None = None) -> str:
    """Markdown block for the Goal diagnosis."""
    s = summary if summary is not None else get_daily_spend()
    if s.get("error"):
        return f"## Cost\n_Cost data unavailable: {s['error']}_"
    lines = [
        "## Cost",
        f"- Today: ${s.get('today', 0):.2f} · Yesterday: ${s.get('yesterday', 0):.2f}",
        f"- Month-to-date: ${s.get('month_to_date', 0):.2f} · "
        f"Projected: ${s.get('projected_monthly', 0):.2f}",
        f"- Burn rate: ${s.get('burn_rate_per_day', 0):.2f}/day · "
        f"Credits runway: {s.get('credits_runway_days', '?')} days",
    ]
    top = s.get("top_services") or []
    if top:
        svcs = ", ".join(f"{t['service']} ${t['amount']:.2f}" for t in top[:4])
        lines.append(f"- Top services: {svcs}")
    return "\n".join(lines)


def journey_cost_monitoring() -> dict[str, Any]:
    """Synthetic: Cost Explorer reachable and returning data."""
    if MODE != "production":
        return {"name": "cost_monitoring", "status": "skip",
                "error": "Requires production Cost Explorer access"}
    s = get_daily_spend()
    if s.get("error"):
        return {"name": "cost_monitoring", "status": "fail",
                "error": s["error"][:200]}
    return {"name": "cost_monitoring", "status": "pass",
            "details": (f"MTD ${s.get('month_to_date', 0):.2f}, "
                         f"burn ${s.get('burn_rate_per_day', 0):.2f}/day")}
