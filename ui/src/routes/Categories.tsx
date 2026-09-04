import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import * as cat from '../lib/catapi';
import { rupees } from '../lib/money';
import {
  Card, Empty, KV, LoadingCard, Pill, Progress, Refusal, Segmented, Verdict, Working,
} from '../components/ui';
import '../styles/categories.css';

/**
 * Categories — where a shopkeeper decides what sits on which shelf.
 *
 * THREE THINGS THIS SCREEN IS CAREFUL ABOUT.
 *
 * 1. FILING IS NOT STOCK. Nothing on this page can change a product, a price or
 *    a taught vector, and deleting a category deletes no packet. The delete
 *    confirmation says how many products lose their shelf and how many
 *    categories move up a level BEFORE the press, using the counts the server
 *    already published — not after, when the shopkeeper's only recourse is to
 *    file them again by hand.
 *
 * 2. A PROPOSAL IS NOT A DECISION. `GET /categories/suggest` is a keyword list
 *    read against product names — no model, no photograph, nothing learned from
 *    what was accepted last time. It is shown with the word that matched, so
 *    the reason is on the row, and nothing is filed until a person ticks lines
 *    and presses accept. The proposals arrive UNTICKED on purpose: the server's
 *    own note says "soap" files a bathing bar under Household, and a screen
 *    that pre-ticks 400 rows makes accepting the mistakes the default. There is
 *    a one-press SELECT ALL for the shopkeeper who has read them.
 *
 * 3. THE MONEY ON THIS PAGE TRAVELS ONE WAY. A price is here because a product
 *    list is unreadable without one. It is the server's integer paise, formatted
 *    and never sent back, and no arithmetic on this page touches it.
 *
 * Every panel has a loading, an empty and a refusal state, and a refusal keeps
 * the server's exact reason — those sentences name the file that could not be
 * written or the rule that was broken, and paraphrasing them would throw away
 * the only part worth reading.
 */

type Refused = { reason: string; detail?: string };

/** A short account of what a write actually did. Never green: no money moved. */
type Flash = { title: string; body: string } | null;

/**
 * Rupees for the screen.
 *
 * `rupees()` asserts integer paise and throws on anything else, which is the
 * right behaviour at a till and the wrong behaviour here: this list exists to
 * let a shopkeeper file four hundred products, and one bad row in the catalogue
 * must not blank the whole page. The server has already rendered the same
 * number with `gawaah/money.py`, so its string is the fallback.
 */
function money(row: { price_paise: number; price_rupees: string }): string {
  try {
    return rupees(row.price_paise);
  } catch {
    return `₹${row.price_rupees}`;
  }
}

/* ------------------------------------------------------------------ icons -- */
/* Inline SVG only — this build ships no icon package. */

