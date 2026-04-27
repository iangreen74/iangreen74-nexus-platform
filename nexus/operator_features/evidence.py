"""EvidenceQuery: report-section data sources, plus FeatureTier.

An EvidenceQuery produces one report section for an OperatorFeature's
generated report. The ``section_kind`` maps to one of the four
section renderers in ``ReportSection.tsx`` (metric | table | list |
text). The ``kind`` selects which data source the report engine queries
against.

``accepts_tenant_id`` opt-in: most evidence queries are fleet-level,
but some (e.g. "tenant-specific failure rate over the last 24h") use
``{tenant_id}`` substitution in their spec.
"""
from __future__ import annotations

from enum import Enum, IntEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class FeatureTier(IntEnum):
    """Priority tier for an OperatorFeature.

    - CRITICAL (1): customer-visible MVP capability. Outage = stop-the-world.
    - IMPORTANT (2): degraded experience but not fatal.
    - NICE_TO_HAVE (3): internal tooling, observability layers.
    """
    CRITICAL = 1
    IMPORTANT = 2
    NICE_TO_HAVE = 3


class EvidenceQueryKind(str, Enum):
    """How an EvidenceQuery sources data for a report section."""
    CLOUDTRAIL_LOOKUP = "cloudtrail_lookup"
    CLOUDWATCH_LOGS = "cloudwatch_logs"
    NEPTUNE_CYPHER = "neptune_cypher"
    POSTGRES_QUERY = "postgres_query"
    ALB_ACCESS_LOGS = "alb_access_logs"
    S3_LISTING = "s3_listing"
    ECS_DESCRIBE = "ecs_describe"


class EvidenceQuery(BaseModel):
    """Produces one section of an OperatorFeature report.

    ``section_kind`` picks the renderer in ``ReportSection.tsx`` (0e.5);
    ``kind`` picks the data source the report engine (0e.2) queries.
    """
    model_config = ConfigDict(frozen=True)

    name: str  # human-readable section title
    kind: EvidenceQueryKind
    spec: dict[str, Any]
    section_kind: Literal["metric", "table", "list", "text"]
    accepts_tenant_id: bool = False
    max_results: int = 100
    freshness_window_seconds: int | None = None
