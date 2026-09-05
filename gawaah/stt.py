"""EARS THAT DO NOT DEPEND ON CHROME — speech to text on the counter's own key.

`ui/src/lib/voice.ts` listens with the browser's `SpeechRecognition`. That API
is a **cloud call to Google's speech service dressed as a browser feature**: it
needs the internet, it is Chrome-and-Edge only, and when the network refuses it
the counter's microphone simply stops with `network` and there is nothing the
shopkeeper can do about it. That happened on the machine this was written on —
"The speech service could not be reached" over a till that was otherwise fine.

So this is a second pair of ears, on the same key the advisor and the voice
already use. The browser records a few seconds of audio and posts the bytes
here; the till asks the model to write down what was said and answers with the
words. Same origin from the page's point of view, which matters because the
till's CSP is `default-src 'self'` and a browser that cannot fetch a CDN
certainly cannot be handed a third-party speech endpoint.

WHAT LEAVES THE MACHINE. The recording. That is the same class of departure the
browser's own recogniser makes — it uploads the audio too, which the till has
always said out loud on the mic panel — except this one goes to the provider the
shopkeeper configured rather than to a service nobody chose. The catalogue, the
prices, the customers and the bill do not go. Nothing else is attached.

WHAT IT REFUSES. A provider with no audio API (xAI's chat endpoint has none). No
key. A clip longer than `MAX_SECONDS`, because the price is per second of audio
and a page that could post an hour is a page that can empty an account. An
answer with no text in it. A mime type this counter did not record. Every
refusal is named and none is a 500 — the browser's own recogniser stays as the
fallback, exactly as this module is the fallback for it.

NOT ON THE AUDIT CHAIN. `results/audit.jsonl` has one writer. What a
transcription produces is a sentence handed straight back to the page; the
advisor's own chain records that a turn happened, the way it already does.
"""
from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Optional

from . import assistant

# ------------------------------------------------------------- refusals --

R_NO_KEY = "ears_no_key"
R_NOT_ON_PROVIDER = "ears_not_on_this_provider"
R_TOO_LONG = "ears_clip_too_long"
R_EMPTY = "ears_no_audio"
R_BAD_MIME = "ears_unknown_audio_format"
R_UNAVAILABLE = "ears_unavailable"
R_NO_TEXT = "ears_heard_nothing"
R_BAD_LANG = "ears_unknown_language"


class STTRefused(Exception):
    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


# -------------------------------------------------------------- choices --

#: The cap, in seconds of audio. An order at a counter is one breath — "do
#: Maggi aur ek Parle-G" is under three. Fifteen is generous and still bounded.
MAX_SECONDS = 15
#: Bytes. Opus at the browser's default bitrate runs ~12 kB/s, so this is the
#: same cap expressed in the only unit the server can actually check before
#: decoding: a 15-second clip has no business being over a megabyte.
MAX_BYTES = 1_500_000

#: What a browser's MediaRecorder actually produces. Chrome gives webm/opus,
#: Safari gives mp4/aac; both are accepted by the model. Anything else is
#: refused by name rather than forwarded and charged for.
MIMES: tuple[str, ...] = (
    "audio/webm", "audio/webm;codecs=opus",
    "audio/ogg", "audio/ogg;codecs=opus",
    "audio/mp4", "audio/mpeg", "audio/wav", "audio/x-wav", "audio/aac",
)

#: The languages the counter hears in, and the name it gives each one to the
#: model. Same three the voice speaks, because a counter that can answer in a
#: language should be able to hear it.
LANGS: dict[str, str] = {
    "hi-IN": "Hindi (Devanagari script), often mixed with English words",
    "en-IN": "Indian English, often mixed with Hindi words",
    "bn-IN": "Bengali (Bengali script), often mixed with English words",
}

#: Flash, not the TTS preview: this is a transcription, the cheapest capable
#: model wins, and the same family the router already uses.
#: `gemini-2.5-flash` was retired for new projects ("no longer available to new
#: users", 404) and the counter went deaf the day a shop's key came from a
#: project made after that. This is what transcribes today; GAWAAH_STT_MODEL
#: still overrides it.
DEFAULT_MODEL = "gemini-3.6-flash"

TIMEOUT_S = 45


def model_name() -> str:
    return (os.environ.get("GAWAAH_STT_MODEL") or DEFAULT_MODEL).strip() or DEFAULT_MODEL


def _host() -> str:
    return assistant.base_url().split("//", 1)[-1].split("/", 1)[0].lower()


def available() -> tuple[bool, Optional[str]]:
    """(can this counter hear, why not). Asked fresh every time, like the key."""
    if not assistant.api_key():
        return False, ("no key is set, so listening falls back to the browser's "
                       "own speech service.")
    if "googleapis.com" not in _host():
        return False, (f"the provider at {_host()} has no speech-to-text "
                       f"endpoint, so the browser's own service is used.")
    return True, None


