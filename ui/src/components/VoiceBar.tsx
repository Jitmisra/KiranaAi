import { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { rupees } from '../lib/money';
import * as adv from '../lib/advisorapi';
import { Card, Pill, Refusal, Verdict, Skeleton, IcoMic } from './ui';
import { useT } from '../lib/i18n';
import { SpeechDirector } from '../lib/lipsync';
import { wordSpans, type Viseme, type WordSpan } from '../lib/visemes';
import {
  speechSupport, VoiceMic, ServerEars, DEFAULT_LANG, pickVoice, tellSalaahkaar, confirmation,
  type MicEvents, type Told, type BillProposal, type BookProposal, type VoiceChoice,
} from '../lib/voice';
import type { PresenterState } from './Presenter';

/**
 * SALAAHKAAR AT THE COUNTER — the "Say the order" card, with somebody in it.
 *
 * This used to be a microphone and a parser: it heard "do Maggi aur ek Pepsi",
 * matched the words against the catalogue in the browser, and listed what it
 * thought was meant under a button. It had no face, no voice back, and it did
 * not answer a question. The advisor — Salaahkaar, the drawn presenter on the
 * Advisor screen — could answer a question but was a screen away from the
 * bill. So the shopkeeper asked for the obvious thing: tell HER, at the till.
 *
 * WHAT SHE DOES HERE, and what she may not:
 *
 *   SHE PUTS LINES ON THE BILL. A PERSON ACCEPTS THEM. An order — a count in
 *   front of a product — goes to the till's own assistant, which resolves the
 *   products, prices them from the catalogue in integer paise and writes a
 *   proposal down. The Till draws those lines on the bill in AMBER, marked
 *   PROPOSED: amber because a proposal is an abstention, the counter saying
 *   "this is what I think you meant" and waiting. `onPropose` fires with the
 *   server's proposal and nothing else; the ACCEPT buttons are the Till's.
 *
 *   SHE ANSWERS TOO. A question — "Parle-G ka daam kya hai" — goes to the
 *   advisor's call endpoint and comes back as a sentence she says out loud.
 *   The bill is not touched, and the screen says which of the two it did.
 *
 *   SHE NEVER CHARGES. CHARGE is a button on the bill, pressed by a hand.
 *   `lib/voice.ts::tellSalaahkaar` is the only network this card does, it
 *   knows two paths, and `voice.test.ts` asserts that nothing said or typed
 *   here reaches /intent, /pay or /mint (invariant 6).
 *
 * THE PARSER IN THE BROWSER IS NO LONGER THE ONE THAT PROPOSES. It classifies
 * — order or question — and that is all; the products and the prices are the
 * server's, so there is one opinion about what "Maggi" means and it is the
 * assistant's. What that costs: this card needs the till. It always did need
 * the internet, because the speech service is Chrome's.
 *
 * THE FACE is `components/Presenter`, the same rig the Advisor screen uses,
 * loaded lazily so the till's first paint does not carry it. The mouth is
 * driven by `lib/lipsync.ts` exactly as on the call, and the voice is the same
 * natural voice the till fetches once per sentence — with the browser's own
 * voice underneath it, always.
 */

export interface VoiceBarProps {
  /**
   * The lines she proposed, as the server wrote them, and the sentence they
   * came from. The Till puts them on the bill as PROPOSED. Fires from a server
   * answer and from nothing else — no timeout, no "confident enough".
   */
  onPropose: (proposal: BillProposal, heard: string) => void;
  /**
   * KHATA: "Sharma ji ke khate mein likh do". The server proposed putting the
   * bill on a household's book; the Till draws it beside the bill for a
   * person to accept. Optional, because only the till has a bill to book.
   */
  onProposeBook?: (proposal: BookProposal, heard: string) => void;
  /**
   * BCP-47 tag for the recogniser AND the voice back. Defaults to 'hi-IN'
   * because the counting words are Hindi and they are what decides a quantity;
   * the chips on the card let the shopkeeper change it for this device.
   */
  lang?: string;
  /** The till's own mute. Muted, she is shown and not heard. */
  muted?: boolean;
}

/**
 * The languages the microphone can be set to, and what they cost.
 *
 * hi-IN is the default because a kirana counter is spoken to in Hindi far more
 * than in English — but it returns brand names in Devanagari, and the server
 * has to respell them. The same tag picks the voice she answers in.
 */
const MIC_LANGS: readonly { id: string; label: string; note: string }[] = [
  { id: 'hi-IN', label: 'हिन्दी', note: 'best for Hindi speech; brand names come back in Devanagari' },
  { id: 'en-IN', label: 'English', note: 'best when the catalogue is written in English' },
  { id: 'bn-IN', label: 'বাংলা', note: 'best for Bengali speech' },
];

const MIC_LANG_KEY = 'gawaah.mic.lang.v1';

/**
 * The Advisor screen's own voice preference, read and not written: a shop
 * that chose "this browser" there — nothing leaves — gets the same answer at
 * the till without being asked twice. The toggle stays on the Advisor screen.
 */
const VOICE_KEY = 'gawaah.advisor.voice.v1';

function stored(key: string, allowed: ReadonlyArray<string>, fallback: string): string {
  try {
    const v = localStorage.getItem(key);
    return v !== null && allowed.includes(v) ? v : fallback;
  } catch {
    // A browser with site data blocked still has to be able to talk.
    return fallback;
  }
}

/** The face is lazy: 19 kB of plate and a rig the first paint does not need. */
const Presenter = lazy(() => import('./Presenter'));

/** What actually voiced one answer. */
type VoicedBy = 'natural' | 'browser';

/** A refusal's first sentence, for a mouth: the whole catalogue is not read out. */
function firstSentence(text: string, max = 240): string {
  const cut = text.search(/[.।]\s/);
  const s = cut > 0 ? text.slice(0, cut + 1) : text;
  return s.length > max ? `${s.slice(0, max - 1)}…` : s;
}

export default function VoiceBar({ onPropose, onProposeBook, lang: langProp = DEFAULT_LANG, muted = false }: VoiceBarProps) {
  const { t, tx, tnx } = useT();

  /* ---- language ------------------------------------------------------------ */

  const [lang, setLang] = useState(() => stored(MIC_LANG_KEY, MIC_LANGS.map((l) => l.id), langProp));
  const chooseLang = useCallback((id: string) => {
    setLang(id);
    try { localStorage.setItem(MIC_LANG_KEY, id); } catch { /* not worth failing over */ }
  }, []);
  const langRef = useRef(lang);
  useEffect(() => { langRef.current = lang; }, [lang]);

  /* ---- what she said last, and what she is doing now ----------------------- */

  const [draft, setDraft] = useState('');
  const [told, setTold] = useState<Told | null>(null);
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const [speaking, setSpeaking] = useState(false);
  const [voicing, setVoicing] = useState(false);
  const [voicedBy, setVoicedBy] = useState<VoicedBy | null>(null);
  const [voiceRefusal, setVoiceRefusal] = useState<string | null>(null);
  /** The advisor's call id, so "uska daam" after "Maggi hai?" is about Maggi. */
  const sessionRef = useRef<string | null>(null);
  const mutedRef = useRef(muted);
  useEffect(() => { mutedRef.current = muted; }, [muted]);

  /* ---- the mouth ----------------------------------------------------------- */

  /**
   * `viseme` and `amp` are REFS, not state — the presenter's own frame loop
   * reads them up to sixty times a second, and routed through React they
   * would re-render the whole till, camera stage and all, at that rate.
   */
  const viseme = useRef<Viseme>('sil');
  const amp = useRef(0);
  const [caption, setCaption] = useState<{ text: string; spans: WordSpan[] } | null>(null);
  const [capWord, setCapWord] = useState(-1);

  const synth = typeof speechSynthesis !== 'undefined' ? speechSynthesis : null;
  const [voices, setVoices] = useState<SpeechSynthesisVoice[]>([]);
  useEffect(() => {
    if (!synth) return;
    const load = () => setVoices(synth.getVoices());
    load();
    // Chrome fills the list asynchronously and says so with this event.
    synth.addEventListener('voiceschanged', load);
    return () => synth.removeEventListener('voiceschanged', load);
  }, [synth]);
  const choice = useMemo<VoiceChoice>(
    () => (synth ? pickVoice(voices, lang)
      : { voice: null, matched: false, note: 'This browser cannot speak, so answers are shown and not spoken.' }),
    [synth, voices, lang],
  );
  const choiceRef = useRef(choice);
  useEffect(() => { choiceRef.current = choice; }, [choice]);

  /** Whether the till can voice her. Asked once; the browser's voice is always there. */
  const [health, setHealth] = useState<adv.AdvisorHealth | null>(null);
  useEffect(() => {
    let alive = true;
    void adv.health().then((r) => {
      if (alive && r.ok) setHealth(r as unknown as adv.AdvisorHealth);
    });
    return () => { alive = false; };
  }, []);
  const useNatural = !!health?.voice?.available
    && stored(VOICE_KEY, ['natural', 'browser'], 'natural') === 'natural';
  const useNaturalRef = useRef(useNatural);
  useEffect(() => { useNaturalRef.current = useNatural; }, [useNatural]);

  /**
   * An <audio> element with an analyser on it, so the presenter's halo and
   * nod follow the measured level of her voice. Copied from the Advisor
   * screen rather than shared: it is thirty lines and that route is lazy.
   * WITHOUT the connect to `destination` the voice is silent — an analyser
   * is a tap, not a sink.
   */
  const audioCtx = useRef<AudioContext | null>(null);
  const analyser = useRef<AnalyserNode | null>(null);
  const ampRaf = useRef(0);
  const stopMeter = useCallback(() => {
    cancelAnimationFrame(ampRaf.current);
    ampRaf.current = 0;
    amp.current = 0;
  }, []);
  const meteredAudio = useCallback((url: string) => {
    const el = new Audio(url);
    try {
      const ctx = audioCtx.current ?? (audioCtx.current = new AudioContext());
      const src = ctx.createMediaElementSource(el);
      const an = analyser.current ?? (analyser.current = ctx.createAnalyser());
      an.fftSize = 512;
      an.smoothingTimeConstant = 0.6;
      src.connect(an);
      an.connect(ctx.destination);
      if (ctx.state === 'suspended') void ctx.resume();
      const buf = new Uint8Array(an.fftSize);
      const tick = () => {
        an.getByteTimeDomainData(buf);
        let sum = 0;
        for (let i = 0; i < buf.length; i++) { const v = ((buf[i] ?? 128) - 128) / 128; sum += v * v; }
        amp.current = Math.min(1, Math.sqrt(sum / buf.length) * 4.2);
        ampRaf.current = requestAnimationFrame(tick);
      };
      if (!ampRaf.current) ampRaf.current = requestAnimationFrame(tick);
      el.addEventListener('ended', stopMeter);
      el.addEventListener('error', stopMeter);
      el.addEventListener('pause', stopMeter);
    } catch {
      // No Web Audio, or a context that refused: the sentence still plays
      // and the halo follows the mouth instead.
    }
    return el;
  }, [stopMeter]);
  useEffect(() => () => { stopMeter(); void audioCtx.current?.close(); }, [stopMeter]);

  const director = useRef<SpeechDirector | null>(null);
  useEffect(() => {
    const d = new SpeechDirector(synth, {
      viseme: (v) => { viseme.current = v; },
      word: (i) => setCapWord(i),
      speaking: (on) => setSpeaking(on),
    });
    director.current = d;
    return () => { d.cancel(); director.current = null; };
  }, [synth]);

  const stopSpeaking = useCallback(() => {
    director.current?.cancel();
    stopMeter();
    setSpeaking(false);
    setCaption(null);
    setCapWord(-1);
  }, [stopMeter]);

  /**
   * Say one sentence. Natural when the till can and the shop has not said
   * otherwise; the browser's own voice underneath it on any refusal, because
   * a robotic sentence beats a silent one. Muted, nothing is said: the words
   * are on the screen either way.
   */
  const speak = useCallback((text: string) => {
    const d = director.current;
    const said = text.trim();
    if (!d || !said || mutedRef.current) { setCaption(null); return; }
    setCaption({ text: said, spans: wordSpans(said) });
    setCapWord(-1);
    const finish = () => { setCaption(null); setCapWord(-1); };
    const viaBrowser = () => {
      const c = choiceRef.current;
      if (!c.voice) { finish(); return; }
      setVoicedBy('browser');
      d.speak({ text: said, voice: c.voice, done: finish });
    };
    if (!useNaturalRef.current) { viaBrowser(); return; }
    setVoicing(true);
    void adv.speak(said, langRef.current).then((r) => {
      setVoicing(false);
      if (!r.ok) { setVoiceRefusal(r.detail ?? r.reason); viaBrowser(); return; }
      setVoiceRefusal(null);
      setVoicedBy('natural');
      const v = r as unknown as adv.Voiced;
      // A same-origin URL, never a blob: the CSP has no media-src.
      d.speakAudio({
        text: said,
        url: v.url,
        done: (outcome) => { stopMeter(); if (outcome === 'failed') viaBrowser(); else finish(); },
      }, meteredAudio);
    }).catch((e: unknown) => {
      setVoicing(false);
      setVoiceRefusal(String(e));
      viaBrowser();
    });
  }, [meteredAudio, stopMeter]);

  useEffect(() => { if (muted) stopSpeaking(); }, [muted, stopSpeaking]);

  /* ---- telling her --------------------------------------------------------- */

  const onProposeRef = useRef(onPropose);
  useEffect(() => { onProposeRef.current = onPropose; }, [onPropose]);
  const onProposeBookRef = useRef(onProposeBook);
  useEffect(() => { onProposeBookRef.current = onProposeBook; }, [onProposeBook]);

  const tell = useCallback(async (text: string, source: 'text' | 'voice') => {
    const said = text.trim();
    if (!said || busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    stopSpeaking();
    setVoicedBy(null);
    const r = await tellSalaahkaar(said, {
      source, lang: langRef.current, sessionId: sessionRef.current,
    });
    busyRef.current = false;
    setBusy(false);
    setTold(r);
    if (r.kind === 'answer') sessionRef.current = r.sessionId;
    if (r.kind === 'proposal') {
      onProposeRef.current(r.proposal, r.heard);
      speak(confirmation(r.proposal, langRef.current));
    } else if (r.kind === 'book') {
      // The proposal is the Till's to draw; this card only says it out loud.
      onProposeBookRef.current?.(r.proposal, r.heard);
      speak(r.spoken);
    } else if (r.kind === 'answer') {
      speak(r.spoken);
    } else {
      // A refusal is read out too: a shopkeeper looking at a packet, not the
      // screen, deserves to hear why. The first sentence — the reason — and
      // not the list of everything the shop sells.
      speak(firstSentence(r.detail || r.reason));
    }
  }, [speak, stopSpeaking]);

  const send = useCallback(() => {
    const said = draft.trim();
    if (!said) return;
    setDraft('');
    void tell(said, 'text');
  }, [draft, tell]);

  /* ---- the microphone ------------------------------------------------------ */

  const support = useMemo(() => speechSupport(), []);
  const [listening, setListening] = useState(false);
  const [interim, setInterim] = useState('');
  const [micError, setMicError] = useState<string | null>(null);
  const tellRef = useRef(tell);
  useEffect(() => { tellRef.current = tell; }, [tell]);
  const micRef = useRef<VoiceMic | null>(null);
  /**
   * WHICH EARS ARE LISTENING. The browser's recogniser is the first choice —
   * no key, no cost, and partial words as they are spoken. When it cannot
   * reach its service (`network`, which on a shop's wifi is the common case,
   * not the exotic one) the counter's own ears take over: record, post to
   * `/advisor/listen`, get the words back. This flips once and stays flipped
   * for the session, because a service that refused a moment ago will refuse
   * the next press too and a shopkeeper should not have to discover that
   * twice.
   */
  const [viaCounter, setViaCounter] = useState(false);
  const earsRef = useRef<ServerEars | null>(null);

  const events = useMemo<MicEvents>(() => ({
    partial: (text) => setInterim(text),
    // One settled utterance is one sentence to her. The microphone closes so
    // her answer is not transcribed back into the next order.
    final: (text) => {
      setInterim('');
      if (!text) return;
      micRef.current?.stop();
      void tellRef.current(text, 'voice');
    },
    error: (message) => setMicError(message),
    end: () => { setListening(false); setInterim(''); },
  }), []);

  /**
   * The recogniser's events, plus the one that switches ears. A `network`
   * failure is not the shopkeeper's problem to solve, so instead of printing
   * it and stopping, the counter takes over and says which ears are on.
   */
  const micEvents = useMemo<MicEvents>(() => ({
    ...events,
    error: (message) => {
      const unreachable = /could not be reached|network|not provide speech/i.test(message);
      if (unreachable && ServerEars.supported && !viaCounter) {
        setViaCounter(true);
        setMicError(null);
        setListening(false);
        return;
      }
      setMicError(message);
    },
  }), [events, viaCounter]);

  const mic = useMemo(() => new VoiceMic({ lang, on: micEvents }), [lang, micEvents]);
  const ears = useMemo(() => new ServerEars({ lang, on: events }), [lang, events]);
  useEffect(() => {
    earsRef.current = ears;
    return () => ears.cancel();
  }, [ears]);
  // A live recogniser holds the microphone. Leaving one running behind a
  // navigation leaves the browser's recording indicator on over a dead page.
  useEffect(() => {
    micRef.current = mic;
    return () => mic.stop();
  }, [mic]);

  const toggleMic = useCallback(() => {
    if (listening) { (viaCounter ? ears : mic).stop(); setListening(false); return; }
    setMicError(null);
    stopSpeaking();
    if (viaCounter) {
      // Recording is async: the permission prompt and the device open both
      // take a moment, so the button goes live now and `end` turns it off.
      setListening(true);
      void ears.start();
      return;
    }
    mic.start();
    // start() is synchronous and reports failure through `error` without ever
    // firing `end`, so the button's state comes from the mic, not from hope.
    setListening(mic.listening);
  }, [listening, mic, ears, viaCounter, stopSpeaking]);

  // A recogniser that failed on the FIRST press has already flipped the flag
  // by the time the click handler returns; start the counter's ears at once
  // rather than making the shopkeeper press twice.
  const flipped = useRef(false);
  useEffect(() => {
    if (viaCounter && !flipped.current) { flipped.current = true; setListening(true); void ears.start(); }
  }, [viaCounter, ears]);

  /* ---- render -------------------------------------------------------------- */

  /**
   * What the tile says it is doing must never contradict what it IS doing:
   * SPEAKING and THINKING come first, then the microphone, then rest.
   */
  const presence: PresenterState = speaking ? 'speaking'
    : (busy || voicing) ? 'thinking'
      : listening ? 'listening'
        : 'idle';
  const stateLabel = voicing ? t('till.sk.state.voicing') : t(`till.sk.state.${presence}` as
    'till.sk.state.idle' | 'till.sk.state.listening' | 'till.sk.state.thinking' | 'till.sk.state.speaking');

  const whyKey = (w: Told['route']['why']) => t(`till.sk.why.${w}` as
    | 'till.sk.why.shop_word' | 'till.sk.why.question_word' | 'till.sk.why.nothing'
    | 'till.sk.why.add_verb' | 'till.sk.why.weight' | 'till.sk.why.count'
    | 'till.sk.why.several' | 'till.sk.why.one_bare');

  return (
    <Card
      className="sk"
      icon={<IcoMic />}
      title={<>{t('till.sk.title')}<span className="deva sk-deva" lang="hi">सलाहकार</span></>}
      sub={t('till.sk.sub')}
      aside={<Pill tone={presence === 'idle' ? 'off' : 'code'} dot={presence !== 'idle'}>{stateLabel}</Pill>}
    >
      <div className="sk-body">
        {/* ------------------------------------------------------ the tile -- */}
        <div className="sk-tile" data-state={presence} aria-label={t('till.sk.title')}>
          <Suspense fallback={<div className="sk-tile-wait"><Skeleton h="100%" radius={12} /></div>}>
            <Presenter state={presence} viseme={viseme} amplitude={amp} className="sk-presenter" />
          </Suspense>
          {/* The wave: bars that breathe with `--amp`, which the presenter
              sets on this tile once a frame. Drawn only while speaking, and
              as a slow idle pulse while the microphone is open. */}
          {(presence === 'speaking' || presence === 'listening') && (
            <div className={`sk-wave ${presence}`} aria-hidden="true">
              {Array.from({ length: 9 }, (_, i) => <i key={i} style={{ '--k': i } as React.CSSProperties} />)}
            </div>
          )}
        </div>

        {/* --------------------------------------------------- the readout --
            Direct children of the grid, on purpose: the readout sits beside
            the tile and the input and the chips take the full width under
            them. Wrapped in a column div they were squeezed beside the tile
            and the text field was a 6 px sliver between two buttons. */}
          <div className="sk-readout" aria-live="polite">
            {caption ? (
              <p className="sk-cap said">
                <b>{voicing ? t('till.sk.state.voicing') : t('till.sk.saying')}</b>
                {caption.spans.map((s, i) => (
                  <span key={`${s.start}-${i}`} className={i < capWord ? 'was' : i === capWord ? 'now' : ''}>
                    {s.text}{' '}
                  </span>
                ))}
              </p>
            ) : interim ? (
              <p className="sk-cap heard"><b>{t('till.sk.heard')}</b>{interim}…</p>
            ) : told ? (
              <p className="sk-cap heard">
                <b>{told.route.route === 'order' ? t('till.sk.route.order') : t('till.sk.route.advice')}</b>
                {told.heard}
              </p>
            ) : (
              <p className="sk-cap muted">{listening ? t('till.sk.listening') : t('till.sk.idle')}</p>
            )}
          </div>

          <div className="sk-input">
            <button
              className={listening ? 'btn danger' : 'btn primary'}
              onClick={toggleMic}
              disabled={!support.ok || busy}
              title={support.ok ? undefined : support.reason}
            >
              {listening ? t('till.sk.stop') : t('till.sk.listen')}
            </button>
            <input
              className="inp sk-text"
              type="text"
              value={draft}
              maxLength={adv.MAX_TEXT}
              placeholder={t('till.sk.placeholder')}
              aria-label={t('till.sk.sub')}
              disabled={busy}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); send(); } }}
            />
            <button className="btn" onClick={send} disabled={busy || draft.trim() === ''}>
              {t('till.sk.send')}
            </button>
          </div>

          {/* Which language she listens in and answers in. A shopkeeper whose
              catalogue is in English was stuck with a Hindi recogniser that
              returned पॉन्ड्स for "ponds" and could not be told otherwise. */}
          <div className="vb-langs" role="group" aria-label={t('till.sk.langs')}>
            {MIC_LANGS.map((l) => (
              <button
                key={l.id}
                className={`btn sm${l.id === lang ? ' primary' : ' ghost'}`}
                /* The BCP-47 tag lives HERE now, not as a chip beside the
                   row. It is real information — it is what the browser sends
                   audio as — but printed next to three buttons that already
                   say which language is chosen it was a fourth, unexplained
                   token on a counter screen. */
                title={`${l.note} — sent as ${l.id}`}
                aria-pressed={l.id === lang}
                disabled={listening || busy}
                onClick={() => chooseLang(l.id)}
              >
                {l.label}
              </button>
            ))}
          </div>
      </div>

      {!support.ok && (
        <p className="hint"><b>{t('till.sk.noMic')}.</b> {support.reason} {t('till.sk.noMic.hint')}</p>
      )}
      {/* AMBER, NOT RED. Red is reserved for something this counter REFUSED.
          An unreachable speech service is not a refusal — it is the counter
          unable to hear, which is an abstention, and the bill and the camera
          are untouched by it. */}
      {micError && <Verdict tone="amber" title={t('till.sk.micStopped')}>{micError}</Verdict>}

      {/* ---------------------------------------- what she made of it -- */}

      {told && (
        <div className="sk-told">
          <div className="sk-route">
            <Pill tone="code">
              {told.route.route === 'order' ? t('till.sk.route.order') : t('till.sk.route.advice')}
            </Pill>
            <span>
              {told.kind === 'refusal'
                ? t('till.sk.route.refused.v')
                : told.route.route === 'order' ? t('till.sk.route.order.v') : t('till.sk.route.advice.v')}
            </span>
            <span className="muted">· {whyKey(told.route.why)}</span>
          </div>

          {told.kind === 'proposal' && (
            <Verdict tone="amber" title={t('till.bill.proposed.pill')}>
              <p className="sk-put">
                {tnx('till.sk.put', told.proposal.lines.length, {
                  n: told.proposal.lines.length,
                  total: rupees(told.proposal.total_paise),
                })}
              </p>
              {/* `sk-put-lines`, not `sk-lines`: that name belongs to the
                  Salaahkaar call's own list (SalaahkaarCall.tsx, salaahkaar.css),
                  and one class in two stylesheets was styling both. */}
              <ul className="sk-put-lines mono">
                {told.proposal.lines.map((l, i) => (
                  <li key={`${l.sku_id}-${i}`}>
                    {l.by === 'weighed' && l.weight ? l.weight : `${l.qty} ×`} {l.name} · {rupees(l.line_paise)}
                  </li>
                ))}
              </ul>
              {told.proposal.caution && (
                <p className="sk-caution"><b>{t('till.sk.check')}:</b> {told.proposal.caution}</p>
              )}
            </Verdict>
          )}

          {told.kind === 'book' && (
            /* Neutral, not amber: a booking is not an abstention and not a
               settlement. The Till draws the ACCEPT beside the bill. */
            <Verdict tone="info" title={t('till.book.action')}>
              {told.answer}
            </Verdict>
          )}

          {told.kind === 'answer' && (
            <Verdict tone="info" title={t('till.sk.answer')}>
              {told.reread && (
                <p className="sk-reread">
                  {told.reread === 'as_question'
                    ? t('till.sk.reread.as_question')
                    : t('till.sk.reread.as_order')}
                </p>
              )}
              <p>{told.spoken}</p>
              {told.spoken !== told.answer && <p className="muted">{told.answer}</p>}
            </Verdict>
          )}

          {told.kind === 'refusal' && (
            <Refusal reason={told.reason} detail={told.detail} hint={t('till.sk.refused')} />
          )}

          {voicedBy && !muted && (
            <p className="hint sk-voiced">
              {voicedBy === 'natural' ? t('till.sk.byVoice') : t('till.sk.byBrowser')}
              {voicedBy === 'browser' && choice.note ? ` — ${choice.note}` : ''}
            </p>
          )}
        </div>
      )}

      {voiceRefusal && <p className="hint">{t('till.sk.voiceRefused', { why: voiceRefusal })}</p>}
      {muted && <p className="hint">{t('till.sk.muted')}</p>}

      <p className="hint">{tx('till.sk.disclose')}</p>
      <p className="hint sk-never">{t('till.sk.never')}</p>
    </Card>
  );
}
