"""The advisor's natural voice: synthesised once, cached beside the catalogue,
and refused by name whenever it should be.

Every test here runs against a FAKE transport and a scratch cache directory.
Nothing reaches the provider, nothing touches `results/`, and the audit chain
is never opened — `results/audit.jsonl` has one writer and it is not this.
"""
from __future__ import annotations

import base64
import json
import struct

import pytest
from fastapi.testclient import TestClient

from gawaah import advisor, assistant, tts
from tools import upload_app

PCM = bytes(range(256)) * 8   # 2048 bytes of something that is not silence


def _envelope(pcm: bytes = PCM, mime: str = "audio/L16;codec=pcm;rate=24000", tokens: int = 152):
    return {
        "candidates": [{"content": {"parts": [
            {"inlineData": {"mimeType": mime, "data": base64.b64encode(pcm).decode()}},
        ]}}],
        "usageMetadata": {"totalTokenCount": tokens},
    }


@pytest.fixture()
def google(monkeypatch, tmp_path):
    """A counter pointed at Google with a key, a scratch cache, and a transport
    that counts how many times it was asked."""
    monkeypatch.setenv("GAWAAH_LLM_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai")
    monkeypatch.setenv("GOOGLE_API_KEY", "AIza-test-not-real")
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.delenv("GAWAAH_LLM_KEY", raising=False)
    monkeypatch.setenv("GAWAAH_SHOP_DIR", str(tmp_path / "shop"))
    monkeypatch.setenv("GAWAAH_DATA_DIR", str(tmp_path))
    tts.set_cache_dir(tmp_path / "tts")
    calls: list[dict] = []

    def fake(url, headers, body, timeout):
        calls.append({"url": url, "headers": headers, "body": json.loads(body)})
        return 200, _envelope()

    tts.set_transport(fake)
    yield calls
    tts.set_transport(None)
    tts.set_cache_dir(None)


# ------------------------------------------------------------- the voice --

def test_a_sentence_is_voiced_once_and_then_served_from_disk(google) -> None:
    first = tts.synthesise("आज कुछ नहीं बिका।", "hi-IN")
    assert first.cached is False
    assert first.wav[:4] == b"RIFF" and first.wav[8:12] == b"WAVE"
    assert first.tokens == 152
    assert first.path.is_file()

    again = tts.synthesise("  आज   कुछ नहीं बिका। ", "hi-IN")   # same words, messier spaces
    assert again.cached is True
    assert again.wav == first.wav
    assert len(google) == 1, "the provider was asked twice for the same sentence"


def test_the_request_carries_the_sentence_and_nothing_else(google) -> None:
    tts.synthesise("Nothing has been billed today.", "en-IN")
    sent = google[0]
    assert sent["body"]["contents"] == [{"parts": [{"text": "Nothing has been billed today."}]}]
    assert "AUDIO" in sent["body"]["generationConfig"]["responseModalities"]
    assert sent["headers"]["x-goog-api-key"] == "AIza-test-not-real"
    assert "Authorization" not in sent["headers"]
    assert sent["url"].startswith("https://generativelanguage.googleapis.com/v1beta/models/")
    assert "/openai/" not in sent["url"], "audio is not on the OpenAI facade"


def test_the_wav_header_describes_the_pcm() -> None:
    wav = tts.wav_from_pcm(PCM, 24000)
    riff, size, wave = struct.unpack("<4sI4s", wav[:12])
    assert (riff, wave) == (b"RIFF", b"WAVE")
    assert size == 36 + len(PCM)
    fmt = struct.unpack("<HHIIHH", wav[20:36])
    assert fmt == (1, 1, 24000, 48000, 2, 16)   # PCM, mono, 24 kHz, 16-bit
    assert wav[36:40] == b"data" and wav[44:] == PCM


def test_the_sample_rate_is_read_off_the_mime_type(google) -> None:
    tts.set_transport(lambda *a: (200, _envelope(mime="audio/L16;codec=pcm;rate=16000")))
    v = tts.synthesise("hello", "en-IN")
    assert struct.unpack("<I", v.wav[24:28])[0] == 16000


