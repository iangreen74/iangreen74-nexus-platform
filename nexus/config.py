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
