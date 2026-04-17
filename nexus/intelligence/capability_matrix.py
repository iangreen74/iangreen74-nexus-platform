"""Forgewing capability matrix — what we can deploy, honestly."""
from collections import Counter

CAPABILITY_MATRIX = [
    ("python/fastapi + sqlite + ecs-fargate", "proven",
     "blog1 PR#1 (iangreen74/blog1)", ""),
    ("python/flask + ecs-fargate", "architected",
     "dogfood catalogue entry; daemon + codegen exist", "production evidence"),
    ("node/express + ecs-fargate", "architected",
     "dogfood catalogue entry; daemon + codegen exist", "production evidence"),
    ("react/vite + s3-cloudfront", "architected",
     "Day 8 deploy router is_spa() path", "production evidence"),
    ("react/vite + fastapi-backend (multi-svc)", "architected",
     "Day 8 multi-service ECS (detect_services)", "production evidence"),
    ("python/fastapi + dynamodb + ecs", "architected",
     "iac_generator CFN fragments", "production evidence"),
    ("python/fastapi + cognito + ecs", "architected",
     "iac_generator CFN fragments", "production evidence"),
    ("python/fastapi + s3 + ecs", "architected",
     "iac_generator CFN fragments", "production evidence"),
    ("python/fastapi + secretsmanager + ecs", "architected",
     "iac_generator CFN fragments", "production evidence"),
    ("rust/go/java runtimes", "roadmap", "",
     "language detection + Dockerfile templates + buildpack"),
    ("persistent disk (EBS/EFS mount)", "roadmap", "",
     "EFS CFN fragment + mount logic in task def"),
    ("background workers (celery/bullmq)", "roadmap", "",
     "non-web service type in deploy_strategy"),
    ("GPU workloads", "roadmap", "",
     "GPU-enabled ECS task defs + instance type selection"),
    ("serverless (Lambda/Step Functions)", "roadmap", "",
     "deploy pipeline target routing"),
    ("multi-region deploys", "roadmap", "",
     "cross-region CFN orchestration + DNS failover"),
    ("multi-account deploys", "roadmap", "",
     "cross-account role chaining + account discovery"),
    ("complex networking (VPC/PrivateLink)", "roadmap", "",
     "VPC template library + customer VPC import flow"),
    ("RDS PostgreSQL", "roadmap", "",
     "RDS CFN fragment + connection string injection"),
    ("Aurora", "roadmap", "",
     "Aurora CFN fragment + cluster management"),
    ("ElastiCache", "roadmap", "",
     "ElastiCache CFN fragment + Redis client injection"),
]


def render_matrix() -> str:
    lines = ["| Capability | Status | Evidence | Gap to close |",
             "|---|---|---|---|"]
    for cap, status, evidence, gap in CAPABILITY_MATRIX:
        s = {"proven": "✓ proven", "architected": "~ architected",
             "roadmap": "○ roadmap"}.get(status, status)
        lines.append(f"| {cap} | {s} | {evidence or '—'} | {gap or '—'} |")
    return "\n".join(lines)


def status_counts() -> dict[str, int]:
    return dict(Counter(row[1] for row in CAPABILITY_MATRIX))
