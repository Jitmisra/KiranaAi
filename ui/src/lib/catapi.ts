/**
 * Every request the Categories screen makes, in one place, with types.
 *
 * What this module is filing, and what it is deliberately not:
 *
 *  1. FILING IS NOT THE CATALOGUE. `gawaah/categories.py` writes a sidecar next
 *     to `catalog.json` and never opens the catalogue itself. Nothing here can
 *     rename a product, move a price or delete stock, and there is no function
 *     in this file that sends any of those. Deleting a category takes a shelf
 *     label away and leaves every packet where it was.
 *  2. THE BROWSER IS NEVER AN AUTHOR OF MONEY. `price_paise` arrives on a
 *     product row because a list of four hundred products is unreadable without
 *     it. It is read-through only: it is displayed and never sent back, and no
 *     function here computes, adjusts or totals a price.
 *  3. A REFUSAL IS A RESULT, NOT AN ERROR. `/categories/*` answers
 *     `{ok:false, reason, detail}` with a 400 on purpose, so the body is parsed
 *     on non-2xx rather than thrown. Only a transport failure produces a
 *     refusal about the network, and it says so in those words, because a dead
 *     server and a refused edit need different fixes.
 *  4. A PROPOSAL IS NOT AN ASSIGNMENT. `suggest()` is a GET that changes
 *     nothing on disk; accepting is a separate `assign()` carrying only the
 *     lines a person ticked. There is no call in this file that does both.
 *
 * `send` is duplicated from `api.ts` rather than imported: it is module-private
 * there, and a new screen must not widen the till's own request layer to borrow
 * one function. The precedence rule it encodes — an explicit `ok`, then the
 * HTTP STATUS — is load-bearing and copied intact. FastAPI's own 422 comes back
 * as `{"detail": ...}` with no `ok` at all, and a rule that read only the body
 * would file a validation crash as a SUCCESS and hand the caller an object
 * whose every field is undefined.
 *
 * NOTE ON DEV: `vite.config.ts` proxies an explicit allowlist of path prefixes
 * to the till on :8790. `/categories` has to be in that list or every request
 * here 404s under `npm run dev` while working perfectly in the built site.
 */

import type { Result } from './api';
import type { Paise } from './money';

async function send<T>(url: string, init?: RequestInit): Promise<Result<T>> {
  let res: Response;
  try {
    res = await fetch(url, { cache: 'no-store', ...init });
  } catch (e) {
    // The network, not the product. Say which, because they need different fixes.
    return { ok: false, reason: 'the counter could not reach its own server', detail: String(e) };
  }
  let body: Record<string, unknown>;
  try {
    body = (await res.json()) as Record<string, unknown>;
  } catch {
    return { ok: false, reason: `server replied ${res.status} with something that was not JSON` };
  }
  if (body && body.ok === undefined) {
    if (!res.ok) {
      return {
        ok: false,
        reason: `the server refused with HTTP ${res.status}`,
        detail: typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail ?? body),
      };
    }
    return { ...body, ok: true } as unknown as Result<T>;
  }
  return body as unknown as Result<T>;
}

const jsonBody = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
});

/* ------------------------------------------------------------- the shapes -- */

/** One shelf label, as the server describes it. */
export interface CategoryRow {
  category_id: string;
  name: string;
  /** null is top level. One level of nesting is the whole tree — see `nesting`. */
  parent_id: string | null;
  sort_order: number;
  created_at: string;
  depth: number;
  parent_name: string | null;
  children: string[];
  /** Products filed directly under this category. */
  products: number;
  /** ...and the ones under its children, which is what a shopkeeper tapping it means. */
  products_including_children: number;
}

export interface TagCount {
  tag: string;
  products: number;
}

export interface CategoryLimits {
  max_categories: number;
  max_name: number;
  max_tags_per_sku: number;
  max_tag: number;
  /** The server's own sentence about nesting. Shown verbatim, never paraphrased. */
  nesting: string;
}

export interface CategoryBook {
  count: number;
  categories: CategoryRow[];
  /** Everything this shop has PRICED. A product without a price cannot be filed. */
  products: number;
  categorised: number;
  uncategorised: number;
  tags: TagCount[];
  limits: CategoryLimits;
  file: string;
}

/**
 * One product with its filing attached.
 *
 * The money is the server's, in integer paise, and travels one way. `marked_*`
 * appears only when an offer is on: the product's marked price beside what is
 * actually charged.
 */
export interface ProductRow {
  sku_id: string;
  name: string;
  price_paise: Paise;
  price_rupees: string;
  marked_paise?: Paise;
  marked_rupees?: string;
  off_paise?: Paise;
  taught_with: string;
  category_id: string | null;
  category_name: string | null;
  parent_name: string | null;
  tags: string[];
}

