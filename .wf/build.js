export const meta = {
  name: 'gawaah-parallel-build',
  description: 'Build the full GAWAAH system: 15 parallel module agents writing real code and running real tests, then adversarial verification',
  phases: [
    { title: 'Build', detail: '15 agents, one module each, strictly non-overlapping files' },
    { title: 'Verify', detail: 'independent adversarial review of each module against its contract' },
  ],
}

const ROOT = '/Users/agnik/Desktop/razor'

const FOUNDATION = [
  '=== EXISTING FOUNDATION — already built, tested, 48 tests green. USE IT, DO NOT MODIFY IT. ===',
  '',
  'Repo root: ' + ROOT,
  'Python:    ' + ROOT + '/.venv/bin/python   (has: cv2 5.0.0 [opencv-contrib-headless], numpy 2.5.2, fastapi, uvicorn, httpx, pytest, hypothesis, pydantic)',
  'Run tests: cd ' + ROOT + ' && ./.venv/bin/python -m pytest tests/<yourfile> -q',
  '',
  '--- gawaah/money.py ---',
  '  Paise = NewType("Paise", int)',
  '  paise(v:int)->Paise           # raises MoneyError on float, bool, str, None',
  '  from_rupees_str("214.50")->21450   # str in, never float',
  '  to_rupees_str(21450)->"214.50"',
  '  add(*Paise)->Paise ; total(iterable)->Paise ; class MoneyError(ValueError)',
  '  INVARIANT: integer paise everywhere. NEVER use float, float(), or "/" in a money path.',
  '  tools/lint_no_float.py AST-checks money.py, ledger.py, paisa.py, kernel.py, session.py.',
  '  If you own one of those files it MUST pass that lint. Use // and int() only.',
  '',
  '--- gawaah/clock.py ---',
  '  Clock protocol: .now_iso()->str, .monotonic_ns()->int',
  '  RealClock() ; VirtualClock(start="2026-08-29T00:00:00.000+00:00", step_ms=100)',
  '  Every function needing time TAKES A CLOCK ARGUMENT. Never call datetime.now() directly.',
  '',
  '--- gawaah/ledger.py ---',
  '  Ledger(path).append(ts=..., module=..., **fields) -> new head hash (sha256 chain)',
  '  Ledger(path).head / .count / .read()',
  '  verify(path) -> (ok:bool, n:int, head:str, err:str|None)  # standalone, recomputes from genesis',
  '  canonical(obj)->bytes  # sorted-key compact JSON; use for any hashing',
  '',
  '--- gawaah/takhti.py ---',
  '  MAT_W_MM=297.0  MAT_H_MM=420.0  BUF_W=840  BUF_H=1188',
  '  PX_PER_MM_X=2.82828  PX_PER_MM_Y=2.82857  PX_PER_MM=~2.8284   # 2*sqrt(2). NOT 2.',
  '  MARKER_MM=30.0  MARGIN_MM=12.0  ARUCO_DICT=cv2.aruco.DICT_4X4_50  MARKER_IDS=(0,1,2,3)',
  '  render_takhti(px_per_mm=4.0) -> np.uint8 grayscale mat image',
  '  marker_centres_mm() -> (4,2) float64 ; mm_to_buffer(pts_mm) -> buffer px',
  '  PlaneEngine().detect(frame) -> MatLock(locked, reason, H, ids_found, scale_err, persp_index, reproj_rmse_px)',
  '  PlaneEngine().rectify(frame, H) -> 840x1188 rectified buffer',
  '  MatLock.H maps FRAME pixels -> RECTIFIED BUFFER pixels.',
  '',
  '--- test helper you may import ---',
  '  from tests.test_plane import synth_frame',
  '  synth_frame(px_per_mm=4.0, tilt=(0,0), size=(960,1280), noise=0.0, seed=0, fit=0.82)',
  '    -> (frame_uint8, dst_quad)   # renders the mat into a synthetic camera frame',
  '',
  '=== NON-NEGOTIABLE INVARIANTS ===',
  '1. Money is integer paise. A float in the money path fails the build.',
  '2. GREEN only when ALL FOUR hold: valid HMAC-SHA256 X-Razorpay-Signature over RAW BYTES before any',
  '   JSON parsing, AND event in the green set, AND notes.session_id matches an OPEN intent, AND',
  '   amount == intent.amount_paise exactly. Never green on mint, render, or a timer.',
  '3. Zero model weights in the browser. The phone does geometry only.',
  '4. The rectified mat crop is the only buffer that survives a frame grab.',
  '5. paisa is the sole holder of secrets and re-runs the crossing predicate server-side.',
  '6. NO FORGERY PRIMITIVES. Never write code that constructs or regenerates a UPI payload.',
  '7. Abstain rather than guess. Unknown -> amber, excluded from total. Stale -> amber, never red.',
  '9. Every reported number must be produced by running code, never typed by hand.',
].join('\n')