def test_one_voice_for_every_language(google) -> None:
    """The presenter is one character. A voice that changed with the language
    toggle would be two people."""
    for lang in tts.LANGS:
        tts.synthesise(f"hello {lang}", lang)
    voices = {c["body"]["generationConfig"]["speechConfig"]["voiceConfig"]["prebuiltVoiceConfig"]["voiceName"] for c in google}
    assert voices == {tts.DEFAULT_VOICE}


# -------------------------------------------------------------- refusals --

def test_no_key_is_a_named_refusal(google, monkeypatch) -> None:
    monkeypatch.delenv("GOOGLE_API_KEY")
    with pytest.raises(tts.TTSRefused) as e:
        tts.synthesise("hello", "en-IN")
    assert e.value.reason == tts.R_NO_KEY
    assert google == [], "asked the provider with no key"


def test_a_provider_with_no_voice_api_is_refused_by_name(google, monkeypatch) -> None:
    monkeypatch.setenv("GAWAAH_LLM_BASE_URL", "https://api.x.ai/v1")
    monkeypatch.setenv("XAI_API_KEY", "xai-test")
    ok, why = tts.available()
    assert ok is False and "api.x.ai" in (why or "")
    with pytest.raises(tts.TTSRefused) as e:
        tts.synthesise("hello", "en-IN")
    assert e.value.reason == tts.R_NOT_ON_PROVIDER
    assert google == []


def test_too_long_is_refused_before_anything_leaves(google) -> None:
    with pytest.raises(tts.TTSRefused) as e:
        tts.synthesise("x" * (tts.MAX_CHARS + 1), "en-IN")
    assert e.value.reason == tts.R_TOO_LONG
    assert google == []


def test_an_unknown_language_is_refused(google) -> None:
    with pytest.raises(tts.TTSRefused) as e:
        tts.synthesise("hello", "fr-FR")
    assert e.value.reason == tts.R_BAD_LANG


def test_a_reply_with_no_audio_is_a_refusal_not_a_crash(google) -> None:
    tts.set_transport(lambda *a: (200, {"candidates": [{"content": {"parts": [{"text": "no audio here"}]}}]}))
    with pytest.raises(tts.TTSRefused) as e:
        tts.synthesise("hello", "en-IN")
    assert e.value.reason == tts.R_BAD_AUDIO


def test_a_provider_error_names_the_status(google) -> None:
    tts.set_transport(lambda *a: (429, {"error": {"message": "quota"}}))
    with pytest.raises(tts.TTSRefused) as e:
        tts.synthesise("hello", "en-IN")
    assert e.value.reason == tts.R_UNAVAILABLE
    assert "429" in e.value.detail and "quota" in e.value.detail


# ------------------------------------------------------------- the route --

