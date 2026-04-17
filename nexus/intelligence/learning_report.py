"""Learning Intelligence Report — orchestrator.

Generates a deep markdown report covering 8 sections. Each section
is independent; a failure in one is caught and surfaced rather than
crashing the whole report.

CLI:  python3 -m nexus.intelligence.learning_report [--save PATH]
API:  GET /api/learning-report
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone

from nexus.intelligence import report_sections as s
from nexus.intelligence import report_sections_ext as sx

logger = logging.getLogger("nexus.intelligence.learning_report")

SECTION_FUNCS = [
    ("1. Executive Summary", s.section_1_executive_summary),
    ("2. Causal Chain", s.section_2_causal_chain),
    ("3. Pattern Library", s.section_3_pattern_library),
    ("4. Failure Taxonomy", s.section_4_failure_taxonomy),
    ("5. Learning Signal", sx.section_5_learning_signal),
    ("6. Cost Economics", sx.section_6_cost_economics),
    ("7. Trajectory", sx.section_7_trajectory),
    ("8. Anomalies", sx.section_8_anomalies),
]


def generate_report() -> str:
    """Render the full report as markdown. Resilient to section failures."""
    now = datetime.now(timezone.utc).isoformat()
    header = (
        f"# Learning Intelligence Report\n\n"
        f"Generated: {now}\n\n"
        f"_Deep observability for the AI-native CI/CD intelligence layer. "
        f"Intentionally shows uncomfortable truths. "
        f"If Section 8 is empty, the system agrees with itself._\n\n---\n"
    )

    sections = [header]
    errors: list[tuple[str, str]] = []
    for name, fn in SECTION_FUNCS:
        try:
            sections.append(fn())
        except Exception as e:
            logger.exception("Section %s failed", name)
            errors.append((name, str(e)))
            sections.append(f"## {name}\n\n_Failed to render: {e}_\n")

    if errors:
        sections.append("\n---\n\n## Section Rendering Errors\n")
        for name, err in errors:
            sections.append(f"- **{name}:** {err}")

    sections.append(f"\n---\n\n_End of report. Generated at {now}._\n")
    return "\n\n".join(sections)


def _cli() -> None:
    parser = argparse.ArgumentParser(
        description="Generate the Learning Intelligence Report")
    parser.add_argument("--save", type=str, help="Save to file")
    args = parser.parse_args()

    md = generate_report()
    if args.save:
        with open(args.save, "w") as f:
            f.write(md)
        print(f"Saved to {args.save}", file=sys.stderr)
    else:
        print(md)


if __name__ == "__main__":
    _cli()
