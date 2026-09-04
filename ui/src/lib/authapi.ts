/**
 * Accounts, sessions and this counter's lock. Every `/auth` request in one place.
 *
 * Five rules this module exists to keep:
 *
 *  1. A PASSWORD NEVER LEAVES THIS FILE EXCEPT IN A POST BODY. It is a
 *     parameter, it goes straight into `JSON.stringify`, and no module-level
 *     variable holds one. There is no `console.log` anywhere in this file, and
 *     no password, invite code or session token is ever placed in a URL, a
 *     query string or a hash — a URL is the one string a browser writes to
 *     history, hands to the next page in `Referer`, and prints in a log.
 *  2. THE TOKEN IS NOT OURS TO SEE. `gawaah/auth.py` delivers the session as an
 *     HttpOnly cookie and deliberately never puts it in a response body, so
 *     this module cannot read it, cannot store it and cannot forward it. Every
 *     request carries `credentials: 'same-origin'` so the browser sends the
 *     cookie back; that is the whole of our participation in it.
 *  3. A REFUSAL IS A RESULT, NOT AN ERROR. The server answers `{ok:false,
 *     reason, detail}` with a 400, a 401 or a 429 on purpose. These wrappers
 *     parse the body on a non-2xx instead of throwing, and carry the status
 *     back, because "wrong password" and "locked out" are different screens.
 *  4. THE SERVER IS THE AUTHORITY ON EVERY RULE. The constants below are a
 *     MIRROR of `gawaah/auth.py`, kept so a form can say "at least 8
 *     characters" before it wastes a round trip. They never decide anything: a
 *     request that passes them can still be refused, and the server's sentence
 *     is what gets shown.
 *  5. NOTHING HERE SETTLES MONEY. Every one of these endpoints answers
 *     `settles_money: false`; this module has no gateway, mints nothing and
 *     builds no payable string.
 *
 * `send` is a near-copy of the ones in `lib/api.ts` and `lib/adminapi.ts`, for
 * the reason recorded there: neither exports it, and a new screen must not edit
 * another screen's request layer to borrow one function.
 */

/* ------------------------------------------------------------- the wire -- */

export type Refusal = {
  ok: false;
  /** The server's own name for what it refused. Shown verbatim, never rephrased. */
  reason: string;
  /** The server's own sentence about it. Also verbatim. */
  detail?: string;
  /** 401 sign-in, 429 rate limit, 400 everything else, 0 when the fetch failed. */
  status: number;
};
export type Ok<T> = { ok: true } & T;
export type Result<T> = Ok<T> | Refusal;

/**
 * The one refusal this module invents rather than receives.
 *
 * THIS COMMENT USED TO SAY `/auth` ALWAYS 404s. It did when it was written and
 * it does not now — `tools/upload_app.py` calls `_auth.install(app)`, and
 * `tests/test_auth.py` checks that calling it twice cannot mount two copies. A
 * comment asserting a route is dead, sitting above the module that talks to it,
 * is worse than no comment: the next reader debugging a sign-in starts from a
 * false premise this file handed them.
 *
 * The constant stays, because a 404 is still POSSIBLE and still needs its own
 * sentence: a till running an older tree, or a deployment that mounts a subset
 * of the routers. What it must not be is reported as a refusal the server
 * reasoned about, because no server saw it.
 */
export const NOT_MOUNTED = 'auth_routes_not_mounted_on_this_till';
const NOT_MOUNTED_DETAIL =
  'This till answered 404 for /auth, so it is not running the accounts router. '
  + 'Accounts, sessions and the guard all live in gawaah/auth.py; on a till that '
  + 'does not include it there is nothing here to sign in to, and every screen '
  + 'stays open.';

