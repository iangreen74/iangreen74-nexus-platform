"""
NEXUS Platform Configuration

All connection details for the systems NEXUS monitors.
NEXUS never imports from aria-platform — it connects through
AWS APIs, Neptune queries, and HTTP endpoints only.
"""
import os

# Mode: 'local' for testing (mock everything), 'production' for real AWS
MODE = os.getenv("NEXUS_MODE", "production")

# AWS
AWS_REGION = "us-east-1"
AWS_ACCOUNT_ID = "418295677815"

# Neptune (read-only access to Forgewing's graph)
NEPTUNE_ENDPOINT = "g-1xwjj34141"
NEPTUNE_PORT = 8182

# ECS clusters and services NEXUS monitors
FORGEWING_CLUSTER = "aria-platform"
FORGEWING_SERVICES = [
    "forgescaler",
    "forgescaler-staging",
    "aria-daemon",
    "aria-console",
]

# Forgewing API endpoints (for health checks)
FORGEWING_API = "https://api.forgescaler.com"
FORGEWING_STAGING_API = "https://staging-api.forgescaler.com"
FORGEWING_WEB = "https://forgescaler.com"

# Operator console
CONSOLE_PORT = 9001

# Telegram alerts
TELEGRAM_SECRET_ID = "hyperlev/slack"  # Contains bot token + chat ID

# GitHub (for CI monitoring)
GITHUB_SECRET_ID = "nexus/github-pat"
GITHUB_ORG = "iangreen74"
GITHUB_REPOS = ["aria-platform", "iangreen74-nexus-platform"]

# Thresholds
DAEMON_CYCLE_STALE_MINUTES = 15  # Alert if daemon hasn't cycled in this long
TENANT_INACTIVE_HOURS = 24  # Alert if tenant has no activity
HEALTH_CHECK_TIMEOUT_SECONDS = 10
MAX_HEALING_ACTIONS_PER_HOUR = 10  # Rate limit on auto-healing

# Capability blast radius classifications
BLAST_SAFE = "safe"          # Read-only or easily reversible
BLAST_MODERATE = "moderate"  # Write operation, reversible
BLAST_DANGEROUS = "dangerous"  # Could affect customer data/infra

# Triage confidence threshold for auto-healing
AUTO_HEAL_CONFIDENCE_THRESHOLD = 0.8
