"""Rule: merge_node on project-scoped types missing pid_prop."""
from __future__ import annotations

import os

from nexus.audit_rules.base import AuditRule, Finding

SCOPED_TYPES = [
    "MissionTask", "MissionBrief", "ConversationMessage", "BriefEntry",
    "RepoFile", "SelfHealAction", "DeployReadiness",
]
EXEMPT_FILES = {"project_manager.py", "test_", "conftest"}


class UntaggedWrites(AuditRule):
    name = "untagged_writes"
    description = "merge_node on project-scoped types without pid_prop"
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
                    lines = open(fpath).read().split("\n")
                except Exception:
                    continue
                for i, line in enumerate(lines):
                    if "merge_node" not in line:
                        continue
                    for stype in SCOPED_TYPES:
                        if f'"{stype}"' not in line and f"'{stype}'" not in line:
                            continue
                        context = "\n".join(lines[max(0, i - 2):min(len(lines), i + 5)])
                        if "pid_prop" in context or "project_id" in context:
                            continue
                        findings.append(Finding(
                            rule=self.name, severity="critical",
                            file=rel, line=i + 1,
                            message=f"merge_node on {stype} without pid_prop — data won't be project-scoped",
                            fix_hint="Add **pid_prop(project_id) to set_props",
                            context=line.strip()[:120]))
        return findings