const RULES = [
  '=== HOW TO WORK ===',
  '1. You OWN exactly the files listed in YOUR FILES. Create them. Do NOT create, edit or delete ANY',
  '   other file — other agents are working in this repo concurrently on other modules.',
  '   Do NOT run git add / git commit / git checkout or any git write command.',
  '   Do NOT modify the shared venv, the Makefile, tools/lint_no_float.py, or any existing gawaah/*.py.',
  '2. WRITE REAL, WORKING CODE. No stubs, no bare pass, no TODO-as-implementation.',
  '3. WRITE TESTS AND ACTUALLY RUN THEM:',
  '     cd ' + ROOT + ' && ./.venv/bin/python -m pytest tests/<your_test_file> -q',
  '   Iterate until they pass. Run at least twice to catch flakiness.',
  '   NEVER report a test as passing that you did not run in this session.',
  '4. If something genuinely cannot work, say so explicitly with evidence. Do NOT fake it, do NOT stub',
  '   it and call it done, do NOT weaken a test to make it pass.',
  '5. Prefer deterministic classical CV over models. Where you must abstain, abstain loudly with a named',
  '   reason code rather than guessing.',
  '6. Keep to what is installed. Do NOT pip install anything. No network downloads.',
  '7. Type hints, and comments only where the reason is non-obvious.',
].join('\n')

const SCHEMA = {
  type: 'object',
  properties: {
    module: { type: 'string' },
    files_written: { type: 'array', items: { type: 'string' } },
    tests_written: { type: 'number' },
    tests_passing: { type: 'number' },
    test_command: { type: 'string' },
    test_output_tail: { type: 'string', description: 'the ACTUAL last lines of the test run you executed' },
    ran_twice: { type: 'boolean' },
    public_api: { type: 'string', description: 'exact exported signatures, so the integrator can wire them' },
    measured_numbers: { type: 'array', items: { type: 'string' } },
    abstentions: { type: 'array', items: { type: 'string' } },
    honest_limits: { type: 'array', items: { type: 'string' } },
    blocked_on: { type: 'array', items: { type: 'string' } },
  },
  required: ['module', 'files_written', 'tests_written', 'tests_passing', 'test_command', 'test_output_tail', 'ran_twice', 'public_api', 'measured_numbers', 'abstentions', 'honest_limits'],
}

