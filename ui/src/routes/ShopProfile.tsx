import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import * as admin from '../lib/adminapi';
import {
  Button, Card, Empty, KV, Modal, Pill, Refusal, Skeleton, Verdict,
} from '../components/ui';
import '../styles/admin.css';
import '../styles/manage.css';
import '../styles/shopface.css';

/**
 * The shop's own name, address, phone and opening hours.
 *
 * A customer scans the QR on the shutter and lands on a catalogue. Until this
 * screen existed that catalogue belonged to NOBODY: no name at the top, no
 * address, no number to ring when the order did not arrive. A printed QR
 * pointing at an anonymous list of prices is not a shop, and no amount of
 * recognition accuracy fixes that.
 *
 * It is one short form, filled in once. Everything on it is validated by the
 * server — this page does not decide what a phone number is, does not
 * normalise one, and does not construct the stored document. It sends what was
 * typed and renders what came back.
 *
 * THIS IS A FORM NOBODY HAD WATCHED FAIL. The server refuses by name, field by
 * field: an address of four characters, a landline where a mobile is needed, no
 * day ticked. Every one of those used to land in one panel under the SAVE
 * button, below the fold on a laptop, describing a box a screen away. They are
 * routed to the control they are about now — verbatim, reason and detail — and
 * the same refusal is never printed twice.
 *
 * COLOUR. Green, amber and red are money and recognition on this product, and
 * this screen has no money on it. A saved profile is BLUE — the machine's own
 * mark — and so is every state pill here. The green "set" pill and the seven
 * green day-pills this screen used to draw were borrowing the colour that means
 * a gateway webhook settled a payment.
 */

const DEFAULT_OPEN = '07:00';
const DEFAULT_CLOSE = '21:00';

type Form = {
  name: string;
  address: string;
  phone: string;
  open: string;
  close: string;
  days: string[];
};

const BLANK: Form = {
  name: '',
  address: '',
  phone: '',
  open: DEFAULT_OPEN,
  close: DEFAULT_CLOSE,
  days: admin.DAYS.map((d) => d.key),
};

/* --------------------------------------------------------------------------
   WHICH BOX A REFUSAL BELONGS TO.

   `/shop/profile` refuses by name and every name says which field was wrong,
   so the reason goes back to the control the shopkeeper typed it in. Nothing
   here rewrites a refusal — the reason and the server's own detail are printed
   exactly as they came. A reason in none of these sets is not about a field
   (an unwritable file, a missing till) and stays a `Refusal` on the screen.
   -------------------------------------------------------------------------- */

const NAME_REASONS: ReadonlySet<string> = new Set(['shop_name_missing']);
const ADDRESS_REASONS: ReadonlySet<string> = new Set([
  'shop_address_missing', 'shop_address_too_short',
]);
const PHONE_REASONS: ReadonlySet<string> = new Set([
  'shop_phone_missing', 'shop_phone_not_an_indian_mobile',
]);
const HOURS_REASONS: ReadonlySet<string> = new Set([
  'opening_hours_not_a_time', 'opening_and_closing_time_are_the_same',
]);
const DAY_REASONS: ReadonlySet<string> = new Set([
  'shop_open_on_no_day', 'unknown_day_of_week',
]);

/** `field_too_long` names its own key first in the detail: "name is 300 …". */
function tooLongFor(r: admin.Refusal | null, key: 'name' | 'address' | 'phone'): boolean {
  return !!r && r.reason === 'field_too_long' && (r.detail ?? '').startsWith(`${key} `);
}

function belongsTo(
  r: admin.Refusal | null,
  reasons: ReadonlySet<string>,
  longKey?: 'name' | 'address' | 'phone',
): boolean {
  if (!r) return false;
  return reasons.has(r.reason) || (longKey !== undefined && tooLongFor(r, longKey));
}

/** The server's refusal, verbatim, under the control it is about. */
function FieldWords({ r }: { r: admin.Refusal }) {
  return (
    <span className="adm-err">
      <span className="adm-mono">{r.reason}</span>
      {r.detail ? <> — {r.detail}</> : null}
    </span>
  );
}

/** One labelled row of the form, with room for the reason underneath it. */
function Row({ label, htmlFor, sub, bad, refusal, children }: {
  label: ReactNode;
  htmlFor?: string;
  sub?: ReactNode;
  bad?: boolean;
  refusal?: admin.Refusal | null;
  children: ReactNode;
}) {
  return (
    <div className={bad ? 'adm-row bad' : 'adm-row'}>
      <label htmlFor={htmlFor}>{label}</label>
      {children}
      {sub !== undefined && <span className="adm-sub">{sub}</span>}
      {bad && refusal && <FieldWords r={refusal} />}
    </div>
  );
}

