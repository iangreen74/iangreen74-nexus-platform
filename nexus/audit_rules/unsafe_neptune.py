"""Rule: unsafe Neptune query patterns."""
from __future__ import annotations

import os
import re

from nexus.audit_rules.base import AuditRule, Finding


class UnsafeNeptune(AuditRule):
    name = "unsafe_neptune"
    description = "CREATE instead of MERGE, missing tenant_id on writes"
    severity = "high"

    def scan(self, repo_path: str) -> list[Finding]:
        findings: list[Finding] = []
        scoped_types = [
            "MissionTask", "MissionBrief", "ConversationMessage",
            "BriefEntry", "RepoFile", "Project",
        ]
        for root, _, files in os.walk(repo_path):
            if ".git" in root or "node_modules" in root:
                continue
            for fname in files:
                if (not fname.endswith(".py")
                        or "test_" in fname
                        or "local_graph" in fname):
                    continue
                fpath = os.path.join(root, fname)
                rel = os.path.relpath(fpath, repo_path)
                try:
                    lines = open(fpath).readlines()
                except Exception:
                    continue
                for i, line in enumerate(lines):
                    if re.search(r"CREATE\s*\(\w+:", line) and "INDEX" not in line:
                        findings.append(Finding(
                            rule=self.name, severity="high",
                            file=rel, line=i + 1,
                            message="CREATE used instead of MERGE — risk of duplicate nodes",
                            fix_hint="Use MERGE to prevent duplicates",
                            context=line.strip()[:120]))
                    if "merge_node" in line and any(t in line for t in scoped_types):
                        context = "\n".join(lines[i:min(len(lines), i + 3)])
                        if "tenant_id" not in context:
                            findings.append(Finding(
                                rule=self.name, severity="high",
                                file=rel, line=i + 1,
                                message="merge_node on tenant-scoped type without tenant_id in match props",
                                context=line.strip()[:120]))
        return findings
