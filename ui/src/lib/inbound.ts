/**
 * WHY is the pay screen still waiting?
 *
 * There are two answers and the screen used to give the identical spinner for
 * both: the customer has not paid yet, or nothing can reach this counter at
 * all. The second one never resolves on its own, and it is what actually
 * happened — cloudflared's quick tunnel had been revoked and was looping on
 * "Unauthorized: Tunnel not found" for hours, so Razorpay's callback had
 * nowhere to land. A payment settled at the gateway and the till span for 78
 * seconds saying "AWAITING_SETTLEMENT".
 *
 * The distinguishing fact is liveness, not payment: has ANY callback reached
 * the money service since this link was minted? Rejected ones count — a POST
 * with a bad signature still proves the path is open. So this can only ever
 * produce a DIAGNOSIS. It never asserts that money did or did not move, and
 * nothing it returns can turn a screen green.
 */

export interface InboundFacts {
  /** Every callback the money service has received, including rejected ones. */
  webhooks_seen?: number;
  /** ISO 8601, or null if it has never received one. */
  last_webhook_at?: string | null;
}

export interface InboundDiagnosis {
  seen: number;
  lastAt: string | null;
  waitedS: number;
}

/**
 * @param startedAt  when this link was minted, ms since epoch
 * @param now        ms since epoch
 * @param quietMs    how long to wait before saying anything about why
 */
export function diagnoseInbound(
  facts: InboundFacts | null | undefined,
  startedAt: number,
  now: number,
  quietMs: number,
): InboundDiagnosis | null {
  if (!facts) return null;
  // Do not accuse a tunnel while a customer is still opening their UPI app.
  if (now - startedAt <= quietMs) return null;
  const lastMs = facts.last_webhook_at ? Date.parse(facts.last_webhook_at) : NaN;
  // Something arrived AFTER we minted, so the path is open and we are simply
  // waiting on this particular payment. Say nothing.
  if (Number.isFinite(lastMs) && lastMs >= startedAt) return null;
  return {
    seen: facts.webhooks_seen ?? 0,
    lastAt: facts.last_webhook_at ?? null,
    waitedS: Math.round((now - startedAt) / 1000),
  };
}

/** "two days ago", "14 minutes ago" — a timestamp nobody can parse at a counter. */
export function ago(iso: string, now: number = Date.now()): string {
  const ms = now - Date.parse(iso);
  if (!Number.isFinite(ms)) return 'at an unreadable time';
  const m = Math.floor(ms / 60_000);
  if (m < 1) return 'less than a minute ago';
  if (m < 60) return `${m} minute${m === 1 ? '' : 's'} ago`;
  const h = Math.floor(m / 60);
  if (h < 48) return `${h} hour${h === 1 ? '' : 's'} ago`;
  return `${Math.floor(h / 24)} days ago`;
}
