import { useCallback, useEffect, useId, useRef, useState } from 'react';
import type { FormEvent, ReactNode } from 'react';
import * as auth from '../lib/authapi';
import { shopNameplate, shopProfile, type ShopProfileDoc } from '../lib/adminapi';
import { BOUNCED_FROM } from '../App';
import { useT } from '../lib/i18n';
import { Card, Pill, Segmented, Verdict, Refusal, Skeleton, SkeletonText } from '../components/ui';
import '../styles/auth.css';

/**
 * SIGN IN, SIGN UP, AND THE ACCOUNT MENU.
 *
 * The screen is one calm centred card with the shop's own name above it, and
 * underneath it the thing this screen exists to say out loud.
 *
 * THE HONEST STATE IS THE FEATURE, AND THERE ARE NOW TWO OF THEM.
 *
 * `GAWAAH_REQUIRE_AUTH` unset is still the default: accounts and sessions work
 * and NOTHING IS BEING CHECKED — anyone on the shop's wifi can open this
 * counter, fill a bill and take a payment. A sign-in page that drew a padlock
 * over that would be lying about the one thing it is for.
 *
 * With the switch set, the guard is now actually applied — `auth.AUTH_GUARD` is
 * on every router the till mounts — and this screen is where a signed-out
 * shopkeeper lands. It has to say WHY they are here in one sentence, because
 * the alternative they were getting is twenty screens each showing their own
 * 401.
 *
 * SO THIS SCREEN NEVER DECIDES WHICH STATE IT IS IN. It asks
 * `GET /auth/status`, whose `enforced` is no longer "is the environment
 * variable set" but "is the guard on every route this app can guard" — the two
 * disagreed for the whole of this feature's life, and the endpoint answered
 * `enforced: true` about a lock attached to nothing. `guard_applied` and
 * `lock.unguarded_paths` are printed below precisely so that a disagreement is
 * visible on the screen instead of being discovered in a shop.
 *
 * WHAT THIS PAGE NEVER DOES.
 *   - It never renders a password. The field is `type="password"` until a
 *     person presses the eye, the value lives in one piece of state, and it is
 *     never put in a message, a heading, a note or a refusal.
 *   - It never puts a secret in a URL. `onSubmit` calls `preventDefault` before
 *     anything else, because a form left to its own devices on a hash-routed
 *     page appends every field to the address bar, and the address bar is
 *     written to history, sent as a `Referer` and printed in server logs.
 *   - It never sees the session token. `gawaah/auth.py` puts it in an HttpOnly
 *     cookie and keeps it out of every response body on purpose, so there is
 *     nothing here to leak.
 *   - It never logs. There is no `console` call in this file.
 *
 * The invitation code is the one secret that IS shown, once, because that is
 * what it is for: `POST /auth/invite` returns it legibly precisely so it can be
 * read off the screen and handed to somebody, and the counter keeps only its
 * hash and cannot show it a second time. The server's own sentence saying so is
 * printed beside it.
 */

/**
 * The route id this screen wants, in one place.
 *
 * `components/shell.tsx` and `App.tsx` belong to the shell and are not edited
 * here, so this constant is what the orchestrator registers and what the
 * account menu navigates to. Until it is registered, `#/signin` falls back to
 * the till — `routeFromHash` returns HOME for an unknown id.
 */
export const ROUTE_ID = 'signin';

/**
 * The fields `GET /auth/status` grew when the guard was actually applied.
 *
 * DECLARED HERE RATHER THAN IN `lib/authapi.ts` because that file belongs to
 * the request layer and is not edited from a screen — the same rule that put
 * `ROUTE_ID` in this file. Everything is optional: a till running an older
 * `gawaah/auth.py` answers without them, and every use below degrades to
 * saying nothing rather than to rendering `undefined`.
 */
interface LockDetail {
  /** Is `GAWAAH_REQUIRE_AUTH` set? SEPARATE from whether anything is locked. */
  switch_on?: boolean;
  /** Is the guard on every route this app can guard? The other half. */
  guard_applied?: boolean;
  /** What THIS deployment leaves reachable without a session, read off the
      guard that is actually mounted rather than off a constant. */
  open_here?: { paths: string[]; prefixes: string[] };
  lock?: {
    guarded_routes: number;
    unguarded_routes: number;
    /** Named, not counted: a count says "something is wrong" and a list says
        which router to go and fix. */
    unguarded_paths: string[];
    no_guard_possible: string[];
  };
}

type Status = auth.AuthStatus & LockDetail;

type Mode = 'in' | 'up';

interface Form {
  name: string;
  phone: string;
  password: string;
  invite: string;
}

const BLANK: Form = { name: '', phone: '', password: '', invite: '' };

type Problems = Partial<Record<keyof Form, string>>;

/* ========================================================== the screen == */