const MODULES = [
  { k: 'placement', files: 'gawaah/placement.py + tests/test_placement.py', brief: [
    'S3a — PLACEMENT DETECTOR. Detect objects resting on the rectified mat with NO neural detector.',
    'Pipeline (all verified present in cv2): maintain an empty-mat reference; absdiff vs reference ->',
    'threshold -> morphologyEx(OPEN then CLOSE) -> findContours -> minAreaRect (ORIENTED box, never',
    'axis-aligned). Then a stability gate: an object is STABLE only after its oriented box centre and',
    'area vary less than a tolerance for 5 consecutive frames.',
    'API: class PlacementDetector(ref_frame). .update(rectified) -> list[Placement].',
    'Placement: id, centre_mm(x,y), long_edge_mm, short_edge_mm, area_mm2, angle_deg, stable, frames_seen.',
    'Convert px->mm using PX_PER_MM_X/Y. Measurements are in MM on the plane.',
    'Also: slow reference maintenance when the mat is empty, and a border refusal — a contour touching the',
    'buffer border is NOT measurable (the "poora rakhiye" case) and must be flagged, never measured.',
    'TESTS: paste bright/dark rectangles of KNOWN mm size onto a rendered mat; assert measured long_edge_mm',
    'within 3mm of truth across sizes and rotations; assert rotation invariance (a 45-degree rotated box',
    'must not inflate area, which an axis-aligned box would); assert the stability gate needs 5 frames;',
    'assert border-touching objects are refused. Report the measured mm error as a real number.',
  ].join('\n') },

  { k: 'sellevent', files: 'gawaah/sellevent.py + tests/test_sellevent.py', brief: [
    'S3b — DETERMINISTIC SELL EVENT. A directional line crossing on the rectified plane. NO MODEL.',
    'API: class LineZone(p1_mm, p2_mm, min_crossing_frames=3). .update(tracks) -> CrossingResult,',
    'where tracks is {track_id: (x_mm, y_mm)}. Count a crossing only when a tracked centroid crosses in',
    'the OUT direction and stays across for min_crossing_frames. Crossing back DECREMENTS.',
    'CRITICAL — this is the money bug in a vision bug\'s clothes. Instrument TWO counters that upstream',
    'libraries silently swallow: (a) crossed_without_tracker_id — a crossing for a centroid with no stable',
    'id; (b) detected_but_never_counted — a track seen and then vanished without ever crossing. Both must',
    'be surfaced as exceptions on the result, never silently dropped.',
    'Also: class CentroidTracker(max_dist_mm, max_missing_frames) assigning stable ids.',
    'TESTS: a scripted path crossing out is counted once; out-then-back nets zero; a centroid jittering',
    'exactly on the line must NOT produce repeated counts (debounce); a track vanishing mid-cross raises',
    'detected_but_never_counted; crossing the wrong way never counts.',
  ].join('\n') },

  { k: 'kernel', files: 'gawaah/kernel.py + tests/test_kernel.py', brief: [
    'S4a — EXACTLY-ONCE KERNEL. A debit for (session_id, cycle, amount_paise) executes once or never.',
    'MUST PASS tools/lint_no_float.py — no float, no "/", use // and int().',
    'Design, and this ordering is the whole point: write-ahead intent row plus nonce committed and the DB',
    'connection RELEASED BEFORE the gateway is called. Never hold a transaction across a network call.',
    'On an indeterminate result (timeout) do NOT retry blind: go to RETRIEVE and reconcile by querying the',
    'gateway for the nonce first.',
    'Use sqlite3 (stdlib) with a UNIQUE index on the nonce. API:',
    '  class Kernel(db_path, clock, ledger)',
    '  .create_intent(session_id, amount_paise, cycle=0) -> Intent(nonce, state)   # idempotent per key',
    '  .mark_calling(nonce) / .mark_settled(nonce, payment_id) / .mark_indeterminate(nonce)',
    '  .intents_needing_retrieve() -> list[Intent]',
    '  .reconcile(nonce, gateway_lookup_fn) -> Intent',
    'States: NEW -> CALLING -> (SETTLED | INDETERMINATE); INDETERMINATE -> RETRIEVE -> SETTLED | FAILED.',
    'TESTS: creating the same (session, cycle, amount) twice returns the SAME nonce and one row; 50',
    'concurrent threads calling create_intent produce exactly ONE intent (use threading); a simulated crash',
    'between commit and gateway call leaves a recoverable INDETERMINATE row; reconcile finds the settled',
    'payment and never double-charges; the ledger records every transition.',
  ].join('\n') },

  { k: 'rzpsim', files: 'gawaah/rzp_sim.py + tests/test_rzp_sim.py', brief: [
    'S4b — LOCAL RAZORPAY SIMULATOR. There are NO real Razorpay keys yet, so the whole money path must be',
    'testable now and swapping in real keys must be a config change only.',
    'Faithfully simulate the subset we use:',
    '  create_payment_link(amount_paise, notes) -> {id:"plink_...", short_url:"https://rzp.io/i/XXXX", status:"created"}',
    '    short_url is a STRING, which is why the QR can be rendered locally on the counter plane.',
    '  fetch_payment_link(id), fetch_payments(...)',
    '  pay_link(id) -> simulates the customer paying and EMITS a webhook',
    '  webhook emission: build the JSON body, sign it with HMAC-SHA256 over the RAW BYTES using a configured',
    '  webhook secret, deliver {headers:{"X-Razorpay-Signature":sig}, body:bytes} to a sink.',
    '  events: payment_link.paid, payment.captured',
    '  failure injection: .set_mode("timeout"|"error"|"duplicate_webhook"|"out_of_order"|"wrong_amount")',
    'API: class RazorpaySim(webhook_secret, clock). Ids derive from a seeded counter, NEVER random, so',
    'replays are byte-identical.',
    'This module must NEVER construct a UPI payment payload string — short_url is an opaque token we mint.',
    'TESTS: a paid link emits exactly one correctly-signed webhook; the signature verifies and FAILS if one',
    'byte of the body changes; duplicate_webhook emits the same event twice with the same event id;',
    'wrong_amount emits a mismatching amount; ids are deterministic across two runs.',
  ].join('\n') },

  { k: 'webhook', files: 'gawaah/webhook.py + tests/test_webhook.py', brief: [
    'S4c — WEBHOOK VERIFICATION AND THE GREEN PREDICATE. This is invariant 2 and the most security-critical',
    'function in the product.',
    'API:',
    '  verify_signature(raw_body: bytes, signature: str, secret: str) -> bool',
    '    HMAC-SHA256 over the RAW BYTES, compared with hmac.compare_digest. NEVER json.loads before verifying.',
    '  class GreenPredicate(open_intents_lookup)',
    '  .evaluate(raw_body: bytes, signature: str, secret: str) -> GreenVerdict(green: bool, reason: str, ...)',
    'ALL FOUR must hold for green: signature valid over raw bytes; event in GREEN_EVENTS =',
    '{"payment_link.paid","payment.captured"}; notes.session_id matches an OPEN intent; amount ==',
    'intent.amount_paise EXACTLY (integer compare).',
    'Every failure returns a DISTINCT machine-readable reason code, never a bare False.',
    'Also: replay protection — an event id already seen returns reason "replay" and does not re-green. And a',
    'stale-ledger rule: if the caller reports the mirror is stale, any "not paid" conclusion is AMBER, never RED.',
    'TESTS: a valid signature greens; flipping ONE byte of the body fails; a re-serialised but semantically',
    'identical body fails (proving bytes are verified, not objects); wrong event type has its own code;',
    'unknown session fails; an amount off by one paisa fails; a duplicate event id yields "replay"; and assert',
    'the source actually uses compare_digest.',
  ].join('\n') },

  { k: 'session', files: 'gawaah/session.py + tests/test_session.py', brief: [
    'S4d — THE SESSION STATE MACHINE, the counter\'s whole lifecycle. MUST PASS tools/lint_no_float.py.',
    'States: SETUP, IDLE, MEASURING, PRICED, AMBER, BASKET_OPEN, AWAITING_SETTLEMENT, PENDING_OFFLINE, PAID,',
    'AMOUNT_MISMATCH, MAT_LOST, BRAIN_LOST, DEGRADED, FROZEN_TOTAL.',
    'API: class Session(clock, ledger). .on_mat_lock(bool), .on_placement(item), .on_price(item_id, paise),',
    '.on_exit(item_id), .on_revert(item_id), .on_done(), .on_webhook(verdict), .on_network(up: bool)',
    'Rules that MUST hold and be tested:',
    ' - AMBER items are EXCLUDED from the total, always.',
    ' - the total is integer paise recomputed from committed line items, never incremented ad hoc.',
    ' - tap-to-revert removes a line and logs human_override=True.',
    ' - money is authorised in NO state except after a green verdict.',
    ' - MAT_LOST and BRAIN_LOST FREEZE the total; they never silently continue billing.',
    ' - offline -> PENDING_OFFLINE: billing continues locally but nothing is authorised.',
    ' - every transition appends one ledger line with its reason.',
    'TESTS: a happy path of 3 items -> done -> green; an amber item never reaches the total; revert',
    'decrements exactly; a wrong-amount webhook lands in AMOUNT_MISMATCH not PAID; mat loss mid-basket',
    'freezes; replaying the same event twice is idempotent; the ledger verifies after every scenario.',
  ].join('\n') },

  { k: 'identity', files: 'gawaah/identity.py + tests/test_identity.py', brief: [
    'S5a — IDENTITY WITH AN EXPLICIT REJECT MARGIN. Identity PROPOSES, thresholds DISPOSE.',
    'Do NOT download any model (no network). The embedder is INJECTED:',
    '  class Gallery: .enroll(sku_id, vectors, footprint_mm) ; .save/.load (json)',
    '  class Identifier(gallery, embed_fn, theta=0.10, phi=0.55, tau_mm=4.0)',
    '  .identify(crop, long_edge_mm) -> Identification(sku_id|None, top1, top2, margin, reason)',
    'Candidates are first filtered by footprint within tau_mm (the metric tiebreak), then ranked by cosine',
    'similarity from embed_fn. Return a match ONLY if (top1 - top2) >= theta AND top1 >= phi. Otherwise',
    'sku_id is None and reason is one of: "below_margin", "below_similarity", "no_candidate_in_footprint",',
    '"ambiguous_pair". AMBER is the correct expected outcome and must never be treated as a failure.',
    'Also the ENROLMENT COLLISION GUARD: .check_collision(new_vectors, new_footprint) refuses to enrol when',
    'an existing entry is within BOTH the appearance margin and the footprint tolerance, returning the',
    'colliding sku so the UI can demand a disambiguation capture.',
    'TESTS: use a deterministic fake embed_fn (seeded hash-based vector) so no model is needed. A clear match',
    'returns the sku; two near-identical vectors return None with "below_margin"; footprint filtering excludes',
    'a same-looking but wrong-sized item; the collision guard fires on an identical-size identical-appearance',
    'pair; cosine is computed correctly; results are deterministic.',
  ].join('\n') },

  { k: 'ident_sticker', files: 'gawaah/ident_sticker.py + tests/test_ident_sticker.py', brief: [
    'S6 IDENT (rescued PEEL) — QR STICKER TAMPER DETECTION WITH NO QR LIBRARY AT ALL.',
    'CRITICAL SAFETY CONSTRAINT: this module must contain NO QR encoder, NO QR decoder, NO module-grid',
    'reconstruction, and NO code path that could construct a UPI payload. It compares IMAGES. That is the',
    'entire point of the rescue — the original design was deleted because regenerating payloads is a forgery',
    'primitive in a public repo.',
    'Mechanism: at enrolment the merchant photographs each sticker ON the mat and we store the rectified crop.',
    'Later a fresh crop is re-registered to the stored one with cv2.findTransformECC, then absdiff ->',
    'threshold -> morphologyEx(OPEN) -> the IGNITED PIXEL FRACTION is the single scalar verdict.',
    'API: class StickerRegistry(dir). .enrol(name, rectified_crop) ; .compare(name, fresh_crop) ->',
    'StickerVerdict(name, ignited_fraction, registered, verdict in {"GENUINE","TAMPERED","UNREGISTERABLE"}, ecc_ok)',
    'findTransformECC MUST be used and its effect MUST be measured: also expose compare_without_ecc() so the',
    'test can prove the delta.',
    'TESTS — this is the important part. Build synthetic stickers as random black/white module-like grids',
    'drawn with numpy (NOT real QR codes). Then measure and ASSERT:',
    ' - a genuine sticker re-photographed with a 1px and a 3px shift has a LOW ignited fraction WITH ecc',
    ' - the SAME cases WITHOUT ecc have a much HIGHER ignited fraction. Report BOTH real numbers: this is the',
    '   "naive absdiff is a false-accusation machine" finding and your test must produce it.',
    ' - a sticker with a 10 percent patch replaced is clearly separable from genuine',
    ' - an unregistered name returns UNREGISTERABLE, never TAMPERED',
  ].join('\n') },

  { k: 'mudra', files: 'gawaah/mudra.py + tests/test_mudra.py', brief: [
    'S6 MUDRA — GESTURE AS OCCLUSION, WITH NO HAND MODEL.',
    'MediaPipe is structurally forbidden here: hand_landmarker.task is 7,819,105 bytes against a 4.8MB',
    'cold-load budget, and invariant 3 says zero model weights in the browser. So the hand is read as an',
    'OCCLUDER of the calibrated mat, by analogy to shadowgraph metrology — characterised by what it hides.',
    'Mechanism: on the rectified buffer, absdiff vs the empty-mat reference -> largest contour -> SOLIDITY =',
    'contourArea / convexHullArea, plus compactness = 4*pi*area/perimeter^2, plus the count of convexity',
    'defects. Reference values measured on real hands in prior research: fist ~0.73, open palm ~0.92, goods',
    '0.96-1.00. So an OPEN PALM separates from GOODS mainly by defect count and compactness, and from a FIST',
    'by solidity. Implement hysteresis so the state does not chatter.',
    'API: class OccluderGesture(ref_frame, open_solidity=(0.80,0.95), fist_solidity_max=0.80, min_defects_open=3)',
    '  .update(rectified) -> GestureState(state in {"NONE","OPEN","FIST","GOODS","AMBIGUOUS"}, solidity,',
    '   defects, compactness, area_mm2)',
    'AMBIGUOUS is a first-class outcome and must be returned rather than guessing.',
    'TESTS: synthesise shapes with numpy/cv2 — a filled rounded rect (goods: high solidity, 0 defects), a',
    'star/comb with 4 deep notches (open palm: mid solidity, >=3 defects), a blob with one notch (fist).',
    'Assert each classifies correctly; assert an in-between shape returns AMBIGUOUS; assert hysteresis',
    'prevents chatter when solidity oscillates across a threshold; and REPORT the measured solidity of each',
    'synthetic shape as a real number.',
  ].join('\n') },

  { k: 'saaf', files: 'gawaah/saaf.py + tests/test_saaf.py', brief: [
    'S6 SAAF (rescued KAMPAN) — MULTI-FRAME SUPER-RESOLUTION WITH THE SHAKE MOVED TO THE SUBJECT.',
    'The original premise was dead on this rig: the phone is CLAMPED to a gooseneck, so there is no hand',
    'tremor. The rescue: during enrolment the shopkeeper already nudges and rotates the packet, so the',
    'SUBJECT supplies the sub-pixel sampling diversity instead of the camera.',
    'Mechanism, fully deterministic and model-free: take a burst of N crops of the same region; register each',
    'to a reference with cv2.findTransformECC (MOTION_EUCLIDEAN or MOTION_TRANSLATION) refined to sub-pixel;',
    'reject frames on a variance-of-Laplacian blur gate; upsample and accumulate into a 2x grid; normalise.',
    'API: class BurstStacker(scale=2, blur_var_min=..., max_shift_px=...)',
    '  .stack(frames) -> StackResult(image, used, rejected, mean_shift_px, sharpness_gain, warning)',
    'CRITICAL HONEST BEHAVIOUR: if inter-frame displacement is near ZERO there is no sub-pixel diversity and',
    'stacking degenerates to plain denoising. Detect that and return a WARNING rather than silently returning',
    'a worse image. That honest failure mode is required, not optional.',
    'TESTS: build a synthetic high-resolution text-like target, downsample it with KNOWN sub-pixel shifts to',
    'make a burst, stack it, and MEASURE the sharpness gain (variance of Laplacian, or MTF via a slanted edge)',
    'against a single frame. Assert a real measured improvement and REPORT the number. Assert blurred frames',
    'are rejected. Assert the zero-motion case sets the warning.',
  ].join('\n') },

  { k: 'chilla', files: 'gawaah/chilla.py + tests/test_chilla.py', brief: [
    'S6 CHILLA (rescued PAKKA) — VERIFY A CUSTOMER "PAYMENT SUCCESSFUL" SCREEN WITHOUT READING THE REFERENCE',
    'STRING. The finding that drives the design: a UPI reference line is 12sp text, about 0.19mm stroke,',
    'which at this rig\'s 2.83 px/mm is 0.54 px — below Nyquist by 4x. It is not hard to read, it is NOT',
    'PRESENT IN THE SIGNAL. No model and no super-resolution recovers it. The hero AMOUNT is 40sp, about',
    '12.6 px, which IS readable. So match on a COMPOSITE KEY of (amount_paise, timestamp window), never the',
    'reference string.',
    'Build TWO things:',
    ' 1. Screen detection on the rectified mat: an emissive rectangle is the highest-contrast blob that lands',
    '    on the mat. absdiff vs reference -> threshold -> largest bright quad -> return its mm rect. No model.',
    ' 2. class LedgerMatcher(mirror, window_seconds=180)',
    '    .match(amount_paise, screen_ts) -> MatchResult(verdict, candidates, reason)',
    '    verdict in {"MATCHED","NO_MATCH","AMBIGUOUS","AMBER_STALE"}.',
    '    RULES: no match -> AMBER, NEVER "FRAUD" or RED. If the mirror is stale (caller passes mirror_age_s',
    '    over a threshold) -> AMBER_STALE regardless. If MORE THAN ONE payment matches the amount inside the',
    '    window -> AMBIGUOUS, never a confident match.',
    '    Also compute and expose collision_risk: given N payments in the window, the chance a same-amount',
    '    collision exists. Quantify it in the test.',
    'TESTS: exact amount in-window -> MATCHED; amount off by one paisa -> NO_MATCH (amber); two identical',
    'amounts in the window -> AMBIGUOUS; a stale mirror -> AMBER_STALE even when a match exists; and a test',
    'that MEASURES the false-accept rate over a synthetic day of transactions and reports the number.',
  ].join('\n') },

  { k: 'paisa', files: 'gawaah/paisa.py + tests/test_paisa.py', brief: [
    'S4e — THE PAISA MONEY SERVICE. FastAPI. Sole holder of secrets. MUST PASS tools/lint_no_float.py.',
    'Endpoints:',
    '  POST /intent  body {session_id, amount_paise, geometry:{H, corners, crossings}}',
    '    -> RE-RUNS the crossing predicate server-side on the submitted geometry before minting anything.',
    '       If the server-side re-run disagrees with the client, REFUSE with 409 and log it. This is',
    '       invariant 5: a compromised phone must not be able to move a rupee.',
    '    -> on agreement, create a kernel intent and mint a payment link via an INJECTED gateway',
    '       (use gawaah.rzp_sim.RazorpaySim when RZP_MODE=sim; keep the real client behind a Protocol).',
    '  POST /webhook  raw body + X-Razorpay-Signature -> GreenPredicate -> updates the session',
    '  GET  /session/{id} -> current state and total',
    '  GET  /health',
    'Secrets come from env only (RAZORPAY_KEY_SECRET, RAZORPAY_WEBHOOK_SECRET); NEVER log them; assert at',
    'startup that they are non-empty when RZP_MODE=live.',
    'Read the raw body with await request.body() and verify BEFORE any JSON parsing.',
    'Import gawaah.kernel, gawaah.webhook, gawaah.session, gawaah.rzp_sim — if one is not present yet, define',
    'a narrow Protocol and inject a local fake so YOUR tests still run standalone. Do not edit those files.',
    'TESTS: a full happy path intent -> pay -> webhook -> session PAID; a tampered webhook body is rejected;',
    'geometry disagreement returns 409 and mints nothing; secrets never appear in any response or log;',
    'replaying a webhook twice is idempotent.',
  ].join('\n') },

  { k: 'counterweb', files: 'web/index.html + web/app.js + web/style.css + web/selftest.mjs + web/README.md', brief: [
    'THE COUNTER PWA — the phone client. Pure static files, no build step, no framework.',
    'INVARIANT 3: ZERO model weights. Cold load must stay small. The phone does GEOMETRY ONLY.',
    'Build a working single-page app that:',
    ' - getUserMedia rear camera, rendering to a canvas at 30fps via requestVideoFrameCallback',
    ' - loads OpenCV from a LOCAL vendored path reference. Document that it must be pinned to',
    '   @techstark/opencv-js@4.11.0-release.1 (11,386,540 bytes); version 5.0.0 is 13,298,869 bytes,',
    '   1.91MB heavier for no benefit. Do NOT download it; reference it and degrade gracefully if absent.',
    ' - detects the 4 ArUco markers, computes the homography, shows a MAT LOCK indicator',
    ' - APPLIES THE MASK AT FRAME GRAB: only the rectified mat crop is ever kept or sent (invariant 4).',
    '   Make this visible as a split preview: raw feed versus the rectified crop.',
    ' - renders rupee glyphs warped back through H-inverse so they paint in perspective on the counter',
    ' - a running total, tap-to-revert on any line, a DONE button',
    ' - AMBER items shown hatched and visibly EXCLUDED from the total',
    ' - a WebSocket client to the brain (ws://localhost:8787) with auto-reconnect and an offline',
    '   AMBER PENDING banner',
    ' - counter chrome colour follows session state (amber / green / red)',
    'Write clean vanilla JS, no dependencies. Since you cannot run a browser, put a pure-function core in',
    'app.js (state reducer, total computation in integer paise, glyph projection maths) and a node-runnable',
    'self-test at web/selftest.mjs exercising those pure functions and PRINTING results.',
    'Run it: cd ' + ROOT + ' && node web/selftest.mjs',
    'Report the actual output. Totals are integer paise only — never floating rupees in JS.',
  ].join('\n') },

  { k: 'bench', files: 'tools/bench.py + tests/test_bench.py', brief: [
    'S7 — THE BENCH HARNESS. Invariant 9: every number in the README is generated, never typed.',
    'Run it as: ' + ROOT + '/.venv/bin/python tools/bench.py --seeds 5 --out results/',
    'It must:',
    ' - discover which gawaah modules exist (importlib) and run only the benchmarks whose modules are',
    '   present, reporting the rest as NOT BUILT rather than crashing. Other agents are building modules',
    '   concurrently, so this is a hard requirement.',
    ' - run each benchmark across N committed seeds and report mean AND worst case, never just the best',
    ' - emit results/metrics.json with schema {generated_at, git_sha, seeds, benchmarks:{name:{...}}}',
    ' - emit results/METRICS.md as a markdown table',
    ' - include verify_claims(md_path, metrics_path) that re-checks every number appearing in a markdown',
    '   file against metrics.json and FAILS on drift. This is what makes "no number typed by hand" true.',
    'Benchmarks if the module exists: plane reprojection RMSE across tilts; placement footprint error in mm;',
    'sell-event recall; kernel concurrency (exactly-once under threads); ledger verify throughput.',
    'TESTS: bench runs end to end and writes both files; metrics.json is valid and deterministic across two',
    'runs with the same seeds; verify_claims detects a deliberately drifted number; missing modules are',
    'reported NOT BUILT and do not crash the run.',
  ].join('\n') },

  { k: 'takhti_print', files: 'tools/make_takhti.py + tests/test_takhti_print.py', brief: [
    'THE PHYSICAL ARTEFACT — generate the printable TAKHTI at true A3 scale. This is the one physical thing',
    'the builder must produce, so it must be exactly right or every millimetre downstream is wrong.',
    'tools/make_takhti.py must emit:',
    ' - takhti_a3.png at 300 DPI (A3 = 297x420mm -> 3508x4961 px) with the 4 ArUco markers at the exact',
    '   positions from gawaah.takhti.marker_centres_mm(), each exactly MARKER_MM=30mm square',
    ' - the 20mm scale-verification patch, and a printed ruler tick strip so the print can be checked with',
    '   any ruler',
    ' - the exit-edge arrow, and small human-readable labels ("EXIT ->", marker ids, "print at 100%, do not',
    '   scale")',
    ' - an A4 fallback layout, scaled proportionally and clearly labelled A4',
    ' - a PDF via cv2/PIL or a minimal hand-written PDF wrapper (no new dependencies)',
    'Also emit a print-verification page stating the expected measured distance between marker centres in mm,',
    'so the builder can confirm print scale with a ruler before taping it down.',
    'TESTS: render the PNG, then RUN gawaah.takhti.PlaneEngine().detect() ON IT and assert all 4 markers are',
    'found and it locks; assert the pixel distance between marker centres corresponds to the expected mm at',
    '300 DPI within 0.5mm; assert the A4 variant also detects. Report the measured mm error as a real number.',
  ].join('\n') },
]