def endpoint() -> str:
    """The native generateContent URL, derived from the chat base URL.

    Same reasoning as `tts.endpoint`: the chat client talks to the OpenAI-shaped
    facade at `/v1beta/openai`, audio is not on that facade, so the origin and
    version are kept and the facade segment is dropped.
    """
    base = assistant.base_url()
    if base.endswith("/openai"):
        base = base[: -len("/openai")]
    return f"{base}/models/{model_name()}:generateContent"


# ------------------------------------------------------------ transport --

Transport = Callable[[str, dict[str, str], bytes, int], "tuple[int, Any]"]
_DEPS: dict[str, Any] = {"transport": None}


def set_transport(fn: Optional[Transport]) -> None:
    _DEPS["transport"] = fn


def _urllib_post(url: str, headers: dict[str, str], body: bytes, timeout: int) -> "tuple[int, Any]":
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
        raise STTRefused(
            R_UNAVAILABLE,
            f"the speech service did not answer ({type(exc).__name__}). The "
            f"browser's own recogniser is used for this attempt.") from None


def transport() -> Transport:
    return _DEPS["transport"] or _urllib_post


# ---------------------------------------------------------------- heard --

@dataclass(frozen=True)
class Heard:
    text: str
    lang: str
    model: str
    seconds: Optional[float]
    bytes_in: int


def _clean_mime(raw: Any) -> str:
    """The mime a browser hands us, trimmed of its codec suffix for matching.

    A MediaRecorder reports `audio/webm;codecs=opus`; the model wants
    `audio/webm`. Both are accepted here and the base form is what is sent.
    """
    m = str(raw or "").strip().lower()
    if not m:
        raise STTRefused(R_BAD_MIME, "the recording carried no format.")
    if m not in MIMES:
        raise STTRefused(
            R_BAD_MIME,
            f"{m!r} is not a recording format this counter accepts. It takes: "
            f"{', '.join(sorted(set(x.split(';')[0] for x in MIMES)))}.")
    return m.split(";")[0]


#: What the model is asked to do, and everything it is asked NOT to do. A
#: transcriber that "helps" is a transcriber that invents an order.
PROMPT = (
    "Write down exactly what is said in this recording, as {lang}. "
    "This is a shopkeeper at a small Indian grocery counter, so expect product "
    "names, brand names and quantities. "
    "Rules: return ONLY the words that were said. Do not translate. Do not "
    "correct grammar. Do not add punctuation that changes meaning. Do not "
    "explain, label, or add any preamble. If the recording contains no speech, "
    "return an empty string and nothing else."
)


def transcribe(audio: bytes, mime: str, lang: str = "hi-IN",
               seconds: Optional[float] = None) -> Heard:
    """The recording, as words. Raises STTRefused, always named."""
    if not audio:
        raise STTRefused(R_EMPTY, "the recording was empty.")
    if lang not in LANGS:
        raise STTRefused(
            R_BAD_LANG,
            f"{lang!r} is not a language this counter hears. It hears: "
            f"{', '.join(LANGS)}.")
    if len(audio) > MAX_BYTES:
        raise STTRefused(
            R_TOO_LONG,
            f"that recording is {len(audio) // 1000} kB and the cap is "
            f"{MAX_BYTES // 1000} kB. The counter listens in short turns — say "
            f"the order, then stop.")
    if seconds is not None and seconds > MAX_SECONDS:
        raise STTRefused(
            R_TOO_LONG,
            f"that recording is {seconds:.0f} seconds and the cap is "
            f"{MAX_SECONDS}. Say the order in one breath.")
    base_mime = _clean_mime(mime)

    ok, why = available()
    if not ok:
        reason = R_NO_KEY if not assistant.api_key() else R_NOT_ON_PROVIDER
        raise STTRefused(reason, why or "no speech service is available.")

    body = json.dumps({
        "contents": [{"parts": [
            {"text": PROMPT.format(lang=LANGS[lang])},
            {"inlineData": {"mimeType": base_mime,
                            "data": base64.b64encode(audio).decode()}},
        ]}],
        # Deterministic: a transcription is a reading, not a composition.
        "generationConfig": {"temperature": 0.0},
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
        raise STTRefused(
            R_UNAVAILABLE,
            f"the speech service answered {status}"
            + (f": {msg}" if msg else "")
            + ". The browser's own recogniser is used for this attempt.")

    said = _text_of(data)
    if not said:
        raise STTRefused(
            R_NO_TEXT,
            "nothing was heard in that recording. Press the microphone and "
            "say it again, a little closer.")
    return Heard(text=said, lang=lang, model=model_name(),
                 seconds=seconds, bytes_in=len(audio))


def _text_of(data: Any) -> str:
    """The words out of the provider's envelope, or "" — never a KeyError.

    An empty answer is a legitimate result here (silence), so this returns the
    empty string and lets the caller decide it is a refusal.
    """
    try:
        parts = data["candidates"][0]["content"]["parts"]
        out = "".join(str(p.get("text") or "") for p in parts)
        return " ".join(out.split()).strip()
    except (KeyError, IndexError, TypeError, ValueError):
        return ""
