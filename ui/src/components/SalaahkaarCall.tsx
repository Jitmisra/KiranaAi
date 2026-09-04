import { memo, useEffect, useRef, useState, type ReactNode } from 'react';
import { isModel } from '../lib/brain';
import * as adv from '../lib/advisorapi';
import * as aa from '../lib/assistantapi';
import { readOrders, readProduct, readTakings } from '../lib/assistantapi';
import { rupees } from '../lib/money';
import { productPhotoUrl } from '../lib/shopapi';
import {
  clock, describeAction, mmss, readLowRows, readTopSellers, shortName, undoPlan,
  LANGS, type Suggestion,
} from '../lib/salaahkaar';
import type { Turn } from '../lib/salaahkaar-store';
import { Empty, Pill, Refusal, Skeleton, Thinking, Verdict } from './ui';
import Presenter from './Presenter';
import type { Engine } from './useSalaahkaar';
import type { WordSpan } from '../lib/visemes';

/**
 * THE VIEWS OF THE CALL — the tile with the presenter in it, the transcript,
 * the one composer that takes text and voice. Two screens draw them: the full
 * page (`routes/Salaahkaar.tsx`) and the modal behind the round button
 * (`SalaahkaarPanel.tsx`). Same objects at two sizes, so a judge who opens the
 * modal on the Till and then the page sees one product and not two.
 *
 * Everything that DECIDES lives in `useSalaahkaar` and `lib/salaahkaar.ts`;
 * this file only draws what they hand it, and abstains the same way they do —
 * a field the server did not send is a row not drawn, never a zero.
 */

export type Size = 'full' | 'dock';

/* ------------------------------------------------------------- the tile -- */

export function Tile({ e, size, children }: { e: Engine; size: Size; children?: ReactNode }) {
  const { presence, call, elapsed } = e;
  const capRef = useRef<HTMLParagraphElement>(null);

  const stateLabel: Record<typeof presence, string> = {
    off: 'not on a call', idle: 'on the line', listening: 'listening', thinking: 'thinking', speaking: 'speaking',
  };

  // The caption follows the voice like a teleprompter: the word being said is
  // kept in the middle of its window rather than clipped below it.
  useEffect(() => {
    const el = capRef.current;
    if (!el) return;
    const now = el.querySelector<HTMLElement>('.now');
    if (!now) { el.scrollTop = 0; return; }
    const want = now.offsetTop - (el.clientHeight - now.offsetHeight) / 2;
    el.scrollTo({ top: Math.max(0, want), behavior: 'smooth' });
  }, [e.capWord, e.caption]);

  return (
    <div className={`adv-tile ${size}`} data-state={presence}>
      <div className="adv-tile-top">
        <div className="adv-tile-name">
          <b>Salaahkaar<span className="deva" lang="hi">सलाहकार</span></b>
          <span>a drawn presenter over this counter’s figures</span>
        </div>
        <span className="adv-state" aria-live="polite"><i />{stateLabel[presence]}</span>
      </div>

      {e.healthLoading ? (
        <div className="adv-tile-wait"><Skeleton h={size === 'full' ? 190 : 90} radius={12} /></div>
      ) : (
        <Presenter state={presence} viseme={e.viseme} amplitude={e.amp} className="adv-presenter" />
      )}

      {/* THE WAVE: fourteen bars that breathe with the voice's measured level
          (`--amp`, set on this tile once a frame by the presenter). Drawn only
          while speaking or listening — a waveform on a silent call is a
          decoration pretending to be a measurement. */}
      {(presence === 'speaking' || presence === 'listening') && (
        <div className={`adv-wave ${presence}`} aria-hidden="true">
          {Array.from({ length: 14 }, (_, i) => <i key={i} style={{ '--k': i } as React.CSSProperties} />)}
        </div>
      )}

      <span className="adv-ornament" aria-hidden="true" />
      <span className="adv-synth">
        <SynthGlyph />
        synthetic presenter · not a person
      </span>

      {call.onCall && <span className="adv-timer tnum">{mmss(elapsed)}</span>}

      {e.interim ? (
        <p className="adv-cap heard"><b>heard</b>{e.interim}…</p>
      ) : e.caption ? (
        <p className="adv-cap said" ref={capRef}>
          <b>{e.voicing ? 'fetching the voice' : 'saying'}</b>
          <Caption text={e.caption.text} spans={e.caption.spans} at={e.capWord} />
        </p>
      ) : null}
      {children}
    </div>
  );
}

function Caption({ text, spans, at }: { text: string; spans: ReadonlyArray<WordSpan>; at: number }) {
  const tail = spans.length > 0 ? (spans[spans.length - 1]?.end ?? 0) : 0;
  return (
    <>
      {spans.map((s, i) => {
        const from = i === 0 ? 0 : (spans[i - 1]?.end ?? 0);
        return (
          <span key={s.start}>
            {text.slice(from, s.start)}
            <span className={i === at ? 'now' : i < at ? 'was' : ''}>{text.slice(s.start, s.end)}</span>
          </span>
        );
      })}
      {text.slice(tail)}
    </>
  );
}

