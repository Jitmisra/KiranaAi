import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import * as adv from '../lib/advisorapi';
import * as aa from '../lib/assistantapi';
import * as api from '../lib/api';
import {
  GREETING, isLangTag, pickVoice, routeTurn, readApplied, sayableProducts, suggestions, undoPlan,
  type Routing, type Suggestion, type VoiceChoice,
} from '../lib/salaahkaar';
import {
  endCall, getCall, patchCall, pushTurn, setLang as storeLang, updateTurn, useCall,
  type CallState, type Presence, type Turn, type VoicedBy,
} from '../lib/salaahkaar-store';
import { speechSupport, VoiceMic, type MicEvents } from '../lib/voice';
import { SpeechDirector } from '../lib/lipsync';
import { wordSpans, type Viseme, type WordSpan } from '../lib/visemes';

/**
 * THE ENGINE — everything Salaahkaar does that is not drawing.
 *
 * One hook, mounted by whichever VIEW is showing the call: the full page at
 * #/salaahkaar, or the modal behind the round button on every other screen.
 * The transcript, the session and the language live in `lib/salaahkaar-store`
 * so the two views are the same call; what lives here is what cannot outlive a
 * view — the microphone, the speech director that moves the mouth, the
 * analyser on the natural voice — plus the decisions that route a sentence
 * and press a proposal into effect.
 *
 * TWO BRAINS BEHIND ONE COMPOSER. Every sentence is routed BEFORE it is sent
 * (`routeTurn`, pure, tested): an instruction — put this on the bill, ten
 * Maggi arrived, write down two hundred for chai — goes to `/assistant/ask`,
 * which answers with a PROPOSAL; a question goes to `/advisor/say`, which
 * answers on the call, reasons about the figures, and voices it. Each turn
 * says which door it went through, and an action turn carries a card with
 * what would change, a button a person has to press, and an UNDO wherever the
 * server has one.
 *
 * WHAT IT WILL NOT DO, and the same rules as before the merge: nothing here
 * writes a bill line — a bill proposal is HELD for the till, which is the only
 * thing that writes one; a stock movement and an expense are accepted at their
 * own endpoints with the server's own body, on a press, never on a voice
 * command; nothing here mints a payment, ever.
 */

export type { Presence, Turn, VoicedBy };

type VoiceMode = 'natural' | 'browser';
/** Same key the advisor used, so a shop's choice carries over the merge. */
const VOICE_KEY = 'gawaah.advisor.voice.v1';

function storedVoiceMode(): VoiceMode | null {
  try {
    const v = localStorage.getItem(VOICE_KEY);
    return v === 'natural' || v === 'browser' ? v : null;
  } catch {
    return null;
  }
}

/**
 * WHO IS ON THE LINE, asked once per page load. Both views open and close a
 * dozen times a shift; re-asking two health routes on every press would spend
 * a shop's connection to be told the same thing.
 */
let advisorHealthOnce: ReturnType<typeof adv.health> | null = null;
let assistantHealthOnce: ReturnType<typeof aa.health> | null = null;
let shopOnce: ReturnType<typeof api.shop> | null = null;

export interface Engine {
  call: CallState;
  health: adv.AdvisorHealth | null;
  healthRefusal: { reason: string; detail?: string } | null;
  healthLoading: boolean;
  askHealth: aa.AssistantHealth | null;
  /** Whether a model will REASON about advice. False means figures only. */
  reasons: boolean;
  chips: Suggestion[];
  catLoading: boolean;
  catRefusal: { reason: string; detail?: string } | null;
  presence: Presence;
  busy: boolean;
  elapsed: number;
  /* voice in */
  micOk: boolean;
  micReason: string;
  listening: boolean;
  interim: string;
  micError: string | null;
  toggleMic: () => void;
  /* voice out */
  canSpeak: boolean;
  speakOn: boolean;
  setSpeakOn: (on: boolean) => void;
  useNatural: boolean;
  naturalAvailable: boolean;
  chooseVoice: (v: VoiceMode) => void;
  choice: VoiceChoice;
  voicing: boolean;
  voiceRefusal: { reason: string; detail?: string } | null;
  caption: { text: string; spans: WordSpan[] } | null;
  capWord: number;
  viseme: { readonly current: Viseme };
  amp: { readonly current: number };
  /* the composer */
  draft: string;
  setDraft: (s: string) => void;
  boxRef: React.RefObject<HTMLTextAreaElement>;
  /* doing */
  say: (text: string, source: 'text' | 'voice') => Promise<void>;
  putBack: (text: string) => void;
  sayAgain: (text: string) => void;
  startCall: () => void;
  hangUp: () => void;
  setLang: (lang: string) => void;
  applyAction: (turnId: number) => Promise<void>;
  undoAction: (turnId: number) => Promise<void>;
  leaveAction: (turnId: number) => void;
}

