import { describe, it, expect } from 'vitest';
import { diagnoseInbound, ago } from './inbound';

const T0 = Date.parse('2026-08-31T22:00:00.000Z');
const QUIET = 25_000;

describe('diagnoseInbound', () => {
  it('says nothing during the quiet window, however dead the path is', () => {
    expect(diagnoseInbound({ webhooks_seen: 0, last_webhook_at: null }, T0, T0 + 24_000, QUIET))
      .toBeNull();
  });

  it('reports a counter that has never heard a callback', () => {
    const d = diagnoseInbound({ webhooks_seen: 0, last_webhook_at: null }, T0, T0 + 30_000, QUIET);
    expect(d).toEqual({ seen: 0, lastAt: null, waitedS: 30 });
  });

  it('reports a callback that last arrived BEFORE this link was minted', () => {
    // The real failure: 12 webhooks in the log, the newest two days old.
    const d = diagnoseInbound(
      { webhooks_seen: 12, last_webhook_at: '2026-08-29T05:34:30.136+00:00' },
      T0, T0 + 78_000, QUIET,
    );
    expect(d?.seen).toBe(12);
    expect(d?.waitedS).toBe(78);
  });

  it('stays silent once ANYTHING has arrived since the mint — the path is open', () => {
    // Even a rejected callback proves reachability, which is the whole question.
    expect(diagnoseInbound(
      { webhooks_seen: 3, last_webhook_at: new Date(T0 + 5_000).toISOString() },
      T0, T0 + 60_000, QUIET,
    )).toBeNull();
  });

  it('treats an unparseable timestamp as "not heard from"', () => {
    const d = diagnoseInbound({ webhooks_seen: 1, last_webhook_at: 'not a date' }, T0, T0 + 40_000, QUIET);
    expect(d).not.toBeNull();
    expect(d?.seen).toBe(1);
  });

  it('says nothing when the server sent no liveness fields at all', () => {
    // An older money service, or a shape change. Absence is not an accusation.
    expect(diagnoseInbound(null, T0, T0 + 99_000, QUIET)).toBeNull();
  });

  it('a boundary exactly at the quiet window is still quiet', () => {
    expect(diagnoseInbound({ webhooks_seen: 0 }, T0, T0 + QUIET, QUIET)).toBeNull();
    expect(diagnoseInbound({ webhooks_seen: 0 }, T0, T0 + QUIET + 1, QUIET)).not.toBeNull();
  });
});

describe('ago', () => {
  it('reads as a person would say it', () => {
    expect(ago(new Date(T0 - 30_000).toISOString(), T0)).toBe('less than a minute ago');
    expect(ago(new Date(T0 - 60_000).toISOString(), T0)).toBe('1 minute ago');
    expect(ago(new Date(T0 - 14 * 60_000).toISOString(), T0)).toBe('14 minutes ago');
    expect(ago(new Date(T0 - 3 * 3600_000).toISOString(), T0)).toBe('3 hours ago');
    expect(ago('2026-08-29T05:34:30.136+00:00', T0)).toBe('2 days ago');
  });

  it('does not invent a number it cannot compute', () => {
    expect(ago('not a date', T0)).toBe('at an unreadable time');
  });
});