phase('Build')
log('Fanning out ' + MODULES.length + ' module agents, strictly non-overlapping files.')

const built = (await parallel(MODULES.map(m => () =>
  agent(
    'You are implementing ONE module of the GAWAAH system. Work in ' + ROOT + '.\n\n' +
    FOUNDATION + '\n\n' + RULES +
    '\n\n=== YOUR FILES (you own these and ONLY these) ===\n' + m.files +
    '\n\n=== YOUR MODULE ===\n' + m.brief +
    '\n\nBuild it, test it, RUN the tests, iterate until green, then report. Your test_output_tail MUST be ' +
    'the real output of a run you actually executed.',
    { label: 'build:' + m.k, phase: 'Build', schema: SCHEMA }
  )
))).filter(Boolean)

log('Build complete: ' + built.length + '/' + MODULES.length + ' modules returned. Verifying independently.')

const VSCHEMA = {
  type: 'object',
  properties: {
    module: { type: 'string' },
    files_exist: { type: 'boolean' },
    tests_actually_pass: { type: 'boolean' },
    real_test_count: { type: 'number' },
    verified_output: { type: 'string' },
    contract_violations: { type: 'array', items: { type: 'string' } },
    invariant_violations: { type: 'array', items: { type: 'string' } },
    stubs_or_fakes_found: { type: 'array', items: { type: 'string' } },
    verdict: { type: 'string' },
  },
  required: ['module', 'files_exist', 'tests_actually_pass', 'real_test_count', 'verified_output', 'contract_violations', 'invariant_violations', 'stubs_or_fakes_found', 'verdict'],
}

