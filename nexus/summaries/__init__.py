"""Rolling summaries — daily/weekly/monthly memory compression for ARIA.

Schedule: Lambdas run on cron, write to Postgres table rolling_summaries.
"""
from nexus.summaries.generator import (
    generate_daily_digest,
    generate_monthly_arc,
    generate_weekly_rollup,
)
from nexus.summaries.store import read_summaries, save_summary

__all__ = [
    "generate_daily_digest",
    "generate_weekly_rollup",
    "generate_monthly_arc",
    "read_summaries",
    "save_summary",
]
