"""A NATURAL VOICE for the advisor — synthesised on the provider, cached here.

The browser's own `speechSynthesis` needs no network and leaks nothing, and on
the machine this was built on it sounds like a machine: the Hindi voice reads
"Rs 80.00" as a robot would. A shopkeeper on a call with a robot hangs up.

So when the counter's provider can voice text, this module asks it to, ONCE
per sentence. The result is written beside the catalogue and every later
request for the same words is served from that file in under a millisecond,
because the counter says the same things all day — "nothing has been billed
today", a price, a stock line — and paying the provider to say them again is
paying for nothing.

WHAT LEAVES THE MACHINE. The text. That is the sentence the counter composed
for the shopkeeper to hear, and it is already what `advisor.py` sends the
model to phrase — rupee strings and product names, never a paise integer, a
sku id, or a customer. Nothing else goes: no call context, no shop name, no
figures the sentence does not itself carry. The page says so, and lists it on
every turn that used this path. A shopkeeper who does not want even that can
switch to the browser voice and nothing leaves at all.

WHAT IT REFUSES. A provider that has no voice API (xAI's chat endpoint does
not). No key. A sentence over `MAX_CHARS`, because the price is per character
and a page that could send a novel is a page that can empty an account. An
answer with no audio in it. Each refusal is named and none is a 500.

NOT ON THE AUDIT CHAIN. `results/audit.jsonl` has one writer. What this module
writes is a WAV file per sentence under `<shop>/tts/`, and `advisor.py` logs
the fact that a sentence left — its length, never its words — on its own
chain, the way it logs every other departure.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import struct
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from . import assistant

# ------------------------------------------------------------- refusals --

R_NO_KEY = "voice_no_key"
R_NOT_ON_PROVIDER = "voice_not_on_this_provider"
R_TOO_LONG = "voice_text_too_long"
R_EMPTY = "voice_no_text"
R_UNAVAILABLE = "voice_unavailable"
R_BAD_AUDIO = "voice_bad_audio"
R_BAD_LANG = "voice_unknown_language"


class TTSRefused(Exception):
    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


# -------------------------------------------------------------- choices --

#: The cap, in characters. `advisor.MAX_ADVICE` is 900, so every sentence the
#: advisor can produce fits; a page sending more than this is not the advisor.
MAX_CHARS = 1000

#: Languages the page can ask for. The model reads the script it is given —
#: this is only for choosing a voice and for naming the language back.
LANGS: dict[str, str] = {
    "hi-IN": "Hindi",
    "en-IN": "Indian English",
    "bn-IN": "Bengali",
    # The picker offers these; a voice that refused them made the button a lie.
    "ta-IN": "Tamil",
    "te-IN": "Telugu",
}

#: How a figure is READ OUT. "Rs 3173.00" is a way of writing money for a
#: page; a mouth says "तीन हज़ार एक सौ तिहत्तर रुपये", and the nearest thing a
#: synthesiser reads reliably in every script is the digits followed by the
#: word for rupees in that language. Left as "Rs", one voice said "dollars"
#: and another spelt the letters. The paise are read only when they are not
#: zero, because "27 रुपये 0 पैसे" is not how anybody says 27 rupees.
_MONEY_WORDS: dict[str, tuple[str, str]] = {
    "hi": ("रुपये", "पैसे"), "bn": ("টাকা", "পয়সা"), "ta": ("ரூபாய்", "பைசா"),
    "te": ("రూపాయలు", "పైసలు"), "en": ("rupees", "paise"),
}
_MONEY_RE = re.compile(r"(?:Rs\.?\s?|₹\s?|INR\s?)(\d[\d,]*)(?:\.(\d{1,2}))?")


def spoken_money(text: str, lang: str = "hi-IN") -> str:
    """Every written amount in `text`, as a voice should read it. Pure."""
    words = _MONEY_WORDS.get((lang or "en").split("-")[0].lower(), _MONEY_WORDS["en"])
    def one(m: "re.Match[str]") -> str:
        whole = m.group(1).replace(",", "")
        paise = (m.group(2) or "").ljust(2, "0")
        out = f"{int(whole)} {words[0]}"
        if paise and int(paise):
            out += f" {int(paise)} {words[1]}"
        return out
    return _MONEY_RE.sub(one, text or "")

DEFAULT_MODEL = "gemini-2.5-flash-preview-tts"
#: Measured on 3 September 2026 against the 3.1 preview: 5.4 s against 7.5 s
#: for the same Hindi sentence, 152 tokens against 217, and no audible
#: difference at a counter. The cheaper, faster one is the default.

DEFAULT_VOICE = "Kore"
#: One voice for every language, on purpose: the presenter is one character,
#: and a character whose voice changes with the language toggle is two people.

TIMEOUT_S = 45
SAMPLE_RATE = 24000
_BITS = 16
_CHANNELS = 1


def model_name() -> str:
    return (os.environ.get("GAWAAH_TTS_MODEL") or DEFAULT_MODEL).strip() or DEFAULT_MODEL


def voice_name(lang: str) -> str:
    """The voice. Overridable per language (`GAWAAH_TTS_VOICE_HI_IN`) or for
    all of them (`GAWAAH_TTS_VOICE`); the default is one voice throughout."""
    per = os.environ.get("GAWAAH_TTS_VOICE_" + lang.replace("-", "_").upper())
    return (per or os.environ.get("GAWAAH_TTS_VOICE") or DEFAULT_VOICE).strip() or DEFAULT_VOICE


def _host() -> str:
    return assistant.base_url().split("//", 1)[-1].split("/", 1)[0].lower()


def available() -> tuple[bool, Optional[str]]:
    """(can this counter voice text, why not). Asked fresh every time, like the
    key: an operator who exports a key after the till started gets a voice
    without a restart."""
    if not assistant.api_key():
        return False, ("no key is set, so the browser's own voice is used and "
                       "nothing leaves this machine to be spoken.")
    if "googleapis.com" not in _host():
        return False, (f"the provider at {_host()} has no text-to-speech "
                       f"endpoint, so the browser's own voice is used.")
    return True, None


def endpoint() -> str:
    """The native generateContent URL, derived from the chat base URL.

    The chat client talks to `/v1beta/openai`, the OpenAI-shaped facade. Audio
    is not on that facade, so the same origin and version are kept and the
    facade segment is dropped.
    """
    base = assistant.base_url()
    if base.endswith("/openai"):
        base = base[: -len("/openai")]
    return f"{base}/models/{model_name()}:generateContent"


# ------------------------------------------------------------ transport --

Transport = Callable[[str, dict[str, str], bytes, int], "tuple[int, Any]"]
_DEPS: dict[str, Any] = {"transport": None, "cache_dir": None}


def set_transport(fn: Optional[Transport]) -> None:
    _DEPS["transport"] = fn


def set_cache_dir(p: Optional[Path]) -> None:
    _DEPS["cache_dir"] = p


def _urllib_post(url: str, headers: dict[str, str], body: bytes, timeout: int) -> "tuple[int, Any]":
    """One POST. The request object holds the key and is never stringified."""
    req = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode())
        except Exception:  # noqa: BLE001 - error bodies are not always JSON
            return exc.code, {}
    except Exception as exc:  # noqa: BLE001 - urllib raises a wide family
        raise TTSRefused(
            R_UNAVAILABLE,
            f"the voice service did not answer ({type(exc).__name__}). The "
            f"browser's own voice is used for this sentence.") from None


def transport() -> Transport:
    return _DEPS["transport"] or _urllib_post


def cache_dir() -> Path:
    """`<shop>/tts/`, beside the catalogue — the same place every module keeps
    its own files, and never `results/audit.jsonl`."""
    forced = _DEPS["cache_dir"]
    if forced is not None:
        return Path(forced)
    return Path(assistant.shop_dir()) / "tts"


# -------------------------------------------------------------- the wav --

def wav_from_pcm(pcm: bytes, rate: int = SAMPLE_RATE) -> bytes:
    """A 44-byte RIFF header in front of raw little-endian 16-bit mono PCM,
    which is what the provider returns and what an <audio> element cannot
    play without the header."""
    byte_rate = rate * _CHANNELS * _BITS // 8
    block_align = _CHANNELS * _BITS // 8
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", 36 + len(pcm), b"WAVE",
        b"fmt ", 16, 1, _CHANNELS, rate, byte_rate, block_align, _BITS,
        b"data", len(pcm),
    )
    return header + pcm


def _rate_of(mime: str) -> int:
    """`audio/L16;codec=pcm;rate=24000` -> 24000. Anything else is the default,
    which is what the provider has always sent."""
    for part in mime.split(";"):
        k, _, v = part.strip().partition("=")
        if k.strip().lower() == "rate" and v.strip().isdigit():
            return int(v.strip())
    return SAMPLE_RATE


# ------------------------------------------------------------ the voice --

@dataclass(frozen=True)
class Voiced:
    wav: bytes
    cached: bool
    model: str
    voice: str
    lang: str
    chars: int
    tokens: Optional[int]
    path: Path


def _clean(text: str) -> str:
    return " ".join((text or "").split())


def cache_key(text: str, lang: str) -> str:
    """The same words, the same voice, the same model: the same file. The
    language is in the key so a per-language voice override cannot serve a
    file voiced under a different one."""
    raw = f"{model_name()}|{voice_name(lang)}|{lang}|{text}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def synthesise(text: str, lang: str = "hi-IN") -> Voiced:
    """The sentence, as a WAV. From disk if it has been said before.

    Raises TTSRefused, always with a name and a sentence a page can show.
    """
    said = _clean(spoken_money(text, lang))
    if not said:
        raise TTSRefused(R_EMPTY, "nothing was given to say.")
    if lang not in LANGS:
        raise TTSRefused(
            R_BAD_LANG,
            f"{lang!r} is not a language this counter speaks. It speaks: "
            f"{', '.join(LANGS)}.")
    if len(said) > MAX_CHARS:
        raise TTSRefused(
            R_TOO_LONG,
            f"that is {len(said)} characters and the cap is {MAX_CHARS}. The "
            f"advisor never says more than {MAX_CHARS} at once, and the price "
            f"of a voice is per character.")
    ok, why = available()
    if not ok:
        reason = R_NO_KEY if not assistant.api_key() else R_NOT_ON_PROVIDER
        raise TTSRefused(reason, why or "no voice is available.")

    model = model_name()
    voice = voice_name(lang)
    d = cache_dir()
    path = d / f"{cache_key(said, lang)}.wav"
    if path.is_file():
        try:
            wav = path.read_bytes()
        except OSError:
            wav = b""
        if len(wav) > 44:
            return Voiced(wav=wav, cached=True, model=model, voice=voice,
                          lang=lang, chars=len(said), tokens=None, path=path)

    body = json.dumps({
        "contents": [{"parts": [{"text": said}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}},
        },
    }).encode("utf-8")
    headers = {"Content-Type": "application/json",
               "x-goog-api-key": assistant.api_key()}
    status, data = transport()(endpoint(), headers, body, TIMEOUT_S)
    if int(status) != 200:
        msg = ""
        if isinstance(data, dict):
            err = data.get("error")
            if isinstance(err, dict):
                msg = str(err.get("message") or "")[:160]
        raise TTSRefused(
            R_UNAVAILABLE,
            f"the voice service answered {status}"
            + (f": {msg}" if msg else "")
            + ". The browser's own voice is used for this sentence.")

    pcm, mime, tokens = _audio_of(data)
    wav = wav_from_pcm(pcm, _rate_of(mime))
    try:
        d.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        tmp.write_bytes(wav)
        os.replace(tmp, path)
    except OSError:
        # A full disk must not cost the sentence; it costs the cache.
        pass
    return Voiced(wav=wav, cached=False, model=model, voice=voice, lang=lang,
                  chars=len(said), tokens=tokens, path=path)


def _audio_of(data: Any) -> tuple[bytes, str, Optional[int]]:
    """The PCM bytes, their mime type, and the token count, out of the
    provider's envelope. A reply with no audio is a named refusal, not a
    KeyError."""
    import base64

    try:
        parts = data["candidates"][0]["content"]["parts"]
        for p in parts:
            inline = p.get("inlineData") or p.get("inline_data")
            if inline and inline.get("data"):
                mime = str(inline.get("mimeType") or inline.get("mime_type") or "")
                pcm = base64.b64decode(inline["data"])
                if len(pcm) < 2:
                    break
                usage = data.get("usageMetadata") or {}
                tokens = usage.get("totalTokenCount")
                return pcm, mime, (int(tokens) if isinstance(tokens, int) else None)
    except (KeyError, IndexError, TypeError, ValueError):
        pass
    raise TTSRefused(
        R_BAD_AUDIO,
        "the voice service answered without any audio in it. The browser's "
        "own voice is used for this sentence.")
