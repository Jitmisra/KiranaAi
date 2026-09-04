import { useCallback, useEffect, useState } from 'react';
import * as manage from '../lib/manageapi';
import {
  Button, Card, Empty, IcoTag, KV, Pill, Refusal, Skeleton, Verdict,
} from '../components/ui';
import '../styles/books.css';

/** A money or count cell waiting, right where the figure lands. The shelves
    table is hand-rolled rather than the shared `Table`, so the right-alignment
    the shared one gives a `num` column has to be asked for here. */
function NumSkel({ w }: { w: number }) {
  return (
    <span style={{ display: 'flex', justifyContent: 'flex-end' }}>
      <Skeleton w={w} h={11} radius={999} />
    </span>
  );
}

/**
 * Inventory.
 *
 * WHAT THIS PAGE WILL NOT DO. There is no stock sensor on this counter and
 * there never has been: no delivery note, no shelf audit, nothing. So there is
 * no honest way to compute what is on the shelf, and a "remaining" column
 * derived from sales alone would be a plausible-looking invention.
 *
 * What it does instead is take the shopkeeper's own count — his word, recorded
 * with the moment he gave it — and subtract only what the counter has billed
 * SINCE. A product he has not counted shows "not counted", never a zero: a zero
 * is a claim.
 *
 * TWO SALES COLUMNS, NOT ONE. `billed` is what went into a basket that closed;
 * `settled` is the subset a signature-verified webhook actually paid for.
 * Collapsing them would lie in one direction or the other, so both are shown,
 * neither is ever added to the other, and the distinction is spelled out in the
 * legend directly under the columns that carry it.
 */

/**
 * How the product was taught. Blue is the machine's own mark, so the mat —
 * measured by the machine in millimetres — wears it. A photograph is the
 * weaker recognition path (no size check, higher bar) and stays amber; a
 * printed code is a quiet fact; GONE is a ghost of something removed.
 */
function TaughtPill({ row }: { row: manage.InventoryRow }) {
  if (row.taught_by === 'mat_measured') return <Pill tone="code">MAT</Pill>;
  if (row.taught_by === 'appearance_only') return <Pill tone="amb">PHOTO</Pill>;
  if (row.taught_by === 'product_code_only') return <span className="pill bk-quiet">CODE</span>;
  return <span className="pill bk-ghost">GONE</span>;
}

