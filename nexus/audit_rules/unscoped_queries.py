"""Rule: Neptune queries on project-scoped types missing project_filter."""
from __future__ import annotations

import os
import re

from nexus.audit_rules.base import AuditRule, Finding

SCOPED_TYPES = [
    "MissionTask", "MissionBrief", "ConversationMessage", "BriefEntry",
    "RepoFile", "IngestRun", "UserInteraction", "SelfHealAction",
    "DeployReadiness", "CustomerCIRun",
]
EXEMPT_FILES = {"project_manager.py", "admin_routes.py", "migrate"}
SCOPED_PATTERNS = [
    r"project_filter", r"project_id\s*=", r"pid_prop",
    r"\.project_id\s*=\s*\$", r"project_id:\s*\$",
]


class UnScopedQueries(AuditRule):
    name = "unscoped_queries"
    description = "Neptune queries on project-scoped types missing project_filter"
    severity = "critical"

    def scan(self, repo_path: str) -> list[Finding]:
        findings: list[Finding] = []
        for root, _, files in os.walk(repo_path):
            if ".git" in root or "node_modules" in root:
                continue
            for fname in files:
                if not fname.endswith(".py"):
                    continue
                if any(ex in fname for ex in EXEMPT_FILES):
                    continue
                fpath = os.path.join(root, fname)
                rel = os.path.relpath(fpath, repo_path)
                try:
                    lines = open(fpath).readlines()
                except Exception:
                    continue
                for i, line in enumerate(lines):
                    for stype in SCOPED_TYPES:
                        if stype not in line:
                            continue
                        if "MATCH" not in line and "execute_query" not in line:
                            continue
                        if "tenant_id" not in line:
                            continue
                        context = "".join(lines[max(0, i - 3):min(len(lines), i + 4)])
                        if any(re.search(p, context) for p in SCOPED_PATTERNS):
                            continue
                        if "merge_node" in line:
                            continue
                        findings.append(Finding(
                            rule=self.name, severity="critical",
                            file=rel, line=i + 1,
                            message=f"Query on {stype} filtered by tenant_id but not project_id",
                            fix_hint="Add project_filter() scoping: pf, pp = project_filter(pid, tid, 'alias')",
                            context=line.strip()[:120]))
        return findings
