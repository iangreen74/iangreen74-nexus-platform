"""Regression guard: pattern_fingerprint_counts queries DeploymentFingerprint."""
import os
os.environ.setdefault("NEXUS_MODE", "local")

from unittest.mock import patch
from nexus.intelligence import report_queries


def test_queries_DeploymentFingerprint_not_PatternFingerprint():
    with patch.object(report_queries, "overwatch_graph") as mock:
        mock.query.return_value = [{"c": 3}]
        report_queries.pattern_fingerprint_counts()
        queries = [c.args[0] for c in mock.query.call_args_list]
        all_q = " ".join(queries)
        assert "DeploymentFingerprint" in all_q, f"Must query DeploymentFingerprint; got: {queries}"
        assert "PatternFingerprint" not in all_q, f"Must NOT query PatternFingerprint; got: {queries}"