/** The form's shape, while it is on its way. Not one grey slab. */
function FormSkeleton() {
  return (
    <div className="adm-form" role="status" aria-label="Reading the shop’s details">
      {[[104, 44], [72, 84], [64, 44]].map(([w, h], i) => (
        <div className="adm-row" key={i} aria-hidden="true">
          <Skeleton w={w} h={9} radius={999} />
          <div style={{ marginTop: 8 }}><Skeleton h={h} radius={8} /></div>
          <div style={{ marginTop: 8 }}><Skeleton w="62%" h={9} radius={999} /></div>
        </div>
      ))}
      <div className="adm-row" aria-hidden="true">
        <Skeleton w={80} h={9} radius={999} />
        <div className="adm-days" style={{ marginTop: 8 }}>
          {admin.DAYS.map((d) => <Skeleton w={56} h={44} radius={8} key={d.key} />)}
        </div>
      </div>
    </div>
  );
}

export default function ShopProfile() {
  const [form, setForm] = useState<Form>(BLANK);
  const [saved, setSaved] = useState<admin.ShopProfileDoc | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [loadErr, setLoadErr] = useState<admin.Refusal | null>(null);
  const [refusal, setRefusal] = useState<admin.Refusal | null>(null);
  const [note, setNote] = useState<{ changed: string[]; first: boolean } | null>(null);
  const [confirmUndo, setConfirmUndo] = useState(false);

  /**
   * THE SHOP'S OWN LINK, AND ITS PICTURE.
   *
   * Both are the server's facts, read after the profile: the link because the
   * slug in it is minted by the server on the profile's first read, and the
   * picture because the URL that serves it is versioned by the server so a
   * replaced photograph is never shown stale. This page renders both; it
   * builds neither.
   */
  const [link, setLink] = useState<admin.ShopLinkRead | null>(null);
  const [linkErr, setLinkErr] = useState<admin.Refusal | null>(null);
  /** Bumped after a renew so the <img> asks for the code again. */
  const [qrKey, setQrKey] = useState(0);
  const [said, setSaid] = useState('');
  const [confirmRenew, setConfirmRenew] = useState(false);
  const [renewed, setRenewed] = useState<admin.ShopLinkRenewed | null>(null);

  const [photoUrl, setPhotoUrl] = useState<string | null>(null);
  const [photoBusy, setPhotoBusy] = useState(false);
  const [photoRefusal, setPhotoRefusal] = useState<admin.Refusal | null>(null);
  const [photoNote, setPhotoNote] = useState<admin.ShopPhotoStored | null>(null);
  const [confirmRemovePhoto, setConfirmRemovePhoto] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const loadFace = useCallback(async () => {
    const [l, s] = await Promise.all([admin.shopLink(), admin.storeShop()]);
    if (l.ok) { setLink(l); setLinkErr(null); } else { setLink(null); setLinkErr(l); }
    // The open header is what the customer's phone reads, so the picture is
    // taken from there rather than from a field this page keeps itself.
    if (s.ok) setPhotoUrl(s.photo_url);
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    const r = await admin.shopProfile();
    setLoading(false);
    if (!r.ok) {
      // An unreadable profile and an unset one are different facts. Rendering
      // both as an empty form would invite a shopkeeper to type their shop's
      // name in again over a file the server could not read.
      setLoadErr(r);
      return;
    }
    setLoadErr(null);
    setSaved(r.profile);
    if (r.profile) setForm(toForm(r.profile));
    void loadFace();
  }, [loadFace]);

  useEffect(() => { void load(); }, [load]);

  /** A press has to have an answer, and the answer has to go away again. */
  const say = useCallback((words: string) => {
    setSaid(words);
    window.setTimeout(() => setSaid(''), 4000);
  }, []);

  const copyLink = useCallback(async () => {
    if (!link) return;
    try {
      await navigator.clipboard.writeText(link.url);
      say('Link copied.');
    } catch {
      say('This browser would not let the page copy. Long-press the address and copy it.');
    }
  }, [link, say]);

  /**
   * PRINTING THE STICKER FROM THIS PAGE, not from a popup.
   *
   * A `@media print` block in shopface.css hides everything but the sticker
   * while `body` carries `shf-printing`; the class is put on before the print
   * dialog and taken off after it, so the screen comes back whole whether the
   * shopkeeper printed or cancelled. No new window, no document.write, no
   * second copy of the code — the one on screen is the one on paper.
   */
  const printSticker = useCallback(() => {
    const off = () => {
      document.body.classList.remove('shf-printing');
      window.removeEventListener('afterprint', off);
    };
    window.addEventListener('afterprint', off);
    document.body.classList.add('shf-printing');
    window.print();
  }, []);

  const renew = useCallback(async () => {
    setConfirmRenew(false);
    const r = await admin.renewShopLink();
    if (!r.ok) { setLinkErr(r); return; }
    setRenewed(r);
    setLink(r);
    setQrKey((k) => k + 1);
    setSaved((s) => (s ? { ...s, slug: r.slug } : s));
  }, []);

  const uploadPhoto = useCallback(async (file: File) => {
    setPhotoBusy(true);
    setPhotoRefusal(null);
    setPhotoNote(null);
    try {
      // The browser's own base64, prefix and all. The server strips the
      // `data:image/…;base64,` itself, decodes, downscales and caps — this
      // page sends the file it was handed and nothing derived from it.
      const b64 = await new Promise<string>((resolve, reject) => {
        const fr = new FileReader();
        fr.onload = () => resolve(String(fr.result ?? ''));
        fr.onerror = () => reject(fr.error);
        fr.readAsDataURL(file);
      });
      const r = await admin.setShopPhoto(b64);
      if (!r.ok) { setPhotoRefusal(r); return; }
      setPhotoNote(r);
      setPhotoUrl(r.photo_url);
    } finally {
      setPhotoBusy(false);
      if (fileRef.current) fileRef.current.value = '';
    }
  }, []);

  const removePhoto = useCallback(async () => {
    setConfirmRemovePhoto(false);
    setPhotoBusy(true);
    setPhotoRefusal(null);
    setPhotoNote(null);
    try {
      const r = await admin.setShopPhoto('');
      if (!r.ok) { setPhotoRefusal(r); return; }
      setPhotoNote(r);
      setPhotoUrl(null);
    } finally {
      setPhotoBusy(false);
    }
  }, []);

  const save = useCallback(async () => {
    setBusy(true);
    setRefusal(null);
    setNote(null);
    try {
      // The three text fields and a list of day keys. Nothing derived, nothing
      // formatted: the server decides what a phone number is and what the
      // stored document looks like.
      const first = saved === null;
      const r = await admin.saveShopProfile({
        name: form.name,
        address: form.address,
        phone: form.phone,
        hours: { open: form.open, close: form.close, days: form.days },
      });
      if (!r.ok) { setRefusal(r); return; }
      setSaved(r.profile);
      setForm(toForm(r.profile));
      setNote({ changed: r.changed, first });
      // The first save mints the slug, so the link card has something to show
      // only now. A later save leaves the slug alone, and re-reading proves it.
      void loadFace();
    } finally {
      setBusy(false);
    }
  }, [form, saved, loadFace]);

  const toggleDay = useCallback((key: string) => {
    setForm((f) => ({
      ...f,
      days: f.days.includes(key) ? f.days.filter((d) => d !== key) : [...f.days, key],
    }));
  }, []);

  /** Anything typed at all — the preview draws from the form as it is typed. */
  const hasDraft = !!(form.name.trim() || form.address.trim() || form.phone.trim());

  /**
   * THE FORM AND THE SAVED DOCUMENT DISAGREE.
   *
   * The preview beside this form is drawn live from what is being typed. That
   * is the point of it — a typo is caught here rather than by a customer — but
   * it also means the preview shows something a customer is NOT being shown
   * until it is saved, and the screen used to say so only before the first
   * save. So it is worked out properly and said every time.
   */
  const dirty = useMemo(() => {
    if (!saved) return hasDraft;
    const was = toForm(saved);
    return was.name !== form.name
      || was.address !== form.address
      || was.phone !== form.phone
      || was.open !== form.open
      || was.close !== form.close
      || was.days.length !== form.days.length
      || admin.DAYS.some((d) => was.days.includes(d.key) !== form.days.includes(d.key));
  }, [saved, form, hasDraft]);

  const badName = belongsTo(refusal, NAME_REASONS, 'name');
  const badAddress = belongsTo(refusal, ADDRESS_REASONS, 'address');
  const badPhone = belongsTo(refusal, PHONE_REASONS, 'phone');
  const badHours = belongsTo(refusal, HOURS_REASONS);
  const badDays = belongsTo(refusal, DAY_REASONS);
  /** Said beside a control already, so it does not get a panel of its own too. */
  const refusalIsOnAField = badName || badAddress || badPhone || badHours || badDays;

  return (
    <div className="stack">
      <div className="page-head">
        <h1>The shop</h1>
        <p>
          Who this counter belongs to. A customer scanning the shutter QR sees this above the
          catalogue, and it is what a printed sheet carries. Fill it in once.
        </p>
      </div>

      <div className="grid two">
        <Card
          title="Name, address and hours"
          /* BLUE, never green. A saved setting is not a settled payment, and
             on this product green means exactly one thing. */
          aside={<Pill tone={saved ? 'code' : 'off'}>{saved ? 'saved' : 'not set yet'}</Pill>}
        >
          {loading ? (
            <FormSkeleton />
          ) : loadErr ? (
            <Refusal
              reason="The shop's details could not be read"
              detail={loadErr.reason}
              hint={loadErr.detail}
              action={<Button size="sm" onClick={() => void load()}>TRY AGAIN</Button>}
            />
          ) : (
            <div className="adm-form">
              {/* IDENTITY — what the board says. */}
              <div className="mg-group">
                <div className="mg-group-h">Identity</div>
                <Row
                  label="Shop name"
                  htmlFor="shop-name"
                  sub="the name on the board, as a customer would read it"
                  bad={badName}
                  refusal={refusal}
                >
                  <input
                    id="shop-name"
                    value={form.name}
                    onChange={(e) => setForm({ ...form, name: e.target.value })}
                    placeholder="Sharma Kirana Store"
                    autoComplete="off"
                    aria-invalid={badName || undefined}
                  />
                </Row>

                <Row
                  label="Address"
                  htmlFor="shop-address"
                  sub="enough for somebody to find the door"
                  bad={badAddress}
                  refusal={refusal}
                >
                  <textarea
                    id="shop-address"
                    value={form.address}
                    onChange={(e) => setForm({ ...form, address: e.target.value })}
                    placeholder={'12 MG Road, near the water tank\nIndiranagar, Bengaluru 560038'}
                    aria-invalid={badAddress || undefined}
                  />
                </Row>
              </div>

              {/* CONTACT — the number an order chases. */}
              <div className="mg-group">
                <div className="mg-group-h">Contact</div>
                <Row
                  label="Phone"
                  htmlFor="shop-phone"
                  sub={
                    <>
                      a mobile — it is what a customer rings when an order has not arrived. Type it
                      however you like; +91 and a leading 0 are both understood.
                    </>
                  }
                  bad={badPhone}
                  refusal={refusal}
                >
                  <input
                    id="shop-phone"
                    value={form.phone}
                    onChange={(e) => setForm({ ...form, phone: e.target.value })}
                    placeholder="98765 43210"
                    inputMode="tel"
                    autoComplete="off"
                    aria-invalid={badPhone || undefined}
                  />
                </Row>
              </div>

              {/* HOURS — when the shutter is up. */}
              <div className="mg-group">
                <div className="mg-group-h">Hours</div>
                <div className={badHours ? 'adm-row adm-times bad' : 'adm-row adm-times'}>
                  <div>
                    <label htmlFor="shop-open">Opens</label>
                    <input
                      id="shop-open"
                      type="time"
                      value={form.open}
                      onChange={(e) => setForm({ ...form, open: e.target.value })}
                      aria-invalid={badHours || undefined}
                    />
                  </div>
                  <div>
                    <label htmlFor="shop-close">Closes</label>
                    <input
                      id="shop-close"
                      type="time"
                      value={form.close}
                      onChange={(e) => setForm({ ...form, close: e.target.value })}
                      aria-invalid={badHours || undefined}
                    />
                  </div>
                </div>
                {badHours && refusal && (
                  <div className="adm-row"><FieldWords r={refusal} /></div>
                )}

                <div className={badDays ? 'adm-row bad' : 'adm-row'}>
                  <label id="shop-days-label">Open on</label>
                  {/* Toggles rather than a multi-select: the question a shopkeeper
                      is actually answering is "which day am I shut", and that is
                      only visible when all seven are on screen at once. */}
                  <div className="adm-days mg-days" role="group" aria-labelledby="shop-days-label">
                    {admin.DAYS.map((d) => (
                      <button
                        key={d.key}
                        type="button"
                        className="adm-day"
                        aria-pressed={form.days.includes(d.key)}
                        title={d.label}
                        onClick={() => toggleDay(d.key)}
                      >
                        {d.short}
                      </button>
                    ))}
                  </div>
                  <span className="adm-sub">
                    {form.days.length === admin.DAYS.length
                      ? 'open every day'
                      : form.days.length === 0
                        ? 'no day is ticked — the server will refuse this, because a shop open on no day is shut'
                        : `shut on ${admin.DAYS.filter((d) => !form.days.includes(d.key)).map((d) => d.label).join(', ')}`}
                  </span>
                  {badDays && refusal && <FieldWords r={refusal} />}
                </div>
              </div>

              <div className="adm-actions">
                <button className="btn primary" onClick={() => void save()} disabled={busy}>
                  {busy ? 'SAVING…' : saved ? 'SAVE CHANGES' : 'SAVE THE SHOP'}
                </button>
                {saved && (
                  <button
                    className="btn ghost"
                    onClick={() => setConfirmUndo(true)}
                    disabled={busy || !dirty}
                    title={busy
                      ? 'The save is still going through.'
                      : !dirty
                        ? 'Nothing has been typed since the last save, so there is nothing to undo.'
                        : undefined}
                  >
                    UNDO MY EDITS
                  </button>
                )}
                {busy && (
                  <span className="adm-sub" aria-live="polite" style={{ marginTop: 0 }}>
                    Sending the four fields to the server, which decides what a phone number is…
                  </span>
                )}
                {!busy && dirty && saved && (
                  <span className="adm-sub" style={{ marginTop: 0 }}>
                    Edited and not saved — a customer still sees the version below.
                  </span>
                )}
              </div>

              {/* Only the part that has NOT already been said under a box. */}
              {refusal && !refusalIsOnAField && (
                <Refusal reason={refusal.reason} detail={refusal.detail} />
              )}
              {refusal && refusalIsOnAField && (
                <p className="adm-hint">
                  Nothing was saved. The reason is written under the box it is about.
                </p>
              )}

              {/* A press has to have an answer. INFO, not the green box this
                  used to be: saving a shop's name is not a payment settling. */}
              {note && !refusal && (
                <div style={{ marginTop: 12 }}>
                  <Verdict
                    tone="info"
                    title={note.changed.length === 0
                      ? 'Saved. Nothing on it had changed.'
                      : note.first
                        ? 'Saved. The storefront now carries this shop’s name.'
                        : `Saved — ${note.changed.join(', ')} updated.`}
                  >
                    This is what a customer sees after scanning the shutter QR, and what a printed
                    sheet carries. The preview beside this form is now the saved version.
                  </Verdict>
                </div>
              )}
            </div>
          )}
        </Card>

        <Card
          title="What a customer sees"
          aside={<Pill tone="code">{dirty ? 'DRAFT' : 'STOREFRONT'}</Pill>}
        >
          {loading ? (
            <div role="status" aria-label="Reading the storefront header">
              <div className="mg-front" aria-hidden="true">
                <div className="mg-front-bar">After scanning the shutter QR</div>
                <div className="mg-front-in">
                  <Skeleton w="58%" h={26} radius={8} />
                  <div style={{ marginTop: 12 }}><Skeleton w="82%" h={11} radius={999} /></div>
                  <div style={{ marginTop: 8 }}><Skeleton w="44%" h={11} radius={999} /></div>
                  <div style={{ marginTop: 14 }}><Skeleton w="62%" h={12} radius={999} /></div>
                </div>
              </div>
            </div>
          ) : (saved || hasDraft) ? (
            <>
              {/* Drawn live from the form, shaped like the storefront's own
                  header — so a typo is seen here, before a customer sees it. */}
              <div className="mg-front">
                <div className="mg-front-bar">After scanning the shutter QR</div>
                <div className="mg-front-in">
                  {/* The picture, at the size a phone draws it, or the initial
                      the storefront falls back to. Drawn from the SAVED photo:
                      a photograph is stored the moment it is chosen, so there
                      is no draft of it to preview. */}
                  <div className="shf-prev-ph" aria-hidden="true">
                    {photoUrl
                      ? <img src={photoUrl} alt="" />
                      : <span>{(form.name.trim() || 'S').slice(0, 1).toUpperCase()}</span>}
                  </div>
                  <h3>{form.name.trim() || <span className="mg-front-unset">the shop&rsquo;s name</span>}</h3>
                  {form.address.trim()
                    ? <p className="mg-front-line">{form.address}</p>
                    : <p className="mg-front-line mg-front-unset">the address a customer follows</p>}
                  {form.phone.trim()
                    ? <p className="mg-front-line">{formatPhone(form.phone)}</p>
                    : <p className="mg-front-line mg-front-unset">the number an order rings</p>}
                  <p className="mg-front-hours">
                    {form.open}&ndash;{form.close}, {daysInWords(form.days)}
                  </p>
                </div>
              </div>

              {/* THIS PREVIEW IS THE DRAFT, NOT THE SHOP. It used to say so only
                  before the very first save, so an edited-and-unsaved profile
                  showed a customer's view that no customer was being shown. */}
              {dirty && (
                <Verdict tone="info" title="This is your draft, not the storefront">
                  {saved
                    ? <>What is above is what you are typing. A customer scanning the shutter QR still gets the saved version — {saved.name} — until SAVE CHANGES is pressed.</>
                    : <>Nothing is saved yet. Press SAVE THE SHOP and the storefront starts carrying it.</>}
                </Verdict>
              )}

              {saved && (
                <>
                  <div style={{ marginTop: 20 }}>
                    <KV k="phone as dialled">
                      <span className="adm-mono">{saved.phone_e164}</span>
                    </KV>
                    <KV k="open on">
                      {/* Not green. A day of the week is not money. */}
                      {saved.hours.days.map((d) => (
                        <Pill key={d} tone="off">{d}</Pill>
                      ))}
                    </KV>
                    <KV k="hours">{saved.hours.label} · {saved.hours.days_label}</KV>
                    <KV k="link handle">
                      <span className="adm-mono">{saved.slug ?? link?.slug ?? '—'}</span>
                    </KV>
                    <KV k="last saved">{whenever(saved.updated_at)}</KV>
                  </div>

                  {saved.hours.crosses_midnight && (
                    <Verdict tone="info" title="This shop shuts after midnight">
                      Closing at {saved.hours.close} is earlier in the day than opening at{' '}
                      {saved.hours.open}, so these hours run past midnight. If that is wrong, the two
                      times are the other way round.
                    </Verdict>
                  )}

                  <p className="adm-hint">
                    The phone number is stored as ten bare digits however it was typed, so the same
                    number entered with +91 in front and with a leading 0 does not look like two
                    different shops.
                  </p>
                </>
              )}
            </>
          ) : (
            <Empty
              title="This shop has no name yet"
              action={
                <Button
                  size="sm"
                  variant="primary"
                  onClick={() => document.getElementById('shop-name')?.focus()}
                >
                  START WITH THE NAME
                </Button>
              }
            >
              A customer who scans the shutter QR sees a list of prices belonging to nobody — no
              name at the top, no address, and no number to ring when an order has not arrived.
              Fill the form in beside this and it gets a header.
            </Empty>
          )}
        </Card>
      </div>

      {/* ------------------------------------------------------------------
          THE LINK AND THE PICTURE. Below the form because both need a saved
          shop: the slug is minted on the first save, and a photograph of a
          shop with no name has nothing to be the photograph of.
          ------------------------------------------------------------------ */}
      <div className="grid two">
        <Card
          title="Your shop's link"
          sub="the one address that opens THIS shop, and the code that carries it"
          aside={link?.slug ? <Pill tone="code">{link.slug}</Pill> : <Pill tone="off">not set yet</Pill>}
        >
          {loading ? (
            <div role="status" aria-label="Reading the shop’s link">
              <Skeleton h={44} radius={12} />
              <div style={{ marginTop: 16 }}><Skeleton w={260} h={260} radius={12} /></div>
            </div>
          ) : !saved ? (
            <Empty title="Name the shop first">
              The link carries the shop&rsquo;s own handle, and the handle is made from its
              name the first time the form beside this is saved. Until then the code would
              point at a shop with no name.
            </Empty>
          ) : linkErr ? (
            <Refusal
              reason="The link could not be read"
              detail={linkErr.reason}
              hint={linkErr.detail}
              action={<Button size="sm" onClick={() => void loadFace()}>TRY AGAIN</Button>}
            />
          ) : link ? (
            <>
              {/* THE ADDRESS, VERBATIM. The slug is the part that is this
                  shop's and no other's, so it is the part set in bold. */}
              <div className="shf-link" data-testid="shop-link" title={link.url}>
                {link.slug ? (
                  <span>
                    {link.url.slice(0, link.url.lastIndexOf(link.slug))}
                    <b>{link.slug}</b>
                  </span>
                ) : link.url}
              </div>

              {/* The existing loopback warning, kept: a code reading
                  127.0.0.1 is a perfectly good code no phone can open. */}
              {!link.reachable_from_a_phone && (
                <div style={{ marginTop: 12 }}>
                  <Verdict tone="amber" title="A phone cannot open this address">
                    {link.note}
                  </Verdict>
                </div>
              )}

              {!link.unique && link.unique_note && (
                <div style={{ marginTop: 12 }}>
                  <Verdict tone="info" title="This is the plain link, not yet this shop's own">
                    {link.unique_note}
                  </Verdict>
                </div>
              )}

              <div className="shf-actions">
                <button
                  className="btn sm primary"
                  onClick={() => void copyLink()}
                  disabled={!link.reachable_from_a_phone}
                  title={link.reachable_from_a_phone
                    ? undefined
                    : 'This address only works on this machine, so there is nothing worth sending.'}
                >
                  COPY THE LINK
                </button>
                <button className="btn sm" onClick={printSticker}>
                  PRINT THE STICKER
                </button>
                {typeof navigator !== 'undefined' && 'share' in navigator && (
                  <button
                    className="btn sm"
                    disabled={!link.reachable_from_a_phone}
                    onClick={async () => {
                      try {
                        await navigator.share({
                          title: saved.name,
                          text: `${saved.name} is open on your phone — no app, no install.`,
                          url: link.url,
                        });
                        say('Sent.');
                      } catch {
                        // A cancelled share is not a failure and says nothing.
                      }
                    }}
                  >
                    SEND IT
                  </button>
                )}
                <button
                  className="btn sm ghost"
                  onClick={() => setConfirmRenew(true)}
                  title="Mint a new handle. Every sticker already printed stops matching this shop."
                >
                  NEW LINK…
                </button>
              </div>
              {said && <p className="shf-said" aria-live="polite">{said}</p>}

              {renewed && (
                <div style={{ marginTop: 12 }}>
                  <Verdict tone="info" title={`New link: ${renewed.slug}`}>
                    {renewed.warning}
                  </Verdict>
                </div>
              )}

              {/* THE STICKER. The code is the server's own PNG of the same
                  string shown above — this page draws nothing into it. The
                  query bumps after a renew so the browser asks again. */}
              <div className="shf-sticker" style={{ marginTop: 16 }}>
                <span className="brand">Scan to shop</span>
                <h3>{saved.name}</h3>
                <img
                  src={`${link.qr_url}?px=700&k=${qrKey}`}
                  alt={`A QR code that opens ${saved.name} on a phone`}
                  width={260}
                  height={260}
                />
                <span className="shf-link" style={{ fontSize: 11 }}>{link.url}</span>
                <span className="sub">
                  The dashed edge is the scissor line. Tape it at eye level, where a
                  person waiting at the counter stands.
                </span>
              </div>

              <p className="adm-hint">
                The handle <span className="adm-mono">{link.slug}</span> was made once from the
                shop&rsquo;s name and stays the same when the name changes, so a sticker printed
                today still opens this shop next year. A customer who scans a code printed for a
                different shop is told so by name, rather than shown this shop as if it were that one.
              </p>
            </>
          ) : null}
        </Card>

        <Card
          title="Photo of the shop"
          sub="shown at the top of the storefront, beside the name"
          aside={<Pill tone={photoUrl ? 'code' : 'off'}>{photoUrl ? 'on the storefront' : 'none yet'}</Pill>}
        >
          {loading ? (
            <div role="status" aria-label="Reading the shop’s photo">
              <Skeleton w={132} h={132} radius={18} />
            </div>
          ) : !saved ? (
            <Empty title="Name the shop first">
              A photograph of a shop with no name has nothing to be the photograph of. Save the
              form above and add one here.
            </Empty>
          ) : (
            <>
              <div className="shf-photo">
                <div className="shf-photo-ph" data-testid="shop-photo">
                  {photoUrl
                    ? <img src={photoUrl} alt={`The front of ${saved.name}`} />
                    : <span>no photo yet</span>}
                </div>
                <div className="shf-photo-txt">
                  <p>
                    The shutter with the board on it, in daylight, is the picture a customer
                    recognises. It is downscaled to {photoNote?.edge_px ?? 256} px on its long
                    side and stored beside the shop&rsquo;s name — never inside the catalogue.
                  </p>
                  <div className="shf-actions">
                    <label className="btn sm primary" htmlFor="shop-photo-file">
                      {photoBusy ? 'STORING…' : photoUrl ? 'REPLACE THE PHOTO' : 'CHOOSE A PHOTO'}
                    </label>
                    <input
                      ref={fileRef}
                      id="shop-photo-file"
                      className="shf-file"
                      type="file"
                      accept="image/*"
                      disabled={photoBusy}
                      onChange={(e) => {
                        const f = e.target.files?.[0];
                        if (f) void uploadPhoto(f);
                      }}
                    />
                    {photoUrl && (
                      <button
                        className="btn sm ghost"
                        disabled={photoBusy}
                        onClick={() => setConfirmRemovePhoto(true)}
                      >
                        REMOVE
                      </button>
                    )}
                  </div>
                </div>
              </div>

              {photoRefusal && (
                <div style={{ marginTop: 12 }}>
                  <Refusal reason={photoRefusal.reason} detail={photoRefusal.detail} />
                </div>
              )}
              {photoNote && !photoRefusal && (
                <div style={{ marginTop: 12 }}>
                  <Verdict
                    tone="info"
                    title={photoNote.has_photo
                      ? `Stored — ${photoNote.photo_bytes.toLocaleString()} bytes, on the storefront now.`
                      : 'Removed. The storefront shows the shop’s initial instead.'}
                  >
                    {photoNote.untouched}
                  </Verdict>
                </div>
              )}
            </>
          )}
        </Card>
      </div>

      <Modal
        open={confirmRenew}
        onClose={() => setConfirmRenew(false)}
        title="Mint a new link?"
        size="narrow"
        note="The shop's name, address and catalogue are untouched."
        foot={
          <>
            <Button variant="ghost" onClick={() => setConfirmRenew(false)}>KEEP THIS ONE</Button>
            <Button variant="danger" onClick={() => void renew()}>NEW LINK</Button>
          </>
        }
      >
        <p>
          Every sticker already printed carries{' '}
          <span className="adm-mono">{link?.slug ?? 'the current handle'}</span>. After this, a
          customer scanning one of them is told the code was made for a different shop. Print the
          new sticker and replace the old one.
        </p>
      </Modal>

      <Modal
        open={confirmRemovePhoto}
        onClose={() => setConfirmRemovePhoto(false)}
        title="Remove the shop's photo?"
        size="narrow"
        note="Nothing else changes."
        foot={
          <>
            <Button variant="ghost" onClick={() => setConfirmRemovePhoto(false)}>KEEP IT</Button>
            <Button variant="danger" onClick={() => void removePhoto()}>REMOVE</Button>
          </>
        }
      >
        <p>The storefront goes back to showing the shop&rsquo;s initial where the picture was.</p>
      </Modal>

      {/* Throwing away what has been typed is not something to do on a
          mis-press: there is no draft anywhere else, so the words are gone. */}
      <Modal
        open={confirmUndo}
        onClose={() => setConfirmUndo(false)}
        title="Throw away these edits?"
        size="narrow"
        note="The saved shop is untouched."
        foot={
          <>
            <Button variant="ghost" onClick={() => setConfirmUndo(false)}>KEEP TYPING</Button>
            <Button
              variant="danger"
              onClick={() => {
                if (saved) setForm(toForm(saved));
                setRefusal(null);
                setNote(null);
                setConfirmUndo(false);
              }}
            >
              THROW THEM AWAY
            </Button>
          </>
        }
      >
        <p>
          The form goes back to the last saved version{saved ? <> — {saved.name} — </> : ' '}
          and everything typed since is gone. Nothing is kept as a draft anywhere.
        </p>
      </Modal>
    </div>
  );
}

/** The stored document back into the form that produced it. */
function toForm(doc: admin.ShopProfileDoc): Form {
  return {
    name: doc.name,
    address: doc.address,
    phone: doc.phone,
    open: doc.hours.open || DEFAULT_OPEN,
    close: doc.hours.close || DEFAULT_CLOSE,
    days: [...doc.hours.days],
  };
}

/** `9876543210` as a person reads it aloud. Never re-parsed, only displayed. */
function formatPhone(digits: string): string {
  return digits.length === 10 ? `${digits.slice(0, 5)} ${digits.slice(5)}` : digits;
}

/**
 * The draft days line for the PREVIEW only. The saved document's own
 * `days_label` is the server's word and is shown untouched in the facts below;
 * this one just has to describe a form that has not been sent yet.
 */
function daysInWords(days: string[]): string {
  if (days.length === admin.DAYS.length) return 'every day';
  if (days.length === 0) return 'no day ticked';
  return admin.DAYS.filter((d) => days.includes(d.key)).map((d) => d.short).join(' · ');
}

function whenever(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}
