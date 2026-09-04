"""The suite runs the same on every machine, whatever is in the shell.

An operator who has a provider key exported — because they were just running
the till — ran this suite and watched 198 native-script parser tests fail.
Nothing in the parser had changed. The key had turned every "no model set"
path into a live routing request, and tests written against the
deterministic parser were suddenly reading a model's answer.

So every provider setting is cleared before each test. A test that wants a
key sets one itself, and says so. The operator's environment is theirs.
"""
from __future__ import annotations

import pytest

#: Everything that selects a provider, a key or a voice. Kept in one place so
#: the next variable added to `assistant.py` or `tts.py` is added here too.
PROVIDER_ENV = (
    "XAI_API_KEY", "XAI_BASE_URL", "XAI_MODEL",
    "GAWAAH_LLM_KEY", "GAWAAH_LLM_BASE_URL", "GAWAAH_LLM_MODEL",
    "GEMINI_API_KEY", "GOOGLE_API_KEY",
    "GAWAAH_TTS_MODEL", "GAWAAH_TTS_VOICE",
    "GAWAAH_TTS_VOICE_HI_IN", "GAWAAH_TTS_VOICE_EN_IN", "GAWAAH_TTS_VOICE_BN_IN",
)


@pytest.fixture(autouse=True)
def _no_provider_from_the_shell(monkeypatch):
    for name in PROVIDER_ENV:
        monkeypatch.delenv(name, raising=False)
