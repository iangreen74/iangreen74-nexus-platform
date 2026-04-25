"""V2 Postgres connection-URL resolver.

Returns a psycopg2-style connection URL by trying, in order:
  1. OVERWATCH_V2_DATABASE_URL env var (operator override / local-dev escape)
  2. Secrets Manager secret 'overwatch-v2/postgres-master' (production path)

The reasoner role grants secretsmanager:GetSecretValue on overwatch-v2/*,
so the production path resolves without any task-def env-var management.
The host is hardcoded against the Day-1 outputs.json value (also public —
RDS endpoint, not a secret).
"""
from __future__ import annotations

import functools
import json
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

V2_POSTGRES_HOST = (
    "overwatch-postgres.cj0quk64skxf.us-east-1.rds.amazonaws.com"
)
V2_POSTGRES_PORT = 5432
V2_POSTGRES_DB = "overwatch"
V2_POSTGRES_SECRET_ID = "overwatch-v2/postgres-master"
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")


def _from_env() -> Optional[str]:
    val = os.environ.get("OVERWATCH_V2_DATABASE_URL", "").strip()
    return val or None


@functools.lru_cache(maxsize=1)
def _from_secrets_manager() -> Optional[str]:
    """Fetch master credentials and assemble a connection URL.

    Cached for the lifetime of the process — the secret rotates infrequently
    and the cost of a Secrets Manager call per query is non-trivial.
    """
    try:
        import boto3
        sm = boto3.client("secretsmanager", region_name=AWS_REGION)
        raw = sm.get_secret_value(SecretId=V2_POSTGRES_SECRET_ID)["SecretString"]
        creds = json.loads(raw)
        user = creds["username"]
        pwd = creds["password"]
    except Exception:
        logger.exception("V2 db url: Secrets Manager fallback failed")
        return None
    return (
        f"postgresql://{user}:{pwd}@{V2_POSTGRES_HOST}:"
        f"{V2_POSTGRES_PORT}/{V2_POSTGRES_DB}"
    )


def database_url() -> Optional[str]:
    """Resolve the V2 Postgres connection URL.

    Returns None when neither path produces a value (typically: local-mode
    runs without env var set). Callers degrade to the in-memory store.
    """
    return _from_env() or _from_secrets_manager()


def reset_cache_for_tests() -> None:
    """Invalidate the secret-fetch cache. Tests only."""
    _from_secrets_manager.cache_clear()
