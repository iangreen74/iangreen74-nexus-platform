"""Rule: code paths where project isolation is bypassed."""
from __future__ import annotations

import os

from nexus.audit_rules.base import AuditRule, Finding


class IsolationEscapes(AuditRule):
    name = "isolation_escapes"
    description = "Code paths where project isolation is bypassed"
    severity = "high"

    def scan(self, repo_path: str) -> list[Finding]:
        findings: list[Finding] = []
        for root, _, files in os.walk(repo_path):
            if ".git" in root or "node_modules" in root:
                continue
            for fname in files:
                if not fname.endswith(".py") or "test_" in fname:
                    continue
                fpath = os.path.join(root, fname)
                rel = os.path.relpath(fpath, repo_path)
                try:
                    lines = open(fpath).readlines()
                except Exception:
                    continue
                for i, line in enumerate(lines):
                    if "resolve_project_id" in line and "tenant_id" in line:
                        context = "\n".join(lines[max(0, i - 2):min(len(lines), i + 3)])
                        if "# WARNING" not in context:
                            findings.append(Finding(
                                rule=self.name, severity="medium",
                                file=rel, line=i + 1,
                                message="resolve_project_id returns tenant_id as fallback — bypasses project_filter",
                                fix_hint="Ensure callers handle pid==tid case explicitly"))
                    if "get_default_project" in line and "def " not in line:
                        findings.append(Finding(
                            rule=self.name, severity="high",
                            file=rel, line=i + 1,
                            message="get_default_project() auto-creates pid==tid Project — bypasses isolation",
                            fix_hint="In three-slot model, don't auto-create default projects"))
        return findings