export default function Inventory() {
  const [body, setBody] = useState<manage.InventoryBody | null>(null);
  const [err, setErr] = useState<{ reason: string; detail?: string } | null>(null);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState('');
  const [saveErr, setSaveErr] = useState<{ reason: string; detail?: string } | null>(null);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    const r = await manage.inventory();
    if (r.ok) { setBody(r); setErr(null); } else { setErr(r); setBody(null); }
    setLoading(false);
  }, []);

  useEffect(() => { void load(); }, [load]);

  const save = useCallback(async (skuId: string) => {
    // The server refuses a float, a bool and a string by name. The page checks
    // first anyway, so a typo produces an instant, specific answer rather than
    // a round trip — and so the number that goes on the wire is one this code
    // has already established is a whole number of packets.
    const text = draft.trim();
    if (!/^\d+$/.test(text)) {
      setSaveErr({
        reason: 'That is not a whole number of packets',
        detail: `“${text || 'nothing'}” — count in packets. Half a packet is not a thing a shelf holds.`,
      });
      return;
    }
    setSaving(true);
    const r = await manage.setStock(skuId, Number(text));
    setSaving(false);
    if (r.ok) {
      setEditing(null);
      setDraft('');
      setSaveErr(null);
      await load();
    } else {
      setSaveErr(r);
    }
  }, [draft, load]);

  const rows = body?.items ?? [];

  return (
    <div>
      <div className="page-head">
        <h1>Inventory</h1>
        <p>
          What this counter has been taught, what it charges for each thing, and how much of it has
          gone across the counter. The sales figures are counted from the audit chain, one line at a
          time — nothing here keeps a running total of its own.
        </p>
      </div>

      {err && (
        <div style={{ marginBottom: 20 }}>
          <Refusal
            reason="The inventory could not be read"
            detail={err.reason}
            hint={err.detail}
            action={<Button size="sm" onClick={() => void load()}>TRY AGAIN</Button>}
          />
        </div>
      )}

      {body && !body.chain.ok && (
        <div style={{ marginBottom: 20 }}>
          <Verdict tone="red" title="The audit chain does not verify">
            Sales are counted only up to the break — <b>{body.chain.lines_verified}</b> lines stood
            up to a re-walk from the beginning. Every figure in the sold columns is therefore a
            floor, not a total, and nothing has been estimated to make up the difference.
            <br />
            <span className="mono">{body.chain.error}</span>
          </Verdict>
        </div>
      )}

      {body && body.catalogue_problems.length > 0 && (
        <div style={{ marginBottom: 20 }}>
          <Verdict tone="amber" title="Part of the catalogue on disk could not be read">
            The rows below are what could be read. Nothing has been substituted for the rest.
            {body.catalogue_problems.map((p) => (
              <span className="mono blk" key={p.file + p.detail}>{p.file}: {p.detail}</span>
            ))}
          </Verdict>
        </div>
      )}

      <div className="stack">
        <Card
          title="The shelves"
          sub="everything this counter can price, and what it knows about each"
          aside={body
            ? (
              <span style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
                {rows.length
                  ? <span className="pill bk-quiet">{rows.length} TAUGHT</span>
                  : <Pill tone="amb">0 taught</Pill>}
                {/* THE COUNTED FACT, AT THE TOP. It was one KV row inside the
                    second card, three screens down, while the column it
                    qualifies — every cell of it reading "not counted" — is up
                    here. A reader met the absence long before its explanation. */}
                {rows.length > 0 && (
                  <span className="pill bk-quiet">
                    {body.counted_skus} OF {rows.length} COUNTED
                  </span>
                )}
              </span>
            )
            : null}
        >
          {!loading && rows.length === 0 ? (
            <Empty
              icon={<IcoTag size={22} />}
              title="Nothing has been taught yet"
              action={
                <Button variant="primary" onClick={() => { location.hash = '#/products'; }}>
                  TEACH A PRODUCT
                </Button>
              }
            >
              This screen lists everything the shop sells: what it is called, how it was taught,
              what it charges, how many went across the counter and how many of those a webhook
              actually paid for. Until a product is taught, the till cannot price anything and
              there is nothing here to list.
            </Empty>
          ) : (
            <div className="scroll-x">
              {/* THE HEADER IS DRAWN WHILE THE ROWS ARE COMING. Four grey bars
                  in place of a seven-column table told the reader a rectangle
                  was loading and then replaced it with something a different
                  shape and height. The header is real from the first frame and
                  the bars sit in the columns their figures will land in —
                  right-aligned in the four that carry money and counts. */}
              <table className="moments inv bk-inv">
                <thead>
                  <tr>
                    <th>Product</th>
                    <th>Taught</th>
                    <th style={{ textAlign: 'right' }}>Price</th>
                    <th style={{ textAlign: 'right' }} title="Committed into a basket that closed — paid or not.">
                      Billed
                    </th>
                    <th style={{ textAlign: 'right' }} title="The subset a signature-verified webhook actually settled.">
                      Paid for
                    </th>
                    <th>Last sold</th>
                    <th style={{ textAlign: 'right' }}>On the shelf</th>
                  </tr>
                </thead>
                <tbody>
                  {loading && [0, 1, 2, 3].map((i) => (
                    <tr key={`skel-${i}`} aria-hidden="true">
                      <td><Skeleton w="62%" h={12} radius={999} /></td>
                      <td><Skeleton w={54} h={16} radius={999} /></td>
                      <td><NumSkel w={58} /></td>
                      <td><NumSkel w={30} /></td>
                      <td><NumSkel w={30} /></td>
                      <td><Skeleton w={82} h={11} radius={999} /></td>
                      <td><NumSkel w={64} /></td>
                    </tr>
                  ))}
                  {!loading && rows.map((row) => (
                    <tr key={row.sku_id}>
                      <td>
                        <span className="bk-prod">
                          <span className="nm">{row.name ?? row.sku_id}</span>
                          <span className="sku">{row.sku_id}</span>
                        </span>
                        {row.amber_count > 0 && (
                          <div style={{ marginTop: 6 }}>
                            <Pill tone="amb">{row.amber_count} TIMES NOT PRICED</Pill>
                          </div>
                        )}
                      </td>
                      <td>
                        <TaughtPill row={row} />
                        {row.codes && row.codes.length > 0 && (
                          <div className="mono" style={{ marginTop: 4 }}>{row.codes.join(', ')}</div>
                        )}
                      </td>
                      <td style={{ textAlign: 'right' }} className="tnum">
                        {typeof row.price_paise === 'number' ? (
                          <b>{manage.money(row.price_paise)}</b>
                        ) : (
                          <span className="bk-dim">no price on disk</span>
                        )}
                      </td>
                      <td style={{ textAlign: 'right' }} className="tnum">
                        {row.billed_count > 0
                          ? row.billed_count
                          : <span className="bk-dim">—</span>}
                      </td>
                      <td style={{ textAlign: 'right' }} className="tnum">
                        {row.settled_count > 0
                          ? row.settled_count
                          : <span className="bk-dim">—</span>}
                      </td>
                      <td>
                        {row.last_billed_at
                          ? manage.when(row.last_billed_at)
                          : <span className="bk-dim">never</span>}
                      </td>
                      <td style={{ textAlign: 'right' }}>
                        {editing === row.sku_id ? (
                          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 6 }}>
                            <div className="stock-edit">
                              <input
                                type="text"
                                inputMode="numeric"
                                value={draft}
                                autoFocus
                                placeholder="packets"
                                aria-label={`Packets of ${row.name ?? row.sku_id} on the shelf`}
                                aria-invalid={saveErr ? true : undefined}
                                aria-describedby={saveErr ? `stock-err-${row.sku_id}` : undefined}
                                style={saveErr
                                  ? { borderColor: 'var(--red-line)', background: 'var(--red-bg)' }
                                  : undefined}
                                onChange={(e) => { setDraft(e.target.value); setSaveErr(null); }}
                                onKeyDown={(e) => {
                                  if (e.key === 'Enter') void save(row.sku_id);
                                  if (e.key === 'Escape') { setEditing(null); setSaveErr(null); }
                                }}
                              />
                              <button
                                className="btn sm primary"
                                disabled={saving || !draft.trim()}
                                title={!draft.trim() ? 'Type how many packets you counted.' : undefined}
                                onClick={() => void save(row.sku_id)}
                              >
                                {saving ? '…' : 'SAVE'}
                              </button>
                              <button
                                className="btn sm ghost"
                                onClick={() => { setEditing(null); setSaveErr(null); }}
                              >
                                CANCEL
                              </button>
                            </div>
                            {/* THE REASON WHERE THE PERSON TYPED. It used to be
                                rendered under the legend at the foot of the
                                card — up to six rows below the box that was
                                refused, and on a phone off the screen
                                entirely, so a rejected count looked like a
                                button that had done nothing. The words are the
                                server's, or this page's own pre-check, and
                                neither is paraphrased. */}
                            {saveErr && (
                              <span
                                id={`stock-err-${row.sku_id}`}
                                style={{
                                  display: 'block',
                                  maxWidth: '34ch',
                                  textAlign: 'right',
                                  fontSize: 'var(--t-micro)',
                                  lineHeight: 1.5,
                                  fontWeight: 400,
                                  color: 'var(--red)',
                                }}
                              >
                                <b style={{ display: 'block' }}>{saveErr.reason}</b>
                                {saveErr.detail}
                              </span>
                            )}
                          </div>
                        ) : (
                          <button
                            className="btn sm ghost stock-cell"
                            title="Count this shelf and type what you find"
                            onClick={() => {
                              setEditing(row.sku_id);
                              setDraft(row.opening_stock_units === null || row.opening_stock_units === undefined
                                ? ''
                                : String(row.opening_stock_units));
                              setSaveErr(null);
                            }}
                          >
                            {row.remaining_units === null || row.remaining_units === undefined ? (
                              <span className="bk-dim">not counted</span>
                            ) : (
                              <>
                                <b className={row.remaining_units < 0 ? 'stock-neg' : ''}>
                                  {row.remaining_units}
                                </b>
                                <span className="stock-sub">
                                  counted {row.opening_stock_units} · {manage.when(row.opening_stock_counted_at)}
                                </span>
                              </>
                            )}
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {!loading && rows.length > 0 && (
            <div className="bk-legend">
              <span><b>Billed</b> — went into a basket that closed, paid or not.</span>
              <span><b>Paid for</b> — the subset a signature-verified webhook actually settled.
              The two are never added together.</span>
            </div>
          )}

          {/* A refusal that arrived while the row it belongs to is no longer
              being edited still has to be shown — the count was not recorded
              and nothing else on the screen would say so. */}
          {saveErr && editing === null && (
            <div style={{ marginTop: 12 }}>
              <Refusal reason={saveErr.reason} detail={saveErr.detail} />
            </div>
          )}

          {rows.some((r) => (r.remaining_units ?? 0) < 0) && (
            <Verdict tone="amber" title="One of these has gone below zero">
              The counter has billed more of it than you said was on the shelf. Either something
              arrived after you counted, or the count was of a different shelf. Count it again — the
              page will not quietly correct it for you.
            </Verdict>
          )}
        </Card>

        <Card title="What “on the shelf” means here" tight>
          <p className="hint" style={{ marginTop: 0 }}>
            <b>This counter cannot see your shelves.</b> It has no stock sensor and nobody has ever
            entered a delivery, so there is no stock level for it to report and it will not invent
            one.
          </p>
          <KV k="what you set">a count you made yourself, in packets</KV>
          <KV k="what it subtracts">only what the counter has billed since that moment</KV>
          <KV k="what it cannot see">breakage, a packet handed over off-counter, a delivery</KV>
          <KV k="counted so far">
            {body ? `${body.counted_skus} of ${rows.length} products` : '—'}
          </KV>
          <p className="hint">
            The figure will drift downward over time, because everything it is blind to takes stock
            off the shelf without telling it. Re-count and it starts again from your number.
            {body?.stock_problem && (
              <><br /><b>Note:</b> <span className="mono">{body.stock_problem}</span></>
            )}
          </p>
        </Card>

        {body && body.sold_but_not_in_catalogue.length > 0 && (
          <Card
            title="Sold, but no longer in the catalogue"
            aside={<span className="pill bk-quiet">{body.sold_but_not_in_catalogue.length} TO RECONCILE</span>}
          >
            <p className="hint" style={{ marginTop: 0 }}>
              A reconciliation note, not a fault. The chain records these going across the counter,
              and the catalogue no longer holds them — renamed, removed, or taught during a demo.
              They are listed because otherwise the sold column silently stops adding up to the
              bills on the history screen, and there would be nowhere to look for the difference.
            </p>
            <div className="bk-recon">
              {body.sold_but_not_in_catalogue.map((row) => (
                <div className="ledger-line" key={row.sku_id}>
                  <span className="nm">{row.sku_id}</span>
                  <span className="why">
                    {row.billed_count > 0 && `billed ${row.billed_count}×`}
                    {row.billed_count > 0 && row.amber_count > 0 && ' · '}
                    {row.amber_count > 0 && `not priced ${row.amber_count}×`}
                    {' · '}{manage.when(row.last_billed_at)}
                  </span>
                  <span className="amt muted">no price</span>
                </div>
              ))}
            </div>
          </Card>
        )}

        {body && body.orphan_code_bindings.length > 0 && (
          <Card title="Codes pointing at nothing">
            <p className="hint" style={{ marginTop: 0 }}>
              A printed code is bound to a product that is not in the catalogue, so scanning it
              prices nothing. Re-teach the product, or the code will keep reading as an unknown
              item and be left off the bill.
            </p>
            {body.orphan_code_bindings.map((sku) => (
              <KV k={<span className="mono">{sku}</span>} key={sku}>bound, but never taught</KV>
            ))}
          </Card>
        )}
      </div>
    </div>
  );
}