export function SynthGlyph() {
  return (
    <svg width="12" height="12" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
      <rect x="1.5" y="2.5" width="13" height="11" rx="2.5" fill="none" stroke="currentColor" strokeWidth="1.4" />
      <circle cx="8" cy="7" r="2" fill="currentColor" />
      <path d="M4.4 12c.7-1.7 2-2.5 3.6-2.5s2.9.8 3.6 2.5" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
    </svg>
  );
}

export function MicGlyph({ size = 22 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true">
      <rect x="9" y="3" width="6" height="11" rx="3" fill="currentColor" />
      <path d="M5.5 11.5a6.5 6.5 0 0 0 13 0M12 18v3" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}

/* --------------------------------------------------------- the composer -- */

/**
 * ONE BAR: the language, the words, the microphone, the send. Text and voice
 * are the same input — a spoken sentence is asked the moment it settles, a
 * typed one when Enter is pressed — and both go through the same router.
 */
export function Composer({ e, size }: { e: Engine; size: Size }) {
  const { draft, setDraft, boxRef } = e;
  const disabled = e.healthLoading || !!e.healthRefusal;

  useEffect(() => {
    const el = boxRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, size === 'full' ? 120 : 96)}px`;
  }, [draft, boxRef, size]);

  return (
    <form
      className={`sk-composer ${size}`}
      onSubmit={(ev) => { ev.preventDefault(); void e.say(draft, 'text'); }}
    >
      <LangPicker value={e.call.lang} onChange={e.setLang} />
      <div className="sk-box">
        <textarea
          ref={boxRef}
          rows={1}
          value={draft}
          maxLength={adv.MAX_TEXT}
          // Short on purpose: a placeholder that wrapped to four lines at 390
          // made the bar a box. The example sentences are the chips above it.
          placeholder={e.listening ? 'listening — or type it here' : 'ask, or say what to do'}
          onChange={(ev) => setDraft(ev.target.value)}
          onKeyDown={(ev) => {
            if (ev.key === 'Enter' && !ev.shiftKey) { ev.preventDefault(); void e.say(draft, 'text'); }
          }}
          aria-label="Ask Salaahkaar, or tell it what to do"
          disabled={disabled}
        />
      </div>
      <button
        type="button"
        className={`sk-mic${e.listening ? ' on' : ''}`}
        onClick={e.toggleMic}
        disabled={!e.micOk || disabled}
        title={!e.micOk ? e.micReason : e.listening ? 'Stop listening' : e.call.onCall ? 'Listen' : 'Start the call'}
        aria-pressed={e.listening}
        aria-label={e.listening ? 'Stop listening' : e.call.onCall ? 'Listen' : 'Start the call'}
      >
        <MicGlyph size={size === 'full' ? 22 : 20} />
      </button>
      <button className="btn primary sk-send" type="submit" disabled={e.busy || !draft.trim() || disabled} aria-label="Send">
        {e.busy ? 'ASKING…' : 'ASK'}
      </button>
    </form>
  );
}

/** The three languages the voice speaks and the microphone hears, in one
    control. It changes the recogniser, the spoken answer's language and the
    voice picked for it together — one choice, not three. */
export function LangPicker({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <div className="sk-lang" role="group" aria-label="Language">
      {LANGS.map((l) => (
        <button
          key={l.tag}
          type="button"
          aria-pressed={value === l.tag}
          title={`${l.tag} — ${l.hears}`}
          lang={l.tag.slice(0, 2)}
          onClick={() => onChange(l.tag)}
        >
          {l.short}
        </button>
      ))}
    </div>
  );
}

/* ----------------------------------------------------------- the thread -- */

export function Thread({ e, size, children }: { e: Engine; size: Size; children?: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  const { turns } = e.call;

  // Keep the newest turn in view by scrolling THE THREAD, never the page —
  // `scrollIntoView` would drag the presenter off the top of a phone.
  useEffect(() => {
    const el = ref.current;
    if (!el || turns.length === 0) return;
    el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' });
  }, [turns]);

  return (
    <div ref={ref} className={`adv-thread ${size}${turns.length === 0 ? ' empty' : ''}`} role="log" aria-live="polite">
      {turns.length === 0 && children}
      <Turns
        turns={turns}
        reasons={e.reasons}
        model={e.health?.model ?? null}
        askModel={e.askHealth?.model ?? null}
        askKey={e.askHealth?.key_present === true}
        onSayAgain={e.canSpeak ? e.sayAgain : null}
        busy={e.busy}
        onAgain={(s) => void e.say(s, 'text')}
        onEdit={e.putBack}
        onApply={(id) => void e.applyAction(id)}
        onUndo={(id) => void e.undoAction(id)}
        onLeave={e.leaveAction}
      />
    </div>
  );
}

/**
 * Memoised, and that is not a micro-optimisation: the caption's word index
 * changes about three times a second while the presenter speaks; without this
 * every one of those re-renders every fact card in the thread on a phone that
 * is also running a face at 60 Hz.
 */
const Turns = memo(function Turns({
  turns, reasons, model, askModel, askKey, onSayAgain, busy, onAgain, onEdit, onApply, onUndo, onLeave,
}: {
  turns: ReadonlyArray<Turn>;
  reasons: boolean;
  model: string | null;
  askModel: string | null;
  askKey: boolean;
  onSayAgain: ((text: string) => void) | null;
  busy: boolean;
  onAgain: (text: string) => void;
  onEdit: (text: string) => void;
  onApply: (id: number) => void;
  onUndo: (id: number) => void;
  onLeave: (id: number) => void;
}) {
  return (
    <>
      {turns.map((turn) => (
        <div key={turn.id} className={`adv-turn ${turn.who}`}>
          {turn.who === 'you' && (
            <div className="adv-said">
              <p>{turn.text}</p>
              <span className="adv-stamp">{turn.source === 'voice' ? 'spoken' : 'typed'} · {clock(turn.at)}</span>
            </div>
          )}
          {turn.who === 'sys' && <p className="adv-sys">{turn.text}</p>}
          {turn.who === 'sk' && (
            <>
              <span className="adv-mark" aria-hidden="true" />
              <div className="adv-reply">
                {turn.state === 'asking' && (
                  turn.route === 'action' ? (
                    <Thinking
                      title={askKey ? `Asking ${askModel ?? 'the model'} what to do` : 'Reading the instruction here'}
                      steps={askKey ? [
                        { label: 'the model reads the sentence and names one tool' },
                        { label: 'that tool runs here, and writes a PROPOSAL — nothing is done yet' },
                        { label: 'anything for the bill comes back priced from this shop’s catalogue, not by the model' },
                      ] : [
                        { label: 'this counter’s own Hinglish parser reads the sentence' },
                        { label: 'one tool runs here, and writes a PROPOSAL — nothing is done yet' },
                      ]}
                      foot="Nothing changes until you press. The sentence is what leaves; no price, sku id or takings goes with it."
                    />
                  ) : (
                    <Thinking
                      title={reasons ? `Asking ${model ?? 'the model'}` : 'Reading this counter’s files'}
                      steps={reasons ? [
                        { label: 'the model reads the sentence and names one tool' },
                        { label: 'that tool runs here, on this machine, over the shop’s own files' },
                        { label: 'the model phrases the answer from that result, and every figure in it is checked' },
                      ] : [
                        { label: 'this counter’s own parser reads the sentence' },
                        { label: 'one tool runs here, over the shop’s own files' },
                      ]}
                      foot={reasons
                        ? 'No price, sku id or customer leaves this machine. The exact fields that did are listed on the answer.'
                        : 'Nothing leaves this machine.'}
                    />
                  )
                )}

                {turn.state === 'refusal' && (
                  <>
                    <div className="adv-refusal" role="status">
                      <header>
                        <span className="mono">{turn.refusal.reason}</span>
                        <div style={{ flex: 1 }} />
                        <Pill tone="amb">NOTHING WAS DONE</Pill>
                      </header>
                      {turn.refusal.detail && <p>{turn.refusal.detail}</p>}
                      {refusalHint(turn.refusal.reason, turn.route) && (
                        <p className="adv-refusal-hint">{refusalHint(turn.refusal.reason, turn.route)}</p>
                      )}
                      <Again busy={busy} onAgain={() => onAgain(turn.asked)} onEdit={() => onEdit(turn.asked)} />
                    </div>
                    <div className="adv-foot">
                      <RouteTag route={turn.route} />
                      {turn.refusal.brain === undefined ? (
                        <span className="adv-tag none">nothing answered</span>
                      ) : (
                        <span className={`adv-tag ${turn.refusal.brain}`}>
                          {isModel(turn.refusal.brain) ? `${turn.refusal.brain} · ${turn.refusal.model ?? 'model'}` : 'local · this machine'}
                        </span>
                      )}
                      <span className="adv-stamp">{clock(turn.at)}</span>
                    </div>
                  </>
                )}

                {turn.state === 'answer' && (
                  <AdviceBody
                    a={turn.answer}
                    at={turn.at}
                    voiced={turn.voiced}
                    onSayAgain={onSayAgain}
                    busy={busy}
                    onAgain={() => onAgain(turn.asked)}
                    onEdit={() => onEdit(turn.asked)}
                  />
                )}

                {turn.state === 'action' && (
                  <ActionBody
                    turn={turn}
                    onSayAgain={onSayAgain}
                    busy={busy}
                    onAgain={() => onAgain(turn.asked)}
                    onEdit={() => onEdit(turn.asked)}
                    onApply={() => onApply(turn.id)}
                    onUndo={() => onUndo(turn.id)}
                    onLeave={() => onLeave(turn.id)}
                  />
                )}
              </div>
            </>
          )}
        </div>
      ))}
    </>
  );
});

/** Which door the sentence went through. Both are the machine's own blue. */
function RouteTag({ route }: { route: 'action' | 'advice' }) {
  return route === 'action'
    ? <span className="adv-tag route" title="An instruction: sent to the assistant, which proposes and never acts on its own">ASSISTANT · PROPOSES</span>
    : <span className="adv-tag route" title="A question: sent to the advisor on this call, which reasons and never acts">ADVISOR · ON THE CALL</span>;
}

function Again({ busy, onAgain, onEdit }: { busy: boolean; onAgain: () => void; onEdit: () => void }) {
  return (
    <div className="btn-row adv-again-row">
      <button type="button" className="btn sm primary" disabled={busy} onClick={onAgain}>SEND IT AGAIN</button>
      <button type="button" className="btn sm ghost" onClick={onEdit}>PUT IT BACK IN THE BOX</button>
    </div>
  );
}

function refusalHint(reason: string, route: 'action' | 'advice'): string | undefined {
  if (reason === 'the counter could not reach its own server') {
    return 'The line to the till dropped mid-sentence. Nothing was asked and nothing was billed. Check the till process is still running, then send it again.';
  }
  if (reason === 'advisor_not_mounted' || reason === 'assistant_not_mounted') {
    return 'Nothing else on the till is affected — the camera, the bill and the storefront do not go through here.';
  }
  if (reason === 'no_such_call') {
    return 'The call this sentence belonged to had already been forgotten by the server. Sending it again starts a fresh one.';
  }
  if (reason === 'sentence_not_understood' && route === 'action') {
    return 'It read this as an instruction and could not make one out. Say the count and the product — “do Maggi bill me daal do” — or ask it as a question.';
  }
  if (reason.startsWith('the server refused with HTTP') || reason.startsWith('server replied ')) {
    return 'The till answered, but not with an answer. Your sentence is still above and can be sent again as it is.';
  }
  return undefined;
}

function VoiceTag({ voiced }: { voiced?: 'natural' | 'natural-cached' | 'browser' }) {
  if (!voiced) return null;
  return (
    <span
      className={`adv-tag voice ${voiced}`}
      title={voiced === 'browser'
        ? 'Read by this browser’s own voice. Nothing left the machine to say it.'
        : voiced === 'natural-cached'
          ? 'Read by the natural voice, from the till’s own copy. Nothing left the machine for this one.'
          : 'Read by the natural voice. This sentence left the machine, once, to be voiced.'}
    >
      {voiced === 'browser' ? 'VOICE · THIS BROWSER' : voiced === 'natural-cached' ? 'VOICE · KEPT ON TILL' : 'VOICE · FETCHED ONCE'}
    </span>
  );
}

/* ------------------------------------------------------------- advice -- */

function AdviceBody({ a, at, voiced, onSayAgain, busy, onAgain, onEdit }: {
  a: adv.SayAnswer;
  at: number;
  voiced?: 'natural' | 'natural-cached' | 'browser';
  onSayAgain: ((text: string) => void) | null;
  busy: boolean;
  onAgain: () => void;
  onEdit: () => void;
}) {
  const phrased = a.advice !== null && a.advice !== a.answer;
  return (
    <>
      {a.spoken.trim() === '' ? (
        <Empty icon={false} title="Nothing came back to say" action={<Again busy={busy} onAgain={onAgain} onEdit={onEdit} />}>
          The advisor answered with no sentence in it, so there was nothing to read aloud. Nothing was
          billed and nothing was changed — ask it again, or put your words back in the box.
        </Empty>
      ) : (
        <p className="adv-spoken">{a.spoken}</p>
      )}
      {phrased && (
        <div className="adv-counter-said">
          <b>what this counter derived</b>
          {a.answer}
        </div>
      )}
      {a.context.carried_product && (
        <p className="adv-why">Read “that one” as <b>{a.context.carried_product}</b>, from earlier on this call.</p>
      )}
      <Facts tool={a.tool} data={a.data} />
      {!a.grounded && (
        <p className="adv-why">
          <b>General advice, not from your shop’s figures.</b> No tool ran for this sentence; the model
          answered from what you said, and was allowed to only because it quoted no number.
        </p>
      )}
      {a.grok_error && (
        <p className="adv-why">
          <b>{a.grok_error.reason === 'model_quoted_a_figure_it_was_not_given'
            ? 'The model’s advice was dropped.'
            : 'The model was not used for this turn.'}</b>{' '}
          {a.grok_error.detail} <span className="mono">{a.grok_error.reason}</span>
        </p>
      )}
      {!a.grok_error && a.cannot_reason_because && <p className="adv-why">{a.cannot_reason_because}</p>}
      <div className="adv-foot">
        <RouteTag route="advice" />
        <span className={`adv-tag ${a.brain}`}>
          {isModel(a.brain) ? `${a.brain} · ${a.model ?? 'model'}` : 'local · this machine'}
        </span>
        {a.tool && <span className="adv-tag">{a.tool}</span>}
        {a.reasoned && a.grounded && <span className="adv-tag reasoned">REASONED, FIGURES CHECKED</span>}
        {!a.reasoned && a.grounded && <span className="adv-tag">FIGURES ONLY</span>}
        {!a.grounded && <span className="adv-tag general">GENERAL, NO FIGURES</span>}
        <VoiceTag voiced={voiced} />
        {onSayAgain && (
          <button type="button" className="adv-again" onClick={() => onSayAgain(a.spoken)}
                  title="Read this answer out again. Nothing is re-asked and nothing leaves this machine.">
            SAY AGAIN
          </button>
        )}
        <span className="adv-stamp">{clock(at)}</span>
      </div>
      {a.left_the_machine ? (
        <details className="adv-left">
          <summary>
            Left this machine for this turn: {a.left_the_machine.sentences} sentence{a.left_the_machine.sentences === 1 ? '' : 's'}
            {a.left_the_machine.answers > 0 ? `, ${a.left_the_machine.answers} earlier answer${a.left_the_machine.answers === 1 ? '' : 's'}` : ''}
            {a.left_the_machine.fields.length > 0 ? `, ${a.left_the_machine.fields.length} fields of the result` : ', no figures'}
          </summary>
          {a.left_the_machine.fields.length > 0 ? (
            <div className="adv-left-fields">
              {a.left_the_machine.fields.map((f) => <span key={f}>{f}</span>)}
            </div>
          ) : (
            <p className="adv-note">Only the sentences went. No figure from this shop was in the request.</p>
          )}
        </details>
      ) : (
        <p className="adv-left-none">Nothing left this machine for this turn.</p>
      )}
    </>
  );
}

/* ------------------------------------------------------------- action -- */

/**
 * AN ACTION TURN: the assistant's sentence, the facts behind it, and — when
 * it proposed a change — THE CARD. The card is the whole point of routing an
 * instruction here: what would change, in this shop's own prices and names;
 * a button a person has to press; the line that says `did:` only after the
 * server (or, for a bill line, this browser) answered; and an UNDO wherever
 * the backend has one. Never silent, never green.
 */
function ActionBody({ turn, onSayAgain, busy, onAgain, onEdit, onApply, onUndo, onLeave }: {
  turn: Extract<Turn, { state: 'action' }>;
  onSayAgain: ((text: string) => void) | null;
  busy: boolean;
  onAgain: () => void;
  onEdit: () => void;
  onApply: () => void;
  onUndo: () => void;
  onLeave: () => void;
}) {
  const a = turn.answer;
  const p = a.proposal;
  const state = turn.undone ? 'undone' : turn.applied ? 'applied' : 'proposed';
  return (
    <>
      {a.answer.trim() === '' ? (
        <Empty icon={false} title="The answer came back empty" action={<Again busy={busy} onAgain={onAgain} onEdit={onEdit} />}>
          The counter replied with no sentence in it. Nothing was billed and nothing was changed.
        </Empty>
      ) : (
        <p className="adv-spoken">{a.answer}</p>
      )}
      <Facts tool={a.tool} data={a.data} />
      {p && !turn.left && (
        <ActionCard turn={turn} p={p} state={state} onApply={onApply} onUndo={onUndo} onLeave={onLeave} />
      )}
      {p && turn.left && (
        <p className="sk-left">
          Left off. The proposal is still on the counter under <span className="mono">{p.proposal_id}</span>; nothing was done.
        </p>
      )}
      <div className="adv-foot">
        <RouteTag route="action" />
        <span className={`adv-tag ${a.brain}`}>
          {isModel(a.brain) ? `${a.brain} · ${a.model ?? 'model'}` : 'local · this machine'}
        </span>
        {a.tool && <span className="adv-tag">{a.tool}</span>}
        {p && (
          <span className={`adv-tag did ${state}`}>{describeAction(p, state)}</span>
        )}
        <VoiceTag voiced={turn.voiced} />
        {onSayAgain && a.answer.trim() !== '' && (
          <button type="button" className="adv-again" onClick={() => onSayAgain(a.answer)}
                  title="Read this answer out again. Nothing is re-asked and nothing leaves this machine.">
            SAY AGAIN
          </button>
        )}
        <span className="adv-stamp">{clock(turn.at)}</span>
      </div>
      {a.grok_error && (
        <p className="adv-why">
          <b>The model was not reached.</b> {a.grok_error.detail} <span className="mono">{a.grok_error.reason}</span>
        </p>
      )}
    </>
  );
}

function ActionCard({ turn, p, state, onApply, onUndo, onLeave }: {
  turn: Extract<Turn, { state: 'action' }>;
  p: aa.Proposal;
  state: 'proposed' | 'applied' | 'undone';
  onApply: () => void;
  onUndo: () => void;
  onLeave: () => void;
}) {
  const kind = p.kind ?? 'bill';
  const plan = turn.applied ? undoPlan(p, turn.applied) : null;
  const eyebrow = kind === 'bill' ? 'Proposed for the bill' : kind === 'stock_movement' ? 'Proposed stock movement' : 'Proposed expense';
  const press = kind === 'bill'
    ? `HOLD ${p.lines.length === 1 ? 'THIS LINE' : `THESE ${p.lines.length} LINES`} FOR THE TILL`
    : kind === 'stock_movement' ? 'RECORD THE MOVEMENT' : 'RECORD THE EXPENSE';
  return (
    <div className={`sk-action ${state}`} role="group" aria-label={eyebrow}>
      <header>
        <span className="eyebrow">{eyebrow}</span>
        <div style={{ flex: 1 }} />
        {state === 'proposed' && <Pill tone="off">NOT DONE</Pill>}
        {state === 'applied' && <Pill tone="code" dot>{kind === 'bill' ? 'HELD FOR THE TILL' : 'RECORDED'}</Pill>}
        {state === 'undone' && <Pill tone="off">UNDONE</Pill>}
      </header>

      {kind === 'bill' && (
        <>
          <div className="sk-lines">
            {p.lines.map((l) => (
              <div className="sk-line" key={l.sku_id}>
                <Thumb sku={l.sku_id} name={l.name} />
                <span className="nm">{shortName(l.name)}<span className="mono">{l.sku_id}</span></span>
                <span className="qty tnum">×{l.qty}</span>
                <span className="each tnum">
                  {l.marked_paise !== undefined && l.marked_paise > l.unit_paise && <s>{rupees(l.marked_paise)}</s>}
                  {rupees(l.unit_paise)} each
                </span>
                <span className="amt tnum">{rupees(l.line_paise)}</span>
              </div>
            ))}
          </div>
          <div className="sk-total">
            <span className="lbl">Would add</span>
            <span className="amt tnum">{rupees(p.total_paise)}</span>
          </div>
        </>
      )}

      {kind === 'stock_movement' && p.movement && (
        <div className="sk-change">
          <Thumb sku={p.movement.sku_id} name={p.movement.name} />
          <div className="sk-change-main">
            <b>{shortName(p.movement.name)}</b>
            <span>{p.movement.units} {p.movement.direction === 'in' ? 'in' : 'out'} · {p.movement.reason_label}</span>
            {p.movement.note && <span className="mono">{p.movement.note}</span>}
          </div>
          <span className={`sk-delta tnum ${p.movement.direction}`}>
            {p.movement.direction === 'in' ? '+' : '−'}{p.movement.units}
          </span>
        </div>
      )}

      {kind === 'expense' && p.expense && (
        <div className="sk-change">
          <span className="sk-thumb glyph" aria-hidden="true">₹</span>
          <div className="sk-change-main">
            <b>{p.expense.category_label}</b>
            <span>{p.expense.note || 'no note'} · paid in {p.expense.paid_with}</span>
          </div>
          <span className="sk-delta tnum out">{rupees(p.expense.amount_paise)}</span>
        </div>
      )}

      {p.caution && <Verdict tone="amber" title="Read this before you press">{p.caution}</Verdict>}

      {turn.refusal && (
        <Refusal reason={turn.refusal.reason} detail={turn.refusal.detail}
                 hint="Nothing was changed. The proposal is still here — press again, or leave it." />
      )}

      {state === 'proposed' && (
        <div className="btn-row sk-act">
          <button type="button" className="btn primary" disabled={turn.working} onClick={onApply}>
            {turn.working ? 'PRESSING…' : press}
          </button>
          <button type="button" className="btn ghost" disabled={turn.working} onClick={onLeave}>LEAVE IT</button>
        </div>
      )}

      {state === 'applied' && turn.applied && (
        <div className="sk-done">
          <p>
            <b>{describeAction(p, 'applied')}</b>
            {' · '}{clock(turn.applied.at)}.{' '}
            {kind === 'bill'
              ? 'Held in this browser for the till, which is the only thing here that writes a bill line. Nothing has been charged.'
              : kind === 'stock_movement'
                ? <>On the stock log as your word, not something the counter saw{turn.applied.on_hand_units !== undefined && turn.applied.on_hand_units !== null ? <> — the shelf now reads <span className="tnum">{turn.applied.on_hand_units}</span></> : null}.</>
                : 'In the day book. Nothing was charged and no payment was made by this counter.'}
          </p>
          <div className="btn-row">
            {kind === 'bill' && <a className="btn sm primary" href="#/till">OPEN THE TILL</a>}
            {kind === 'stock_movement' && <a className="btn sm" href="#/stock">SEE THE STOCK LOG</a>}
            {kind === 'expense' && <a className="btn sm" href="#/expenses">SEE THE DAY BOOK</a>}
            {plan ? (
              <button type="button" className="btn sm ghost sk-undo" disabled={turn.working} onClick={onUndo} title={plan.says}>
                {turn.working ? 'UNDOING…' : 'UNDO'}
              </button>
            ) : (
              <span className="hint">No undo: the server answered without the id an undo needs.</span>
            )}
          </div>
          {plan && <p className="hint">{plan.says}</p>}
        </div>
      )}

      {state === 'undone' && turn.undone && (
        <p className="sk-undone">
          <b>{describeAction(p, 'undone')}</b> · {clock(turn.undone)}
        </p>
      )}

      <p className="sk-foot">
        <span className="mono">{p.proposal_id}</span>
        {p.audited === true ? ' · written to the assistant’s own hash-chained log' : p.audited === false ? ' · not written to the log' : ''}
        {kind === 'bill' ? ' · priced from this shop’s catalogue, not by the model' : ' · accepted at the server’s own endpoint, with its own body'}
      </p>
    </div>
  );
}

/* -------------------------------------------------------------- thumbs -- */

/**
 * A product's own photograph, from the storefront's same-origin route, with
 * its initial standing in when there is none. `onError` hides the image, so a
 * product taught without a photo shows a letter and never a broken icon.
 */
export function Thumb({ sku, name }: { sku: string; name: string }) {
  const [broken, setBroken] = useState(false);
  const initial = (shortName(name).match(/\p{L}/u)?.[0] ?? '·').toUpperCase();
  return (
    <span className={`sk-thumb${broken ? ' glyph' : ''}`} aria-hidden="true">
      {broken ? initial : <img src={productPhotoUrl(sku)} alt="" loading="lazy" onError={() => setBroken(true)} />}
    </span>
  );
}

/* --------------------------------------------------------------- facts -- */

const rec = (v: unknown): Record<string, unknown> =>
  v !== null && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : {};

/**
 * The numbers behind the sentence, for either brain's tools. Every block
 * abstains: a field the server did not send is a row not drawn, never a zero.
 * The settled figure is the one thing allowed to be green; a partial margin is
 * amber because the counter abstained on part of it.
 */
export function Facts({ tool, data }: { tool: string | null; data: unknown }) {
  if (!tool || !data) return null;

  if (tool === 'price_of' || tool === 'find_product') {
    const p = readProduct(data);
    if (!p) return null;
    return (
      <div className="adv-facts">
        <div className="adv-product">
          <Thumb sku={p.sku_id} name={p.name} />
          <div className="adv-product-main">
            <b>{shortName(p.name)}</b>
            <span className="mono">{p.sku_id}</span>
          </div>
          <div className="adv-price tnum">
            {p.marked_paise !== null && p.price_paise !== null && p.marked_paise > p.price_paise && <s>{rupees(p.marked_paise)}</s>}
            {p.price_paise !== null ? rupees(p.price_paise) : '—'}
          </div>
        </div>
        {p.taught_with && <p className="adv-note">Taught by {p.taught_with}.</p>}
      </div>
    );
  }

  if (tool === 'todays_takings' || tool === 'day_close_preview') {
    const t = readTakings(data) ?? readTakings(rec(data).derived);
    const top = readTopSellers(data);
    if (!t && top.length === 0) return null;
    return (
      <div className="adv-facts">
        {t && (
          <div className="adv-stats">
            <Stat label="bills" value={String(t.bills ?? '—')} sub={t.date ?? ''} />
            <Stat label="billed" value={t.revenue_paise !== null ? rupees(t.revenue_paise) : '—'} />
            <Stat label="settled" value={t.settled_paise !== null ? rupees(t.settled_paise) : '—'} sub={t.settled_count !== null ? `${t.settled_count} bills` : ''} green={(t.settled_paise ?? 0) > 0} />
            <Stat label="awaiting" value={t.awaiting_paise !== null ? rupees(t.awaiting_paise) : '—'} sub={t.awaiting_count !== null ? `${t.awaiting_count} links` : ''} />
          </div>
        )}
        <SellerBars rows={top} />
        {t?.chain_break && (
          <Verdict tone="amber" title="The audit chain stops before the end">
            It stops verifying at line {t.chain_break.lines_checked ?? '?'}. Anything recorded after that
            break is missing from these figures, and nothing has been adjusted to cover the gap.
          </Verdict>
        )}
      </div>
    );
  }

  if (tool === 'todays_margin' || tool === 'margin_today') {
    const m = adv.readMargin(data);
    if (!m) return null;
    const most = Math.max(1, ...m.items.map((r) => Math.abs(r.margin_paise ?? 0)), ...m.items.map((r) => r.revenue_paise ?? 0));
    return (
      <div className="adv-facts">
        <div className="adv-stats">
          <Stat label="bills" value={String(m.bills)} sub={m.date ?? ''} />
          <Stat label="took" value={m.revenue_paise !== null ? rupees(m.revenue_paise) : '—'} />
          <Stat
            label="margin"
            value={m.margin_paise !== null && (m.covered_units ?? 0) > 0 ? rupees(m.margin_paise) : '—'}
            sub={m.margin_pct_of_price !== null && (m.covered_units ?? 0) > 0 ? `${m.margin_pct_of_price}% of price` : 'no cost recorded'}
            unknown={(m.covered_units ?? 0) === 0}
          />
          <Stat
            label="not known"
            value={m.uncovered_revenue_paise !== null && (m.uncovered_units ?? 0) > 0 ? rupees(m.uncovered_revenue_paise) : '—'}
            sub={(m.uncovered_units ?? 0) > 0 ? `${m.uncovered_units} units, no cost on file` : ''}
            unknown
          />
        </div>
        {m.items.length > 0 && (
          <div className="sk-margin-rows">
            {m.items.map((row) => (
              <div className="sk-margin-row" key={row.sku_id}>
                <Thumb sku={row.sku_id} name={row.name} />
                <span className="nm">{shortName(row.name)}<span className="st">{row.units} sold · took {row.revenue_paise !== null ? rupees(row.revenue_paise) : '—'}</span></span>
                <span className="bar" aria-hidden="true">
                  <i className="took" style={{ width: `${Math.round(((row.revenue_paise ?? 0) / most) * 100)}%` }} />
                  {row.cost_known && row.margin_paise !== null && (
                    <i className={row.below_cost ? 'below' : 'kept'} style={{ width: `${Math.round((Math.abs(row.margin_paise) / most) * 100)}%` }} />
                  )}
                </span>
                <span className={`tnum${row.cost_known ? '' : ' unknown'}`}>
                  {row.cost_known && row.margin_paise !== null
                    ? <>{rupees(row.margin_paise)}{row.margin_pct_of_price !== null && <small> {row.margin_pct_of_price}%</small>}</>
                    : 'cost not known'}
                </span>
              </div>
            ))}
          </div>
        )}
        {m.margin_is_partial && (
          <Verdict tone="amber" title="Part of today’s margin is not known">
            Some of what sold has no purchase recorded, so its margin is not known — it is not zero, and
            it is not in the figure above. Record those purchases and the figure completes.
          </Verdict>
        )}
        {m.chain_break && (
          <Verdict tone="amber" title="The audit chain stops before the end">
            It stops verifying at line {m.chain_break.lines_checked ?? '?'}; anything after that is missing here.
          </Verdict>
        )}
      </div>
    );
  }

  if (tool === 'list_pending_orders') {
    const o = readOrders(data);
    if (!o || o.orders.length === 0) return null;
    return (
      <div className="adv-facts">
        <div className="adv-rows">
          {o.orders.map((row) => (
            <div className="adv-row" key={row.order_id}>
              <span className="nm">{row.name ?? 'no name on the order'}<span className="mono">{row.order_id}</span></span>
              <span className="st">{row.status.replace(/_/g, ' ')}</span>
              <span className="tnum">{row.total_paise !== null ? rupees(row.total_paise) : '—'}</span>
            </div>
          ))}
        </div>
        {o.listed < o.pending && (
          <p className="adv-note">{o.pending - o.listed} more not listed. The Orders screen has all of them.</p>
        )}
      </div>
    );
  }

  if (tool === 'low_stock' || tool === 'reorder_list') {
    const s = readLowRows(data);
    if (!s) return null;
    return (
      <div className="adv-facts">
        {s.rows.length > 0 ? (
          <div className="sk-low">
            {s.rows.map((row) => (
              <div className="sk-low-row" key={row.sku_id}>
                <Thumb sku={row.sku_id} name={row.name} />
                <span className="nm">
                  {shortName(row.name)}
                  <span className="st">{row.billed_since_count !== null ? `${row.billed_since_count} billed since the count` : row.sku_id}</span>
                </span>
                <span className={`sk-left-n tnum${row.left === 0 ? ' none' : ''}`}>
                  {row.left === null ? 'no count' : row.left === 0 ? 'none left' : `${row.left} left`}
                </span>
              </div>
            ))}
          </div>
        ) : (
          <p className="adv-note">Nothing is at its reorder level.</p>
        )}
        {(s.uncounted ?? 0) > 0 && (
          <p className="adv-note">
            {s.uncounted} products have never been counted. This counter has no stock sensor, so it says
            nothing about them rather than calling them zero.
          </p>
        )}
        {(s.without_level ?? 0) > 0 && (
          <p className="adv-note">{s.without_level} products have no reorder level set, so “low” has no meaning for them yet.</p>
        )}
      </div>
    );
  }

  // Any other tool that happens to carry a seller table gets the bars.
  const top = readTopSellers(data);
  if (top.length > 0) {
    return <div className="adv-facts"><SellerBars rows={top} /></div>;
  }
  return null;
}

/** Top sellers as bars: units decide the length, the revenue rides beside. */
function SellerBars({ rows }: { rows: ReturnType<typeof readTopSellers> }) {
  if (rows.length === 0) return null;
  const most = Math.max(1, ...rows.map((r) => r.units));
  return (
    <div className="sk-sellers">
      <span className="sk-sellers-h">what sold, by units</span>
      {rows.map((r) => (
        <div className="sk-seller" key={r.sku_id}>
          <Thumb sku={r.sku_id} name={r.name} />
          <span className="nm">{shortName(r.name)}</span>
          <span className="bar" aria-hidden="true"><i style={{ width: `${Math.round((r.units / most) * 100)}%` }} /></span>
          <span className="tnum">{r.units}{r.revenue_paise !== null && <small> · {rupees(r.revenue_paise)}</small>}</span>
        </div>
      ))}
    </div>
  );
}

export function Stat({ label, value, sub, green, unknown }: {
  label: string; value: string; sub?: string; green?: boolean; unknown?: boolean;
}) {
  return (
    <div className={`adv-stat${green ? ' green' : ''}${unknown ? ' unknown' : ''}`}>
      <span className="l">{label}</span>
      <span className="v tnum">{value}</span>
      {sub && <span className="s">{sub}</span>}
    </div>
  );
}

/* --------------------------------------------------------------- chips -- */

export function Chips({ chips, loading, busy, small, onPick }: {
  chips: Suggestion[]; loading: boolean; busy: boolean; small?: boolean; onPick: (say: string) => void;
}) {
  if (loading) {
    return (
      <div className="adv-chips" role="status" aria-label="Reading this shop’s shelf">
        {[168, 186, 158, 172, 150].map((w, i) => <Skeleton key={i} w={w} h={small ? 26 : 30} radius={999} />)}
      </div>
    );
  }
  return (
    <div className="adv-chips">
      {chips.map((s) => (
        <button key={s.say} type="button" className={`adv-chip${small ? ' sm' : ''}${s.route === 'action' ? ' does' : ''}`}
                disabled={busy} onClick={() => onPick(s.say)} title={s.route === 'action' ? `${s.what} — it proposes, you press` : s.what}>
          {small ? s.what : s.say}
        </button>
      ))}
    </div>
  );
}
