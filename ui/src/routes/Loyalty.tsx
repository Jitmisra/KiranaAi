import { useCallback, useEffect, useState } from 'react';
import * as ly from '../lib/loyaltyapi';
import { money, when } from '../lib/manageapi';
import {
  Button, Card, Empty, Field, Input, KV, LoadingCard, Modal, Pill, Refusal, Skeleton, Table,
  Verdict, type Column,
} from '../components/ui';
import '../styles/loyalty.css';

/**
 * Loyalty — points on money that arrived.
 *
 * EVERY NUMBER ON THIS SCREEN WAS DERIVED BY THE SERVER from the audit chain at
 * the moment it was asked for. The page multiplies nothing: a balance, what it
 * is worth, what a proposal takes off a bill — each is read from the response
 * and formatted. If a figure is missing the screen says so rather than showing
 * a plausible zero.
 *
 * WHAT EARNS. A bill earns when the gateway's signature-verified webhook has
 * settled it, and only then. A link that was sent and never paid earns
 * nothing; the ledger below lists that bill anyway, with the reason, because a
 * customer asking "where are my points" deserves the answer "the money never
 * arrived" and not a blank row.
 *
 * WHAT THIS PAGE CANNOT DO. A redemption here is a PROPOSAL. Nothing leaves the
 * balance until a bill's session id is named against it, and even then the
 * money service re-prices the basket from its own tables before it mints. The
 * server sends the list of what the till has to do with a proposal, and it is
 * printed as it came — the last item on it is a limit, not a feature.
 *
 * Colour: green means a webhook settled the bill; amber means the counter is
 * waiting on one. A point balance is ink, because a point is not a rupee.
 */

type Err = { reason: string; detail?: string };

function n(v: number): string {
  return v.toLocaleString('en-IN');
}

/* --------------------------------------------------------------------------
   WHICH FIELD A REFUSAL BELONGS TO.

   `/loyalty/*` refuses by NAME, and every name says which thing was wrong.
   These sets route the server's own reason back to the box the shopkeeper
   typed it in, so a refused phone number is answered under the phone number
   and not in a panel below the fold. Nothing here rewrites a refusal: the
   reason and the detail are printed exactly as they came, in the field.

   A reason that is in none of these sets is not about a field — a broken
   chain, an unwritable file — and stays a `Refusal` block on the screen.
   -------------------------------------------------------------------------- */

const RULE_REASONS: ReadonlySet<string> = new Set([
  'rule_missing', 'rule_not_a_whole_number', 'rule_out_of_range',
]);
const PHONE_REASONS: ReadonlySet<string> = new Set([
  'phone_missing', 'phone_not_a_number', 'phone_too_short', 'phone_too_long',
]);
const SESSION_REASONS: ReadonlySet<string> = new Set([
  'session_id_missing', 'session_id_malformed',
  'bill_already_credited_to_another_number', 'bill_already_settled',
  'redemption_already_applied',
]);
const POINTS_REASONS: ReadonlySet<string> = new Set([
  'points_missing', 'points_not_a_whole_number', 'points_not_positive',
  'points_beyond_this_counter', 'redemption_exceeds_balance',
]);

/** The server's refusal, verbatim, sized to sit under the control it is about. */
function FieldWords({ e }: { e: Err }) {
  return (
    <>
      <span className="mono">{e.reason}</span>
      {e.detail ? <> — {e.detail}</> : null}
    </>
  );
}

/** The refusal if it belongs to this field, and nothing otherwise. */
function forField(e: Err | null, reasons: ReadonlySet<string>) {
  return e && reasons.has(e.reason) ? <FieldWords e={e} /> : undefined;
}

/** The rule refusals name their own key in the detail, so each box gets its own. */
function forRule(e: Err | null, key: 'points_per_rupee' | 'paise_per_point') {
  if (!e || !RULE_REASONS.has(e.reason)) return undefined;
  const detail = e.detail ?? '';
  const other = key === 'points_per_rupee' ? 'paise_per_point' : 'points_per_rupee';
  // `rule_missing` names both keys; the others name exactly one.
  if (detail.includes(key) || (!detail.includes(key) && !detail.includes(other))) {
    return <FieldWords e={e} />;
  }
  return undefined;
}

/** True when a refusal has already been said beside a field. */
function routed(e: Err | null, ...sets: ReadonlySet<string>[]) {
  return !!e && sets.some((s) => s.has(e.reason));
}

/** What the chain says about a bill, as a pill. Green ONLY for settled. */
function BillPill({ bill }: { bill: ly.BillState | null }) {
  if (!bill || !bill.found) return <Pill tone="off">not in the ledger</Pill>;
  if (bill.settled) return <Pill tone="ok" dot>settled</Pill>;
  if (bill.minted) return <Pill tone="amb">link sent</Pill>;
  if (bill.closed) return <Pill tone="amb">closed, no link</Pill>;
  return <Pill tone="off">open</Pill>;
}