async function send<T>(url: string, init?: RequestInit): Promise<Result<T>> {
  let res: Response;
  try {
    res = await fetch(url, {
      cache: 'no-store',
      // The session is an HttpOnly cookie. Without this the browser is free to
      // leave it behind and every signed-in request looks like a stranger's.
      credentials: 'same-origin',
      ...init,
    });
  } catch (e) {
    // The network, not the product. They need different fixes, so say which.
    return {
      ok: false,
      reason: 'the counter could not reach its own server',
      detail: String(e),
      status: 0,
    };
  }

  let body: Record<string, unknown>;
  try {
    body = (await res.json()) as Record<string, unknown>;
  } catch {
    return {
      ok: false,
      reason: res.status === 404
        ? NOT_MOUNTED
        : `server replied ${res.status} with something that was not JSON`,
      detail: res.status === 404 ? NOT_MOUNTED_DETAIL : undefined,
      status: res.status,
    };
  }

  // The guard in gawaah/auth.py raises an HTTPException whose `detail` IS a
  // refusal body. With `auth.install(app)` called it arrives flat like every
  // other refusal here; without it, Starlette nests the same fields one level
  // down. Unwrap that case rather than showing a shopkeeper `[object Object]`.
  const nested = body.detail;
  if (
    body.ok === undefined
    && nested !== null && typeof nested === 'object' && !Array.isArray(nested)
    && (nested as Record<string, unknown>).ok === false
  ) {
    body = nested as Record<string, unknown>;
  }

  // An explicit `ok` is authoritative; then the HTTP status. FastAPI's own
  // failures carry `{"detail": ...}` with no `ok` at all, and a rule that read
  // only the body would file a crash as a SUCCESS and hand the caller an
  // object whose every field is undefined.
  if (body.ok === undefined) {
    if (!res.ok) {
      // A 404 with FastAPI's own `{"detail":"Not Found"}` is not a refusal, it
      // is the till having no /auth routes mounted at all. Saying "the server
      // refused with HTTP 404" would send somebody looking for a bug in their
      // password.
      if (res.status === 404) {
        return { ok: false, reason: NOT_MOUNTED, detail: NOT_MOUNTED_DETAIL, status: 404 };
      }
      return {
        ok: false,
        reason: `the server refused with HTTP ${res.status}`,
        detail: typeof body.detail === 'string'
          ? body.detail
          : JSON.stringify(body.detail ?? body),
        status: res.status,
      };
    }
    return { ...body, ok: true } as unknown as Result<T>;
  }

  if (body.ok === false) {
    return {
      ok: false,
      reason: typeof body.reason === 'string' ? body.reason : 'auth_refused',
      detail: typeof body.detail === 'string' ? body.detail : undefined,
      status: res.status,
    };
  }
  return body as unknown as Result<T>;
}

const post = (body: unknown): RequestInit => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
});

/* ------------------------------------------------------------- the shapes -- */

/** `GET /auth/status` — what the lock is doing. Names no person. */
export interface AuthStatus {
  accounts: number;
  /** False means the accounts file could not be read. Not the same as zero. */
  store_readable: boolean;
  signup_open: boolean;
  signup_needs_invite: boolean;
  /** Whether anything is actually being checked. Off by default this session. */
  enforced: boolean;
  switch: string;
  session_hours_switch: string;
  open_paths_switch: string;
  session_seconds: number;
  open_paths: string[];
  signed_in: boolean;
  rate_limit: { attempts: number; window_s: number; lock_s: number };
  /** The server's own sentence about the state it is in. Shown verbatim. */
  note: string;
}

/** The four fields an account is allowed to say about itself out loud. */
export interface Account {
  account_id: string;
  name: string;
  phone: string;
  /** `owner` or `staff`. A RECORD, NOT A PERMISSION — nothing reads it. */
  role: string;
  created_at: string;
}

export interface Me {
  signed_in: true;
  account: Account;
  session: { created_at: string; expires_at: string; expires_in_s: number };
  enforced: boolean;
}

export interface SignedIn {
  signed_in: true;
  account: Account;
  expires_at: string;
  expires_in_s?: number;
  /** Only on sign-up: this account was the first on the counter. */
  first_account?: boolean;
  enforced?: boolean;
  audited: boolean;
  note: string;
}

export interface SignedOut {
  signed_in: false;
  /** False means there was no session on the request — signing out achieved it anyway. */
  cleared: boolean;
  audited: boolean;
  note: string;
}