export function useSalaahkaar(): Engine {
  const call = useCall();
  const { onCall, startedAt, lang } = call;

  const [health, setHealth] = useState<adv.AdvisorHealth | null>(null);
  const [healthRefusal, setHealthRefusal] = useState<{ reason: string; detail?: string } | null>(null);
  const [healthLoading, setHealthLoading] = useState(true);
  const [askHealth, setAskHealth] = useState<aa.AssistantHealth | null>(null);

  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false);
  const [products, setProducts] = useState<string[]>([]);
  const [catLoading, setCatLoading] = useState(true);
  const [catRefusal, setCatRefusal] = useState<{ reason: string; detail?: string } | null>(null);
  const [elapsed, setElapsed] = useState(0);

  const [listening, setListening] = useState(false);
  const [interim, setInterim] = useState('');
  const [micError, setMicError] = useState<string | null>(null);

  const [speaking, setSpeaking] = useState(false);
  const [speakOn, setSpeakOn] = useState(true);
  const [voicePref, setVoicePref] = useState<VoiceMode | null>(() => storedVoiceMode());
  const [voicing, setVoicing] = useState(false);
  const [voiceRefusal, setVoiceRefusal] = useState<{ reason: string; detail?: string } | null>(null);
  const [voices, setVoices] = useState<SpeechSynthesisVoice[]>([]);
  const chooseVoice = useCallback((v: VoiceMode) => {
    setVoicePref(v);
    try { localStorage.setItem(VOICE_KEY, v); } catch { /* not worth failing over */ }
  }, []);

  /**
   * The mouth, and the caption under it. `viseme` is a REF: it changes up to
   * fifteen times a second and the presenter's own frame loop reads it; routed
   * through React it would re-render the transcript at that rate.
   */
  const viseme = useRef<Viseme>('sil');
  const [caption, setCaption] = useState<{ text: string; spans: WordSpan[] } | null>(null);
  const [capWord, setCapWord] = useState(-1);
  const boxRef = useRef<HTMLTextAreaElement>(null);

  /* ---- who is on the line ------------------------------------------------ */

  useEffect(() => {
    let alive = true;
    advisorHealthOnce ??= adv.health();
    assistantHealthOnce ??= aa.health();
    void advisorHealthOnce.then((r) => {
      if (!alive) return;
      if (r.ok) { setHealth(r as unknown as adv.AdvisorHealth); setHealthRefusal(null); }
      else setHealthRefusal({ reason: r.reason, detail: r.detail });
      setHealthLoading(false);
    });
    void assistantHealthOnce.then((r) => {
      if (alive && r.ok) setAskHealth(r as unknown as aa.AssistantHealth);
    });
    return () => { alive = false; };
  }, []);

  // A CATALOGUE THAT DID NOT ARRIVE IS SAID OUT LOUD: the only symptom would be
  // the product chip quietly missing, which looks like a shop with no shelf.
  useEffect(() => {
    let alive = true;
    shopOnce ??= api.shop();
    void shopOnce.then((r) => {
      if (!alive) return;
      if (r.ok) { setCatRefusal(null); setProducts(sayableProducts(r.skus)); }
      else setCatRefusal({ reason: r.reason, detail: r.detail });
      setCatLoading(false);
    });
    return () => { alive = false; };
  }, []);

  const chips = useMemo(() => suggestions(products), [products]);

  /* ---- the voice out ------------------------------------------------------ */

  const synth = typeof speechSynthesis !== 'undefined' ? speechSynthesis : null;

  useEffect(() => {
    if (!synth) return;
    const load = () => setVoices(synth.getVoices());
    load();
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

  /**
   * THE VOICE'S LEVEL, measured, as a ref. `Presenter` reads it every frame for
   * the halo and the nod. Fed by a Web Audio analyser on the <audio> element
   * the natural voice plays through — same origin, so `connect-src 'self'` is
   * satisfied. On the browser's own voice there is no stream to measure and
   * the presenter falls back to the mouth.
   */
  const amp = useRef(0);
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
      // WITHOUT THIS THE VOICE IS SILENT. An analyser is a tap, not a sink.
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
      // No Web Audio, or a context that refused: the sentence still plays and
      // the halo follows the mouth instead.
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
    setSpeaking(false);
    setCaption(null);
    setCapWord(-1);
  }, []);

  const naturalAvailable = !!health?.voice?.available;
  const useNatural = (voicePref ?? (naturalAvailable ? 'natural' : 'browser')) === 'natural' && naturalAvailable;
  const useNaturalRef = useRef(useNatural);
  useEffect(() => { useNaturalRef.current = useNatural; }, [useNatural]);
  const langRef = useRef(lang);
  useEffect(() => { langRef.current = lang; }, [lang]);

  /**
   * Say one text, then call `done`. An unavailable voice or a disabled speaker
   * resolves at once, so the microphone reopens either way — a path that never
   * calls `done` is a call that goes deaf. Natural when the till can and the
   * shopkeeper has not said otherwise; the browser's own voice underneath it.
   */
  const speak = useCallback((text: string, done: () => void, turnId?: number) => {
    const c = choiceRef.current;
    const d = director.current;
    const said = text.trim();
    if (!d || !speakOn || !said) { setCaption(null); done(); return; }
    setCaption({ text: said, spans: wordSpans(said) });
    setCapWord(-1);
    const finish = () => { setCaption(null); setCapWord(-1); done(); };
    const mark = (voiced: VoicedBy) => {
      if (turnId === undefined) return;
      updateTurn(turnId, (t) => (t.who === 'sk' && (t.state === 'answer' || t.state === 'action') ? { ...t, voiced } : t));
    };
    const viaBrowser = () => {
      if (!c.voice) { finish(); return; }
      mark('browser');
      d.speak({ text: said, voice: c.voice, done: finish });
    };
    if (!useNaturalRef.current) { viaBrowser(); return; }
    setVoicing(true);
    void adv.speak(said, langRef.current).then((r) => {
      setVoicing(false);
      if (!r.ok) { setVoiceRefusal({ reason: r.reason, detail: r.detail }); viaBrowser(); return; }
      setVoiceRefusal(null);
      const v = r as unknown as adv.Voiced;
      mark(v.cached ? 'natural-cached' : 'natural');
      // A same-origin URL, never a blob: the CSP has no media-src.
      d.speakAudio({
        text: said,
        url: v.url,
        done: (outcome) => { stopMeter(); if (outcome === 'failed') viaBrowser(); else finish(); },
      }, meteredAudio);
    }).catch((e: unknown) => {
      setVoicing(false);
      setVoiceRefusal({ reason: 'the counter could not reach its own voice', detail: String(e) });
      viaBrowser();
    });
  }, [speakOn, meteredAudio, stopMeter]);

  useEffect(() => { if (!speakOn) stopSpeaking(); }, [speakOn, stopSpeaking]);

  /* ---- the microphone ---------------------------------------------------- */

  const support = useMemo(() => speechSupport(), []);
  const micRef = useRef<VoiceMic | null>(null);
  const busyRef = useRef(false);
  const speakingRef = useRef(false);
  const quietEnds = useRef(0);
  useEffect(() => { busyRef.current = busy; }, [busy]);
  useEffect(() => { speakingRef.current = speaking; }, [speaking]);

  const sayRef = useRef<(text: string, source: 'text' | 'voice') => Promise<void>>(async () => {});

  /** Open the microphone if the call is live and nothing else is talking. */
  const reopen = useCallback(() => {
    if (!support.ok) return;
    if (!getCall().onCall || busyRef.current || speakingRef.current) return;
    const mic = micRef.current;
    if (!mic || mic.listening) return;
    setMicError(null);
    mic.start();
    setListening(mic.listening);
  }, [support]);

  const events = useMemo<MicEvents>(() => ({
    partial: (text) => setInterim(text),
    final: (text) => {
      setInterim('');
      if (!text) return;
      quietEnds.current = 0;
      micRef.current?.stop();
      void sayRef.current(text, 'voice');
    },
    error: (message) => { setMicError(message); quietEnds.current = 99; },
    end: () => {
      setListening(false);
      setInterim('');
      // Chrome closes a continuous recogniser after a few seconds of silence.
      // On a call that is a pause, not a stop — reopen, up to four times, so
      // an empty shop does not keep a recogniser running all afternoon.
      if (!getCall().onCall || busyRef.current || speakingRef.current) return;
      quietEnds.current += 1;
      if (quietEnds.current > 4) return;
      setTimeout(reopen, 350);
    },
  }), [reopen]);

  const mic = useMemo(() => new VoiceMic({ lang, on: events }), [lang, events]);
  useEffect(() => {
    micRef.current = mic;
    return () => mic.stop();
  }, [mic]);

  /* ---- saying something -------------------------------------------------- */

  const beginCallQuietly = useCallback(() => {
    if (getCall().onCall) return;
    quietEnds.current = 0;
    patchCall({ onCall: true, startedAt: Date.now() });
  }, []);

  const say = useCallback(async (text: string, source: 'text' | 'voice') => {
    const said = text.trim();
    if (!said || busyRef.current) return;
    const routing: Routing = routeTurn(said);
    const at = Date.now();
    beginCallQuietly();
    pushTurn({ who: 'you', at, text: said, source });
    const pendingId = pushTurn({ who: 'sk', at, asked: said, route: routing.route, state: 'asking' });
    setDraft('');
    setBusy(true);
    busyRef.current = true;
    micRef.current?.stop();
    stopSpeaking();

    let toSay: string;
    if (routing.route === 'action') {
      // THE ASSISTANT: one sentence, no memory, a proposal back.
      const r = await aa.ask(said, source);
      if (r.ok) {
        const a = r as unknown as aa.AskAnswer;
        updateTurn(pendingId, () => ({
          id: pendingId, who: 'sk', at: Date.now(), asked: said, route: 'action', state: 'action',
          answer: a, applied: null, undone: null, refusal: null, working: false, left: false,
        }));
        toSay = a.answer;
      } else {
        updateTurn(pendingId, () => ({
          id: pendingId, who: 'sk', at: Date.now(), asked: said, route: 'action', state: 'refusal', refusal: r,
        }));
        toSay = `I could not do that. ${r.detail ?? r.reason}`.slice(0, 320);
      }
      if (typeof r.key_present === 'boolean') {
        const present = r.key_present;
        setAskHealth((h) => (h ? { ...h, key_present: present } : h));
      }
    } else {
      // THE ADVISOR: on the call, with the session, in the chosen language.
      const before = getCall().sessionId;
      const r = await adv.say(said, source, before, langRef.current);
      if (r.ok) {
        const a = r as unknown as adv.SayAnswer;
        updateTurn(pendingId, () => ({
          id: pendingId, who: 'sk', at: Date.now(), asked: said, route: 'advice', state: 'answer', answer: a,
        }));
        if (a.session_id !== before) patchCall({ sessionId: a.session_id });
        if (a.previous_call === 'expired_or_unknown' && before) {
          pushTurn({ who: 'sys', at: Date.now(), text: 'The earlier part of this call had expired on the server, so it was answered without that context.' });
        }
        toSay = a.spoken;
      } else {
        updateTurn(pendingId, () => ({
          id: pendingId, who: 'sk', at: Date.now(), asked: said, route: 'advice', state: 'refusal', refusal: r,
        }));
        if (r.session_id && r.session_id !== before) patchCall({ sessionId: r.session_id });
        toSay = `I could not answer that. ${r.detail ?? r.reason}`.slice(0, 320);
      }
      if (typeof r.key_present === 'boolean') {
        const present = r.key_present;
        const model = typeof r.model === 'string' && r.model !== '' ? r.model : null;
        setHealth((h) => (h ? { ...h, key_present: present, reasons: present, model: model ?? h.model } : h));
      }
    }
    setBusy(false);
    busyRef.current = false;
    // Read it out, then reopen the line. A refusal is read out too: a
    // shopkeeper looking at a packet, not the screen, deserves to hear why.
    speak(toSay, () => setTimeout(reopen, 250), pendingId);
  }, [beginCallQuietly, speak, stopSpeaking, reopen]);
  useEffect(() => { sayRef.current = say; }, [say]);

  const putBack = useCallback((said: string) => {
    setDraft(said);
    const el = boxRef.current;
    if (!el) return;
    el.focus();
    try { el.setSelectionRange(said.length, said.length); } catch { /* not every engine allows it */ }
  }, []);

  /** Read one earlier answer out again. Nothing is re-asked; nothing leaves. */
  const sayAgain = useCallback((text: string) => {
    if (busyRef.current) return;
    micRef.current?.stop();
    speak(text, () => setTimeout(reopen, 250));
  }, [speak, reopen]);

  /* ---- pressing a proposal ------------------------------------------------ */

  const applyAction = useCallback(async (turnId: number) => {
    const t = getCall().turns.find((x) => x.id === turnId);
    if (!t || t.who !== 'sk' || t.state !== 'action' || t.applied || t.working) return;
    const p = t.answer.proposal;
    if (!p) return;
    const set = (patch: Partial<Extract<Turn, { state: 'action' }>>) =>
      updateTurn(turnId, (x) => (x.who === 'sk' && x.state === 'action' ? { ...x, ...patch } : x));
    set({ working: true, refusal: null });
    const kind = p.kind ?? 'bill';
    if (kind === 'bill') {
      // HELD, NOT BILLED. The till owns the basket and is the only thing that
      // writes a line; this records the person's decision in this browser.
      const r = aa.holdForTill(p);
      if (!r.ok) { set({ working: false, refusal: { reason: r.reason, detail: r.detail } }); return; }
      set({ working: false, applied: { at: Date.now() } });
      return;
    }
    if (!p.accept_by) {
      set({ working: false, refusal: { reason: 'nothing_to_accept_with', detail: 'The proposal names no endpoint to accept it at, so this counter cannot press it. Nothing was changed.' } });
      return;
    }
    // THE SERVER'S OWN INSTRUCTION, forwarded whole. Integer paise and all.
    const r = await aa.acceptProposal(p.accept_by);
    if (!r.ok) { set({ working: false, refusal: { reason: r.reason, detail: r.detail } }); return; }
    set({ working: false, applied: readApplied(r, Date.now()) });
  }, []);

  const undoAction = useCallback(async (turnId: number) => {
    const t = getCall().turns.find((x) => x.id === turnId);
    if (!t || t.who !== 'sk' || t.state !== 'action' || !t.applied || t.undone || t.working) return;
    const p = t.answer.proposal;
    if (!p) return;
    const set = (patch: Partial<Extract<Turn, { state: 'action' }>>) =>
      updateTurn(turnId, (x) => (x.who === 'sk' && x.state === 'action' ? { ...x, ...patch } : x));
    const plan = undoPlan(p, t.applied);
    if (!plan) return;
    set({ working: true, refusal: null });
    if (plan.kind === 'unhold') {
      const r = aa.unholdForTill(plan.proposal_id);
      if (!r.ok) { set({ working: false, refusal: { reason: r.reason, detail: r.detail } }); return; }
      set({ working: false, undone: Date.now() });
      return;
    }
    const r = await aa.post(plan.path, plan.body);
    if (!r.ok) { set({ working: false, refusal: { reason: r.reason, detail: r.detail } }); return; }
    set({ working: false, undone: Date.now() });
  }, []);

  const leaveAction = useCallback((turnId: number) => {
    updateTurn(turnId, (x) => (x.who === 'sk' && x.state === 'action' && !x.applied ? { ...x, left: true } : x));
  }, []);

  /* ---- the call ------------------------------------------------------------ */

  const startCall = useCallback(() => {
    beginCallQuietly();
    pushTurn({ who: 'sys', at: Date.now(), text: `Call started. ${choiceRef.current.note}` });
    const tag = langRef.current;
    const g = GREETING[isLangTag(tag) ? tag : 'en-IN'];
    speak(health?.reasons ? g.reasons : g.figures, () => setTimeout(reopen, 200));
  }, [beginCallQuietly, health, speak, reopen]);

  const hangUp = useCallback(() => {
    micRef.current?.stop();
    stopSpeaking();
    setListening(false);
    setInterim('');
    const id = endCall();
    pushTurn({ who: 'sys', at: Date.now(), text: id
      ? 'Call ended. The server forgot it; nothing about it was on disk.'
      : 'Call ended. Nothing was said.' });
    if (id) void adv.hangUp(id);
  }, [stopSpeaking]);

  // THE CALL OUTLIVES THE VIEW — that is the point of the store — but the
  // microphone and the voice do not: a recogniser left running behind a closed
  // modal holds the microphone over a page nobody is talking to.
  useEffect(() => () => {
    if (synth) synth.cancel();
  }, [synth]);

  useEffect(() => {
    if (!onCall || startedAt === null) return;
    const tick = () => setElapsed(Math.floor((Date.now() - startedAt) / 1000));
    tick();
    const t = setInterval(tick, 1000);
    return () => clearInterval(t);
  }, [onCall, startedAt]);

  const toggleMic = useCallback(() => {
    if (listening) { mic.stop(); quietEnds.current = 99; return; }
    if (!getCall().onCall) { startCall(); return; }
    quietEnds.current = 0;
    stopSpeaking();
    reopen();
  }, [listening, mic, startCall, stopSpeaking, reopen]);

  const setLang = useCallback((l: string) => { storeLang(l); }, []);

  /* ---- presence ------------------------------------------------------------ */

  /**
   * What the tile says it is doing, and it must never contradict what it IS
   * doing. SPEAKING and THINKING come first, ahead of the call state: a
   * shopkeeper who typed a question without pressing START is not on a call,
   * but the counter is still thinking and then still talking.
   */
  const presence: Presence = speaking ? 'speaking'
    : busy ? 'thinking'
      : !onCall ? 'off'
        : listening ? 'listening'
          : 'idle';

  // Written through to the store for the ring on the round button, and reset
  // when this view goes: a button pulsing SPEAKING beside a closed modal is a
  // face claiming to talk with nobody listening.
  useEffect(() => { patchCall({ presence }); }, [presence]);
  useEffect(() => {
    patchCall({ views: getCall().views + 1 });
    return () => {
      patchCall({ views: Math.max(0, getCall().views - 1), presence: getCall().onCall ? 'idle' : 'off' });
    };
  }, []);

  return {
    call,
    health, healthRefusal, healthLoading, askHealth,
    reasons: health?.reasons === true,
    chips, catLoading, catRefusal,
    presence, busy, elapsed,
    micOk: support.ok, micReason: support.reason, listening, interim, micError, toggleMic,
    canSpeak: speakOn && !!choice.voice,
    speakOn, setSpeakOn, useNatural, naturalAvailable, chooseVoice, choice, voicing, voiceRefusal,
    caption, capWord, viseme, amp,
    draft, setDraft, boxRef,
    say, putBack, sayAgain, startCall, hangUp, setLang,
    applyAction, undoAction, leaveAction,
  };
}