export default function SignIn() {
  const [status, setStatus] = useState<Status | null>(null);
  const [statusRefusal, setStatusRefusal] = useState<auth.Refusal | null>(null);
  const [account, setAccount] = useState<auth.Account | null>(null);
  const [session, setSession] = useState<auth.Me['session'] | null>(null);
  const [shop, setShop] = useState<ShopProfileDoc | null>(null);
  const [loading, setLoading] = useState(true);

  const [mode, setMode] = useState<Mode>('in');
  const [form, setForm] = useState<Form>(BLANK);
  /** Field errors appear only after a first attempt. Nobody wants to be told
      their password is too short before they have finished typing it. */
  const [tried, setTried] = useState(false);
  const [busy, setBusy] = useState(false);
  const [refusal, setRefusal] = useState<auth.Refusal | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const [invitation, setInvitation] = useState<auth.Invitation | null>(null);
  const [inviteRefusal, setInviteRefusal] = useState<auth.Refusal | null>(null);
  const [inviting, setInviting] = useState(false);

  const load = useCallback(async () => {
    // ASK THE LOCK FIRST, THEN THE PERSON. `/auth/status` names nobody and
    // always answers 200, including the fact of whether anyone is signed in.
    // Asking both at once meant a signed-out visitor always drew a 401 in the
    // console — correct HTTP, but an error line on a healthy screen teaches
    // whoever reads that console to ignore the next one.
    const s = await auth.status();
    const m = s.ok && s.signed_in ? await auth.me() : null;
    setLoading(false);
    if (!s.ok) {
      // The lock's own state could not be read. Drawing a form over that would
      // invite somebody to type a password at a server that is not answering.
      setStatusRefusal(s);
      setStatus(null);
      return;
    }
    setStatusRefusal(null);
    setStatus(s);
    // NOT SIGNED IN IS NOT AN ERROR. `m` is null when the lock already said
    // nobody is signed in, and a refusal when /auth/me was asked and declined
    // — three named reasons, all meaning the same thing on this screen.
    if (m && m.ok) {
      setAccount(m.account);
      setSession(m.session);
    } else {
      setAccount(null);
      setSession(null);
      // With no account on the counter there is nothing to sign in to, so the
      // form opens on the only thing that can be done.
      setMode(s.accounts === 0 ? 'up' : 'in');
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  /** The name over the door, when the lock has hidden the profile. */
  const [plate, setPlate] = useState<{ name: string | null; address: string | null } | null>(null);

  useEffect(() => {
    // The shop's identity above the card. Its absence is a fact, not a failure.
    //
    // THE NAMEPLATE IS ASKED FOR TOO, AND IT IS THE ONE THAT WORKS HERE. With
    // the lock on, `/shop/profile` answers 401 on exactly this screen — the one
    // screen a locked-out shopkeeper can reach — so the header reported that
    // the counter had no shop name. It has one; it would not say it. The
    // nameplate is open, GET-only and carries just the name and the address.
    void (async () => {
      const r = await shopProfile();
      if (r.ok) { setShop(r.profile); return; }
      const n = await shopNameplate();
      if (n.ok && n.configured) setPlate({ name: n.name, address: n.address });
    })();
  }, []);

  const set = useCallback(<K extends keyof Form>(k: K, v: string) => {
    setForm((f) => ({ ...f, [k]: v }));
  }, []);

  const problems = validate(mode, form, status);
  const showProblems = tried ? problems : {};

  const submit = useCallback(async (e: FormEvent) => {
    // FIRST, before anything can go wrong: a submitted form on a hash-routed
    // page otherwise writes every field, password included, into the address bar.
    e.preventDefault();
    setTried(true);
    if (Object.keys(validate(mode, form, status)).length > 0) return;

    setBusy(true);
    setRefusal(null);
    setNote(null);
    try {
      // ONE RULE FOR WHICH FORM THIS IS, and `isSignUp` is it. `mode` alone is
      // not the answer: with no account on the counter the sign-up form is
      // rendered regardless of the mode, and a submit that read `mode` would
      // post the sign-up form's fields to /auth/signin.
      const r = isSignUp(mode, status)
        ? await auth.signUp({
          name: form.name,
          phone: form.phone,
          password: form.password,
          invite: form.invite.trim() || undefined,
        })
        : await auth.signIn(form.phone, form.password);
      if (!r.ok) {
        // The password is deliberately LEFT in the field on a refusal. A wrong
        // password is usually one character wrong, and clearing it turns a typo
        // into a retype — which, with five attempts before a lock-out, is a
        // worse outcome than the field staying filled on a screen somebody is
        // standing in front of.
        setRefusal(r);
        return;
      }
      setForm(BLANK);
      setTried(false);
      setAccount(r.account);
      setNote(r.note);
      auth.authChanged();
      await load();
      // AND LEAVE THIS SCREEN. It used to stay here — session set, cookie good
      // for twelve hours, the same form still on screen — which a shopkeeper
      // reads as being asked to sign in again. Back to where the lock turned
      // them away, or the till, which is what a counter opens on.
      let back = 'till';
      try {
        const kept = sessionStorage.getItem(BOUNCED_FROM);
        if (kept) { back = kept; sessionStorage.removeItem(BOUNCED_FROM); }
      } catch { /* private mode: the till is a fine default */ }
      window.location.hash = `#/${back}`;
    } finally {
      setBusy(false);
    }
  }, [mode, form, status, load]);

  const out = useCallback(async () => {
    setBusy(true);
    setRefusal(null);
    setInvitation(null);
    setInviteRefusal(null);
    try {
      const r = await auth.signOut();
      if (!r.ok) { setRefusal(r); return; }
      setAccount(null);
      setSession(null);
      setNote(r.note);
      auth.authChanged();
      await load();
    } finally {
      setBusy(false);
    }
  }, [load]);

  const invite = useCallback(async () => {
    setInviting(true);
    setInviteRefusal(null);
    setInvitation(null);
    try {
      const r = await auth.mintInvite();
      if (!r.ok) { setInviteRefusal(r); return; }
      setInvitation(r);
    } finally {
      setInviting(false);
    }
  }, []);

  const shopName = shop?.name?.trim();

  return (
    <div className="auth-page">
      <div className="auth-col">
        {/* THE SHOP, ABOVE ITS OWN COUNTER. Whoever is about to type a password
            should be able to see which shop and which machine they are typing
            it into. */}
        <div className="auth-mark">
          <div className="auth-mark-badge" aria-hidden="true">
            {(shopName || plate?.name) ? (shopName || plate!.name!).slice(0, 1).toUpperCase() : 'ग'}
          </div>
          <h1>{shopName || plate?.name || 'This counter has no shop name yet'}</h1>
          <p>
            {(shop?.address ?? plate?.address)?.trim()
              ? firstLine((shop?.address ?? plate?.address)!)
              : 'GAWAAH — the counter that witnesses what leaves the shelf'}
          </p>
          <p className="auth-host mono">{hostLabel()}</p>
        </div>

        {/* WHY YOU ARE LOOKING AT THIS SCREEN, above the form and before
            anything else. When the counter is locked and this browser has no
            session, every other screen in the shop answers 401; landing here
            without a sentence explaining that is indistinguishable from the
            till being broken. Rendered only when it is TRUE — a banner that is
            always there is a banner nobody reads. */}
        {!loading && status && !account && <WhyYouAreHere status={status} />}

        {loading ? (
          /* LOADING. The shape of what is coming — two fields and a button —
             not a spinner, so nothing jumps when the answer lands. Nothing on
             this screen renders blank while it waits, and no password field is
             offered before the counter has said whether it is locked. */
          <Card title="Sign in">
            <SkeletonText lines={2} />
            <div style={{ marginTop: 20 }}><Skeleton h={44} radius={6} /></div>
            <div style={{ marginTop: 20 }}><Skeleton h={44} radius={6} /></div>
            <div style={{ marginTop: 24 }}><Skeleton w={140} h={40} radius={10} /></div>
          </Card>
        ) : statusRefusal ? (
          <Card title="Sign in">
            <Refusal
              reason={statusRefusal.reason === auth.NOT_MOUNTED
                ? 'This till has no accounts to sign in to'
                : 'This counter could not say whether it is locked'}
              detail={statusRefusal.reason}
              hint={statusRefusal.detail}
              action={<button className="btn sm" onClick={() => void load()}>TRY AGAIN</button>}
            />
            <p className="auth-hint">
              {statusRefusal.reason === auth.NOT_MOUNTED
                ? 'Nothing is locked and nothing is broken — this counter simply has no account '
                  + 'layer running. Anyone on this network can open it, and there is no password '
                  + 'that would change that until the router is mounted.'
                : 'No password is asked for until the counter answers. A form drawn over a server '
                  + 'that is not replying is a form that throws away what you type.'}
            </p>
          </Card>
        ) : account && status ? (
          <SignedInCard
            account={account}
            session={session}
            status={status}
            busy={busy}
            note={note}
            refusal={refusal}
            onOut={() => void out()}
            invitation={invitation}
            inviteRefusal={inviteRefusal}
            inviting={inviting}
            onInvite={() => void invite()}
            onDismissInvite={() => setInvitation(null)}
          />
        ) : status ? (
          <FormCard
            status={status}
            mode={mode}
            onMode={(m) => {
              setMode(m);
              setRefusal(null);
              setNote(null);
              setTried(false);
              // The password and the invitation code do not survive the switch.
              // The name and the number are the same person either way; a
              // secret left in a field on a screen that now says something
              // else is a secret nobody remembers is there.
              setForm((f) => ({ ...f, password: '', invite: '' }));
            }}
            form={form}
            onSet={set}
            problems={showProblems}
            busy={busy}
            refusal={refusal}
            note={note}
            onSubmit={submit}
          />
        ) : null}

        {status && <LockState status={status} />}
        {status && <Limits status={status} />}

      </div>
    </div>
  );
}

/* ================================================== the form, signed out == */

function FormCard({
  status, mode, onMode, form, onSet, problems, busy, refusal, note, onSubmit,
}: {
  status: auth.AuthStatus;
  mode: Mode;
  onMode: (m: Mode) => void;
  form: Form;
  onSet: <K extends keyof Form>(k: K, v: string) => void;
  problems: Problems;
  busy: boolean;
  refusal: auth.Refusal | null;
  note: string | null;
  onSubmit: (e: FormEvent) => void;
}) {
  const first = status.accounts === 0;
  const signUp = isSignUp(mode, status);

  return (
    <Card
      title={signUp ? (first ? 'Open the first account' : 'Create an account') : 'Sign in'}
      aside={
        <Pill tone={status.enforced ? 'code' : 'off'}>
          {status.enforced ? 'sign-in required' : 'not required'}
        </Pill>
      }
    >
      {/* THE EMPTY STATE OF THIS SCREEN is "no account exists on this counter",
          and it is rendered as the thing to do about it rather than as an empty
          box: there is exactly one action available and putting it behind an
          `Empty` panel would be a screen that says "nothing here" above a form
          that is the whole point. */}
      {first ? (
        <p className="auth-lede">
          No account exists on this counter yet, so there is nothing to sign in to. The first
          account is free and anyone who reaches this till before you can claim it. Every account
          after it needs an invitation from somebody signed in.
        </p>
      ) : (
        <div className="auth-switch">
          <Segmented
            value={mode}
            onChange={onMode}
            options={[
              { value: 'in', label: 'Sign in', title: 'You already have an account here' },
              { value: 'up', label: 'Create an account', title: 'Needs an invitation code' },
            ]}
          />
        </div>
      )}

      <form className="auth-form" onSubmit={onSubmit} aria-busy={busy}>
        {signUp && (
          <TextField
            label="Your name"
            value={form.name}
            onChange={(v) => onSet('name', v)}
            problem={problems.name}
            autoComplete="name"
            placeholder="Ramesh Sharma"
            sub="The log should say who did a thing, not which phone number did it."
            disabled={busy}
          />
        )}

        <TextField
          label="Phone number"
          value={form.phone}
          onChange={(v) => onSet('phone', v)}
          problem={problems.phone}
          autoComplete="tel"
          inputMode="tel"
          placeholder="98765 43210"
          sub="Type it however you like. +91 and a leading 0 are both understood, so one number never becomes two accounts."
          disabled={busy}
        />

        <Secret
          label="Password"
          value={form.password}
          onChange={(v) => onSet('password', v)}
          problem={problems.password}
          autoComplete={signUp ? 'new-password' : 'current-password'}
          sub={signUp
            ? `At least ${auth.MIN_PASSWORD} characters, and not the number above — that one is written on the shop board outside.`
            : undefined}
          disabled={busy}
        />

        {signUp && !first && (
          <TextField
            label="Invitation code"
            value={form.invite}
            onChange={(v) => onSet('invite', v)}
            problem={problems.invite}
            autoComplete="off"
            mono
            placeholder="inv_…"
            sub="From somebody already signed in here. It opens one account and then stops working."
            disabled={busy}
          />
        )}

        <div className="auth-actions">
          <button className="btn primary" type="submit" disabled={busy}>
            {busy
              ? (signUp ? 'CREATING…' : 'SIGNING IN…')
              : (signUp ? (first ? 'OPEN THE ACCOUNT' : 'CREATE THE ACCOUNT') : 'SIGN IN')}
          </button>
        </div>

        {refusal && (
          <>
            <Refusal
              reason={headline(refusal)}
              detail={refusal.reason}
              hint={refusal.detail}
            />
            {refusal.status === 429 && (
              <p className="auth-hint">
                This counter allows {status.rate_limit.attempts} attempts in{' '}
                {auth.forHowLong(status.rate_limit.window_s)} and then locks that number for{' '}
                {auth.forHowLong(status.rate_limit.lock_s)}. The lock is on the NUMBER and is held
                in memory, so anyone who can reach this till can trigger it, and restarting the
                till clears it. That is the cost of not allowing unlimited guessing.
              </p>
            )}
          </>
        )}
        {note && <p className="auth-note">{note}</p>}
      </form>
    </Card>
  );
}

/* =================================================== the account, signed in == */

function SignedInCard({
  account, session, status, busy, note, refusal, onOut,
  invitation, inviteRefusal, inviting, onInvite, onDismissInvite,
}: {
  account: auth.Account;
  session: auth.Me['session'] | null;
  status: auth.AuthStatus;
  busy: boolean;
  note: string | null;
  refusal: auth.Refusal | null;
  onOut: () => void;
  invitation: auth.Invitation | null;
  inviteRefusal: auth.Refusal | null;
  inviting: boolean;
  onInvite: () => void;
  onDismissInvite: () => void;
}) {
  return (
    <Card
      title="Signed in"
      aside={<Pill tone="code">{account.role}</Pill>}
    >
      <div className="auth-who">
        <span className="auth-ini lg" aria-hidden="true">{auth.initialsOf(account.name)}</span>
        <span className="auth-who-text">
          <b>{account.name}</b>
          <span className="tnum">{account.phone}</span>
        </span>
      </div>

      <dl className="auth-facts">
        <div><dt>Account opened</dt><dd>{auth.whenever(account.created_at)}</dd></div>
        <div><dt>Signed in</dt><dd>{auth.whenever(session?.created_at)}</dd></div>
        <div>
          <dt>This session ends</dt>
          <dd>
            {auth.whenever(session?.expires_at)}
            {session ? <span className="auth-dim"> · {auth.forHowLong(session.expires_in_s)} left</span> : null}
          </dd>
        </div>
        <div>
          <dt>Recorded as</dt>
          <dd>{account.role} <span className="auth-dim">— a record, not a permission</span></dd>
        </div>
      </dl>

      <p className="auth-hint">
        The session itself is in a cookie this page cannot read, and the counter keeps only its
        fingerprint. Signing out deletes it here, not just in this browser, so the same token
        cannot be replayed from anywhere else.
      </p>

      <div className="auth-actions">
        <button className="btn" onClick={onOut} disabled={busy}>
          {busy ? 'SIGNING OUT…' : 'SIGN OUT'}
        </button>
        <button className="btn ghost" onClick={onInvite} disabled={inviting}>
          {inviting ? 'MAKING A CODE…' : 'INVITE SOMEBODY'}
        </button>
      </div>

      {refusal && (
        <Refusal reason={headline(refusal)} detail={refusal.reason} hint={refusal.detail} />
      )}
      {note && <p className="auth-note">{note}</p>}

      {inviteRefusal && (
        <Refusal
          reason="No invitation was made"
          detail={inviteRefusal.reason}
          hint={inviteRefusal.detail}
        />
      )}

      {invitation && (
        /* SHOWN ONCE, ON PURPOSE. This is the only moment the code is legible:
           the counter stores only its hash and cannot print it again. It is not
           a password and not a session token — it is a thing meant to be read
           off this screen and handed to one person. */
        <div className="auth-invite">
          <div className="auth-invite-h">Give this to one person</div>
          <div className="auth-invite-code mono">{invitation.invite}</div>
          <div className="auth-actions">
            <CopyBtn text={invitation.invite} />
            <button className="btn ghost sm" onClick={onDismissInvite}>HIDE IT</button>
          </div>
          <p className="auth-invite-note">{invitation.note}</p>
          <p className="auth-invite-note">
            Expires {auth.whenever(invitation.expires_at)}.
            {invitation.audited ? '' : ' It could not be written to the counter’s log.'}
          </p>
        </div>
      )}

      {status.accounts > 1 && (
        <p className="auth-hint">
          {status.accounts} accounts exist on this counter. There is no way to list them, no way
          to remove one, and no way for an owner to end somebody else’s session.
        </p>
      )}
    </Card>
  );
}

/* ============================================ why you are on this screen == */

/**
 * ONE SENTENCE, AT THE TOP, ONLY WHEN IT IS TRUE.
 *
 * With the guard applied and no session, `/shop`, `/manage/today`, `/orders`
 * and every other screen answer 401. A shopkeeper who is bounced here needs to
 * know that the till is fine and their session is not — those two look
 * identical from the outside, and one of them means "type your password" while
 * the other means "the counter is down".
 *
 * There is no third state to draw. `status.accounts === 0` is already handled
 * by the form below, which opens on sign-up and says so; repeating it here
 * would be two boxes making the same point.
 */
function WhyYouAreHere({ status }: { status: Status }) {
  if (!status.enforced || status.accounts === 0) return null;
  return (
    <Verdict tone="amber" title="This counter is locked and this browser is not signed in">
      <span className="auth-p">
        Every screen in the shop — the till, the catalogue, the day’s takings, the orders —
        is refusing until somebody signs in here. Nothing is broken and nothing was lost: the
        session either ran out or was never made in this browser.
      </span>
      <span className="auth-p auth-dim">
        Sessions last {auth.forHowLong(status.session_seconds)}. The customer’s side of the shop
        is unaffected — a phone that scans the shutter QR still reaches the storefront, the bill
        it was sent, and its own payment code.
      </span>
    </Verdict>
  );
}

/* ================================================ the state of the lock == */

/**
 * THE PANEL THIS SCREEN EXISTS FOR.
 *
 * Not amber and not red: on this product those colours mean money and
 * recognition, and an unlocked counter is neither. It is a plain statement of
 * fact at the size of the fact.
 */
function LockState({ status }: { status: Status }) {
  const holes = status.lock?.unguarded_paths ?? [];

  // THE STATE THIS WHOLE FEATURE EXISTED IN AND NEVER REPORTED: the switch is
  // set, so the counter used to answer `enforced: true`, while the guard was
  // attached to nothing and every screen answered 200 to a stranger. It is
  // drawn RED because it is the only state on this screen where what the
  // counter says and what the counter does are different things.
  if (status.switch_on && status.guard_applied === false) {
    return (
      <Verdict tone="red" title="The switch is on and this counter is NOT locked">
        <span className="auth-p">
          <b className="mono">{status.switch}</b> is set, but {holes.length} route
          {holes.length === 1 ? '' : 's'} on this till do not carry the guard, so they answer
          anybody who asks. A counter that reports a lock it has not got is worse than one that
          reports none.
        </span>
        {holes.length > 0 && (
          <span className="auth-p auth-dim">
            {holes.slice(0, 12).map((p, i) => (
              <span key={p}>{i > 0 ? ' · ' : ''}<span className="mono">{p}</span></span>
            ))}
            {holes.length > 12 ? ` · and ${holes.length - 12} more` : ''}
          </span>
        )}
        <span className="auth-p auth-dim">
          Every router in <span className="mono">tools/upload_app.py</span> is mounted with{' '}
          <span className="mono">dependencies=AUTH_GUARD</span>. One mounted without it looks
          exactly like this.
        </span>
      </Verdict>
    );
  }

  if (!status.enforced) {
    return (
      <Verdict tone="info" title="Anyone on this network can open this counter">
        <span className="auth-p">
          Nothing is being checked. Accounts work and sessions work, and every screen — the till,
          the catalogue, the day’s takings — is exactly as reachable as it was before anybody
          signed in. Signing in here records the sign-in in the counter’s own log; it does not
          yet put a name on a bill.
        </span>
        {status.lock && (
          <span className="auth-p">
            The guard is already fitted to{' '}
            <b>{status.lock.guarded_routes} of this till’s routes</b> and is doing nothing but
            recording who is signed in. Setting the switch is the whole change — there is no
            second step and no router left to decorate.
          </span>
        )}
        <span className="auth-p">
          To switch it on: set <b className="mono">{status.switch}=1</b> in the environment the
          till starts in, and restart it. Only <span className="mono">1</span>,{' '}
          <span className="mono">true</span>, <span className="mono">yes</span>,{' '}
          <span className="mono">on</span> and <span className="mono">y</span> turn it on. A typo
          leaves it off, deliberately: a switch that locks a live counter has to fail towards
          open.
        </span>
        <span className="auth-p auth-dim">
          Sessions last {auth.forHowLong(status.session_seconds)} (
          <span className="mono">{status.session_hours_switch}</span>).{' '}
          {status.accounts === 0
            ? 'No account exists here yet.'
            : `${status.accounts} account${status.accounts === 1 ? '' : 's'} on this counter.`}
        </span>
      </Verdict>
    );
  }

  const openHere = [
    ...(status.open_here?.paths ?? []),
    ...(status.open_here?.prefixes ?? []).map((p) => `${p}*`),
  ];

  return (
    <Verdict tone="info" title="This counter asks for a sign-in">
      <span className="auth-p">{status.note}</span>
      <span className="auth-p auth-dim">
        The way back in answers without a session, or nobody could ever sign in:{' '}
        {status.open_paths.map((p, i) => (
          <span key={p}>
            {i > 0 ? ' · ' : ''}<span className="mono">{p}</span>
          </span>
        ))}
      </span>
      {openHere.length > 0 && (
        <span className="auth-p auth-dim">
          {/* NOT the same list, and the difference matters: above is auth’s own
              way back in, below is what THIS shop leaves open for the person
              standing outside it. Read off the guard actually mounted, so the
              readout cannot drift from the wiring. */}
          So does the customer’s side of the shop — the shutter QR, the bill they were sent, and
          the payment code their own order page draws:{' '}
          {openHere.map((p, i) => (
            <span key={p}>
              {i > 0 ? ' · ' : ''}<span className="mono">{p}</span>
            </span>
          ))}
        </span>
      )}
      <span className="auth-p auth-dim">
        Sessions last {auth.forHowLong(status.session_seconds)} (
        <span className="mono">{status.session_hours_switch}</span>).{' '}
        {status.lock
          ? `The guard is on all ${status.lock.guarded_routes} of this till's routes; `
            + `${status.lock.no_guard_possible.length} cannot carry one `
            + `(${status.lock.no_guard_possible.join(', ')}) and serve no shop data.`
          : 'The guard only covers routers it has been applied to.'}
      </span>
    </Verdict>
  );
}

/** What signing in does not do. Stated, rather than implied away. */
function Limits({ status }: { status: auth.AuthStatus }) {
  return (
    <Card title="What this does not do">
      <ul className="auth-limits">
        <li>
          <b>No password reset and no password change.</b> Losing the only password means deleting{' '}
          <span className="mono">auth_accounts.json</span> by hand on the counter itself. There is
          also no way to delete an account.
        </li>
        <li>
          <b>Over plain http the session cookie crosses the shop’s wifi in the clear.</b> It is
          not marked Secure on http on purpose — a Secure cookie set over http is thrown away by
          the browser, and sign-in would appear to work while nothing stayed signed in. Put the
          till behind https if that matters.
        </li>
        <li>
          <b>owner and staff are recorded, not enforced.</b> Nothing in this program reads the
          role. It is a note in the log about who did a thing, not a fence.
        </li>
        <li>
          <b>Anyone who can reach this till can lock a number out of sign-in.</b>{' '}
          {status.rate_limit.attempts} wrong passwords in{' '}
          {auth.forHowLong(status.rate_limit.window_s)} locks that number for{' '}
          {auth.forHowLong(status.rate_limit.lock_s)}. The count is held in memory and a restart
          clears it. There is no version of a per-number limit without that property; the
          alternative is unlimited guessing.
        </li>
        <li>
          <b>
            {status.accounts === 0
              ? 'The first account is claimable by whoever reaches this till first.'
              : 'The first account was claimable by whoever reached this till first.'}
          </b>{' '}
          There is nobody yet who could invite anyone, so that moment cannot be protected.{' '}
          {status.accounts === 0
            ? 'It has not been claimed. The right time to open it is now.'
            : 'That moment has passed.'}
        </li>
        {!status.store_readable && (
          <li>
            <b>The accounts file could not be read.</b> This counter is reporting zero accounts
            because it could not open the file, which is not the same fact as having none.
          </li>
        )}
      </ul>
    </Card>
  );
}

/* ============================================== the menu in the top bar == */

/**
 * THE ACCOUNT MENU, for the dark top bar.
 *
 * It lives in this file because the shell (`components/shell.tsx`, `App.tsx`)
 * belongs to another surface and is not edited here. The orchestrator drops it
 * into the `status` slot of `TopBar`; it needs no props to work, and takes an
 * optional `onGo` so it can use the app's own navigation rather than the hash
 * when one is offered.
 *
 * It shows a NAME, never a token, and it never renders while it does not yet
 * know: the chip says what it is doing at every moment, because a bar that
 * flickers between "signed in" and blank on every route change is a bar nobody
 * trusts.
 *
 * THE FIRST RUN IS A STATE OF ITS OWN, AND IT WAS THE MISSING ONE. A counter
 * that has just been installed has ZERO accounts: there is nothing to sign in
 * to, and "not signed in" reads as a thing that will sort itself out. It will
 * not. Until this chip is mounted in the top bar, `#/signin` is reachable only
 * by typing it — the sidebar leaves it out on purpose (`shell.tsx` UNLISTED)
 * and the palette builds its list from the tabs — so the one action a new
 * shopkeeper must take has no door in the product at all. With the lock on,
 * that stops being an inconvenience and becomes a counter nobody can open.
 *
 * MOUNTING IT IS ONE LINE IN `App.tsx`, which this file does not own; see the
 * comment on `ROUTE_ID`. Nothing here needs props.
 */
export function AccountMenu({ onGo }: { onGo?: (route: string) => void }) {
  const { t } = useT();
  const [account, setAccount] = useState<auth.Account | null>(null);
  const [session, setSession] = useState<auth.Me['session'] | null>(null);
  const [enforced, setEnforced] = useState<boolean | null>(null);
  /** How many accounts exist. Zero is the first run and its own sentence. */
  const [accounts, setAccounts] = useState<number | null>(null);
  const [reachable, setReachable] = useState(true);
  const [ready, setReady] = useState(false);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [refusal, setRefusal] = useState<auth.Refusal | null>(null);
  const box = useRef<HTMLDivElement>(null);

  const look = useCallback(async () => {
    // THE LOCK FIRST, THE PERSON SECOND — the same order as `load` above, and
    // for a reason that only appeared once this chip moved into the top bar.
    //
    // It used to ask `/auth/me` first and fall back to `/auth/status`. On the
    // account screen that cost one 401 on one page. In the top bar it is on
    // EVERY page, so a signed-out counter — the state a shop is in until
    // somebody makes an account, and the state every e2e run starts in —
    // logged `Failed to load resource: 401` into the console on every single
    // load. Chrome writes that line itself; no catch can suppress it. Two of
    // this suite's specs assert the console is clean, and they were right to
    // fail: a permanent error line on a healthy screen is how a reader learns
    // to scroll past the next one, which will be real.
    //
    // `/auth/status` is open, always 200, and names nobody. It already carries
    // `signed_in`, `accounts` and `enforced` — everything this chip draws
    // except a name and an expiry. So: signed out is now ONE request and no
    // error; signed in is two, both 200. The signed-out case is the one that
    // happens on every page of a fresh counter, and it got cheaper.
    const s = await auth.status();
    if (!s.ok) {
      // Not "signed out" — the lock itself could not be read. A chip that drew
      // CREATE AN ACCOUNT here would be inviting a tap at a server that is not
      // answering.
      setAccount(null);
      setSession(null);
      setEnforced(null);
      setAccounts(null);
      setReachable(false);
      setReady(true);
      return;
    }
    setReachable(true);
    setEnforced(s.enforced);
    setAccounts(s.accounts);
    if (!s.signed_in) {
      setAccount(null);
      setSession(null);
      setReady(true);
      return;
    }
    // Somebody is signed in, so the name and the expiry are worth a request.
    const m = await auth.me();
    setAccount(m.ok ? m.account : null);
    setSession(m.ok ? m.session : null);
    if (m.ok) setEnforced(m.enforced);
    setReady(true);
  }, []);

  useEffect(() => { void look(); }, [look]);
  // Signing out on the account screen has to be true up here immediately.
  useEffect(() => auth.onAuthChanged(() => { void look(); }), [look]);
  // A session ends after twelve hours. Coming back to the tab is the moment a
  // stale name in the bar would matter.
  useEffect(() => {
    const on = () => { void look(); };
    addEventListener('focus', on);
    return () => removeEventListener('focus', on);
  }, [look]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    const onDown = (e: Event) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    };
    addEventListener('keydown', onKey);
    addEventListener('pointerdown', onDown);
    return () => {
      removeEventListener('keydown', onKey);
      removeEventListener('pointerdown', onDown);
    };
  }, [open]);

  const go = useCallback(() => {
    setOpen(false);
    if (onGo) onGo(ROUTE_ID);
    else location.hash = `#/${ROUTE_ID}`;
  }, [onGo]);

  const out = useCallback(async () => {
    setBusy(true);
    setRefusal(null);
    try {
      const r = await auth.signOut();
      if (!r.ok) { setRefusal(r); return; }
      setOpen(false);
      auth.authChanged();
      await look();
    } finally {
      setBusy(false);
    }
  }, [look]);

  return (
    <div className="auth-menu" ref={box}>
      <button
        type="button"
        className="auth-menu-btn"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        title={account ? `Signed in as ${account.name}` : 'Accounts on this counter'}
      >
        {!ready ? (
          <Pill tone="off">account</Pill>
        ) : account ? (
          <>
            <span className="auth-ini" aria-hidden="true">{auth.initialsOf(account.name)}</span>
            <span className="auth-menu-nm">{account.name}</span>
          </>
        ) : (
          // FOUR STATES, NOT THREE. "create an account" is the first run and it
          // is the only one of these that is an instruction — a counter with no
          // account has nothing to sign in to, and a chip reading "not signed
          // in" tells a new shopkeeper to wait for something that will never
          // come. Amber, because it is the one that wants a hand.
          <Pill tone={accounts === 0 ? 'amb' : 'off'} dot={accounts === 0}>
            {/* TWO LABELS, ONE SHOWN. At 390 px the bar has ~390 px for a
                130 px wordmark and a status row that wanted 270 — so the long
                label overflowed and painted across GAWAAH. Rather than drop
                the chip (it is the only door to an account) or truncate it to
                nonsense, the narrow bar shows the short word and the popover
                carries the instruction. The amber dot survives at both widths,
                because that is what makes a new shopkeeper look. */}
            <span className="auth-menu-long">
              {t(!reachable
                ? 'auth.chip.none'
                : accounts === 0
                  ? 'auth.chip.create'
                  : enforced ? 'auth.chip.signIn' : 'auth.chip.out')}
            </span>
            <span className="auth-menu-short">
              {t(!reachable
                ? 'auth.chip.none.short'
                : accounts === 0
                  ? 'auth.chip.create.short'
                  : enforced ? 'auth.chip.signIn' : 'auth.chip.create.short')}
            </span>
          </Pill>
        )}
      </button>

      {open && (
        <div className="auth-pop" role="menu">
          {account ? (
            <>
              <div className="auth-pop-head">
                <span className="auth-ini lg" aria-hidden="true">{auth.initialsOf(account.name)}</span>
                <span className="auth-who-text">
                  <b>{account.name}</b>
                  <span className="tnum">{account.phone}</span>
                </span>
              </div>
              <div className="auth-pop-facts">
                <span>{account.role} — a record, not a permission</span>
                {session && (
                  <span>
                    Session ends {auth.whenever(session.expires_at)} ·{' '}
                    {auth.forHowLong(session.expires_in_s)} left
                  </span>
                )}
              </div>
            </>
          ) : (
            <div className="auth-pop-facts pad">
              <span>
                <b>{accounts === 0 ? 'This counter has no account yet.' : 'Nobody is signed in.'}</b>
              </span>
              <span>
                {!reachable
                  ? 'This till is not answering for accounts at all.'
                  : accounts === 0
                    // The one state that names what to do rather than what is.
                    ? 'The first account is free and takes a name, a phone number and a '
                      + 'password. Whoever reaches this till first can claim it, so the right '
                      + 'time is now.'
                    : enforced === false
                      ? 'This counter is not locked — anyone on this network can open it.'
                      : enforced
                        ? 'This counter asks for a sign-in on every screen but the storefront.'
                        : 'The counter has not said whether it is locked.'}
              </span>
            </div>
          )}

          {refusal && <p className="auth-pop-refusal">{refusal.reason}</p>}

          <div className="auth-pop-actions">
            <button type="button" className="auth-pop-btn" onClick={go} role="menuitem">
              {account
                ? 'Account and invitations'
                : accounts === 0 ? 'Open the first account' : 'Open the account screen'}
            </button>
            {account && (
              <button
                type="button"
                className="auth-pop-btn"
                onClick={() => void out()}
                disabled={busy}
                role="menuitem"
              >
                {busy ? 'Signing out…' : 'Sign out'}
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

/* ============================================================== pieces == */

function TextField({
  label, value, onChange, problem, sub, placeholder, autoComplete, inputMode, mono, disabled,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  problem?: string;
  sub?: ReactNode;
  placeholder?: string;
  autoComplete?: string;
  inputMode?: 'tel' | 'text';
  mono?: boolean;
  disabled?: boolean;
}) {
  const id = useId();
  return (
    <div className="auth-row">
      <label htmlFor={id}>{label}</label>
      <input
        id={id}
        className={mono ? 'mono-in' : undefined}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        autoComplete={autoComplete}
        inputMode={inputMode}
        autoCapitalize="off"
        spellCheck={false}
        disabled={disabled}
        aria-invalid={problem ? true : undefined}
        aria-describedby={`${id}-sub`}
      />
      <span className="auth-sub" id={`${id}-sub`}>
        {problem ? <span className="auth-bad">{problem}</span> : sub}
      </span>
    </div>
  );
}

/**
 * A password field with a show/hide toggle.
 *
 * `type` flips between `password` and `text`, and NOTHING ELSE ON THIS PAGE
 * EVER DISPLAYS THE VALUE. The toggle starts hidden every time the field is
 * mounted, so switching between sign-in and sign-up cannot leave a password
 * legible on a counter that faces a shop.
 */
function Secret({
  label, value, onChange, problem, sub, autoComplete, disabled,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  problem?: string;
  sub?: ReactNode;
  autoComplete: string;
  disabled?: boolean;
}) {
  const id = useId();
  const [shown, setShown] = useState(false);
  return (
    <div className="auth-row">
      <label htmlFor={id}>{label}</label>
      <span className="auth-secret">
        <input
          id={id}
          type={shown ? 'text' : 'password'}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          autoComplete={autoComplete}
          autoCapitalize="off"
          spellCheck={false}
          disabled={disabled}
          aria-invalid={problem ? true : undefined}
          aria-describedby={`${id}-sub`}
        />
        <button
          type="button"
          className="auth-eye"
          onClick={() => setShown((v) => !v)}
          aria-pressed={shown}
          aria-controls={id}
          aria-label={shown ? 'Hide the password' : 'Show the password'}
          title={shown ? 'Hide the password' : 'Show the password'}
          disabled={disabled}
        >
          <EyeIcon off={shown} />
        </button>
      </span>
      <span className="auth-sub" id={`${id}-sub`}>
        {problem ? <span className="auth-bad">{problem}</span> : sub}
      </span>
    </div>
  );
}

/** Inline SVG. There is no icon package in this repo and there will not be one. */
function EyeIcon({ off }: { off: boolean }) {
  return (
    <svg viewBox="0 0 20 20" width="17" height="17" fill="none" aria-hidden="true">
      <path
        d="M1.7 10S4.9 4.6 10 4.6 18.3 10 18.3 10 15.1 15.4 10 15.4 1.7 10 1.7 10Z"
        stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round"
      />
      <circle cx="10" cy="10" r="2.4" stroke="currentColor" strokeWidth="1.4" />
      {off && <path d="M3.5 3.5 16.5 16.5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />}
    </svg>
  );
}

/** Copy one string. `navigator.clipboard`, nothing else, same as Settings. */
function CopyBtn({ text }: { text: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      type="button"
      className="btn sm"
      onClick={() => {
        if (!navigator.clipboard) return;
        void navigator.clipboard.writeText(text).then(() => {
          setDone(true);
          setTimeout(() => setDone(false), 1600);
        });
      }}
    >
      {done ? 'COPIED' : 'COPY THE CODE'}
    </button>
  );
}

/* ============================================================== helpers == */

/**
 * The local checks. They mirror `gawaah/auth.py` and decide nothing — a form
 * that passes all of them can still be refused, and when it is, the server's
 * own sentence is what appears. They exist so an obviously incomplete form does
 * not cost a round trip, and so the rule is on screen before it is broken.
 */
/**
 * Whether this form opens an account or signs in to one. THE ONLY PLACE THAT
 * DECIDES — the card, the validation and the submit all ask this, because a
 * screen that renders one form and posts the other is the kind of bug that only
 * appears on the very first counter anybody sets up.
 */
function isSignUp(mode: Mode, status: auth.AuthStatus | null): boolean {
  return mode === 'up' || status?.accounts === 0;
}

function validate(mode: Mode, form: Form, status: auth.AuthStatus | null): Problems {
  const p: Problems = {};
  const signUp = isSignUp(mode, status);

  if (signUp) {
    if (!form.name.trim()) {
      p.name = 'A name is required — the log should say who did a thing, not which number did it.';
    } else if (form.name.trim().length > auth.MAX_NAME) {
      p.name = `That name is ${form.name.trim().length} characters and the cap is ${auth.MAX_NAME}.`;
    }
  }

  const digits = auth.phoneDigits(form.phone);
  if (!form.phone.trim()) {
    p.phone = 'A phone number is required — it is how this counter tells one person from another.';
  } else if (digits.length < auth.MIN_PHONE_DIGITS || digits.length > auth.MAX_PHONE_DIGITS) {
    p.phone = `That has ${digits.length} digit${digits.length === 1 ? '' : 's'} in it. `
      + `A number that can be dialled has between ${auth.MIN_PHONE_DIGITS} and ${auth.MAX_PHONE_DIGITS}.`;
  }

  // Described by its LENGTH and never by its value, the same way auth.py
  // refuses one: a message about a password is a thing that gets screenshotted.
  if (!form.password) {
    p.password = 'A password is required.';
  } else if (form.password.length < auth.MIN_PASSWORD) {
    p.password = `That is ${form.password.length} characters. The shortest this counter accepts is ${auth.MIN_PASSWORD}.`;
  } else if (form.password.length > auth.MAX_PASSWORD) {
    p.password = `That is ${form.password.length} characters and the cap is ${auth.MAX_PASSWORD}.`;
  } else if (signUp && auth.phoneDigits(form.password) === digits && digits.length > 0) {
    p.password = 'That password is this account’s own phone number, which is written on the shop board.';
  }

  if (signUp && status && status.accounts > 0 && !form.invite.trim()) {
    p.invite = 'This counter already has an account, so a new one needs an invitation code from somebody signed in.';
  }

  return p;
}

/**
 * A plain-English heading for a refusal. THE SERVER'S OWN REASON AND SENTENCE
 * ARE STILL SHOWN UNDERNEATH, VERBATIM — this only decides what the line above
 * them says, and falls back to the server's reason when it has nothing better.
 */
function headline(r: auth.Refusal): string {
  switch (r.reason) {
    case 'auth_phone_or_password_wrong':
      return 'That did not sign you in';
    case 'auth_too_many_sign_in_attempts':
      return 'That number is locked out for now';
    case 'auth_signup_needs_an_invite':
    case 'auth_invite_not_from_this_shop':
    case 'auth_invite_already_used':
    case 'auth_invite_expired':
      return 'No account was created';
    case 'auth_phone_already_has_an_account':
      return 'That number already has an account here';
    case 'auth_password_is_the_phone_number':
    case 'auth_password_too_short':
    case 'auth_password_too_long':
      return 'Pick a different password';
    case 'auth_sign_in_required':
    case 'auth_no_session_presented':
    case 'auth_session_expired':
    case 'auth_session_not_known_here':
      return 'You are not signed in';
    case 'auth_store_unreadable':
      return 'This counter could not read its own accounts';
    default:
      return r.status === 0 ? 'The counter could not be reached' : 'The counter refused';
  }
}

/** The first line of an address, for the mark above the card. */
function firstLine(address: string): string {
  const line = address.split('\n')[0];
  return (line ?? '').trim() || address.trim();
}

/** Which machine this is. Honest, and never a secret. */
function hostLabel(): string {
  try {
    return location.host || 'this machine';
  } catch {
    return 'this machine';
  }
}
