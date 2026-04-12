"""Base class for audit rules."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class Finding:
    rule: str
    severity: str  # critical, high, medium, low
    file: str
    line: int
    message: str
    fix_hint: str = ""
    context: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v}


class AuditRule:
    name: str = "base"
    description: str = ""
    severity: str = "medium"

    def scan(self, repo_path: str) -> list[Finding]:
        raise NotImplementedError
