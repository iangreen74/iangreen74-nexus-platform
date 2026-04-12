"""Rule: stale brand references, deleted entities, outdated names."""
from __future__ import annotations

import os
import re

from nexus.audit_rules.base import AuditRule, Finding

STALE_PATTERNS = [
    (r"\bForgeScaler\b", "body_copy", "Brand reference 'ForgeScaler' — should be 'Forgewing'", False),
    (r"\bHyperLev\b", "deleted_entity", "Reference to decommissioned HyperLev", False),
    (r"\bmeridian\b", "deleted_entity", "Reference to deleted Meridian resources", True),
    (r"CORD\b", "terminology", "Term 'CORD' does not exist — use 'Claude prompts'", False),
]
EXEMPT_DIRS = {"node_modules", ".git", "dist", "build", "__pycache__"}
EXEMPT_EXTENSIONS = {".pyc", ".woff", ".woff2", ".png", ".jpg", ".svg", ".ico"}


class StaleReferences(AuditRule):
    name = "stale_references"
    description = "Outdated brand names, deleted entity references"
    severity = "medium"

    def scan(self, repo_path: str) -> list[Finding]:
        findings: list[Finding] = []
        for root, dirs, files in os.walk(repo_path):
            dirs[:] = [d for d in dirs if d not in EXEMPT_DIRS]
            for fname in files:
                ext = os.path.splitext(fname)[1]
                if ext in EXEMPT_EXTENSIONS:
                    continue
                fpath = os.path.join(root, fname)
                rel = os.path.relpath(fpath, repo_path)
                try:
                    lines = open(fpath, errors="replace").readlines()
                except Exception:
                    continue
                for i, line in enumerate(lines):
                    low = line.lower()
                    if "forgescaler" in low and ("renamed" in low or "formerly" in low):
                        continue
                    for pattern, category, msg, case_insensitive in STALE_PATTERNS:
                        flags = re.IGNORECASE if case_insensitive else 0
                        if not re.search(pattern, line, flags):
                            continue
                        if category == "body_copy":
                            if any(x in line for x in [
                                "forgescaler.com", "class ", "def ", "import ", "from ",
                                "ForgeScaler-", "forgescaler/",
                            ]):
                                continue
                        findings.append(Finding(
                            rule=self.name, severity="low",
                            file=rel, line=i + 1,
                            message=msg, context=line.strip()[:100]))
        return findings
