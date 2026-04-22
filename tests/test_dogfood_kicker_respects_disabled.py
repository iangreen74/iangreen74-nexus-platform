"""Sprint 13 Day 1 P0: kicker must respect disabled flag unconditionally.

Structural invariant: disabled → zero kicks, regardless of batch state.
"""
import os
os.environ.setdefault("NEXUS_MODE", "local")

from unittest.mock import patch, MagicMock
from nexus.capabilities.deploy_cycle import _kick_dogfood_if_needed


def test_disabled_with_active_batch_does_not_kick():
    """disabled + batch.remaining > 0 → zero kicks."""
    with patch("nexus.capabilities.dogfood_capability._is_enabled", return_value=False), \
         patch("nexus.capabilities.dogfood_capability.run_dogfood_cycle") as mock_run, \
         patch("nexus.overwatch_graph.get_active_batch",
               return_value={"batch_id": "b1", "remaining": 5}):
        result = _kick_dogfood_if_needed()
    assert result.get("skipped") is True
    assert "not enabled" in result.get("reason", "")
    mock_run.assert_not_called()


def test_enabled_with_active_batch_kicks():
    """enabled + batch.remaining > 0 → kicks once."""
    with patch("nexus.capabilities.dogfood_capability._is_enabled", return_value=True), \
         patch("nexus.capabilities.dogfood_capability.run_dogfood_cycle",
               return_value={"status": "kicked_off", "run_id": "r1"}) as mock_run, \
         patch("nexus.overwatch_graph.get_active_batch",
               return_value={"batch_id": "b1", "remaining": 5}), \
         patch("nexus.overwatch_graph.reserve_batch_slot", return_value=True), \
         patch("nexus.overwatch_graph.update_dogfood_run"):
        result = _kick_dogfood_if_needed()
    assert result.get("status") == "kicked_off"
    mock_run.assert_called_once()


def test_disabled_no_batch_does_not_kick():
    """disabled + no batch → zero kicks."""
    with patch("nexus.capabilities.dogfood_capability._is_enabled", return_value=False), \
         patch("nexus.capabilities.dogfood_capability.run_dogfood_cycle") as mock_run, \
         patch("nexus.overwatch_graph.get_active_batch", return_value=None):
        result = _kick_dogfood_if_needed()
    assert result.get("skipped") is True
    mock_run.assert_not_called()
