/**
 * ONE CONVERSATION, HOWEVER MANY WINDOWS SHOW IT.
 *
 * Salaahkaar is reachable from two places — the full page at #/salaahkaar and
 * the round button at the bottom-right of every other screen — and they have
 * to be the SAME call: a question asked from the modal on the Till is still in
 * the transcript when the page is opened, and the server's session id (which
 * is what makes "that one" resolve to the product named two turns ago) must
 * not be thrown away by a navigation.
 *
 * So the transcript, the session id, the chosen language and the call state
 * live here, at module level, outside any component. Each VIEW owns only what
 * cannot outlive it — the microphone, the speech director, the analyser on the
 * natural voice — and reads and writes the rest through this store.
 *
 * Deliberately tiny and dependency-free: the round button is in the eager
 * bundle on every page, and it needs the presence ring and nothing else. The
 * engine that talks (`components/useSalaahkaar.ts`) is loaded on the first
 * press.
 */

import { useSyncExternalStore } from 'react';
import type { SayAnswer, SayRefusal } from './advisorapi';
import type { AskAnswer, AskRefusal } from './assistantapi';
import type { Applied, TurnRoute } from './salaahkaar';
import { DEFAULT_LANG } from './voice';

export type Presence = 'off' | 'idle' | 'listening' | 'thinking' | 'speaking';

/** What actually voiced one answer. 'natural-cached': the till already had it. */
export type VoicedBy = 'natural' | 'natural-cached' | 'browser';

/**
 * `asked` rides on every reply. If a turn fails, the sentence is still in the
 * transcript AND still sendable — on a call, with hands full, saying it again
 * into a microphone that has closed is not a recovery.
 */
export type Turn =
  | { id: number; who: 'you'; at: number; text: string; source: 'text' | 'voice' }
  | { id: number; who: 'sk'; at: number; asked: string; route: TurnRoute; state: 'asking' }
  | { id: number; who: 'sk'; at: number; asked: string; route: 'advice'; state: 'answer'; answer: SayAnswer; voiced?: VoicedBy }
  | { id: number; who: 'sk'; at: number; asked: string; route: 'advice'; state: 'refusal'; refusal: SayRefusal }
  | {
    id: number; who: 'sk'; at: number; asked: string; route: 'action'; state: 'action';
    answer: AskAnswer; voiced?: VoicedBy;
    /** Set once a person pressed and the server (or this browser, for a held
        bill line) answered. Null until then: proposed, not done. */
    applied: Applied | null;
    /** Set once the undo went through. The card stays, and says so. */
    undone: number | null;
    /** The last refusal from pressing, shown on the card. */
    refusal: { reason: string; detail?: string } | null;
    /** True while a press is in flight. */
    working: boolean;
    /** The person said LEAVE IT: the card folds to one line. */
    left: boolean;
  }
  | { id: number; who: 'sk'; at: number; asked: string; route: 'action'; state: 'refusal'; refusal: AskRefusal }
  | { id: number; who: 'sys'; at: number; text: string };

/** `Omit` over a union keeps only the shared keys; this keeps each member. */
type DistributiveOmit<T, K extends PropertyKey> = T extends unknown ? Omit<T, K> : never;
export type TurnIn = DistributiveOmit<Turn, 'id'>;

export interface CallState {
  turns: Turn[];
  /** The server's id for this call. Null before the first advisor turn. */
  sessionId: string | null;
  onCall: boolean;
  startedAt: number | null;
  lang: string;
  /** What the mounted view is doing right now, for the ring on the button. */
  presence: Presence;
  /** Whether a view is mounted at all — the button hides itself on the page. */
  views: number;
}

const LANG_KEY = 'gawaah.salaahkaar.lang.v1';

function storedLang(): string {
  try {
    const v = localStorage.getItem(LANG_KEY);
    return v && /^[a-z]{2}-[A-Z]{2}$/.test(v) ? v : DEFAULT_LANG;
  } catch {
    return DEFAULT_LANG;
  }
}

let state: CallState = {
  turns: [], sessionId: null, onCall: false, startedAt: null,
  lang: storedLang(), presence: 'off', views: 0,
};
let seq = 0;
const listeners = new Set<() => void>();

const emit = () => { for (const l of listeners) l(); };

export function getCall(): CallState { return state; }

export function subscribe(fn: () => void): () => void {
  listeners.add(fn);
  return () => { listeners.delete(fn); };
}

export function patchCall(p: Partial<CallState>): void {
  state = { ...state, ...p };
  emit();
}

export function setLang(lang: string): void {
  try { localStorage.setItem(LANG_KEY, lang); } catch { /* not worth failing over */ }
  patchCall({ lang });
}

/** Append a turn; the id is minted here, once, outside any React updater. */
export function pushTurn(t: TurnIn): number {
  const id = ++seq;
  state = { ...state, turns: [...state.turns, { ...t, id } as Turn] };
  emit();
  return id;
}

/** Replace or amend one turn by id. `f` returns the next turn, or the same
    one to leave it be. */
export function updateTurn(id: number, f: (t: Turn) => Turn): void {
  let changed = false;
  const turns = state.turns.map((t) => {
    if (t.id !== id) return t;
    const n = f(t);
    if (n !== t) changed = true;
    return n;
  });
  if (changed) { state = { ...state, turns }; emit(); }
}

/** Hang up: the transcript stays on screen, the server's memory is let go. */
export function endCall(): string | null {
  const id = state.sessionId;
  state = { ...state, sessionId: null, onCall: false, startedAt: null, presence: 'off' };
  emit();
  return id;
}

export function useCall(): CallState {
  return useSyncExternalStore(subscribe, getCall, getCall);
}

/** For tests, and for a sign-out: forget everything. */
export function resetCallForTests(): void {
  state = { turns: [], sessionId: null, onCall: false, startedAt: null, lang: DEFAULT_LANG, presence: 'off', views: 0 };
  seq = 0;
  emit();
}