export interface ProductList {
  count: number;
  products: ProductRow[];
  filter: {
    category: string | null;
    tag: string | null;
    q: string | null;
    /** The child categories a parent filter swept in with it. */
    included_children: string[];
  };
  catalogue_size: number;
  paginated: boolean;
}

/** One proposal. NOTHING has been filed — `ready` says whether it could be. */
export interface Proposal {
  sku_id: string;
  name: string;
  suggested_name: string;
  /** null when this shop has not created a category by that name yet. */
  category_id: string | null;
  /** The word in the product's name that decided it. This is the whole "why". */
  matched_keyword: string;
  ready: boolean;
  /** What it is filed under now, if anything. */
  currently: string | null;
}

export interface Rule {
  category: string;
  keywords: string[];
}

export interface Suggestions {
  method: string;
  how: string;
  count: number;
  proposals: Proposal[];
  unmatched: Array<{ sku_id: string; name: string }>;
  already_categorised: number;
  missing_categories: string[];
  rules: Rule[];
  accept_with: string;
  changed_nothing: boolean;
  note: string;
}

export interface CategoriesHealth {
  module: string;
  /** The resolved sidecar path. Printed on screen: see the note in Categories.tsx. */
  file: string;
  exists: boolean;
  audit_file: string;
  shop_dir: string;
  catalogue_readable: boolean;
  categories: number;
  top_level: number;
  nested: number;
  filed: number;
  /** Filings for products that have left the catalogue. Kept, not swept. */
  orphans: number;
  owns_catalog_json: boolean;
}

export interface Created {
  category: CategoryRow;
  audited: boolean;
}

export interface Edited {
  category: CategoryRow;
  was: Omit<CategoryRow, 'depth' | 'parent_name' | 'children' | 'products' | 'products_including_children'>;
  audited: boolean;
}

/** What a delete actually did. `products_deleted` is always 0 and is shown. */
export interface Deleted {
  category_id: string;
  removed: string;
  uncategorised: number;
  children_promoted: number;
  promoted: string[];
  products_deleted: number;
  audited: boolean;
  note: string;
}

export interface Assigned {
  considered: number;
  changed: number;
  unchanged: number;
  uncategorised: number;
  audited: boolean;
  note: string;
}

export interface Filed {
  product: ProductRow;
  was_category_id: string | null;
  audited: boolean;
}

/* --------------------------------------------------------------- requests -- */

/** What `?category=` means when you want the products that are in nothing. */
export const NO_CATEGORY = 'none';

export const list = () => send<CategoryBook>('/categories');

export const health = () => send<CategoriesHealth>('/categories/health');

export interface ProductFilter {
  /** A category id, or `NO_CATEGORY` for everything unfiled. */
  category?: string;
  tag?: string;
  q?: string;
}

export function products(filter: ProductFilter = {}) {
  const q = new URLSearchParams();
  if (filter.category) q.set('category', filter.category);
  if (filter.tag) q.set('tag', filter.tag);
  if (filter.q && filter.q.trim()) q.set('q', filter.q.trim());
  const qs = q.toString();
  return send<ProductList>(`/categories/products${qs ? `?${qs}` : ''}`);
}

/**
 * Propose a category per product from its NAME.
 *
 * A GET, and it writes nothing — the server asserts that about itself. Products
 * already filed are left out unless `includeAssigned`, because re-proposing over
 * a decision a person already made is how a suggestion turns into a nuisance.
 */
export const suggest = (includeAssigned = false) =>
  send<Suggestions>(`/categories/suggest${includeAssigned ? '?include_assigned=1' : ''}`);

export const create = (body: { name: string; parent_id?: string | null; sort_order?: number }) =>
  send<Created>('/categories', jsonBody('POST', body));

/**
 * Rename a category, move it under a parent, or change its place in the menu.
 *
 * The id never moves, so every product filed under it stays filed under it —
 * which is why a rename is safe and why the id is not derived from the name.
 * Send `parent_id: null` to bring a child back to the top level.
 */
export const edit = (
  categoryId: string,
  body: { name?: string; parent_id?: string | null; sort_order?: number },
) => send<Edited>(`/categories/${encodeURIComponent(categoryId)}`, jsonBody('PATCH', body));

export const remove = (categoryId: string) =>
  send<Deleted>(`/categories/${encodeURIComponent(categoryId)}`, { method: 'DELETE' });

