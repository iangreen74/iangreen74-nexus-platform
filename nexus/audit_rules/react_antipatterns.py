"""Rule: DOM manipulation in React components."""
from __future__ import annotations

import os
import re

from nexus.audit_rules.base import AuditRule, Finding

BAD_PATTERNS = [
    (r"document\.querySelector", "DOM querySelector in React — use props/state instead"),
    (r"document\.getElementById", "DOM getElementById in React — use refs or props"),
    (r"document\.getElementsBy", "DOM getElementsBy in React — use state/props"),
    (r"\.innerHTML\s*=", "Direct innerHTML assignment — XSS risk, use React rendering"),
    (r"\.style\.\w+\s*=", "Direct style mutation — use React inline styles or CSS"),
]


class ReactAntiPatterns(AuditRule):
    name = "react_antipatterns"
    description = "DOM manipulation in React components (use props/state)"
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
                fpath = os.path.join(root, fname)
                rel = os.path.relpath(fpath, repo_path)
                try:
                    lines = open(fpath).readlines()
                except Exception:
                    continue
                for i, line in enumerate(lines):
                    for pattern, msg in BAD_PATTERNS:
                        if re.search(pattern, line):
                            findings.append(Finding(
                                rule=self.name, severity="high",
                                file=rel, line=i + 1,
                                message=msg,
                                context=line.strip()[:120]))
        return findings