phase('Verify')
log('Re-running every module test independently and auditing against the invariants.')

const verified = (await parallel(built.map(b => () => {
  const mod = MODULES.find(m => (b.module || '').toLowerCase().indexOf(m.k) >= 0)
  return agent(
    'You are the independent verifier for a GAWAAH module. Work in ' + ROOT + '.\n\n' +
    'DO NOT trust the builder report. Verify it yourself:\n' +
    '1. Confirm every claimed file exists and contains real code — not stubs, not bare pass, not TODO.\n' +
    '2. RE-RUN the tests yourself: cd ' + ROOT + ' && ./.venv/bin/python -m pytest <their test file> -q\n' +
    '   (or node for the web module). Report the REAL output and REAL count.\n' +
    '3. Audit the invariants: integer paise only in money paths; run tools/lint_no_float.py; grep for any QR\n' +
    '   encoder or UPI payload construction anywhere; confirm abstention paths genuinely abstain rather than\n' +
    '   guessing; confirm no secrets are logged.\n' +
    '4. Hunt for tests weakened to pass: tautological asserts, asserts on constants, mocks that hide the real\n' +
    '   logic, try/except swallowing failures, or any test that would still pass if the implementation were\n' +
    '   deleted. Name every one you find.\n' +
    '5. Check the module does what its contract says, not merely something that passes its own tests.\n\n' +
    'Be adversarial and specific.\n\n=== BUILDER REPORT ===\n' + JSON.stringify(b, null, 1) +
    '\n\n=== THE CONTRACT ===\n' + (mod ? mod.brief : '(contract not matched)'),
    { label: 'verify:' + (b.module || '?').slice(0, 18), phase: 'Verify', schema: VSCHEMA }
  )
}))).filter(Boolean)

const clean = verified.filter(v => v.tests_actually_pass &&
  !(v.contract_violations || []).length && !(v.invariant_violations || []).length)
log('Verification: ' + clean.length + '/' + verified.length + ' modules clean.')

return {
  built: built.map(b => ({ module: b.module, files: b.files_written, passing: b.tests_passing, limits: b.honest_limits, blocked: b.blocked_on })),
  verified: verified.map(v => ({ module: v.module, pass: v.tests_actually_pass, n: v.real_test_count, contract: v.contract_violations, invariants: v.invariant_violations, stubs: v.stubs_or_fakes_found })),
  measured: built.flatMap(b => (b.measured_numbers || []).map(x => b.module + ': ' + x)),
  abstentions: built.flatMap(b => (b.abstentions || []).map(x => b.module + ': ' + x)),
}
