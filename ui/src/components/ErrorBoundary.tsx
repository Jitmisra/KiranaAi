import { Component, type ErrorInfo, type ReactNode } from 'react';

/**
 * A thrown error must become a refusal, never a blank page.
 *
 * `rupees()` throws by design on a non-integer, an undefined or an absurd
 * amount — that assertion is invariant 1 doing its job. But it is called inside
 * render in five files, so one bad value from a server unmounted the entire
 * application to a white screen: the precise inversion of "abstain rather than
 * guess". An abstention the operator cannot read is indistinguishable from a
 * crash, and a crash tells them nothing at all.
 *
 * The message is shown verbatim. These assertions are written to be read.
 *
 * IT MUST NOT NAME A CAUSE IT DOES NOT KNOW. This screen used to print, under
 * every error it ever caught: "This is an assertion, not a guess. A price that
 * is not a whole number of paise…" — unconditionally. An audit caught it live
 * explaining `Unable to preload CSS for /assets/Today-CwcbXBrj.css` as a money
 * assertion. On a product whose whole argument is that every refusal is named,
 * naming the WRONG one is the sharpest possible own-goal. So the cause is
 * classified from the message, and where it cannot be classified this screen
 * says so rather than reaching for the most impressive explanation available.
 */
interface State { error: Error | null }

/** What kind of failure this is, decided from the message and nothing else. */
type Kind = 'money' | 'chunk' | 'unknown';

/**
 * A money assertion carries the vocabulary `lib/money.ts` and `gawaah/money.py`
 * throw with. Nothing else on this counter talks about paise.
 */
const MONEY = /\bpaise\b|not money|rupee|sub-paisa|integer paise|amount .*(too|out of)/i;

/**
 * A code-split chunk that did not arrive. The till serves a built `dist/`, so
 * EVERY redeploy strands every open tab one click from this screen — this is
 * the common case in practice, not an exotic one. Vite, Chrome, Firefox and
 * Safari each word it differently; all four are here.
 */
const CHUNK = /dynamically imported module|Importing a module script failed|error loading dynamically imported|Unable to preload|preload (CSS|module)|Loading (CSS )?chunk|ChunkLoadError/i;

function classify(message: string): Kind {
  if (CHUNK.test(message)) return 'chunk';
  if (MONEY.test(message)) return 'money';
  return 'unknown';
}

export class ErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // Keep the stack reachable for whoever is debugging the counter.
    console.error('[gawaah] a screen refused rather than rendered:', error, info.componentStack);
  }

  render(): ReactNode {
    const { error } = this.state;
    if (!error) return this.props.children;

    const message = error.message || 'an unnamed failure';
    const kind = classify(message);

    /**
     * CLEARING THE ERROR CANNOT FIX A MISSING CHUNK. `React.lazy` caches the
     * rejected promise, so re-rendering the same route re-throws the same
     * failure — measured: pressing the old TRY THIS SCREEN AGAIN twice
     * re-threw both times, and only a full page load recovered. So the
     * primary action for a chunk failure is a reload, and for anything else
     * it is the retry that can actually work.
     */
    const chunk = kind === 'chunk';

    return (
      <div className="main">
        <div className="page-head">
          <h1>
            {chunk
              ? 'This screen could not be loaded'
              : 'This screen stopped rather than show you something wrong'}
          </h1>
          <p>
            {chunk ? (
              <>
                Part of the page failed to arrive from this counter. This usually means the
                counter was rebuilt while this tab was open, so the file this tab is asking for
                no longer exists. The rest of the counter is unaffected — the till keeps its
                bill, and nothing here has settled any money.
              </>
            ) : (
              <>
                Something the counter was asked to display did not satisfy its own checks, so it
                refused to render it. The rest of the counter is unaffected — the till keeps its
                bill, and nothing here has settled any money.
              </>
            )}
          </p>
        </div>
        <div className="card">
          <div className="card-body">
            <div className="verdict amber" role="alert">
              <h4>{message}</h4>
              <p>
                {kind === 'money' ? (
                  <>
                    This is an assertion, not a guess. A price that is not a whole number of
                    paise, or an amount outside what this till will handle, is refused at the
                    point of display rather than rounded into a bill.
                  </>
                ) : chunk ? (
                  <>
                    Reloading the page fetches the current files and should clear it. Nothing is
                    wrong with the shop’s own records — this is the page, not the counter.
                  </>
                ) : (
                  <>
                    That is the message the failure carried, verbatim. This counter will not
                    guess at a cause it cannot read from it — the line above, and the stack in
                    this browser’s console, are everything it knows.
                  </>
                )}
              </p>
            </div>
            <div className="btn-row" style={{ marginTop: 18 }}>
              {chunk ? (
                <button className="btn primary" onClick={() => location.reload()}>
                  RELOAD THIS PAGE
                </button>
              ) : (
                <button className="btn primary" onClick={() => this.setState({ error: null })}>
                  TRY THIS SCREEN AGAIN
                </button>
              )}
              <a className="btn" href="#/till" onClick={() => this.setState({ error: null })}>
                BACK TO THE TILL
              </a>
            </div>
          </div>
        </div>
      </div>
    );
  }
}
