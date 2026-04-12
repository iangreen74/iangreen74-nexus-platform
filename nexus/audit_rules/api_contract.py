"""Rule: frontend/backend API field mismatches."""
from __future__ import annotations

import os
import re

from nexus.audit_rules.base import AuditRule, Finding


class ApiContractMismatch(AuditRule):
    name = "api_contract_mismatch"
    description = "Fields sent by frontend don't match what backend reads"
    severity = "critical"

    def scan(self, repo_path: str) -> list[Finding]:
        findings: list[Finding] = []
        api_js = os.path.join(repo_path, "forgescaler-web", "src", "api.js")
        if not os.path.exists(api_js):
            return findings
        try:
            api_content = open(api_js).read()
        except Exception:
            return findings

        if "sendMsg" in api_content or "sendMessage" in api_content:
            body_match = re.search(r"const\s+body\s*=\s*\{([^}]+)\}", api_content)
            if body_match:
                body_fields = body_match.group(1)
                if "project_id" not in body_fields:
                    findings.append(Finding(
                        rule=self.name, severity="critical",
                        file="forgescaler-web/src/api.js", line=0,
                        message="sendMsg body does not include project_id at top level",
                        fix_hint="Add project_id: context?.project_id to the body object"))
                if "new_project" not in body_fields:
                    findings.append(Finding(
                        rule=self.name, severity="high",
                        file="forgescaler-web/src/api.js", line=0,
                        message="sendMsg body does not include new_project flag",
                        fix_hint="Add new_project: context?.new_project || false"))

        api_calls = re.findall(
            r"(\w+):\s*\([^)]*\)\s*=>\s*(?:req|optReq)\('GET',\s*([^,)]+)",
            api_content,
        )
        scoped_endpoints = [
            "status", "brief", "analysis", "conversation", "cicd",
            "tasks", "guidance", "insights", "infrastructure", "deployment",
        ]
        for name, path in api_calls:
            if "wp(" in path or "project_id" in path:
                continue
            if any(ep in path.lower() for ep in scoped_endpoints):
                findings.append(Finding(
                    rule=self.name, severity="high",
                    file="forgescaler-web/src/api.js", line=0,
                    message=f"api.{name} GET call doesn't use wp() for project_id scoping",
                    fix_hint="Change to: optReq('GET', wp(`...`, pid))"))
        return findings
