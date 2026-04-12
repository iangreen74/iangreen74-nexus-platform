"""Rule: frontend API calls missing project_id scoping."""
from __future__ import annotations

import os
import re

from nexus.audit_rules.base import AuditRule, Finding

SCOPED_ENDPOINTS = [
    "status", "brief", "analysis", "conversation", "cicd",
    "tasks", "guidance", "insights", "infrastructure", "deployment",
    "deploy-progress", "deployment-dna",
]


class FrontendScoping(AuditRule):
    name = "frontend_scoping"
    description = "Frontend components making API calls without project_id"
    severity = "high"

    def scan(self, repo_path: str) -> list[Finding]:
        findings: list[Finding] = []
        web_dir = os.path.join(repo_path, "forgescaler-web", "src")
        if not os.path.isdir(web_dir):
            return findings
        for root, _, files in os.walk(web_dir):
            if "node_modules" in root:
                continue
            for fname in files:
                if not fname.endswith((".jsx", ".tsx", ".js")):
                    continue
                if fname == "api.js":
                    continue  # covered by api_contract rule
                fpath = os.path.join(root, fname)
                rel = os.path.relpath(fpath, repo_path)
                try:
                    lines = open(fpath).readlines()
                except Exception:
                    continue
                for i, line in enumerate(lines):
                    for ep in SCOPED_ENDPOINTS:
                        pattern = rf"api\.{ep}\s*\(\s*tenantId\s*\)"
                        if re.search(pattern, line):
                            findings.append(Finding(
                                rule=self.name, severity="high",
                                file=rel, line=i + 1,
                                message=f"api.{ep}() called without project_id parameter",
                                fix_hint=f"Change to: api.{ep}(tenantId, activeProjectId)",
                                context=line.strip()[:120]))
        return findings