/**
 * File several products at once. This is where an accepted proposal lands.
 *
 * ALL OR NOTHING on the server: every line is validated before any line is
 * written, so a list with one unknown sku changes nothing rather than filing
 * half of it. The page relies on that and never splits a batch to get past a
 * refusal — half a filing a shopkeeper cannot see the edge of is worse than
 * none.
 */
export const assign = (rows: ReadonlyArray<{ sku_id: string; category_id: string | null }>) =>
  send<Assigned>('/categories/assign', jsonBody('POST', { assign: rows }));

/**
 * File ONE product, set its tags, or both.
 *
 * ABSENT AND NULL ARE DIFFERENT INSTRUCTIONS and the server draws that line: a
 * key that is not sent means "leave this alone", `category_id: null` means "take
 * it off the shelf", and `tags: []` means "clear its tags". So only send the
 * half the shopkeeper touched.
 */
export const fileSku = (skuId: string, body: { category_id?: string | null; tags?: string[] }) =>
  send<Filed>(`/categories/sku/${encodeURIComponent(skuId)}`, jsonBody('PUT', body));

/* ------------------------------------------------------------ the ordering -- */

export interface SortMove {
  category_id: string;
  sort_order: number;
}

/** The gap the server leaves between two default positions. Kept in step. */
export const SORT_STEP = 10;

/**
 * One item moved up or down its own level, as a new list. Pure.
 *
 * Returns the list unchanged when the move would fall off either end, so the
 * caller can compare and skip the save rather than ask the server to change
 * nothing and be refused for it.
 */
export function moved<T>(rows: readonly T[], index: number, delta: number): T[] {
  const to = index + delta;
  if (index < 0 || index >= rows.length || to < 0 || to >= rows.length) return [...rows];
  const out = [...rows];
  const [item] = out.splice(index, 1);
  out.splice(to, 0, item as T);
  return out;
}

/**
 * The saves that turn a desired order into stored sort orders.
 *
 * Renumbering the whole level in steps of ten — rather than swapping two
 * numbers — is what keeps the menu sane when a hand-edited file has left three
 * categories all sitting on 0, where a swap would visibly do nothing. Rows
 * whose number is already right are left out, because the server refuses a
 * PATCH that changes nothing and that refusal would be reported as a failure.
 */
export function renumber(siblings: ReadonlyArray<{ category_id: string; sort_order: number }>): SortMove[] {
  const out: SortMove[] = [];
  siblings.forEach((c, i) => {
    const want = (i + 1) * SORT_STEP;
    if (c.sort_order !== want) out.push({ category_id: c.category_id, sort_order: want });
  });
  return out;
}

/**
 * Apply an order, one PATCH at a time.
 *
 * NOT ATOMIC, and the screen says so where it offers the arrows. There is no
 * bulk-reorder route — the module has one category per PATCH — so a failure
 * partway leaves the level half renumbered. It stops at the first refusal and
 * hands it back with the count that DID land, so the page can reload and show
 * the true order rather than the one it hoped for.
 */
export async function saveOrder(moves: readonly SortMove[]): Promise<Result<{ saved: number }>> {
  let saved = 0;
  for (const m of moves) {
    const r = await edit(m.category_id, { sort_order: m.sort_order });
    if (!r.ok) {
      return {
        ok: false,
        reason: r.reason,
        detail: `${r.detail ?? ''} ${saved} of ${moves.length} positions were saved before this. `
          + 'Reordering is one save per category and there is no route that does them together.',
      };
    }
    saved += 1;
  }
  return { ok: true, saved };
}

/* ------------------------------------------------------------- the tagging -- */

/**
 * A tag as the server will store it: lowercase, single-spaced.
 *
 * The page normalises before it sends so the chip a shopkeeper sees is the chip
 * that lands — `Daily`, `daily` and `daily ` are one tag, and finding that out
 * only after saving reads as the counter having ignored what was typed. The
 * server does this again on arrival; this is not the check, it is the preview.
 */
export function cleanTag(raw: string): string {
  return raw.split(/\s+/).filter(Boolean).join(' ').toLowerCase();
}

/** Why a tag would be refused, in the shopkeeper's words, or null if it is fine. */
export function tagProblem(raw: string, limits?: CategoryLimits): string | null {
  const tag = cleanTag(raw);
  const cap = limits?.max_tag ?? 24;
  if (!tag) return 'An empty tag is not a tag.';
  if (tag.length > cap) return `That is ${tag.length} characters and the cap is ${cap}.`;
  if (!/^[a-z0-9][a-z0-9 _-]*$/.test(tag)) {
    return 'A tag is letters, digits, spaces, - and _, and it starts with a letter or a digit.';
  }
  return null;
}
