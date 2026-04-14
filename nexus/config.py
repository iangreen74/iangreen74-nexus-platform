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

# Neptune Analytics (read-only access to Forgewing's graph)
# Forgewing runs on the neptune-graph API with openCypher, not classic Neptune.
NEPTUNE_GRAPH_ID = "g-1xwjj34141"

# Overwatch's own graph storage. Per the OVERWATCH doc, Overwatch shares the
# underlying Neptune Analytics graph with Forgewing but writes to a dedicated
# label namespace (Overwatch*) so the two systems never collide.
# This avoids the ~$300/mo baseline cost of a second Neptune Analytics graph.
OVERWATCH_GRAPH_ID = NEPTUNE_GRAPH_ID

# ECS clusters and services NEXUS monitors.
# Two-cluster split: aria-platform holds customer-facing services;
# overwatch-platform holds the control plane (aria-console / Overwatch).
FORGEWING_CLUSTER = "aria-platform"
FORGEWING_SERVICES = [
    "forgescaler",
    "forgescaler-staging",
    "aria-daemon",
]
OVERWATCH_CLUSTER = "overwatch-platform"
OVERWATCH_SERVICES = ["aria-console"]

# Canonical service → cluster map. Iterate this when checking every
# monitored service so new services don't have to be added in two places.
SERVICE_CLUSTERS: dict[str, str] = {
    **{s: FORGEWING_CLUSTER for s in FORGEWING_SERVICES},
    **{s: OVERWATCH_CLUSTER for s in OVERWATCH_SERVICES},
}
ALL_MONITORED_SERVICES = list(SERVICE_CLUSTERS.keys())

# Forgewing API endpoints (for health checks)
FORGEWING_API = "https://api.forgescaler.com"
FORGEWING_STAGING_API = "https://staging-api.forgescaler.com"
FORGEWING_WEB = "https://forgescaler.com"

# Operator console
CONSOLE_PORT = 9001

# Telegram alerts
TELEGRAM_SECRET_ID = "hyperlev/slack"  # Contains bot token + chat ID

# GitHub (for CI monitoring + Forge engine PR creation)
# `github-token` is a plain-string PAT (not JSON) shared with aria-platform.
GITHUB_SECRET_ID = "github-token"
GITHUB_ORG = "iangreen74"
GITHUB_REPOS = ["aria-platform", "iangreen74-nexus-platform"]

# Forge Engine — the aria-platform repo Overwatch can propose changes to.
ARIA_PLATFORM_REPO = "iangreen74/aria-platform"
ARIA_PLATFORM_DEFAULT_BRANCH = "main"
FORGE_PR_LABEL = "overwatch-fix"

# Ops Chat — Bedrock model used by /api/ops/chat. The aria-ecs-task-role
# already grants bedrock:InvokeModel on *. Override via env var if a
# different model needs to be tried.
OPS_CHAT_MODEL_ID = os.getenv(
    "OVERWATCH_OPS_MODEL", "us.anthropic.claude-sonnet-4-6"
)
OPS_CHAT_MAX_TOKENS = 2000

# Infrastructure lockdown — values that must NEVER drift. Any mismatch
# fires a critical alert. Add to this list cautiously; every entry is a
# promise that Overwatch will defend it on every poll.
COGNITO_USER_POOL_ID = "us-east-1_3dzaO4Dzl"
GITHUB_APP_ID = "2782895"

# Preemptive health thresholds
PREEMPTIVE_TASK_AGE_DAYS = 7         # alert if any ECS task is older than this
PREEMPTIVE_CERT_EXPIRY_DAYS = 30     # alert this many days before ACM cert expiry
PREEMPTIVE_SECRET_EXPIRY_DAYS = 14   # alert this many days before known secret expiries
# Known secret expiries by name → ISO date. Empty by default; populate via
# the operator console as you rotate things. Overwatch can't introspect PAT
# expiry from Secrets Manager metadata, so we track it here explicitly.
KNOWN_SECRET_EXPIRIES: dict[str, str] = {
    # "github-token": "2026-07-01",
}

# Thresholds
DAEMON_CYCLE_STALE_MINUTES = 15  # Alert if daemon hasn't cycled in this long
TENANT_INACTIVE_HOURS = 24  # Alert if tenant has no activity
HEALTH_CHECK_TIMEOUT_SECONDS = 30  # 10s was too tight — Neptune-backed /brief and /projects can take 15-20s under load, causing false-degraded synthetics (brief_exists, project_separation) that cascade into 3 degraded feature tiles.
MAX_HEALING_ACTIONS_PER_HOUR = 10  # Rate limit on auto-healing

# Capability blast radius classifications
BLAST_SAFE = "safe"          # Read-only or easily reversible
BLAST_MODERATE = "moderate"  # Write operation, reversible
BLAST_DANGEROUS = "dangerous"  # Could affect customer data/infra

# Triage confidence threshold for auto-healing
AUTO_HEAL_CONFIDENCE_THRESHOLD = 0.8
