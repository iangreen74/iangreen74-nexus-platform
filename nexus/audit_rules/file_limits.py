"""Rule: Python files exceeding 200-line limit."""
from __future__ import annotations

import os

from nexus.audit_rules.base import AuditRule, Finding


class FileLimits(AuditRule):
    name = "file_limits"
    description = "Python files exceeding 200-line CI limit"
    severity = "medium"

    def scan(self, repo_path: str) -> list[Finding]:
        findings: list[Finding] = []
        for root, _, files in os.walk(repo_path):
            if "test" in root or "node_modules" in root or ".git" in root:
                continue
            for fname in files:
                if not fname.endswith(".py"):
                    continue
                fpath = os.path.join(root, fname)
                rel = os.path.relpath(fpath, repo_path)
                try:
                    count = sum(1 for _ in open(fpath))
                except Exception:
                    continue
                if count > 200:
                    findings.append(Finding(
                        rule=self.name, severity="medium",
                        file=rel, line=count,
                        message=f"File has {count} lines (limit: 200)",
                        fix_hint="Split into smaller modules"))
                elif count >= 190:
                    findings.append(Finding(
                        rule=self.name, severity="low",
                        file=rel, line=count,
                        message=f"File has {count} lines — approaching 200-line limit",
                        fix_hint="Plan refactoring before adding more code"))
        return findings
