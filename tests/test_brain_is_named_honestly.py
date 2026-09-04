"""The counter must name the provider that actually answered.

`brain` is printed on two screens under "which brain answers", and it was the
hardcoded string "grok". The moment the counter was pointed at Google it said
`grok` while calling `generativelanguage.googleapis.com`, with the real model
id sitting three rows below it on the same card.

The browser half was worse than a wrong label. Both screens compared
`brain === 'grok'`, so a Gemini-routed turn stopped matching and reported
itself as "local · this machine" — a turn claiming the shop answered from its
own files when a model had routed it. On a product whose whole argument is
saying where every number came from, that is the one lie that matters.
"""
from __future__ import annotations

import pathlib

import pytest

from gawaah import assistant

UI = pathlib.Path(__file__).resolve().parent.parent / "ui" / "src"


@pytest.mark.parametrize("base,expected", [
    ("https://api.x.ai/v1", "grok"),
    ("https://generativelanguage.googleapis.com/v1beta/openai", "gemini"),
    ("https://api.openai.com/v1", "openai"),
])
def test_the_brain_is_named_after_the_host_it_calls(monkeypatch, base, expected) -> None:
    monkeypatch.setenv("GAWAAH_LLM_BASE_URL", base)
    assert assistant.brain_name() == expected


def test_an_unknown_provider_is_named_not_guessed(monkeypatch) -> None:
    """An operator pointing this at their own gateway gets the host back."""
    monkeypatch.setenv("GAWAAH_LLM_BASE_URL", "https://llm.internal.example/v1")
    assert assistant.brain_name() == "llm.internal.example"


def test_the_key_follows_the_host(monkeypatch) -> None:
    """A machine can hold both keys. Sending one to the other's host is a 401
    that reads like a broken key rather than a mismatched one."""
    monkeypatch.setenv("XAI_API_KEY", "xai-aaa")
    monkeypatch.setenv("GOOGLE_API_KEY", "AIza-bbb")

    monkeypatch.setenv("GAWAAH_LLM_BASE_URL", "https://api.x.ai/v1")
    assert assistant.api_key() == "xai-aaa"

    monkeypatch.setenv("GAWAAH_LLM_BASE_URL",
                       "https://generativelanguage.googleapis.com/v1beta/openai")
    assert assistant.api_key() == "AIza-bbb"


def test_the_neutral_name_outranks_a_leftover_vendor_one(monkeypatch) -> None:
    """A stale XAI_MODEL must not send a Grok id to Google. It did, once."""
    monkeypatch.setenv("XAI_MODEL", "grok-4.20-0309-non-reasoning")
    monkeypatch.setenv("GAWAAH_LLM_MODEL", "gemini-3.1-flash-lite")
    assert assistant.model_name() == "gemini-3.1-flash-lite"


# Advisor.tsx and Assistant.tsx were merged into one screen. The rule they
# were held to — no `=== 'grok'` comparison anywhere the brain is named — now
# applies to the screen that replaced them.
@pytest.mark.parametrize("name", ["Salaahkaar.tsx"])
def test_no_screen_compares_the_brain_against_one_vendor(name) -> None:
    """The browser must ask "did a model answer", not "was it grok"."""
    src = (UI / "routes" / name).read_text()
    for wrong in ("=== 'grok'", '=== "grok"', "!== 'grok'"):
        assert wrong not in src, (
            f"{name} compares brain against a single vendor. Use isModel() from "
            f"lib/brain.ts — the server sends the provider's real name, so this "
            f"comparison silently reports every Gemini turn as local.")


def test_the_helper_treats_anything_but_local_as_a_model() -> None:
    src = (UI / "lib" / "brain.ts").read_text()
    assert "brain !== LOCAL_BRAIN" in src
