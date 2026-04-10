"""Tests for heal chain logic."""
import os

os.environ.setdefault("NEXUS_MODE", "local")

from nexus.reasoning.heal_chain import ChainProgress, HealChain, HealStep, get_chain


def test_chain_progress_awaiting():
    p = ChainProgress(chain_name="test", cycles_to_wait=2)
    assert p.awaiting_verification()
    p.tick()
    assert p.awaiting_verification()
    p.tick()
    assert not p.awaiting_verification()


def test_chain_progress_advance():
    p = ChainProgress(chain_name="test", cycles_to_wait=1)
    assert p.current_step == 0
    p.advance()
    assert p.current_step == 1


def test_chain_progress_record():
    p = ChainProgress(chain_name="test")
    p.record_step("restart_daemon", "success", "deployment started")
    assert len(p.step_results) == 1
    assert p.total_attempts == 1


def test_chain_exhausted():
    chain = HealChain(pattern_name="test", steps=[
        HealStep(capability="a", description="first"),
        HealStep(capability="b", description="second"),
    ])
    assert not chain.is_exhausted(0)
    assert not chain.is_exhausted(1)
    assert chain.is_exhausted(2)


def test_known_chains_exist():
    assert get_chain("daemon_stale") is not None
    assert get_chain("ci_failing") is not None
    assert get_chain("empty_tenant_token") is not None
    assert get_chain("nonexistent") is None


def test_daemon_chain_steps():
    chain = get_chain("daemon_stale")
    assert len(chain.steps) == 3
    assert chain.steps[0].capability == "restart_daemon"
    assert chain.steps[1].capability == "diagnose_daemon_timeout"
    assert chain.steps[2].capability == "check_daemon_code_version"


def test_summary():
    p = ChainProgress(chain_name="test")
    p.record_step("restart_daemon", "success", "ok")
    p.record_step("diagnose_timeout", "failed", "no logs")
    summary = p.summary()
    assert "restart_daemon" in summary
    assert "diagnose_timeout" in summary
