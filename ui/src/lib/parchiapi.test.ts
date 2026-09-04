import { describe, it, expect } from 'vitest';
import {
  defaultChoices, readyToAccept, bookBody,
  type ParsedLine, type Gate, type Match,
} from './parchiapi';

/**
 * The one decision the browser makes on the photographed bill — may ACCEPT
 * fire — and the body it sends when it does. Both are pure, so both are held
 * here: the gate is the server's and this file can only ADD reasons to say no.
 */

const match = (status: Match['status'], sku: string | null): Match => ({
  status, sku_id: sku, sku_name: sku, score: status === 'none' ? 0 : 700, why: '',
  candidates: sku ? [{ sku_id: sku, name: sku, score: 700, why: '', sell_paise: 1000 }] : [],
  query: '',
});

const line = (i: number, status: ParsedLine['status'], sku: string | null): ParsedLine => ({
  i, name: `LINE ${i + 1}`, qty: 2, rate: '8.20', rate_paise: 820, rate_rupees: '8.20',
  amount: '16.40', amount_paise: 1640, amount_rupees: '16.40', computed_paise: 1640,
  computed_rupees: '16.40',
  arithmetic: status === 'arithmetic_fails' ? 'fails' : status === 'unreadable' ? 'unreadable' : 'ok',
  arithmetic_detail: null,
  match: match(status === 'proposed' ? 'proposed' : status === 'confirm' ? 'confirm' : 'none', sku),
  status,
});

const gate = (ok: boolean): Gate => ({
  ok, reason: ok ? null : 'parchi_arithmetic_refused', detail: ok ? null : 'one paisa',
  failing_lines: ok ? [] : [1], lines_checked: 3, sum_of_lines_paise: 0, subtotal_printed: false,
  subtotal_paise: null, taxes: [], tax_paise: 0, expected_total_paise: 0, printed_total: '0',
  printed_total_paise: 0, rule: '',
});

const LINES = [
  line(0, 'proposed', 'parle_g_biscuit'),
  line(1, 'confirm', 'maggi_noodles_70g'),
  line(2, 'no_match', null),
];
const SUP = { id: null, name: 'Sharma Distributors', phone: '98200 44711' };

describe('the defaults a parse arrives with', () => {
  it('keeps proposed and confirm rows in, leaves the rest out', () => {
    const c = defaultChoices(LINES);
    expect(c[0]).toEqual({ include: true, sku_id: 'parle_g_biscuit', confirmed: true });
    expect(c[1]).toEqual({ include: true, sku_id: 'maggi_noodles_70g', confirmed: false });
    expect(c[2]).toEqual({ include: false, sku_id: null, confirmed: false });
  });

  it('never includes a line the gate failed, whatever it matched', () => {
    const c = defaultChoices([line(0, 'arithmetic_fails', 'parle_g_biscuit')]);
    expect(c[0]!.include).toBe(false);
  });
});

describe('may ACCEPT fire', () => {
  it('never over a refused gate, whatever the person ticked', () => {
    const r = readyToAccept({ gate: gate(false), lines: LINES }, defaultChoices(LINES), SUP);
    expect(r.ready).toBe(false);
    expect(!r.ready && r.why).toMatch(/does not add up/);
  });

  it('not until a confirm row is confirmed', () => {
    const c = defaultChoices(LINES);
    const r = readyToAccept({ gate: gate(true), lines: LINES }, c, SUP);
    expect(!r.ready && r.why).toMatch(/Line 2 .* is a guess/);
    c[1] = { ...c[1]!, confirmed: true };
    expect(readyToAccept({ gate: gate(true), lines: LINES }, c, SUP)).toEqual({ ready: true });
  });

  it('an unticked confirm row does not block', () => {
    const c = defaultChoices(LINES);
    c[1] = { ...c[1]!, include: false };
    expect(readyToAccept({ gate: gate(true), lines: LINES }, c, SUP)).toEqual({ ready: true });
  });

  it('needs a supplier: on file, or a name and a phone', () => {
    const c = defaultChoices(LINES);
    c[1] = { ...c[1]!, confirmed: true };
    const doc = { gate: gate(true), lines: LINES };
    expect(readyToAccept(doc, c, { id: null, name: '', phone: '' })).toMatchObject({ ready: false, why: expect.stringMatching(/which supplier/) });
    expect(readyToAccept(doc, c, { id: null, name: 'X', phone: '' })).toMatchObject({ ready: false, why: expect.stringMatching(/phone/) });
    expect(readyToAccept(doc, c, { id: 'sup_abc', name: '', phone: '' })).toEqual({ ready: true });
  });

  it('needs at least one line, and every kept line needs a product', () => {
    const c = defaultChoices(LINES);
    for (const k of Object.keys(c)) c[Number(k)] = { ...c[Number(k)]!, include: false };
    const doc = { gate: gate(true), lines: LINES };
    expect(!readyToAccept(doc, c, SUP).ready).toBe(true);
    c[2] = { include: true, sku_id: null, confirmed: false };
    const r = readyToAccept(doc, c, SUP);
    expect(!r.ready && r.why).toMatch(/Line 3 .* no product chosen/);
  });
});

describe('the body ACCEPT sends', () => {
  it('carries lines and products and a supplier — never a figure', () => {
    const c = defaultChoices(LINES);
    c[1] = { ...c[1]!, confirmed: true };
    const body = bookBody({ lines: LINES, date: '2026-09-03', invoice_no: 'SD/1' }, c, SUP);
    expect(body).toEqual({
      lines: [{ i: 0, sku_id: 'parle_g_biscuit' }, { i: 1, sku_id: 'maggi_noodles_70g' }],
      new_supplier: { name: 'Sharma Distributors', phone: '98200 44711' },
      date: '2026-09-03',
      invoice_no: 'SD/1',
    });
    expect(JSON.stringify(body)).not.toMatch(/paise|rate|amount|cost|qty|units/);
  });

  it('uses the supplier on file when there is one, and the person’s date over the bill’s', () => {
    const c = defaultChoices(LINES);
    const body = bookBody({ lines: LINES, date: '2026-09-03', invoice_no: null }, c,
      { id: 'sup_abc', name: '', phone: '' }, { date: '2026-09-04' });
    expect(body.supplier_id).toBe('sup_abc');
    expect(body.new_supplier).toBeUndefined();
    expect(body.date).toBe('2026-09-04');
    expect(body.invoice_no).toBeUndefined();
  });
});