function Chevron({ up }: { up?: boolean }) {
  return (
    <svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true" focusable="false">
      <path
        d={up ? 'M3 10.5 8 5.5l5 5' : 'M3 5.5 8 10.5l5-5'}
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function Cross() {
  return (
    <svg viewBox="0 0 16 16" width="10" height="10" aria-hidden="true" focusable="false">
      <path d="M4 4l8 8M12 4l-8 8" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}

function Pencil() {
  return (
    <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true" focusable="false">
      <path
        d="M11 2.5 13.5 5 5.5 13H3v-2.5zM9.5 4l2.5 2.5"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function Bin() {
  return (
    <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true" focusable="false">
      <path
        d="M3 4.5h10M6.5 4.5V3h3v1.5M4.5 4.5l.6 8.5h5.8l.6-8.5M6.8 7v3.6M9.2 7v3.6"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/* ------------------------------------------------------------- the screen -- */

export default function Categories() {
  const [book, setBook] = useState<cat.CategoryBook | null>(null);
  const [bookErr, setBookErr] = useState<Refused | null>(null);
  const [loadingBook, setLoadingBook] = useState(true);

  const [prods, setProds] = useState<cat.ProductList | null>(null);
  const [prodErr, setProdErr] = useState<Refused | null>(null);
  const [loadingProds, setLoadingProds] = useState(true);

  const [sug, setSug] = useState<cat.Suggestions | null>(null);
  const [sugErr, setSugErr] = useState<Refused | null>(null);
  const [loadingSug, setLoadingSug] = useState(true);
  const [overFiled, setOverFiled] = useState<'new' | 'all'>('new');

  /**
   * WHERE THE FILING LIVES. This panel used to be all-or-nothing: `h.ok ? h :
   * null`, so a refusal from `/categories/health` was thrown away and the card
   * simply was not there — and "the resolved path is printed rather than
   * assumed" is the whole reason the card exists. A path that could not be read
   * is exactly the case it was written for.
   */
  const [health, setHealth] = useState<cat.CategoriesHealth | null>(null);
  const [healthErr, setHealthErr] = useState<Refused | null>(null);
  const [loadingHealth, setLoadingHealth] = useState(true);

  /* the shelves ---------------------------------------------------------- */
  const [newName, setNewName] = useState('');
  const [newParent, setNewParent] = useState('');
  const [creating, setCreating] = useState(false);
  const [shelfErr, setShelfErr] = useState<Refused | null>(null);
  /**
   * WHICH ROW THE REFUSAL IS ABOUT.
   *
   * A rename refused halfway down a list of forty shelves was announced at the
   * top of the card, where the shopkeeper was not looking and could not tell
   * which row it meant. `null` is the one case that genuinely belongs at the
   * top: adding a new category, which happens in the box up there.
   */
  const [shelfErrAt, setShelfErrAt] = useState<string | null>(null);
  const newNameRef = useRef<HTMLInputElement | null>(null);
  /** Categories created so far by CREATE ALL — N requests, one at a time. */
  const [made, setMade] = useState<{ done: number; of: number } | null>(null);
  const [shelfFlash, setShelfFlash] = useState<Flash>(null);
  const [editId, setEditId] = useState<string | null>(null);
  const [editName, setEditName] = useState('');
  const [editParent, setEditParent] = useState('');
  const [confirmId, setConfirmId] = useState<string | null>(null);
  const [busyCat, setBusyCat] = useState<string | null>(null);

  /* the product list ------------------------------------------------------ */
  const [fCat, setFCat] = useState('');
  const [fTag, setFTag] = useState('');
  const [q, setQ] = useState('');
  const [qLive, setQLive] = useState('');
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [bulkCat, setBulkCat] = useState('');
  const [bulkBusy, setBulkBusy] = useState(false);
  const [fileErr, setFileErr] = useState<Refused | null>(null);
  const [fileFlash, setFileFlash] = useState<Flash>(null);
  const [busySku, setBusySku] = useState<string | null>(null);
  const [tagFor, setTagFor] = useState<string | null>(null);
  const [tagDraft, setTagDraft] = useState('');

  /* the proposals --------------------------------------------------------- */
  const [ticked, setTicked] = useState<Set<string>>(new Set());
  const [acceptBusy, setAcceptBusy] = useState(false);
  const [acceptErr, setAcceptErr] = useState<Refused | null>(null);
  const [acceptFlash, setAcceptFlash] = useState<Flash>(null);

  const tagInput = useRef<HTMLInputElement | null>(null);

  /* ------------------------------------------------------------- loading -- */

  const loadBook = useCallback(async () => {
    setLoadingBook(true);
    const r = await cat.list();
    if (r.ok) { setBook(r); setBookErr(null); } else { setBookErr(r); }
    setLoadingBook(false);
  }, []);

  // EVERY ONE OF THESE MARKS ITSELF IN FLIGHT. They used to only ever clear the
  // flag, so `loadingProds` was true once, on mount, and false for the rest of
  // the session — a filter change re-queried the server and the screen sat
  // there showing the previous answer with nothing to say a new one was coming.
  const loadProducts = useCallback(async (filter: cat.ProductFilter) => {
    setLoadingProds(true);
    const r = await cat.products(filter);
    if (r.ok) { setProds(r); setProdErr(null); } else { setProdErr(r); }
    setLoadingProds(false);
  }, []);

  const loadSuggest = useCallback(async (all: boolean) => {
    setLoadingSug(true);
    const r = await cat.suggest(all);
    if (r.ok) { setSug(r); setSugErr(null); } else { setSugErr(r); }
    setLoadingSug(false);
  }, []);

  const loadHealth = useCallback(async () => {
    setLoadingHealth(true);
    const h = await cat.health();
    if (h.ok) { setHealth(h); setHealthErr(null); } else { setHealthErr(h); }
    setLoadingHealth(false);
  }, []);

  /**
   * Everything again, after a write. All four views are derived from one file
   * on disk, so a filing that moved a product has also changed the menu counts,
   * the proposals and the orphan count — reloading only the panel that was
   * pressed leaves the other three quietly stale.
   */
  const refresh = useCallback(async () => {
    await Promise.all([
      loadBook(),
      loadProducts({ category: fCat, tag: fTag, q: qLive }),
      loadSuggest(overFiled === 'all'),
      loadHealth(),
    ]);
  }, [loadBook, loadProducts, loadSuggest, loadHealth, fCat, fTag, qLive, overFiled]);

  // The menu and the health panel have nothing to re-read them on; the product
  // list and the proposals have their own effects below, which fire on mount as
  // well as on a change, so calling refresh() here would fetch both of them
  // twice on every arrival at this screen.
  useEffect(() => {
    void loadBook();
    void loadHealth();
  }, [loadBook, loadHealth]);

  // The filters are a server query, so a keystroke is a request. A quarter of a
  // second is long enough that typing "shampoo" is one query and not seven, and
  // short enough that it still feels like the list is answering the typing.
  useEffect(() => {
    const id = setTimeout(() => setQLive(q), 250);
    return () => clearTimeout(id);
  }, [q]);

  // The + TAG button REPLACES ITSELF with this input, so the browser moves
  // focus to the body and everything typed next goes nowhere. Found by driving
  // the real page: the input sat there empty while the keystrokes vanished.
  useEffect(() => {
    if (tagFor) tagInput.current?.focus();
  }, [tagFor]);

  useEffect(() => {
    void loadProducts({ category: fCat, tag: fTag, q: qLive });
  }, [fCat, fTag, qLive, loadProducts]);

  useEffect(() => {
    void loadSuggest(overFiled === 'all');
  }, [overFiled, loadSuggest]);

  /* --------------------------------------------------------------- shape -- */

  const cats = useMemo(() => book?.categories ?? [], [book]);
  const tops = useMemo(() => cats.filter((c) => !c.parent_id), [cats]);
  const kidsOf = useCallback(
    (id: string) => cats.filter((c) => c.parent_id === id),
    [cats],
  );
  const full = book ? book.count >= book.limits.max_categories : false;

  /* ------------------------------------------------------------ the shelves */

  const addCategory = useCallback(async () => {
    const name = newName.trim();
    if (!name) return;
    setCreating(true);
    setShelfErr(null); setShelfErrAt(null);
    const r = await cat.create({ name, ...(newParent ? { parent_id: newParent } : {}) });
    setCreating(false);
    if (!r.ok) { setShelfErr(r); setShelfErrAt(null); return; }
    setNewName('');
    setShelfFlash({ title: `${r.category.name} added`, body: 'Nothing is filed under it yet.' });
    await refresh();
  }, [newName, newParent, refresh]);

  const saveEdit = useCallback(async (row: cat.CategoryRow) => {
    const name = editName.trim();
    const body: { name?: string; parent_id?: string | null } = {};
    if (name && name !== row.name) body.name = name;
    // ABSENT AND NULL ARE DIFFERENT INSTRUCTIONS to this server, so the parent
    // key is only sent when it actually moved — sending it every time would
    // re-file a category nobody touched.
    const wantParent = editParent || null;
    if (wantParent !== row.parent_id) body.parent_id = wantParent;
    if (!Object.keys(body).length) { setEditId(null); return; }
    setBusyCat(row.category_id);
    setShelfErr(null); setShelfErrAt(null);
    const r = await cat.edit(row.category_id, body);
    setBusyCat(null);
    if (!r.ok) { setShelfErr(r); setShelfErrAt(row.category_id); return; }
    setEditId(null);
    setShelfFlash({
      title: `${r.was.name} is now ${r.category.name}`,
      body: `Every product filed under it stayed filed under it — ${r.category.products} of them. `
        + 'The category keeps its id through a rename, which is the whole reason the id is not the name.',
    });
    await refresh();
  }, [editName, editParent, refresh]);

  const move = useCallback(async (row: cat.CategoryRow, delta: number) => {
    const level = row.parent_id ? kidsOf(row.parent_id) : tops;
    const i = level.findIndex((c) => c.category_id === row.category_id);
    const plan = cat.renumber(cat.moved(level, i, delta));
    if (!plan.length) return;
    setBusyCat(row.category_id);
    setShelfErr(null); setShelfErrAt(null);
    const r = await cat.saveOrder(plan);
    setBusyCat(null);
    if (!r.ok) { setShelfErr(r); setShelfErrAt(row.category_id); }
    await refresh();
  }, [kidsOf, tops, refresh]);

  const drop = useCallback(async (row: cat.CategoryRow) => {
    setBusyCat(row.category_id);
    setShelfErr(null); setShelfErrAt(null);
    const r = await cat.remove(row.category_id);
    setBusyCat(null);
    // A REFUSED DELETE KEEPS ITS CONFIRMATION OPEN. Closing it threw away the
    // sentence that said what the press was going to do, and put the refusal
    // where the shelf no longer had a box to hold it.
    if (!r.ok) { setShelfErr(r); setShelfErrAt(row.category_id); return; }
    setConfirmId(null);
    setShelfFlash({
      title: `${r.removed} removed`,
      body: `${r.uncategorised} product(s) are now unfiled and ${r.products_deleted} were deleted — `
        + `every one of them is still in the catalogue at the same price. `
        + (r.children_promoted
          ? `${r.children_promoted} category that was inside it moved to the top level. `
          : '')
        + 'Tags belong to the product and were left alone.',
    });
    if (fCat === row.category_id) setFCat('');
    await refresh();
  }, [fCat, refresh]);

  /* ------------------------------------------------------------ the filing -- */

  const setShelf = useCallback(async (sku: string, categoryId: string | null) => {
    setBusySku(sku);
    setFileErr(null);
    const r = await cat.fileSku(sku, { category_id: categoryId });
    setBusySku(null);
    if (!r.ok) { setFileErr(r); return; }
    await refresh();
  }, [refresh]);

  const setTags = useCallback(async (sku: string, tags: string[]) => {
    setBusySku(sku);
    setFileErr(null);
    const r = await cat.fileSku(sku, { tags });
    setBusySku(null);
    if (!r.ok) { setFileErr(r); return; }
    setTagDraft('');
    await refresh();
  }, [refresh]);

  const fileSelected = useCallback(async () => {
    const rows = [...picked].map((sku_id) => ({
      sku_id,
      category_id: bulkCat === cat.NO_CATEGORY ? null : bulkCat,
    }));
    if (!rows.length || !bulkCat) return;
    setBulkBusy(true);
    setFileErr(null);
    const r = await cat.assign(rows);
    setBulkBusy(false);
    if (!r.ok) { setFileErr(r); return; }
    setPicked(new Set());
    setFileFlash({
      title: `${r.changed} of ${r.considered} moved`,
      body: `${r.unchanged} were already filed that way and ${r.uncategorised} came off a shelf. ${r.note}`,
    });
    await refresh();
  }, [picked, bulkCat, refresh]);

  /* --------------------------------------------------------- the proposals -- */

  const ready = useMemo(() => (sug?.proposals ?? []).filter((p) => p.ready), [sug]);

  const makeMissing = useCallback(async () => {
    const names = sug?.missing_categories ?? [];
    if (!names.length) return;
    setAcceptBusy(true);
    setAcceptErr(null);
    // ONE REQUEST PER CATEGORY, IN ORDER. There is no route that makes several
    // at once, so a run of twelve is twelve round trips and the screen counts
    // them — the count is honest because this browser is the thing issuing
    // them, and a failure partway is reported with how far it got.
    setMade({ done: 0, of: names.length });
    let n = 0;
    for (const name of names) {
      const r = await cat.create({ name });
      if (!r.ok) {
        setAcceptErr({
          reason: r.reason,
          detail: `${r.detail ?? ''} ${n} of ${names.length} were created before this one.`,
        });
        break;
      }
      n += 1;
      setMade({ done: n, of: names.length });
    }
    setAcceptBusy(false);
    setMade(null);
    setAcceptFlash(n ? {
      title: `${n} categor${n === 1 ? 'y' : 'ies'} created`,
      body: 'Empty. The proposals below can now be ticked and accepted — nothing has been filed yet.',
    } : null);
    await refresh();
  }, [sug, refresh]);

  const acceptTicked = useCallback(async () => {
    const rows = ready
      .filter((p) => ticked.has(p.sku_id) && p.category_id)
      .map((p) => ({ sku_id: p.sku_id, category_id: p.category_id as string }));
    if (!rows.length) return;
    setAcceptBusy(true);
    setAcceptErr(null);
    const r = await cat.assign(rows);
    setAcceptBusy(false);
    if (!r.ok) { setAcceptErr(r); return; }
    setTicked(new Set());
    setAcceptFlash({
      title: `${r.changed} of ${r.considered} filed`,
      body: `${r.unchanged} were already where the keyword list would have put them. `
        + 'Only the lines you ticked were sent; the rest are still proposals.',
    });
    await refresh();
  }, [ready, ticked, refresh]);

  /* ----------------------------------------------------------------- view -- */

  const filterLabel = fCat === cat.NO_CATEGORY
    ? 'not filed yet'
    : cats.find((c) => c.category_id === fCat)?.name ?? 'everything';

  return (
    <div className="cat-page">
      <div className="page-head">
        <h1>Categories</h1>
        <p>
          Somewhere to put four hundred packets. A category is a label beside the catalogue, not part
          of it — deleting one takes the label away and leaves every product, every price and every
          taught photograph exactly where it was. Only priced products can be filed.
        </p>
      </div>

      {bookErr && (
        <div>
          <Refusal
            reason="The filing could not be read"
            detail={bookErr.reason}
            hint={bookErr.detail}
            action={<button className="btn sm" onClick={() => void refresh()}>TRY AGAIN</button>}
          />
        </div>
      )}

      {/* The four figures the whole screen is about. Deliberately plain: green,
          amber and red mean money and recognition on this product, and a count
          of unfiled packets is neither. */}
      <div className="cat-stats" role="group" aria-label="What is filed">
        {loadingBook && !book && (
          <>
            <div className="skel cat-statskel" /><div className="skel cat-statskel" />
            <div className="skel cat-statskel" /><div className="skel cat-statskel" />
          </>
        )}
        {book && (
          <>
            <div className="cat-stat"><b className="tnum">{book.products}</b><span>PRICED PRODUCTS</span></div>
            <div className="cat-stat"><b className="tnum">{book.categorised}</b><span>ON A SHELF</span></div>
            <div className="cat-stat"><b className="tnum">{book.uncategorised}</b><span>NOT FILED YET</span></div>
            <div className="cat-stat">
              <b className="tnum">{book.count}</b>
              <span>CATEGORIES · CAP {book.limits.max_categories}</span>
            </div>
          </>
        )}
      </div>

      <div className="cat-grid">
        {/* ------------------------------------------------------- shelves -- */}
        <Card
          title="The shelves"
          /* An em dash while it loads, not a nought: zero categories is a fact
             about the shop and this panel does not know it yet. */
          aside={<Pill tone="code">{book ? book.count : '—'} OF {book?.limits.max_categories ?? 60}</Pill>}
        >
          <div className="cat-new">
            <div className="field">
              <label htmlFor="cat-name">Add a category</label>
              <input
                id="cat-name"
                ref={newNameRef}
                type="text"
                placeholder="Snacks"
                maxLength={book?.limits.max_name ?? 40}
                value={newName}
                disabled={full}
                onChange={(e) => setNewName(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') void addCategory(); }}
              />
            </div>
            <div className="field">
              <label htmlFor="cat-parent">Inside</label>
              <select
                id="cat-parent"
                value={newParent}
                disabled={full || !tops.length}
                onChange={(e) => setNewParent(e.target.value)}
              >
                <option value="">Top level</option>
                {tops.map((c) => (
                  <option key={c.category_id} value={c.category_id}>{c.name}</option>
                ))}
              </select>
            </div>
            <button
              className="btn primary"
              disabled={creating || full || !newName.trim()}
              title={full
                ? 'This counter is at its category cap.'
                : !newName.trim() ? 'Type a name for the shelf first.' : undefined}
              onClick={() => void addCategory()}
            >
              {creating ? 'ADDING…' : 'ADD'}
            </button>
          </div>

          {/* A DISABLED CONTROL SAYS WHY — UNDER THE ROW, NOT INSIDE IT.
              These two boxes share a two-column grid that bottom-aligns its
              cells, so a hint hung off ONE of them made that cell taller and
              dropped the other one's label below the neighbouring select.
              Photographed at 1440: "ADD A CATEGORY" sat under "Top level".
              A line of its own under both keeps the pair on one baseline. */}
          {(full || !tops.length) && (
            <p className="cat-why">
              {full
                ? 'Both boxes are off: this counter is at its category cap, so nothing new can be named until one is deleted.'
                : 'INSIDE is off because there is nothing to go inside yet — the first shelf is a top-level one.'}
            </p>
          )}

          {/* The nesting rule is the server's own sentence, quoted rather than
              paraphrased — it is the thing that will refuse a third level. */}
          <p className="cat-note">
            Nesting is {book?.limits.nesting
              ?? 'one level: a category may have a parent, and a category that has a parent may not be a parent'}.
            Names are unique across the whole shop, so two shelves cannot both be called Cleaning.
          </p>

          {full && (
            /* The cap is the server's number, not a word typed here: a page that
               says "sixty" is wrong the day the module says eighty. */
            <Verdict tone="info" title={`This counter holds ${book?.limits.max_categories ?? 60} categories`}>
              Delete one to add another. Deleting a category keeps its products.
            </Verdict>
          )}

          {/* Only the refusal that belongs to the box above stays up here.
              Anything about a ROW is rendered on that row instead. */}
          {shelfErr && shelfErrAt === null && (
            <Refusal
              reason={shelfErr.reason}
              detail={shelfErr.detail}
              hint="No shelf was made. The name you typed is still in the box above."
            />
          )}

          {shelfFlash && (
            <Verdict tone="info" title={shelfFlash.title}>{shelfFlash.body}</Verdict>
          )}

          {loadingBook && !book && (
            <div className="cat-skels" aria-hidden="true">
              <div className="skel" style={{ height: 46 }} />
              <div className="skel" style={{ height: 46 }} />
              <div className="skel" style={{ height: 46 }} />
            </div>
          )}

          {/* A RE-READ WITH ROWS ALREADY ON SCREEN. They are still true, only a
              moment old, so they stay and this says a fresh answer is coming. */}
          {loadingBook && book && (
            <p className="cat-working"><Working /> reading the shelves again</p>
          )}

          {book && !cats.length && (
            <Empty
              title="No shelves yet"
              action={(
                <button className="btn sm primary" onClick={() => newNameRef.current?.focus()}>
                  NAME THE FIRST SHELF
                </button>
              )}
            >
              Nothing is filed anywhere, which is not a problem — it is a shop that has not been
              tidied yet. Add one above, or take the proposals further down the page: they read the
              product names you have already taught.
            </Empty>
          )}

          <div className="cat-list">
            {cats.map((c) => {
              const level = c.parent_id ? kidsOf(c.parent_id) : tops;
              const at = level.findIndex((x) => x.category_id === c.category_id);
              const busy = busyCat === c.category_id;
              const editing = editId === c.category_id;
              const confirming = confirmId === c.category_id;

              if (editing) {
                return (
                  <div className="cat-row editing" key={c.category_id}>
                    <div className="field">
                      <label htmlFor={`nm-${c.category_id}`}>Name</label>
                      <input
                        id={`nm-${c.category_id}`}
                        type="text"
                        value={editName}
                        maxLength={book?.limits.max_name ?? 40}
                        onChange={(e) => setEditName(e.target.value)}
                        onKeyDown={(e) => { if (e.key === 'Enter') void saveEdit(c); }}
                      />
                    </div>
                    <div className="field">
                      <label htmlFor={`pa-${c.category_id}`}>Inside</label>
                      <select
                        id={`pa-${c.category_id}`}
                        value={editParent}
                        disabled={c.children.length > 0}
                        onChange={(e) => setEditParent(e.target.value)}
                      >
                        <option value="">Nothing — top level</option>
                        {tops.filter((t) => t.category_id !== c.category_id).map((t) => (
                          <option key={t.category_id} value={t.category_id}>{t.name}</option>
                        ))}
                      </select>
                      {c.children.length > 0 && (
                        <span className="sub">
                          This one has categories inside it, so it cannot also go inside one.
                          Move its children out first.
                        </span>
                      )}
                    </div>
                    <div className="btn-row">
                      <button className="btn sm primary" disabled={busy} onClick={() => void saveEdit(c)}>
                        {busy ? 'SAVING…' : 'SAVE'}
                      </button>
                      <button className="btn sm ghost" onClick={() => setEditId(null)}>CANCEL</button>
                    </div>
                    {/* The refusal for THIS rename, under the two boxes that
                        caused it, with what was typed still in them. */}
                    {shelfErr && shelfErrAt === c.category_id && (
                      <Refusal
                        reason={shelfErr.reason}
                        detail={shelfErr.detail}
                        hint="Nothing was renamed and nothing moved. What you typed is still above."
                      />
                    )}
                  </div>
                );
              }

              return (
                <div className={`cat-row${c.parent_id ? ' kid' : ''}`} key={c.category_id}>
                  <button
                    className="cat-name"
                    aria-pressed={fCat === c.category_id}
                    title="Show what is on this shelf"
                    onClick={() => setFCat(fCat === c.category_id ? '' : c.category_id)}
                  >
                    <span className="l">{c.name}</span>
                    {/* A child says whose shelf it is on rather than relying on
                        an indent alone: at a glance down a menu, twenty pixels
                        of margin is not a fact anybody reads. */}
                    <span className="s tnum">
                      {c.parent_name && `in ${c.parent_name} · `}
                      {c.products} filed
                      {c.children.length > 0
                        && ` · ${c.children.length} shelf${c.children.length === 1 ? '' : 'ves'} inside`}
                    </span>
                  </button>

                  {/* FOUR ICONS, NOT FOUR WORDS. The shelves column is 340 px
                      wide and RENAME plus DELETE plus two arrows spelled out
                      left the category name about fifty pixels, which is how
                      "Household" rendered as "Househo" over the top of an
                      arrow. Each carries its name for a screen reader and a
                      tooltip for everyone else, and nothing destructive
                      happens without the confirmation below. */}
                  <div className="cat-tools">
                    {/* NOT the till's `.qbtn`: that one is invisible until its
                        bill line is hovered, which is right on a bill and wrong
                        on a menu you are rearranging. */}
                    <button
                      className="cat-icon"
                      title={busy ? 'This shelf is being saved.' : at <= 0 ? 'Already first at this level.' : 'Move up'}
                      aria-label={`Move ${c.name} up`}
                      disabled={busy || at <= 0}
                      onClick={() => void move(c, -1)}
                    ><Chevron up /></button>
                    <button
                      className="cat-icon"
                      title={busy
                        ? 'This shelf is being saved.'
                        : at >= level.length - 1 ? 'Already last at this level.' : 'Move down'}
                      aria-label={`Move ${c.name} down`}
                      disabled={busy || at < 0 || at >= level.length - 1}
                      onClick={() => void move(c, 1)}
                    ><Chevron /></button>
                    <button
                      className="cat-icon"
                      title="Rename or move"
                      aria-label={`Rename ${c.name}`}
                      disabled={busy}
                      onClick={() => {
                        setEditId(c.category_id);
                        setEditName(c.name);
                        setEditParent(c.parent_id ?? '');
                        setShelfErr(null);
                      }}
                    ><Pencil /></button>
                    <button
                      className={`cat-icon${confirming ? ' armed' : ''}`}
                      title="Delete this shelf"
                      aria-label={`Delete ${c.name}`}
                      aria-expanded={confirming}
                      disabled={busy}
                      onClick={() => { setConfirmId(confirming ? null : c.category_id); setShelfErr(null); }}
                    ><Bin /></button>
                  </div>

                  {confirming && (
                    /* The counts come from the list the server already sent, so
                       the warning is exact before the press rather than after. */
                    <div className="cat-confirm">
                      <b>Delete {c.name}?</b>
                      <p>
                        {c.products === 0 && 'Nothing is filed under it. '}
                        {c.products === 1
                          && '1 product loses its shelf and becomes unfiled. It stays in the catalogue at '
                            + 'the same price, with its tags. '}
                        {c.products > 1
                          && `${c.products} products lose their shelf and become unfiled. Every one of them `
                            + 'stays in the catalogue at the same price, with its tags. '}
                        {c.children.length === 1
                          && 'The category inside it moves to the top level, and what is filed under it '
                            + 'stays filed. '}
                        {c.children.length > 1
                          && `The ${c.children.length} categories inside it move to the top level, and what `
                            + 'is filed under them stays filed. '}
                        No product is deleted. There is no undo — the shelf has to be made again by hand.
                      </p>
                      <div className="btn-row">
                        <button className="btn sm danger" disabled={busy} onClick={() => void drop(c)}>
                          {busy ? 'DELETING…' : 'DELETE THE SHELF'}
                        </button>
                        <button className="btn sm ghost" onClick={() => setConfirmId(null)}>KEEP IT</button>
                      </div>
                      {shelfErr && shelfErrAt === c.category_id && (
                        <Refusal
                          reason={shelfErr.reason}
                          detail={shelfErr.detail}
                          hint="The shelf is still here and everything filed under it is still filed under it."
                        />
                      )}
                    </div>
                  )}
                  {/* A refusal about a move: the row it was about, not the top
                      of a list of forty. */}
                  {shelfErr && shelfErrAt === c.category_id && !confirming && (
                    <div className="cat-rowerr">
                      <Refusal reason={shelfErr.reason} detail={shelfErr.detail} />
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          {cats.length > 1 && (
            <p className="cat-note">
              The arrows renumber one level at a time and each category is saved on its own — there is
              no route that moves several at once, so a failure partway is reported rather than hidden.
            </p>
          )}
        </Card>

        {/* ------------------------------------------------------ products -- */}
        <Card
          title="What is filed where"
          aside={<Pill tone="off">{prods ? prods.count : '—'} SHOWN</Pill>}
        >
          <div className="cat-filters">
            <div className="field">
              <label htmlFor="f-cat">Shelf</label>
              <select id="f-cat" value={fCat} onChange={(e) => setFCat(e.target.value)}>
                <option value="">Everything</option>
                <option value={cat.NO_CATEGORY}>Not filed yet</option>
                {cats.map((c) => (
                  <option key={c.category_id} value={c.category_id}>
                    {c.parent_id ? `— ${c.name}` : c.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label htmlFor="f-tag">Tag</label>
              <select
                id="f-tag"
                value={fTag}
                disabled={!book?.tags.length}
                title={book && !book.tags.length ? 'No product carries a tag yet.' : undefined}
                onChange={(e) => setFTag(e.target.value)}
              >
                <option value="">Any</option>
                {(book?.tags ?? []).map((t) => (
                  <option key={t.tag} value={t.tag}>{t.tag} ({t.products})</option>
                ))}
              </select>
              {book && !book.tags.length && (
                <span className="sub">Nothing is tagged yet — add one with + TAG on a row below.</span>
              )}
            </div>
            <div className="field">
              <label htmlFor="f-q">Name contains</label>
              <input
                id="f-q"
                type="text"
                placeholder="shampoo"
                value={q}
                onChange={(e) => setQ(e.target.value)}
              />
            </div>
          </div>

          {fCat && prods?.filter.included_children.length ? (
            <p className="cat-note">
              Showing {filterLabel} and the {prods.filter.included_children.length} categor
              {prods.filter.included_children.length === 1 ? 'y' : 'ies'} inside it.
            </p>
          ) : null}

          {fileErr && (
            <Refusal
              reason={fileErr.reason}
              detail={fileErr.detail}
              hint="Nothing moved. Every product is on the shelf it was on before the press."
            />
          )}
          {fileFlash && <Verdict tone="info" title={fileFlash.title}>{fileFlash.body}</Verdict>}

          {prodErr && (
            <Refusal
              reason="The product list could not be read"
              detail={prodErr.reason}
              hint={prodErr.detail}
              action={
                <button className="btn sm" onClick={() => void loadProducts({ category: fCat, tag: fTag, q: qLive })}>
                  TRY AGAIN
                </button>
              }
            />
          )}

          {loadingProds && !prods && (
            <div className="cat-skels" aria-hidden="true">
              <div className="skel" style={{ height: 40 }} />
              <div className="skel" style={{ height: 40 }} />
              <div className="skel" style={{ height: 40 }} />
              <div className="skel" style={{ height: 40 }} />
            </div>
          )}

          {/* EVERY FILTER IS A SERVER QUERY. Typing "shampoo" sends one, and
              the rows on screen are the answer to the LAST one until the new
              one lands. They stay — an answer a quarter of a second old is
              better than four skeletons — and this says a newer one is coming. */}
          {loadingProds && prods && (
            <p className="cat-working"><Working /> asking the server for that filter</p>
          )}

          {prods && !prods.products.length && (
            prods.catalogue_size === 0 ? (
              <Empty
                title="Nothing is priced yet"
                action={<a className="btn sm" href="#/products">TEACH A PRODUCT</a>}
              >
                A product taught without a price cannot be filed. Put a price on it in Products and
                it appears here.
              </Empty>
            ) : (
              <Empty
                title="No product matches those filters"
                action={(fCat || fTag || q) ? (
                  <button
                    className="btn sm"
                    onClick={() => { setFCat(''); setFTag(''); setQ(''); }}
                  >
                    CLEAR THE FILTERS
                  </button>
                ) : undefined}
              >
                The catalogue has {prods.catalogue_size} priced products in it — none of them is on
                this shelf, under this tag, or spelled that way.
              </Empty>
            )
          )}

          {prods && prods.products.length > 0 && (
            <>
              {/* Every SHELF control below is off until there is a shelf to
                  put something on. Said once, here, rather than twenty times
                  as twenty grey selects with no explanation. */}
              {!cats.length && (
                <p className="cat-note">
                  The shelf control on every row is off because this shop has no shelves yet. Make
                  one on the left and each of these becomes a menu.
                </p>
              )}
              {/* The bulk bar only exists while something is selected — a control
                  that is always there and usually does nothing teaches people to
                  ignore the row it sits in — and it sits ABOVE the list, pinned
                  under the top bar. Below it, a floating bar covers the rows you
                  are still working down. */}
              {picked.size > 0 && (
                <div className="cat-bulk">
                  <b className="tnum">{picked.size} selected</b>
                  <select
                    value={bulkCat}
                    aria-label="File the selected products under"
                    onChange={(e) => setBulkCat(e.target.value)}
                  >
                    <option value="">File them under…</option>
                    {cats.map((c) => (
                      <option key={c.category_id} value={c.category_id}>
                        {c.parent_id ? `— ${c.name}` : c.name}
                      </option>
                    ))}
                    <option value={cat.NO_CATEGORY}>Take them off the shelf</option>
                  </select>
                  <button
                    className="btn primary sm"
                    disabled={bulkBusy || !bulkCat}
                    onClick={() => void fileSelected()}
                  >
                    {bulkBusy ? 'FILING…' : 'FILE'}
                  </button>
                  <button className="btn sm ghost" onClick={() => setPicked(new Set())}>CLEAR</button>
                  <span className="sub">
                    All or nothing: if one product cannot be filed, none of them move.
                  </span>
                </div>
              )}

              <div className="cat-prods">
                <div className="cat-prod head" aria-hidden="true">
                  <span />
                  <span>PRODUCT</span>
                  <span className="num">CHARGED</span>
                  <span>SHELF</span>
                  <span>TAGS</span>
                </div>
                {prods.products.map((p) => {
                  const busy = busySku === p.sku_id;
                  const on = picked.has(p.sku_id);
                  return (
                    <div className={`cat-prod${on ? ' on' : ''}`} key={p.sku_id}>
                      <input
                        type="checkbox"
                        checked={on}
                        aria-label={`Select ${p.name}`}
                        onChange={(e) => {
                          const next = new Set(picked);
                          if (e.target.checked) next.add(p.sku_id); else next.delete(p.sku_id);
                          setPicked(next);
                        }}
                      />
                      <div className="cat-prod-main">
                        <span className="nm">{p.name}</span>
                        <span className="mono">{p.sku_id} · {p.taught_with.replace(/_/g, ' ')}</span>
                      </div>
                      <div className="cat-prod-price tnum">
                        {p.marked_paise !== undefined && (
                          <s>{money({ price_paise: p.marked_paise, price_rupees: p.marked_rupees ?? '' })}</s>
                        )}
                        <b>{money(p)}</b>
                      </div>
                      <div className="cat-prod-shelf">
                        <select
                          value={p.category_id ?? ''}
                          disabled={busy || !cats.length}
                          title={busy
                            ? 'This product is being filed.'
                            : !cats.length ? 'There are no shelves yet — make one on the left.' : undefined}
                          aria-label={`Shelf for ${p.name}`}
                          onChange={(e) => void setShelf(p.sku_id, e.target.value || null)}
                        >
                          <option value="">Not filed</option>
                          {cats.map((c) => (
                            <option key={c.category_id} value={c.category_id}>
                              {c.parent_id ? `— ${c.name}` : c.name}
                            </option>
                          ))}
                        </select>
                      </div>
                      <div className="cat-prod-tags">
                        {p.tags.map((t) => (
                          <span className="cat-chip" key={t}>
                            {t}
                            <button
                              type="button"
                              aria-label={`Remove tag ${t} from ${p.name}`}
                              disabled={busy}
                              onClick={() => void setTags(p.sku_id, p.tags.filter((x) => x !== t))}
                            ><Cross /></button>
                          </span>
                        ))}
                        {tagFor === p.sku_id ? (
                          <span className="cat-tagadd">
                            <input
                              ref={tagInput}
                              type="text"
                              value={tagDraft}
                              maxLength={book?.limits.max_tag ?? 24}
                              placeholder="daily"
                              aria-label={`New tag for ${p.name}`}
                              onChange={(e) => setTagDraft(e.target.value)}
                              onKeyDown={(e) => {
                                if (e.key === 'Escape') { setTagFor(null); setTagDraft(''); }
                                if (e.key !== 'Enter') return;
                                const t = cat.cleanTag(tagDraft);
                                if (cat.tagProblem(tagDraft, book?.limits)) return;
                                void setTags(p.sku_id, [...p.tags, t]);
                              }}
                            />
                            {/* An empty box is a box nobody has typed in yet, not
                                a mistake. The problem is only named once there
                                is something in it to be wrong about. */}
                            <span className="sub">
                              {(tagDraft.trim() && cat.tagProblem(tagDraft, book?.limits))
                                || (p.tags.length >= (book?.limits.max_tags_per_sku ?? 12)
                                  ? `This product already has ${p.tags.length} tags, which is the cap.`
                                  : 'Enter to add. Tags are stored lowercase.')}
                            </span>
                          </span>
                        ) : (
                          <button
                            className="cat-chip add"
                            disabled={busy}
                            onClick={() => { setTagFor(p.sku_id); setTagDraft(''); }}
                          >+ TAG</button>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            </>
          )}
        </Card>
      </div>

      {/* ------------------------------------------------------- proposals -- */}
      <Card
        title="What the names suggest"
        aside={<Pill tone="code">NOTHING IS FILED UNTIL YOU ACCEPT</Pill>}
      >
        <p className="cat-lede">
          {sug?.how ?? 'The product’s name is lowercased and split into words. The first rule with a '
            + 'matching whole word wins, so the order of the rules is the tie-break.'}
          {' '}The same catalogue proposes the same things tomorrow.
        </p>

        <div className="cat-sugbar">
          <Segmented
            value={overFiled}
            onChange={setOverFiled}
            options={[
              { value: 'new', label: 'UNFILED ONLY', title: 'Leave alone what you have already decided' },
              { value: 'all', label: 'EVERYTHING', title: 'Also propose over products already filed' },
            ]}
          />
          <div className="spacer" />
          {sug && sug.already_categorised > 0 && overFiled === 'new' && (
            <span className="sub">{sug.already_categorised} already filed and left alone.</span>
          )}
        </div>

        {acceptErr && (
          <Refusal
            reason={acceptErr.reason}
            detail={acceptErr.detail}
            hint="The lines you ticked are still ticked. Nothing else was filed."
          />
        )}
        {acceptFlash && <Verdict tone="info" title={acceptFlash.title}>{acceptFlash.body}</Verdict>}

        {/* CREATE ALL IS N REQUESTS, NOT ONE. There is no route that makes
            several categories at once, so twelve shelves is twelve round trips
            — and the count is this browser's own, which is why it may be shown. */}
        {made && (
          <div className="cat-run">
            <div className="cat-run-head">
              <Working />
              <span>Making the shelves, one request each</span>
              <span className="tnum">{made.done} / {made.of}</span>
            </div>
            <Progress pct={(made.done / Math.max(1, made.of)) * 100} label="Categories created" />
          </div>
        )}

        {sugErr && (
          <Refusal
            reason="The proposals could not be worked out"
            detail={sugErr.reason}
            hint={sugErr.detail}
            action={
              <button className="btn sm" onClick={() => void loadSuggest(overFiled === 'all')}>TRY AGAIN</button>
            }
          />
        )}

        {loadingSug && !sug && (
          <div className="cat-skels" aria-hidden="true">
            <div className="skel" style={{ height: 38 }} />
            <div className="skel" style={{ height: 38 }} />
            <div className="skel" style={{ height: 38 }} />
          </div>
        )}

        {loadingSug && sug && (
          <p className="cat-working"><Working /> reading the names again</p>
        )}

        {sug && sug.missing_categories.length > 0 && (
          <Verdict tone="info" title={`${sug.missing_categories.length} of these shelves do not exist yet`}>
            {sug.missing_categories.join(', ')}. A proposal cannot be accepted into a category this
            shop has not made. Create the ones you want — or make your own and file by hand.
            <span className="btn-row" style={{ display: 'flex', marginTop: 12 }}>
              <button className="btn sm" disabled={acceptBusy} onClick={() => void makeMissing()}>
                {acceptBusy ? 'CREATING…' : `CREATE ALL ${sug.missing_categories.length}`}
              </button>
            </span>
          </Verdict>
        )}

        {sug && !sug.proposals.length && (
          sug.already_categorised > 0 && overFiled === 'new' ? (
            <Empty
              title="Every priced product is already filed"
              action={(
                <button className="btn sm" onClick={() => setOverFiled('all')}>
                  SHOW WHAT IT WOULD HAVE SAID
                </button>
              )}
            >
              There is nothing left to propose over. Switch to EVERYTHING and the keyword list will
              read the names you have already decided about — it still files nothing until you tick.
            </Empty>
          ) : (
            <Empty title="The keyword list matched nothing here">
              File by hand on the left. An unmatched product is a small nuisance; a confidently
              wrong one is a menu quietly filling with mistakes, which is why the rules stay out of
              honestly ambiguous words.
            </Empty>
          )
        )}

        {sug && sug.proposals.length > 0 && (
          <>
            <div className="cat-props">
              <div className="cat-prop head" aria-hidden="true">
                <span />
                <span>PRODUCT</span>
                <span>BECAUSE OF THE WORD</span>
                <span>PROPOSED SHELF</span>
              </div>
              {sug.proposals.map((p) => {
                const on = ticked.has(p.sku_id);
                return (
                  <div className={`cat-prop${on ? ' on' : ''}${p.ready ? '' : ' cold'}`} key={p.sku_id}>
                    <input
                      type="checkbox"
                      checked={on}
                      disabled={!p.ready}
                      aria-label={`Accept ${p.suggested_name} for ${p.name}`}
                      onChange={(e) => {
                        const next = new Set(ticked);
                        if (e.target.checked) next.add(p.sku_id); else next.delete(p.sku_id);
                        setTicked(next);
                      }}
                    />
                    <div className="cat-prop-main">
                      <span className="nm">{p.name}</span>
                      {p.currently && <span className="sub">already filed — accepting would move it</span>}
                    </div>
                    <div className="cat-prop-why">
                      <span className="cat-word">{p.matched_keyword}</span>
                    </div>
                    <div className="cat-prop-to">
                      {p.suggested_name}
                      {!p.ready && <span className="sub">this shop has no {p.suggested_name} yet</span>}
                    </div>
                  </div>
                );
              })}
            </div>

            <div className="cat-accept">
              <button
                className="btn sm"
                disabled={!ready.length}
                title={!ready.length ? 'Every proposal here names a shelf this shop has not made yet.' : undefined}
                onClick={() => setTicked(new Set(ready.map((p) => p.sku_id)))}
              >{ready.length ? `SELECT ALL ${ready.length}` : 'SELECT ALL'}</button>
              <button
                className="btn sm ghost"
                disabled={!ticked.size}
                title={!ticked.size ? 'Nothing is ticked.' : undefined}
                onClick={() => setTicked(new Set())}
              >
                CLEAR
              </button>
              <div className="spacer" />
              <button
                className="btn primary"
                disabled={acceptBusy || !ticked.size}
                title={!acceptBusy && !ticked.size
                  ? 'Tick the lines you agree with. Nothing is filed until you do.'
                  : undefined}
                onClick={() => void acceptTicked()}
              >
                {acceptBusy ? 'FILING…' : ticked.size ? `ACCEPT ${ticked.size}` : 'ACCEPT'}
              </button>
              {/* Why ACCEPT is off. It is off on arrival, every time, because
                  the proposals come UNTICKED on purpose. */}
              {!ticked.size && !acceptBusy && (
                <span className="sub cat-accept-why">
                  ACCEPT is off until you tick a line. These arrive unticked on purpose — the
                  keyword list files a bathing bar under Household if nobody reads it.
                </span>
              )}
            </div>

            <p className="cat-note">{sug.note}</p>
          </>
        )}

        {sug && sug.unmatched.length > 0 && (
          <details className="cat-details">
            <summary>{sug.unmatched.length} products the keyword list would not guess at</summary>
            <p className="cat-note">
              Words that are honestly ambiguous — powder is chilli powder and it is talcum powder — are
              in no rule at all, so these come back unmatched rather than confidently wrong. File them
              on the left.
            </p>
            <div className="cat-unmatched">
              {sug.unmatched.map((u) => (
                <span className="cat-chip flat" key={u.sku_id}>{u.name}</span>
              ))}
            </div>
          </details>
        )}

        {sug && (
          <details className="cat-details">
            <summary>The whole keyword list, in the order it is read</summary>
            <p className="cat-note">
              The first rule with a matching word wins, so the order is the tie-break. Baby soap is Baby
              rather than Household because Baby is read first.
            </p>
            <div className="cat-rules">
              {sug.rules.map((r) => (
                <div className="cat-rule" key={r.category}>
                  <b>{r.category}</b>
                  <div className="cat-words">
                    {r.keywords.map((w) => <span className="cat-chip flat" key={w}>{w}</span>)}
                  </div>
                </div>
              ))}
            </div>
          </details>
        )}
      </Card>

      {/* ---------------------------------------------------------- health -- */}
      {/* THIS CARD IS FOR THE BAD CASE. Its whole argument is that a counter
          pointed at the wrong shop directory would describe a shop that is not
          this one — so the panel that vanished silently when the check failed
          was the panel failing at the one job it has. */}
      {loadingHealth && !health && !healthErr && (
        <Card title="Where the filing lives">
          <LoadingCard lines={5} label="Reading where the filing lives" />
        </Card>
      )}
      {healthErr && (
        <Card title="Where the filing lives">
          <Refusal
            reason="Where the filing lives could not be read"
            detail={healthErr.reason}
            hint={healthErr.detail ?? 'The shelves above may still be right; this is the check that says so, and it did not answer.'}
            action={<button className="btn sm" onClick={() => void loadHealth()}>TRY AGAIN</button>}
          />
        </Card>
      )}
      {health && (
        <Card title="Where the filing lives">
          <p className="cat-lede">
            The categories sit in a file BESIDE the catalogue and never inside it. If this counter was
            started pointing at a different shop directory than the one holding the products, the
            filing would describe a shop that is not this one — so the resolved path is printed rather
            than assumed.
          </p>
          <KV k="Categories file"><span className="mono">{health.file}</span></KV>
          <KV k="On disk">{health.exists ? 'yes' : 'not written yet'}</KV>
          <KV k="Its own audit chain"><span className="mono">{health.audit_file}</span></KV>
          <KV k="Catalogue readable">{health.catalogue_readable ? 'yes' : 'no'}</KV>
          <KV k="Categories">{health.categories} · {health.top_level} top level · {health.nested} nested</KV>
          <KV k="Owns catalog.json">no — deleting this file loses the grouping and no products</KV>
          {health.orphans > 0 && (
            <KV k="Filings for products that left">
              {health.orphans} kept, not swept — a product taught again under the same id gets its shelf back
            </KV>
          )}
        </Card>
      )}
    </div>
  );
}