export interface Invitation {
  /** Legible exactly once. The server keeps only a hash and cannot show it again. */
  invite: string;
  expires_at: string;
  single_use: boolean;
  audited: boolean;
  note: string;
}

/* ------------------------------------------------------------- the calls -- */

export const status = () => send<AuthStatus>('/auth/status');
export const me = () => send<Me>('/auth/me');

export const signIn = (phone: string, password: string) =>
  send<SignedIn>('/auth/signin', post({ phone, password }));

export interface SignUpForm {
  name: string;
  phone: string;
  password: string;
  /** Required once this counter has an account. Absent for the first one. */
  invite?: string;
}

export const signUp = (form: SignUpForm) =>
  send<SignedIn>('/auth/signup', post(
    form.invite
      ? { name: form.name, phone: form.phone, password: form.password, invite: form.invite }
      : { name: form.name, phone: form.phone, password: form.password },
  ));

export const signOut = () => send<SignedOut>('/auth/signout', post({}));

export const mintInvite = () => send<Invitation>('/auth/invite', post({}));

/* ----------------------------------------------- who is signed in, shared -- */

/**
 * Two places on screen show the same fact — the account screen and the menu in
 * the top bar — and they are mounted by different parents. Signing out in one
 * has to be true in the other immediately, or the bar keeps a name on it for a
 * session that no longer exists, which is the worst possible thing for this
 * particular widget to be wrong about.
 *
 * So: a subscription, not a poll and not a second copy of the state. Nothing is
 * cached here; a listener is told to go and ask the server again.
 */
type Listener = () => void;
const listeners = new Set<Listener>();

export function onAuthChanged(fn: Listener): () => void {
  listeners.add(fn);
  return () => { listeners.delete(fn); };
}

/** Call after any sign-in, sign-up or sign-out. */
export function authChanged(): void {
  for (const fn of [...listeners]) fn();
}

/* ---------------------------------------------- the server's own rules, mirrored -- */

/**
 * MIRRORS OF `gawaah/auth.py`. They exist so a form can say what is wrong
 * before spending a round trip on it, and they decide nothing: the server
 * re-checks every one and its refusal is what gets shown.
 */
export const MIN_PASSWORD = 8;
export const MAX_PASSWORD = 256;
export const MAX_NAME = 80;
export const MIN_PHONE_DIGITS = 7;
export const MAX_PHONE_DIGITS = 15;

/**
 * The digits a phone number comes down to, as `normalise_phone` in auth.py
 * files them: a leading `91` on twelve digits and a leading `0` on eleven are
 * dropped. India-shaped, deliberately, and stated as such there.
 *
 * Used here ONLY to count digits for the hint under the field. The account is
 * keyed on the server's own version of this, never on ours.
 */
export function phoneDigits(raw: string): string {
  let digits = (raw || '').replace(/\D/g, '');
  if (digits.length === 12 && digits.startsWith('91')) digits = digits.slice(2);
  else if (digits.length === 11 && digits.startsWith('0')) digits = digits.slice(1);
  return digits;
}

/* ------------------------------------------------------------ formatting -- */

/** `43200` -> `12 hours`. Whole units only; nobody reads 11.97 hours. */
export function forHowLong(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return 'no time at all';
  const s = Math.floor(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (h >= 1 && m >= 1) return `${h}h ${m}m`;
  if (h >= 1) return `${h} hour${h === 1 ? '' : 's'}`;
  if (m >= 1) return `${m} minute${m === 1 ? '' : 's'}`;
  return `${s} second${s === 1 ? '' : 's'}`;
}

/** An ISO instant as the clock on the wall reads it. Never re-parsed. */
export function whenever(iso: string | undefined | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

/** The one or two letters a face gets in the top bar. Never a photograph. */
export function initialsOf(name: string): string {
  const parts = (name || '').trim().split(/\s+/).filter(Boolean);
  const first = parts[0];
  if (!first) return '?';
  const last = parts.length > 1 ? parts[parts.length - 1] : undefined;
  const a = first.slice(0, 1);
  const b = last ? last.slice(0, 1) : '';
  return (a + b).toUpperCase();
}
