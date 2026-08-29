export const meta = {
  name: 'gawaah-photo-enrol-recognise',
  description: 'Upload a photo of an item, name and price it, then have the camera recognise it live — a real embedder, an enrol flow, and honest measured accuracy',
  phases: [
    { title: 'Build', detail: '5 agents: embedder, enrol store, upload-to-enrol, live recognition, UI' },
    { title: 'Measure', detail: 'honest held-out accuracy, confusion matrix, abstention rate' },
  ],
}

const ROOT = '/Users/agnik/Desktop/razor'

const BASE = [
  'GAWAAH — a camera-native kirana counter. Repo: ' + ROOT,
  'Python: ' + ROOT + '/.venv/bin/python   Tests: ./.venv/bin/python -m pytest tests/<file> -q',
  'JS:     node web/selftest.mjs  |  node web/panels/<x>.test.mjs',
  'Browser: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless --disable-gpu \\',
  '           --virtual-time-budget=25000 --dump-dom "http://127.0.0.1:8787/?v=1" > /tmp/dom.html',
  '',
  'STATE: 1930 Python + 954 JS tests pass. The counter already works end to end with no camera:',
  '`--sim --sim-source` drives 13 beats to 13950 paise with 1 amber line excluded. Real money works:',
  'a genuine Razorpay test-mode link was paid and the counter went PAID off a signed webhook.',
  '',
  '*** WHAT YOU ARE BUILDING ***',
  'The shopkeeper takes a PHOTO of a product, types a name and a price, and from then on the camera',
  'RECOGNISES that product and prices it automatically. Today this is impossible: gawaah/identity.py',
  'has a Gallery and an Identifier that take an INJECTED embed_fn, and nothing anywhere implements one.',
  'Every SKU gallery in the repo is fed by a test double.',
  '',
  'EXISTING PIECES YOU BUILD ON:',
  '  gawaah/identity.py    Gallery.enroll(sku_id, vectors, footprint_mm) / .save / .load',
  '                        Identifier(gallery, embed_fn, theta=0.10, phi=0.55, tau_mm=4.0)',
  '                          .identify(crop, long_edge_mm) -> Identification(sku_id|None, top1, top2,',
  '                           margin, reason)   reasons: below_margin, below_similarity,',
  '                           no_candidate_in_footprint, ambiguous_pair',
  '                          .check_collision(vectors, footprint) -> refuses a colliding enrolment',
  '  gawaah/takhti.py      PlaneEngine().detect/.rectify -> the 840x1188 metric buffer @ 2.8283 px/mm',
  '  gawaah/placement.py   PlacementDetector(ref).update(rect) -> Placement(long_edge_mm, ...)',
  '  gawaah/brain.py       Brain._crop(buffer, placement) -> the item crop the embedder sees',
  '  gawaah/sim_source.py  SimSource(seed).enrol_gallery(gallery, embed_fn, crop_fn) -> {sku: paise}',
  '  gawaah/brain_server.py  the websocket + /sim endpoints; PANELS, canonical_panel()',
  '  tools/upload_app.py   a working upload page on :8790 that already locks the mat and measures',
  '                        objects in mm against known truth (38.19 vs 38.0 etc)',
  '  web/panels/enrol.js   an ENROL panel that can already add an SKU name + price in integer paise',
  '',
  'THE HARD CONSTRAINT, and it shapes everything:',
  'INVARIANT 3 -- ZERO MODEL WEIGHTS IN THE BROWSER. And there is NO reliable network here, so you',
  'CANNOT download MobileCLIP or any other checkpoint. The embedder must therefore be CLASSICAL and',
  'computed from cv2 primitives that already ship: colour histograms, ORB descriptors, edge and shape',
  'statistics, moments. This is not a downgrade dressed up as a choice -- for a BOUNDED gallery of ~24',
  'kirana packets that a shopkeeper enrols himself, a classical descriptor is a defensible engineering',
  'answer, and it is honest about what it can and cannot separate.',
  '',
  'INVARIANTS:',
  '1. Money is integer paise. tools/lint_no_float.py must stay green.',
  '2. GREEN only from a signature-verified webhook. Recognition NEVER settles money.',
  '3. Zero model weights in the browser. No checkpoint downloads anywhere.',
  '4. Only the rectified 840x1188 mat crop survives a frame grab.',
  '7. ABSTAIN RATHER THAN GUESS. This is the whole product. A recogniser that is confidently wrong',
  '   about a price is worse than one that says "I do not know" and asks for a tap. Identifier already',
  '   has theta/phi/tau gates -- honour them, never widen them to make a demo look better.',
  '',
  'RULES: own ONLY your files. No git write commands. No pip install. No network fetches.',
  'RUN what you write. Report REAL numbers. Never report a test you did not run. Do not weaken a test.',
].join('\n')