function TillMust({ items }: { items: string[] }) {
  return (
    <ol className="ly-must">
      {items.map((s, i) => <li key={i}>{s}</li>)}
    </ol>
  );
}

export default function Loyalty() {
  /** Bumped whenever something has made every derived figure below stale. */
  const [derived, setDerived] = useState(0);

  /* ---------------------------------------------------------- the rule -- */
  const [rules, setRules] = useState<ly.RulesView | null>(null);
  const [rulesErr, setRulesErr] = useState<Err | null>(null);
  const [rulesLoading, setRulesLoading] = useState(true);
  const [ppr, setPpr] = useState('');
  const [ppp, setPpp] = useState('');
  const [saving, setSaving] = useState(false);
  const [saveErr, setSaveErr] = useState<Err | null>(null);
  /** What the save actually did, from the server's own answer. */
  const [saved, setSaved] = useState<ly.RulesView | null>(null);

  const loadRules = useCallback(async () => {
    const r = await ly.rules();
    if (r.ok) {
      setRules(r);
      setRulesErr(null);
      setPpr(String(r.rules.points_per_rupee));
      setPpp(String(r.rules.paise_per_point));
    } else {
      setRulesErr(r);
    }
    setRulesLoading(false);
  }, []);

  const saveRules = useCallback(async () => {
    setSaving(true);
    setSaveErr(null);
    setSaved(null);
    // Whole numbers only. Anything else is sent as typed and the server
    // refuses it by name — the page does not quietly round.
    const a = /^\d+$/.test(ppr.trim()) ? Number(ppr.trim()) : (ppr as unknown as number);
    const b = /^\d+$/.test(ppp.trim()) ? Number(ppp.trim()) : (ppp as unknown as number);
    const r = await ly.setRules(a, b);
    setSaving(false);
    if (r.ok) {
      setRules(r);
      setRulesErr(null);
      setSaved(r);
      // A rule that has just been turned on or off changes what every panel
      // below this one is allowed to say — what a point is worth, whether
      // anything can be redeemed — so they are re-derived rather than left
      // describing the rule that was in force a second ago. Done through a
      // counter and an effect at the bottom of this component, because the
      // functions that do the re-reading are declared below this one.
      setDerived((k) => k + 1);
    } else {
      setSaveErr(r);
    }
  }, [ppr, ppp]);

  /* ------------------------------------------------------------ lookup -- */
  const [phone, setPhone] = useState('');
  const [looking, setLooking] = useState(false);
  const [lookErr, setLookErr] = useState<Err | null>(null);
  const [account, setAccount] = useState<ly.Ledger | null>(null);

  const lookUp = useCallback(async (p?: string) => {
    const target = (p ?? phone).trim();
    if (!target) return;
    setLooking(true);
    setLookErr(null);
    setProposal(null);
    setProposeErr(null);
    const r = await ly.ledger(target);
    setLooking(false);
    if (r.ok) {
      setAccount(r);
    } else {
      setAccount(null);
      setLookErr(r);
    }
  }, [phone]);

  /* ---------------------------------------------------------- redeeming -- */
  const [pts, setPts] = useState('');
  const [proposing, setProposing] = useState(false);
  const [proposeErr, setProposeErr] = useState<Err | null>(null);
  const [proposal, setProposal] = useState<ly.Proposal | null>(null);
  const [applySid, setApplySid] = useState('');
  const [applying, setApplying] = useState(false);
  const [applyErr, setApplyErr] = useState<Err | null>(null);
  /** Putting a redemption on a bill is the moment points LEAVE a balance, and
      there is no route in this module that puts them back. So it asks first. */
  const [confirmApply, setConfirmApply] = useState(false);

  const propose = useCallback(async () => {
    if (!account) return;
    setProposing(true);
    setProposeErr(null);
    setApplyErr(null);
    const count = /^\d+$/.test(pts.trim()) ? Number(pts.trim()) : (pts as unknown as number);
    const r = await ly.redeem(account.phone, count);
    setProposing(false);
    if (r.ok) {
      setProposal(r);
      // The proposal is listed in the ledger as "proposed, not deducted".
      const again = await ly.ledger(account.phone);
      if (again.ok) setAccount(again);
    } else {
      setProposal(null);
      setProposeErr(r);
    }
  }, [account, pts]);

  const apply = useCallback(async () => {
    if (!proposal) return;
    setConfirmApply(false);
    setApplying(true);
    setApplyErr(null);
    const r = await ly.apply(proposal.redemption.redemption_id, applySid.trim());
    setApplying(false);
    if (r.ok) {
      setProposal(r);
      setPts('');
      const again = await ly.ledger(proposal.redemption.phone);
      if (again.ok) setAccount(again);
      void loadMembers();
    } else {
      setApplyErr(r);
    }
  }, [proposal, applySid]);

  /* ---------------------------------------------------------- attaching -- */
  const [atSid, setAtSid] = useState('');
  const [atPhone, setAtPhone] = useState('');
  const [attaching, setAttaching] = useState(false);
  const [attachErr, setAttachErr] = useState<Err | null>(null);
  const [attached, setAttached] = useState<ly.Attached | null>(null);

  const attach = useCallback(async () => {
    setAttaching(true);
    setAttachErr(null);
    setAttached(null);
    const r = await ly.attach(atSid.trim(), atPhone.trim());
    setAttaching(false);
    if (r.ok) {
      setAttached(r);
      void loadMembers();
      if (account && account.phone === r.phone) void lookUp(r.phone);
    } else {
      setAttachErr(r);
    }
  }, [atSid, atPhone, account, lookUp]);

  /* ------------------------------------------------------------ members -- */
  const [members, setMembers] = useState<ly.Members | null>(null);
  const [membersErr, setMembersErr] = useState<Err | null>(null);
  const [membersLoading, setMembersLoading] = useState(true);
  const [health, setHealth] = useState<ly.Health | null>(null);
  /** A refused health read used to be dropped on the floor and the card simply
      never appeared — which reads as a screen with one card fewer, not as a
      question that went unanswered. */
  const [healthErr, setHealthErr] = useState<Err | null>(null);
  const [healthLoading, setHealthLoading] = useState(true);

  const loadMembers = useCallback(async () => {
    setMembersLoading(true);
    setHealthLoading(true);
    const [m, h] = await Promise.all([ly.members(), ly.health()]);
    if (m.ok) {
      setMembers(m);
      setMembersErr(null);
    } else {
      setMembersErr(m);
    }
    if (h.ok) { setHealth(h); setHealthErr(null); } else { setHealth(null); setHealthErr(h); }
    setMembersLoading(false);
    setHealthLoading(false);
  }, []);

  useEffect(() => {
    void loadRules();
    void loadMembers();
  }, [loadRules, loadMembers]);

  /* Something changed the rule, so everything derived from it is re-read. It
     lives here, below every function it calls, so nothing is referenced before
     it exists. `derived` starts at 0 and this does nothing on the first pass. */
  useEffect(() => {
    if (derived === 0) return;
    void loadMembers();
    if (account) void lookUp(account.phone);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [derived]);

  const on = !!rules?.rules.on;
  /** Nobody has been given a point yet, however many numbers the chain knows. */
  const noPointsAnywhere =
    !!members && members.members.length > 0 && members.members.every((m) => m.balance_points === 0);

  /* ------------------------------------------------------------- tables -- */

  /* The time is its own column on a desk and a third line of "What" on a
     phone: a 9em column of "02 Sept, 09:18 am" wraps to four lines at 390px
     and pushes the points off the edge. */
  const ledgerCols: ReadonlyArray<Column<ly.Entry>> = [
    {
      key: 'when', head: 'When', width: '9.5em', drop: true,
      cell: (e) => <span className="muted ly-when">{when(e.at)}</span>,
    },
    {
      key: 'what', head: 'What',
      cell: (e) => {
        const id = e.kind === 'earn' ? (e.order_id ?? e.session_id) : (e.session_id ?? e.redemption_id);
        return (
          <div className="ly-what">
            <span>
              {e.kind === 'earn'
                ? <>{e.source === 'storefront_order' ? 'Storefront order' : 'Counter bill'}
                    {e.bill.settled_paise !== null && <> · {money(e.bill.settled_paise)}</>}</>
                : <>Redeemed · {money(e.value_paise)} off{e.session_id ? ' a bill' : ''}</>}
            </span>
            <span className="id" title={id ?? undefined}>{id}</span>
            <span className="ly-when-sm">{when(e.at)}</span>
          </div>
        );
      },
    },
    {
      key: 'state', head: 'Bill', width: '9em',
      cell: (e) => e.kind === 'earn'
        ? <BillPill bill={e.bill} />
        : e.applied ? <Pill tone="code">on a bill</Pill> : <Pill tone="off">proposed</Pill>,
    },
    {
      key: 'pts', head: 'Points', num: true, width: '6em',
      cell: (e) => e.kind === 'earn'
        ? <span className={e.points ? 'ly-pts' : 'ly-pts zero'}>{e.points ? `+${n(e.points)}` : '0'}</span>
        : <span className={e.applied ? 'ly-pts' : 'ly-pts zero'}>−{n(e.points)}</span>,
    },
    {
      key: 'why', head: 'Why', drop: true,
      cell: (e) => <span className="ly-why">{e.said}</span>,
    },
  ];

  const memberCols: ReadonlyArray<Column<ly.Member>> = [
    {
      key: 'phone', head: 'Number',
      cell: (m) => <span className="ly-phone">{m.phone}</span>,
    },
    {
      key: 'bal', head: 'Balance', num: true,
      cell: (m) => <span className="ly-pts">{n(m.balance_points)}</span>,
    },
    {
      key: 'worth', head: 'Worth', num: true,
      cell: (m) => money(m.balance_value_paise),
    },
    { key: 'earned', head: 'Earned', num: true, drop: true, cell: (m) => n(m.earned_points) },
    { key: 'redeemed', head: 'Redeemed', num: true, drop: true, cell: (m) => n(m.redeemed_points) },
    {
      key: 'bills', head: 'Bills', num: true, drop: true,
      cell: (m) => <>{n(m.bills_settled)} settled{m.bills_awaiting ? <span className="muted"> · {n(m.bills_awaiting)} waiting</span> : null}</>,
    },
  ];

  return (
    <div className="ly-page">
      <div className="page-head">
        <h1>Loyalty</h1>
        <p>
          Points on money that arrived. A bill earns when the gateway&rsquo;s signed webhook settles
          it, at the rule in force that day. A link that was sent and never paid earns nothing, and
          the ledger says so.
        </p>
      </div>

      <div className="ly-grid">
        {/* ------------------------------------------------------ the rule */}
        <Card
          title="The rule"
          sub="whole points per whole rupee, and what a point is worth"
          aside={<Pill tone={on ? 'code' : 'off'}>{rulesLoading ? '…' : on ? 'ON' : 'OFF'}</Pill>}
        >
          {rulesLoading && <LoadingCard lines={3} label="Loading the rule" />}
          {rulesErr && (
            <Refusal
              reason={rulesErr.reason}
              detail={rulesErr.detail}
              action={<Button size="sm" onClick={() => void loadRules()}>TRY AGAIN</Button>}
            />
          )}
          {!rulesLoading && !rulesErr && (
            <>
              {/* THE STATE THIS SHOP IS ACTUALLY IN, said once, at the top.
                  Off is not a failure and not an abstention, so it is neither
                  amber nor red: it is blue, the machine reporting its own
                  setting. Until it is on, every panel below it is describing
                  a scheme that awards nothing. */}
              {!on && (
                <Verdict tone="info" title="The scheme is off, so no bill earns anything">
                  Points per rupee is {n(rules?.rules.points_per_rupee ?? 0)}. Set it to 1 and every
                  whole rupee a settled bill was worth becomes a point; paise per point is what one
                  point then takes off a later bill. Nothing already settled is back-dated — a bill
                  keeps the rule that was in force the day it settled, so turning this on today
                  earns nothing on yesterday.
                </Verdict>
              )}

              <div className="ly-rule-form">
                <Field
                  label="Points per rupee settled"
                  htmlFor="ly-ppr"
                  sub="0 turns the scheme off"
                  error={forRule(saveErr, 'points_per_rupee')}
                >
                  <Input id="ly-ppr" type="text" inputMode="numeric" placeholder="1"
                         bad={!!forRule(saveErr, 'points_per_rupee')}
                         value={ppr} onChange={(e) => setPpr(e.target.value)} />
                </Field>
                <Field
                  label="Paise per point"
                  htmlFor="ly-ppp"
                  sub="what one point takes off a bill"
                  error={forRule(saveErr, 'paise_per_point')}
                >
                  <Input id="ly-ppp" type="text" inputMode="numeric" placeholder="25"
                         bad={!!forRule(saveErr, 'paise_per_point')}
                         value={ppp} onChange={(e) => setPpp(e.target.value)} />
                </Field>
              </div>

              {rules?.example ? (
                <div className="ly-example">
                  <span>{n(rules.example.points)} points buy</span>
                  <b>{money(rules.example.value_paise)}</b>
                </div>
              ) : (
                <div className="ly-example muted">
                  <span>A point is worth nothing yet. Save a rule to see what 100 points buy.</span>
                </div>
              )}

              <div className="btn-row">
                {/* `loading` hides the label and disables the button, so the
                    sentence beside it is where "what is happening" lives. */}
                <Button variant="primary" loading={saving} onClick={() => void saveRules()}>
                  SAVE THE RULE
                </Button>
                {saving && (
                  <span className="muted" aria-live="polite">
                    Writing both numbers to this shop’s own file…
                  </span>
                )}
                {!saving && rules?.rules.set_at && (
                  <span className="muted">set {when(rules.rules.set_at)}</span>
                )}
              </div>

              {/* Only what has NOT already been said under a box. A refusal
                  shown twice teaches a reader to skip one of them. */}
              {saveErr && !routed(saveErr, RULE_REASONS) && (
                <div style={{ marginTop: 12 }}>
                  <Refusal reason={saveErr.reason} detail={saveErr.detail} />
                </div>
              )}

              {/* A press has to have an answer. INFO, not green: saving a
                  setting is not a payment settling. */}
              {saved && !saveErr && (
                <div style={{ marginTop: 12 }}>
                  <Verdict
                    tone="info"
                    title={saved.rules.on
                      ? `Saved. ${n(saved.rules.points_per_rupee)} point${saved.rules.points_per_rupee === 1 ? '' : 's'} per rupee settled.`
                      : 'Saved. The scheme is off.'}
                  >
                    {saved.was && (saved.was.points_per_rupee !== saved.rules.points_per_rupee
                      || saved.was.paise_per_point !== saved.rules.paise_per_point) ? (
                        <>
                          Was {n(saved.was.points_per_rupee)} per rupee at {n(saved.was.paise_per_point)}{' '}
                          paise a point; now {n(saved.rules.points_per_rupee)} at{' '}
                          {n(saved.rules.paise_per_point)}.{' '}
                        </>
                      ) : <>Nothing on it had changed. </>}
                    {saved.audited === false
                      ? 'The rule was written, but the change could not be added to the loyalty chain.'
                      : 'The change is on this shop’s own hash-chained log.'}
                  </Verdict>
                </div>
              )}

              <p className="ly-note">
                Every bill keeps the rule that was in force when it settled. Changing the rule today
                does not rewrite yesterday&rsquo;s balances, and a bill settled before any rule was
                set earns nothing.
              </p>
            </>
          )}
        </Card>

        {/* ------------------------------------------------------ look up */}
        <Card title="A customer's points" sub="looked up by the number they say at the counter">
          <form
            className="ly-row"
            onSubmit={(e) => { e.preventDefault(); void lookUp(); }}
          >
            <Field label="Phone number" htmlFor="ly-phone" error={forField(lookErr, PHONE_REASONS)}>
              <Input id="ly-phone" type="tel" inputMode="tel" placeholder="98765 43210"
                     bad={!!forField(lookErr, PHONE_REASONS)}
                     autoComplete="off" value={phone} onChange={(e) => setPhone(e.target.value)} />
            </Field>
            <Button
              type="submit" variant="primary" loading={looking} disabled={!phone.trim()}
              title={phone.trim() ? undefined : 'Type the number the customer said at the counter first.'}
            >
              LOOK UP
            </Button>
          </form>
          {looking && (
            <p className="muted" aria-live="polite" style={{ marginTop: -8 }}>
              Deriving the balance from the chain…
            </p>
          )}

          {lookErr && !routed(lookErr, PHONE_REASONS) && (
            <div style={{ marginTop: 16 }}>
              <Refusal reason={lookErr.reason} detail={lookErr.detail} />
            </div>
          )}

          {looking && !account && (
            <div style={{ marginTop: 16 }}>
              <LoadingCard lines={4} label="Deriving the balance" />
            </div>
          )}

          {account && (
            <div className="ly-result">
              <div>
                <div className="ly-balance">
                  <span className="n">{n(account.balance_points)}</span>
                  <span className="u">points</span>
                  <span className="w">
                    {account.rules.paise_per_point > 0
                      ? <>worth <b>{money(account.balance_value_paise)}</b> at today&rsquo;s rule, for {account.phone}</>
                      : <>for {account.phone}. A point is worth nothing until paise per point is set.</>}
                  </span>
                </div>
              </div>

              <div className="ly-parts">
                <div>
                  <span className="l">Earned</span>
                  <span className="v">{n(account.earned_points)}</span>
                  <span className="s">{n(account.bills_settled)} settled bill{account.bills_settled === 1 ? '' : 's'} · {money(account.settled_paise)}</span>
                </div>
                <div>
                  <span className="l">Redeemed</span>
                  <span className="v">{n(account.redeemed_points)}</span>
                  <span className="s">put on a bill</span>
                </div>
                <div>
                  <span className="l">Proposed</span>
                  <span className="v">{n(account.proposed_points)}</span>
                  <span className="s">not deducted</span>
                </div>
                <div>
                  <span className="l">Waiting</span>
                  <span className="v">{n(account.bills_awaiting + account.bills_not_in_ledger)}</span>
                  <span className="s">bills not settled yet</span>
                </div>
              </div>

              {!account.chain.ok && (
                <Verdict tone="red" title="The audit chain is broken">
                  {account.chain.error ?? 'a line did not verify'}. Bills after the break are not
                  counted, because a line whose hash does not recompute is not evidence.
                </Verdict>
              )}

              {/* ------------------------------------------------ redeem */}
              <div className="ly-redeem">
                <div className="ly-redeem-h">Redeem</div>
                {!on || account.rules.paise_per_point <= 0 ? (
                  <p className="muted">
                    {on
                      ? 'A point is worth 0 paise, so there is nothing to take off. Set paise per point first.'
                      : 'No rule is set, so nothing has been earned and nothing can be redeemed.'}
                  </p>
                ) : account.balance_points <= 0 ? (
                  <p className="muted">Nothing to redeem: the balance is {n(account.balance_points)}.</p>
                ) : (
                  <form className="ly-row" onSubmit={(e) => { e.preventDefault(); void propose(); }}>
                    <Field
                      label={`Points to redeem, up to ${n(account.balance_points)}`}
                      htmlFor="ly-pts"
                      error={forField(proposeErr, POINTS_REASONS)}
                    >
                      <Input id="ly-pts" type="text" inputMode="numeric" placeholder="50"
                             bad={!!forField(proposeErr, POINTS_REASONS)}
                             value={pts} onChange={(e) => setPts(e.target.value)} />
                    </Field>
                    <Button
                      type="submit" loading={proposing} disabled={!pts.trim()}
                      title={pts.trim() ? undefined : 'Say how many points first. Nothing is deducted by proposing.'}
                    >
                      PROPOSE
                    </Button>
                  </form>
                )}

                {proposeErr && !routed(proposeErr, POINTS_REASONS) && (
                  <div style={{ marginTop: 12 }}>
                    <Refusal reason={proposeErr.reason} detail={proposeErr.detail} />
                  </div>
                )}

                {proposal && (
                  <div className="ly-proposal" style={{ marginTop: 12 }}>
                    <div className="ly-line">
                      <span>
                        {proposal.line.label}
                        <span className="id">{proposal.line.redemption_id}</span>
                      </span>
                      <b>− {money(proposal.line.off_paise)}</b>
                    </div>
                    {proposal.applied ? (
                      <Verdict tone="info" title={`${n(proposal.redemption.points)} points left the balance`}>
                        Recorded against bill <span className="mono">{proposal.redemption.session_id}</span>.
                        Balance {n(proposal.balance_before_points)} → {n(proposal.balance_after_points ?? proposal.balance_before_points)}.
                      </Verdict>
                    ) : (
                      <>
                        <p className="muted">
                          Nothing has been deducted. The till puts this line on a bill and then names
                          the bill&rsquo;s session id; that is when the points leave. Balance would go
                          {' '}{n(proposal.balance_before_points)} → {n(proposal.balance_if_applied_points ?? proposal.balance_before_points)}.
                        </p>
                        <form
                          className="ly-row"
                          onSubmit={(e) => { e.preventDefault(); setConfirmApply(true); }}
                        >
                          <Field
                            label="Bill session id"
                            htmlFor="ly-apply-sid"
                            error={forField(applyErr, SESSION_REASONS)}
                          >
                            <Input id="ly-apply-sid" type="text" placeholder="till_…" autoComplete="off"
                                   bad={!!forField(applyErr, SESSION_REASONS)}
                                   value={applySid} onChange={(e) => setApplySid(e.target.value)} />
                          </Field>
                          <Button
                            type="submit" variant="primary" loading={applying} disabled={!applySid.trim()}
                            title={applySid.trim()
                              ? undefined
                              : 'The till prints a session id on every bill. Type the one this discount goes on.'}
                          >
                            PUT ON THIS BILL
                          </Button>
                        </form>
                        <p className="muted">
                          The till does this itself once it is wired in. From here it is by hand.
                        </p>
                        {applying && (
                          <p className="muted" aria-live="polite">
                            Recording {n(proposal.redemption.points)} points against{' '}
                            <span className="mono">{applySid.trim()}</span>…
                          </p>
                        )}
                        {applyErr && !routed(applyErr, SESSION_REASONS) && (
                          <Refusal reason={applyErr.reason} detail={applyErr.detail} />
                        )}
                      </>
                    )}
                    <div>
                      <div className="ly-redeem-h">What the till must do</div>
                      <TillMust items={proposal.till_must} />
                    </div>
                  </div>
                )}
              </div>

              {/* ------------------------------------------------ ledger */}
              <div>
                <div className="ly-redeem-h">Every bill and redemption</div>
                <Table<ly.Entry>
                  cols={ledgerCols}
                  rows={account.entries}
                  rowKey={(e) => e.kind === 'earn' ? `e-${e.session_id}` : `r-${e.redemption_id}`}
                  loading={looking}
                  maxHeight="420px"
                  label="Loyalty ledger"
                  empty={
                    <Empty title="No bills are tied to this number yet">
                      A storefront order settling under this number earns on its own. A counter bill
                      has no number on it until you tie one below.
                    </Empty>
                  }
                />
              </div>
            </div>
          )}

          {!account && !looking && !lookErr && (
            <div style={{ marginTop: 16 }}>
              <Empty icon={false}>
                Type the customer&rsquo;s number to see what they have earned, redeem points, and
                read why each bill earned what it did.
              </Empty>
            </div>
          )}
        </Card>
      </div>

      {/* ------------------------------------------------------ attach */}
      <Card title="Tie a counter bill to a number" sub="a counter bill has no phone on it unless you type one">
        <p className="ly-lede">
          The till shows a session id on every bill. Type it here with the customer&rsquo;s number
          and the bill earns when it settles — not before. A storefront order already carries a
          number and needs nothing.
        </p>
        <form className="ly-row" onSubmit={(e) => { e.preventDefault(); void attach(); }}>
          <Field label="Session id" htmlFor="ly-at-sid" error={forField(attachErr, SESSION_REASONS)}>
            <Input id="ly-at-sid" type="text" placeholder="till_…" autoComplete="off"
                   bad={!!forField(attachErr, SESSION_REASONS)}
                   value={atSid} onChange={(e) => setAtSid(e.target.value)} />
          </Field>
          <Field label="Phone number" htmlFor="ly-at-phone" error={forField(attachErr, PHONE_REASONS)}>
            <Input id="ly-at-phone" type="tel" inputMode="tel" placeholder="98765 43210" autoComplete="off"
                   bad={!!forField(attachErr, PHONE_REASONS)}
                   value={atPhone} onChange={(e) => setAtPhone(e.target.value)} />
          </Field>
          <Button type="submit" variant="primary" loading={attaching}
                  disabled={!atSid.trim() || !atPhone.trim()}
                  title={atSid.trim() && atPhone.trim()
                    ? undefined
                    : !atSid.trim() && !atPhone.trim()
                      ? 'Both a session id from the bill and the customer’s number are needed.'
                      : !atSid.trim()
                        ? 'The till prints a session id on every bill. Type that one.'
                        : 'Type the number the customer said at the counter.'}>
            TIE IT
          </Button>
        </form>
        {attaching && (
          <p className="muted" aria-live="polite">
            Tying <span className="mono">{atSid.trim()}</span> to {atPhone.trim()}…
          </p>
        )}
        {attachErr && !routed(attachErr, SESSION_REASONS, PHONE_REASONS) && (
          <div style={{ marginTop: 12 }}>
            <Refusal reason={attachErr.reason} detail={attachErr.detail} />
          </div>
        )}
        {attached && (
          <div style={{ marginTop: 12 }}>
            <Verdict
              tone={attached.bill.settled ? 'green' : attached.bill.found ? 'amber' : 'info'}
              title={attached.earns.points
                ? `${n(attached.earns.points)} points for ${attached.phone}`
                : `Tied to ${attached.phone}. Nothing earned yet.`}
            >
              <BillPill bill={attached.bill} />{' '}
              {attached.bill.settled_paise !== null && <>{money(attached.bill.settled_paise)} settled. </>}
              {attached.earns.said}{' '}
              {attached.audited === false && 'The change was saved but could not be written to the loyalty chain.'}
            </Verdict>
          </div>
        )}
      </Card>

      {/* ------------------------------------------------------ members */}
      <Card
        title="Everyone with points"
        sub="derived from the chain each time; no names, no addresses"
        aside={<Pill tone={members?.count ? 'code' : 'off'}>{members ? `${n(members.count)} NUMBER${members.count === 1 ? '' : 'S'}` : '…'}</Pill>}
      >
        {membersErr && (
          <Refusal
            reason={membersErr.reason}
            detail={membersErr.detail}
            action={<Button size="sm" onClick={() => void loadMembers()}>TRY AGAIN</Button>}
          />
        )}
        {/* The chain knows these numbers, and not one of them has a point. That
            is a state worth naming rather than leaving a reader to scan a
            column of zeros for. Blue: it is the machine reporting its own
            setting, not a refusal and not an abstention. */}
        {!membersErr && !membersLoading && noPointsAnywhere && (
          <Verdict
            tone="info"
            title={`${n(members?.count ?? 0)} number${members?.count === 1 ? '' : 's'} on the chain, none with a point yet`}
          >
            {on
              ? 'Every bill against these numbers is still waiting on the gateway’s signed webhook. Points are credited when the money arrives and not when a link is sent.'
              : 'The scheme is off, so nothing has been awarded. Set points per rupee above; bills that settle after that earn at the rule in force on the day they settle.'}
          </Verdict>
        )}
        {!membersErr && (
          <Table<ly.Member>
            cols={memberCols}
            rows={members?.members ?? []}
            rowKey={(m) => m.phone}
            loading={membersLoading}
            onRowClick={(m) => { setPhone(m.phone); void lookUp(m.phone); window.scrollTo({ top: 0 }); }}
            maxHeight="480px"
            label="Everyone with points"
            empty={
              <Empty title="Nobody has points yet">
                {on
                  ? 'The next bill that settles under a number will appear here.'
                  : 'The scheme is off. Set a rule above, then tie bills to numbers.'}
              </Empty>
            }
          />
        )}
        {members?.truncated && (
          <p className="ly-note">Only the first {n(members.members.length)} of {n(members.count)} are shown.</p>
        )}
        {members && !members.chain.orders_readable && (
          <p className="ly-note">
            The storefront&rsquo;s orders could not be read, so numbers that only ever ordered online
            are missing from this list.
          </p>
        )}
      </Card>

      {/* The card is always here now. It used to appear only once the read had
          come back, so a refused read looked like a screen that simply had one
          card fewer — and a broken money chain is the last thing this product
          should be quiet about. */}
      <Card title="Where the points live">
        <p className="ly-lede">
          Balances are derived from the money chain; the rule, which bill belongs to which number,
          and every redemption are written to this shop&rsquo;s own file and chained beside it.
        </p>

        {healthLoading && (
          <div role="status" aria-label="Reading where the points live">
            {[0, 1, 2, 3, 4].map((i) => (
              <div className="kv" key={i} aria-hidden="true">
                <b><Skeleton w={i % 2 ? 86 : 104} h={10} radius={999} /></b>
                <span><Skeleton w={i === 0 ? '78%' : '42%'} h={10} radius={999} /></span>
              </div>
            ))}
          </div>
        )}

        {healthErr && (
          <Refusal
            reason={healthErr.reason}
            detail={healthErr.detail}
            hint="Where the points live could not be read. The balances above were derived by a different request and are not affected by this."
            action={<Button size="sm" onClick={() => void loadMembers()}>TRY AGAIN</Button>}
          />
        )}

        {health && (
          <>
            <KV k="Loyalty file"><span className="mono">{health.file}</span></KV>
            <KV k="On disk">{health.exists ? 'yes' : 'not written yet'}</KV>
            <KV k="Loyalty chain">
              {health.audit.ok ? `${n(health.audit.lines)} lines, verified` : `broken: ${health.audit.error}`}
            </KV>
            <KV k="Money chain">
              {health.money_chain.ok === null ? 'not readable' : health.money_chain.ok ? 'verified' : 'BROKEN'}
            </KV>
            <KV k="Earns on">{health.earns_on}</KV>

            {/* RED, and the only red on this screen: a chain that does not
                recompute is the one thing here that is a refusal rather than a
                setting. Nothing decorative may borrow it. */}
            {(!health.audit.ok || health.money_chain.ok === false) && (
              <Verdict tone="red" title="A chain did not verify">
                {!health.audit.ok && <>The loyalty chain: {health.audit.error}. </>}
                {health.money_chain.ok === false && <>The money chain at {health.money_chain.path} did not verify. </>}
                Balances derived after a break are not evidence, because a line whose hash does not
                recompute is not evidence.
              </Verdict>
            )}
          </>
        )}
      </Card>

      {/* THE ONE PRESS ON THIS SCREEN THAT CANNOT BE TAKEN BACK. Proposing
          deducts nothing and can be done again; applying is the debit, and
          this module has no route that puts points back on a balance. */}
      <Modal
        open={confirmApply}
        onClose={() => setConfirmApply(false)}
        title="Take these points off the balance?"
        size="narrow"
        note="Nothing has been deducted yet."
        foot={
          <>
            <Button variant="ghost" onClick={() => setConfirmApply(false)}>NOT YET</Button>
            <Button variant="primary" onClick={() => void apply()}>PUT IT ON THE BILL</Button>
          </>
        }
      >
        {proposal && (
          <>
            <p>
              {n(proposal.redemption.points)} points come off {proposal.redemption.phone} and{' '}
              {money(proposal.line.off_paise)} goes on bill{' '}
              <span className="mono">{applySid.trim()}</span>. The balance goes{' '}
              {n(proposal.balance_before_points)} →{' '}
              {n(proposal.balance_if_applied_points ?? proposal.balance_before_points)}.
            </p>
            <p className="ly-note">
              There is no route in this counter that puts redeemed points back. If the bill is
              wrong, close this and check the session id on the till first — the same id has to be
              the one the money actually settles against.
            </p>
          </>
        )}
      </Modal>
    </div>
  );
}
