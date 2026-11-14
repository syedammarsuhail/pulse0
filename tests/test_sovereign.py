"""Sovereign / data-residency mode: PULSE0_SOVEREIGN must hard-disable the Anthropic path."""
import server


def test_sovereign_disables_ai_even_with_key(monkeypatch):
    monkeypatch.setattr(server, "SOVEREIGN", True)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-be-ignored")
    assert server._ai_key() == ""  # no key is ever surfaced to the SDK


def test_sovereign_surfaced_in_mode(monkeypatch):
    monkeypatch.setattr(server, "SOVEREIGN", True)
    feats = server.api_mode()["features"]
    assert feats["sovereign"] is True
    assert feats["ai_assistant"] is False  # assistant is off because _ai_key() is empty


def test_non_sovereign_allows_configured_key(monkeypatch):
    monkeypatch.setattr(server, "SOVEREIGN", False)
    monkeypatch.setattr(server, "HOSTED", False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-123")
    assert server._ai_key() == "sk-test-123"


def test_mode_reports_not_sovereign_by_default(monkeypatch):
    monkeypatch.setattr(server, "SOVEREIGN", False)
    assert server.api_mode()["features"]["sovereign"] is False