const S = {
  type: 'object',
  properties: {
    task: { type: 'string' },
    files_changed: { type: 'array', items: { type: 'string' } },
    what_now_works: { type: 'string' },
    proof: { type: 'string', description: 'the REAL command output proving it' },
    measured_numbers: { type: 'array', items: { type: 'string' } },
    tests_passing: { type: 'number' },
    abstentions: { type: 'array', items: { type: 'string' } },
    honest_limits: { type: 'array', items: { type: 'string' } },
  },
  required: ['task', 'files_changed', 'what_now_works', 'proof', 'measured_numbers', 'tests_passing', 'abstentions', 'honest_limits'],
}

const JOBS = [
  { k: 'embedder', files: 'gawaah/embedder.py + tests/test_embedder.py', brief: [
    'THE MISSING PIECE. Implement the embed_fn that gawaah/identity.py has always taken and nobody has',
    'ever written. Everything else in this workflow depends on it.',
    '',
    'gawaah/embedder.py: a deterministic, model-free descriptor of an item crop.',
    '  def embed(crop_bgr) -> np.ndarray   # L2-normalised float32 vector, fixed length',
    'Compose it from cv2 primitives that ship in this build (VERIFIED PRESENT: calcHist, compareHist,',
    'ORB, findContours, minAreaRect, HuMoments, Canny, cvtColor. AKAZE and SIFT are ABSENT in the',
    'Python build -- ORB is the only descriptor available on BOTH the Python and JS sides, so ORB it is).',
    'Suggested channels, but measure and keep what actually separates:',
    '  - HSV colour histogram, hue and saturation, coarse bins, L1-normalised per channel',
    '  - a small spatial colour grid (e.g. 3x3 mean HSV) so a red cap on a white tube differs from a',
    '    white cap on a red tube -- a global histogram cannot tell those apart',
    '  - edge-orientation histogram from Sobel/Canny',
    '  - shape: Hu moments and aspect ratio of the oriented box',
    'Weight and concatenate, then L2-normalise so cosine similarity is meaningful.',
    '',
    'REQUIREMENTS THAT MATTER MORE THAN THE FEATURE CHOICE:',
    '  - DETERMINISTIC: the same crop gives a byte-identical vector, twice, in two processes.',
    '  - ROBUST to the things that will actually vary: modest rotation (the item is on a mat and may be',
    '    turned), scale (crop tightness varies), and brightness (shop lighting). Test each explicitly',
    '    and report the measured cosine similarity under each perturbation.',
    '  - SEPARATING: report the measured cosine between DIFFERENT products, and between two views of',
    '    the SAME product. If those distributions overlap, the descriptor is useless and you must say',
    '    so rather than shipping it.',
    '  - Fast enough to run per placement (not per frame): report the measured ms.',
    'TESTS: build a small synthetic product set (coloured/patterned rectangles with distinct layouts),',
    'measure same-vs-different cosine distributions, and ASSERT a real separation margin. Report the',
    'actual numbers. Include a deliberately hard pair (same size, same colour, different layout) and',
    'report honestly whether the descriptor separates it.',
  ].join('\n') },

  { k: 'store', files: 'gawaah/shop_store.py + tests/test_shop_store.py', brief: [
    'THE SHOPKEEPER\'S CATALOG ON DISK. Today prices live in results/shop.json as a bare',
    '{"sku": paise} map loaded by gawaah/live_app.py, and the enrolled VECTORS live nowhere at all --',
    'a restart forgets every product the shopkeeper taught it.',
    '',
    'gawaah/shop_store.py: class ShopStore(dir) persisting the whole catalog:',
    '  .add_sku(sku_id, name, price_paise, vectors, footprint_mm, photo_png=None) -> Result',
    '  .remove(sku_id) ; .get(sku_id) ; .all() ; .price_paise(sku_id)',
    '  .to_gallery() -> a gawaah.identity.Gallery ready to hand to an Identifier',
    '  .price_book() -> the mapping gawaah/paisa.py wants',
    'Persist as JSON plus the vectors (json list of floats is fine; state the size cost). Store the',
    'enrolment PHOTO so the UI can show what was taught, but downscale it and say the cap.',
    '',
    'MONEY RULES, tested explicitly because this is where a price enters the system:',
    '  - price is INTEGER PAISE, always. Reject float, reject a rupee string with sub-paisa precision,',
    '    reject negative, reject bool. Use gawaah.money.paise() and let MoneyError through.',
    '  - adding an SKU whose id already exists REPLACES it and says so; it never silently doubles.',
    '  - to_gallery() and price_book() must never disagree about which SKUs exist.',
    'COLLISION: on add, run Identifier.check_collision against the existing gallery and REFUSE an',
    'enrolment that is inside both the appearance margin and the footprint tolerance, returning the',
    'colliding sku id. Catching a collision at enrolment time -- when fixing is free -- is the whole',
    'point; catching it at the till means a wrong price on a real sale.',
    'TESTS: round-trip through disk; a float price is refused; a duplicate replaces; a collision is',
    'refused by name; to_gallery and price_book agree; a corrupt file fails loudly rather than silently',
    'returning an empty shop.',
  ].join('\n') },

  { k: 'enrolapi', files: 'tools/upload_app.py + tests/test_upload_enrol.py', brief: [
    'PHOTO -> PRODUCT. You own tools/upload_app.py, which already serves an upload page on :8790 and',
    'measures objects on the mat in millimetres. Extend it into the enrolment surface.',
    '',
    'ADD:',
    '  POST /enrol   multipart: image + sku_id + name + price_rupees',
    '    -> lock the mat, find the LARGEST placement, crop it, embed it (gawaah.embedder.embed),',
    '       and add it to the ShopStore (gawaah.shop_store) with its measured footprint_mm.',
    '    -> return what was measured and what was stored, including the collision verdict.',
    '  POST /recognise  multipart: image',
    '    -> lock, find every placement, embed each, identify against the stored gallery, and return',
    '       per-item: sku or None, the reason when None, top1/top2/margin, measured mm, and the price.',
    '    -> report a TOTAL in integer paise, with unrecognised items EXCLUDED and listed as amber.',
    '  GET  /shop     the current catalog with prices and thumbnails',
    '  DELETE /shop/{sku_id}',
    'The page must let a user do all of this with a mouse: upload a photo, name it, price it in rupees,',
    'see it appear in the catalog, then upload a SECOND photo and watch it be recognised and priced.',
    'That round trip -- teach it, then show it -- is the whole demonstration.',
    '',
    'HONESTY REQUIREMENTS: an item the gallery cannot place is AMBER with its named reason and is',
    'EXCLUDED from the total; never priced by guess. If the mat does not lock, say how many markers were',
    'found and what to change. Nothing here settles money and the page must say so.',
    'TESTS: enrol from a synthetic photo then recognise the same product in a DIFFERENT synthetic photo',
    '(different position/rotation) and assert it is found; assert an unenrolled product returns None',
    'with a named reason and is excluded from the total; assert a float price is refused at the API.',
  ].join('\n') },

  { k: 'liverec', files: 'gawaah/recogniser.py + tests/test_recogniser.py', brief: [
    'RECOGNITION IN THE LIVE LOOP. The brain must use the shopkeeper\'s taught catalog, not a test double.',
    '',
    'gawaah/recogniser.py: the component the brain asks "what is this, and what does it cost?"',
    '  class Recogniser(store, embed_fn, theta, phi, tau_mm)',
    '    .identify(crop, long_edge_mm) -> Recognition(sku_id|None, price_paise|None, reason,',
    '                                                 top1, top2, margin, abstained: bool)',
    '    .reload()          # pick up SKUs enrolled since start, without a restart',
    '    .stats()           # counts by reason, so the abstention rate is publishable',
    'It wraps gawaah.identity.Identifier and adds the price lookup, and it is the ONLY place those two',
    'are joined -- a sku with no price must abstain rather than bill zero.',
    '',
    'ABSTENTION IS THE FEATURE. Every path that cannot name an item with confidence returns',
    'abstained=True with a NAMED reason and price_paise=None, and the caller excludes it from the total.',
    'Explicitly required: no gallery at all; sku matched but no price; margin below theta; similarity',
    'below phi; no candidate inside the footprint tolerance; two candidates equally close.',
    'Expose the running abstention rate, because publishing it beside the accuracy is the argument.',
    'TESTS: a taught product is recognised across position and rotation; an untaught one abstains with',
    'the right reason; a priceless sku abstains rather than billing 0; stats() counts every reason;',
    'reload() picks up a new SKU without reconstructing the object.',
  ].join('\n') },

  { k: 'enrolui', files: 'web/panels/enrol.js + web/panels/enrol.test.mjs', brief: [
    'THE BROWSER FLOW. You own the ENROL panel. Make it the place a shopkeeper teaches the counter.',
    '',
    'It must support, entirely with a mouse and a keyboard:',
    '  1. TEACH FROM A PHOTO: pick an image file, type a name and a price in RUPEES, submit. The price',
    '     converts to integer paise at the boundary and is REFUSED loudly if it is not exact -- 214.507',
    '     must be rejected, never rounded. Show the paise that will be stored.',
    '  2. TEACH FROM THE CAMERA: capture the current rectified crop instead of a file, when one exists.',
    '  3. THE CATALOG: every taught SKU with its thumbnail, name, price in rupees AND paise, measured',
    '     footprint in mm, and a remove button.',
    '  4. TRY IT: upload a second photo and see what the counter thinks it is -- the sku, the price, or',
    '     an AMBER with its named reason. This is the payoff and it should be the most prominent thing',
    '     on the panel after the catalog.',
    'Talk to the endpoints another agent is adding to tools/upload_app.py on :8790 (/enrol, /recognise,',
    '/shop). Make the base URL configurable and degrade with a named reason when that service is down --',
    'never a blank panel.',
    'Pure render functions so enrol.test.mjs unit-tests them with no browser. Keep the existing 88 tests',
    'passing and add tests for the price boundary specifically: 214.507, "abc", "", -5, 1e3, 0.',
    'Run: cd ' + ROOT + ' && node web/panels/enrol.test.mjs',
  ].join('\n') },
]