def test_the_route_answers_with_a_same_origin_url_and_the_file_plays_from_it(google) -> None:
    """Not the bytes. A `blob:` URL made from the bytes was refused by the
    till's own CSP (`default-src 'self'`, no media-src), and the answer was
    silent while the page said "fetched once". A same-origin path needs no
    widening and no object URL."""
    client = TestClient(upload_app.app)
    r = client.post("/advisor/speak", json={"text": "Nothing has been billed today.", "lang": "en-IN"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["cached"] is False
    assert body["chars"] == 30 and body["settles_money"] is False
    assert body["url"].startswith("/advisor/voice/") and body["url"].endswith(".wav")
    assert "Nothing" not in body["url"], "the sentence must not be in the URL"

    audio = client.get(body["url"])
    assert audio.status_code == 200
    assert audio.headers["content-type"].startswith("audio/wav")
    assert audio.content[:4] == b"RIFF"

    again = client.post("/advisor/speak", json={"text": "Nothing has been billed today.", "lang": "en-IN"}).json()
    assert again["cached"] is True and again["url"] == body["url"]
    assert len(google) == 1


def test_a_voice_name_that_is_not_a_key_never_reaches_the_disk(google) -> None:
    client = TestClient(upload_app.app)
    for bad in ("nothing-here.wav", "ABCDEF.wav", "0" * 63 + ".wav", "zz" * 32):
        r = client.get(f"/advisor/voice/{bad}")
        assert r.status_code == 404, bad
        assert r.json()["reason"] == "voice_no_such_sentence"
    # A path with a slash in it never reaches the handler at all.
    assert client.get("/advisor/voice/../../etc/passwd").status_code == 404
    missing = client.get("/advisor/voice/" + "0" * 64 + ".wav")
    assert missing.status_code == 404


def test_the_route_refuses_in_json_when_it_cannot_voice(google, monkeypatch) -> None:
    monkeypatch.delenv("GOOGLE_API_KEY")
    client = TestClient(upload_app.app)
    r = client.post("/advisor/speak", json={"text": "hello", "lang": "en-IN"})
    assert r.status_code == 400
    body = r.json()
    assert body["ok"] is False and body["reason"] == tts.R_NO_KEY
    assert body["settles_money"] is False


def test_the_route_never_touches_the_audit_chain(google, tmp_path) -> None:
    chain = tmp_path / "audit.jsonl"
    assert not chain.exists()
    client = TestClient(upload_app.app)
    client.post("/advisor/speak", json={"text": "hello", "lang": "en-IN"})
    assert not chain.exists(), "tts wrote to results/audit.jsonl's stand-in"
    # But the advisor's OWN chain records that a sentence left: its length only.
    own = advisor.audit_path()
    assert own.is_file()
    lines = [json.loads(l) for l in own.read_text().splitlines() if l.strip()]
    voiced = [l for l in lines if l.get("event") == "advisor.voiced"]
    assert voiced and voiced[-1]["chars"] == 5
    assert "hello" not in own.read_text()


def test_health_says_whether_a_voice_is_available(google) -> None:
    client = TestClient(upload_app.app)
    h = client.get("/advisor/health").json()
    assert h["voice"]["available"] is True
    assert h["voice"]["model"] == tts.DEFAULT_MODEL
    assert h["voice"]["why_not"] is None


# ------------------------------------------ the answer in the shopkeeper's language --

def test_indic_digits_are_the_same_figure_as_ascii_ones() -> None:
    """A Hindi answer writes ८० for 80. The figure check must read it as 80,
    or every Hindi answer with a number in it is 'invented' and dropped."""
    assert advisor.figures_in("आज ८० रुपये") == {"80"}
    assert advisor.figures_in("আজ ৮০ টাকা") == {"80"}
    assert advisor.unbacked_figures("आज ८० रुपये की बिक्री", {"80"}) == []
    assert advisor.unbacked_figures("आज ९० रुपये", {"80"}) == ["९०"]


def test_the_language_asked_for_reaches_the_phrasing_prompt() -> None:
    sess, _ = advisor._open_session(None)
    p = advisor.advice_payload(sess, "aaj kitna hua", "todays_takings", {}, {"total_rupees": "80.00"}, lang="hi-IN")
    system = p["messages"][0]["content"]
    assert "Devanagari" in system and "Hindi" in system
    p2 = advisor.advice_payload(sess, "how much today", "todays_takings", {}, {"total_rupees": "80.00"}, lang="en-IN")
    assert "Devanagari" not in p2["messages"][0]["content"]


# ===========================================================================
# MONEY, AS A MOUTH SAYS IT
# "Rs 3173.00" is how a page writes money. One voice read it as "dollars" and
# another spelt the letters. A voice gets digits plus the word for rupees in
# the asker's language, and the paise only when they are not zero.
# ===========================================================================
import pytest as _pt

@_pt.mark.parametrize("text,lang,expect", [
    ("12 bills come to Rs 3173.00.", "hi-IN", "12 bills come to 3173 रुपये."),
    ("Rs 27.50 is owed", "hi-IN", "27 रुपये 50 पैसे is owed"),
    ("Rs 3,173.00 and ₹12", "bn-IN", "3173 টাকা and 12 টাকা"),
    ("Rs 399.00", "ta-IN", "399 ரூபாய்"),
    ("Rs 399.00", "te-IN", "399 రూపాయలు"),
    ("Rs 399.00", "en-IN", "399 rupees"),
    ("no money here", "hi-IN", "no money here"),
])
def test_spoken_money(text, lang, expect):
    assert tts.spoken_money(text, lang) == expect


def test_the_voice_speaks_every_language_on_the_picker():
    for tag in ("hi-IN", "en-IN", "bn-IN", "ta-IN", "te-IN"):
        assert tag in tts.LANGS
