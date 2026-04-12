"""Overwatch Code Audit — rule registry."""
from nexus.audit_rules.api_contract import ApiContractMismatch
from nexus.audit_rules.file_limits import FileLimits
from nexus.audit_rules.frontend_scoping import FrontendScoping
from nexus.audit_rules.isolation_escapes import IsolationEscapes
from nexus.audit_rules.param_propagation import ParamPropagation
from nexus.audit_rules.react_antipatterns import ReactAntiPatterns
from nexus.audit_rules.stale_references import StaleReferences
from nexus.audit_rules.unsafe_neptune import UnsafeNeptune
from nexus.audit_rules.unscoped_queries import UnScopedQueries
from nexus.audit_rules.untagged_writes import UntaggedWrites

ALL_RULES = [
    UnScopedQueries,
    UntaggedWrites,
    ApiContractMismatch,
    ParamPropagation,
    ReactAntiPatterns,
    FileLimits,
    IsolationEscapes,
    StaleReferences,
    UnsafeNeptune,
    FrontendScoping,
]