phase('Build')
log('5 agents: the missing embedder, the shop store, photo-enrol API, live recogniser, browser flow.')

const built = (await parallel(JOBS.map(j => () =>
  agent(BASE + '\n\n=== YOUR FILES (you own these and ONLY these) ===\n' + j.files +
    '\n\n=== YOUR TASK ===\n' + j.brief +
    '\n\nBuild it, RUN it, iterate until it genuinely works, then report REAL numbers.',
    { label: 'build:' + j.k, phase: 'Build', schema: S })
))).filter(Boolean)

phase('Measure')
log('Measuring recognition honestly on a held-out set.')

const measured = await agent(
  BASE +
  '\n\n=== WHAT WAS BUILT ===\n' + JSON.stringify(built.map(b => ({
    task: b.task, works: b.what_now_works, numbers: b.measured_numbers, limits: b.honest_limits })), null, 1) +
  '\n\n=== YOUR JOB: MEASURE IT HONESTLY, AND TRY TO BREAK IT ===\n' +
  'You own tools/bench_recognise.py + tests/test_bench_recognise.py and NOTHING else.\n' +
  '\n' +
  'Build a held-out evaluation of the photo-enrol -> recognise loop and publish the numbers the way\n' +
  'the buildathon asks for: accuracy WITH the abstention rate beside it, and the false-price rate\n' +
  'separately, because a confidently wrong price is the error that costs a shopkeeper money.\n' +
  '\n' +
  'Build a synthetic product set of at least 12 items INCLUDING deliberately hard pairs: same size\n' +
  'different colour, same colour different layout, and one near-identical pair that SHOULD collide.\n' +
  'Enrol from one view, evaluate on DIFFERENT views (moved, rotated, relit, differently cropped).\n' +
  'The enrolment views and the evaluation views must be disjoint -- state how you guaranteed that.\n' +
  '\n' +
  'REPORT, as real measured numbers:\n' +
  '  - top-1 accuracy on DECIDED items, and the abstention rate, side by side\n' +
  '  - the FALSE-PRICE RATE: how often a wrong sku was returned confidently. This is the number that\n' +
  '    matters most and it must be reported even if it is bad\n' +
  '  - a confusion matrix over the 12 products\n' +
  '  - the same-product vs different-product cosine distributions, and their overlap\n' +
  '  - which hard pairs the descriptor separates and which it does not\n' +
  '  - per-item latency in ms\n' +
  'Then write results/RECOGNISE.md with the tables, and a "where this loses" section naming the\n' +
  'conditions under which it fails.\n' +
  '\n' +
  'BE ADVERSARIAL. If the embedder cannot separate real products, say so plainly with the numbers --\n' +
  'a measured negative result published honestly is worth more here than a flattering demo. Check\n' +
  'specifically whether anyone widened theta/phi to make accuracy look better, and whether the\n' +
  'evaluation views are genuinely disjoint from the enrolment views.',
  { label: 'measure:honest', phase: 'Measure', schema: S }
)

return {
  built: built.map(b => ({ task: b.task, works: b.what_now_works, numbers: b.measured_numbers, limits: b.honest_limits })),
  measured: { numbers: measured.measured_numbers, limits: measured.honest_limits, proof: measured.proof },
}
