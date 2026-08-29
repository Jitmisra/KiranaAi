export const meta = {
  name: 'gawaah-harden-and-integrate',
  description: 'Fix the real defects the adversarial verifiers found, then wire the 17 island modules into one runnable end-to-end system with a demo runner',
  phases: [
    { title: 'Fix', detail: '8 agents fixing named, reproduced defects — money bugs first' },
    { title: 'Integrate', detail: 'wire the modules into a runnable brain + true end-to-end test + demo runner' },
    { title: 'Harden', detail: 'mutation testing and property-based invariants across the whole repo' },
  ],
}

const ROOT = '/Users/agnik/Desktop/razor'

const BASE = [
  'GAWAAH — a camera-native kirana counter. Repo: ' + ROOT,
  'Python: ' + ROOT + '/.venv/bin/python   Tests: cd ' + ROOT + ' && ./.venv/bin/python -m pytest tests/ -q',
  'STATE: 887 tests passing, 17 modules built, lint clean. Everything below is real, working code.',
  '',
  'MODULES (all exist, all tested):',
  '  gawaah/money.py      integer paise; paise(), from_rupees_str(), to_rupees_str(), add(), total()',
  '  gawaah/clock.py      Clock protocol; RealClock, VirtualClock(start, step_ms)',
  '  gawaah/ledger.py     Ledger(path).append(ts=,module=,**f)->hash; verify(path)->(ok,n,head,err)',
  '  gawaah/takhti.py     PlaneEngine().detect(frame)->MatLock(locked,H,scale_err,persp_index,reproj_rmse_px)',
  '                       .rectify(frame,H)->840x1188 buffer @ 2.8283 px/mm; render_takhti()',
  '  gawaah/placement.py  PlacementDetector(ref).update(rect)->[Placement(centre_mm,long_edge_mm,area_mm2,stable,...)]',
  '  gawaah/sellevent.py  LineZone(p1_mm,p2_mm,min_crossing_frames).update(tracks)->CrossingResult;',
  '                       CentroidTracker(max_dist_mm,max_missing_frames). NO cv2 import -- runs server-side.',
  '  gawaah/identity.py   Gallery, Identifier(gallery,embed_fn,theta,phi,tau_mm).identify()->Identification',
  '  gawaah/kernel.py     Kernel(db,clock,ledger).create_intent/mark_*/reconcile -- exactly-once via sqlite UNIQUE',
  '  gawaah/rzp_sim.py    RazorpaySim(webhook_secret,clock): create_payment_link, pay_link, HMAC-signed webhooks,',
  '                       set_mode(timeout|error|duplicate_webhook|out_of_order|wrong_amount)',
  '  gawaah/webhook.py    verify_signature(raw,sig,secret); GreenPredicate.evaluate()->GreenVerdict',
  '  gawaah/session.py    Session(clock,ledger) state machine: IDLE/MEASURING/PRICED/AMBER/BASKET_OPEN/',
  '                       AWAITING_SETTLEMENT/PENDING_OFFLINE/PAID/AMOUNT_MISMATCH/MAT_LOST/...',
  '  gawaah/paisa.py      FastAPI money service: POST /intent (re-runs crossing server-side), POST /webhook',
  '  gawaah/saaf.py       BurstStacker(scale,...).stack(frames)->StackResult  (ORB, ECC-guarded)',
  '  gawaah/ident_sticker.py StickerRegistry(dir).enrol/compare -> StickerVerdict (NO QR library, pixel diff)',
  '  gawaah/mudra.py      OccluderGesture(ref,...).update(rect)->GestureState(NONE/OPEN/FIST/GOODS/AMBIGUOUS)',
  '  gawaah/chilla.py     ScreenFinder.detect(); LedgerMatcher(mirror,window_s).match()->MatchResult',
  '  tools/bench.py       benchmark harness -> results/metrics.json + METRICS.md; verify_claims()',
  '  tools/make_takhti.py build_a3_page/build_a4_page/render_page/emit -> printable mat',
  '  web/                 index.html app.js style.css selftest.mjs (173 node selftests pass)',
  '',
  'NON-NEGOTIABLE INVARIANTS:',
  '1. Money is integer paise. tools/lint_no_float.py has a STRICT whole-file list and a repo-wide',
  '   SEMANTIC check for floats reaching money-named identifiers. It must stay green.',
  '2. GREEN only when ALL FOUR hold: valid HMAC-SHA256 over RAW BYTES before any JSON parse, AND event in',
  '   the green set, AND notes.session_id matches an OPEN intent, AND amount == intent.amount_paise exactly.',
  '3. Zero model weights in the browser.',
  '4. Only the rectified mat crop survives a frame grab.',
  '5. paisa is the sole secret holder and re-runs the crossing predicate server-side.',
  '6. NO FORGERY PRIMITIVES -- never construct or regenerate a UPI payload. Disqualifying.',
  '7. Abstain rather than guess. Unknown -> amber, excluded from total. Stale -> amber, never red.',
  '9. Every published number is produced by running code.',
  '',
  'RULES: You own ONLY your listed files. Do not touch others -- agents run concurrently.',
  'No git write commands. No pip install. WRITE REAL CODE, RUN THE TESTS, iterate until green.',
  'Never report a test you did not run. Do not weaken a test to make it pass. If something cannot',
  'work, say so with evidence.',
].join('\n')

const S = {
  type: 'object',
  properties: {
    task: { type: 'string' },
    files_changed: { type: 'array', items: { type: 'string' } },
    defects_fixed: { type: 'array', items: { type: 'string' } },
    tests_added: { type: 'number' },
    total_passing: { type: 'number' },
    test_output_tail: { type: 'string' },
    proof_of_fix: { type: 'string', description: 'the concrete evidence the defect is gone -- e.g. the failing case now returns X' },
    measured_numbers: { type: 'array', items: { type: 'string' } },
    honest_limits: { type: 'array', items: { type: 'string' } },
    regressions_checked: { type: 'boolean', description: 'did you re-run the FULL suite, not just your file?' },
  },
  required: ['task', 'files_changed', 'defects_fixed', 'tests_added', 'total_passing', 'test_output_tail', 'proof_of_fix', 'measured_numbers', 'honest_limits', 'regressions_checked'],
}

const FIXES = [
  { k: 'webhook-money', files: 'gawaah/webhook.py + tests/test_webhook.py', brief: [
    'TWO REAL, REPRODUCED MONEY DEFECTS found by adversarial verification. Fix both, prove both.',
    '',
    'DEFECT 1 (INVARIANT 2, medium-high): A PARTIAL PAYMENT CAN GREEN A FULL INTENT.',
    'When the webhook payload carries only the payment_link entity (not a nested payment entity), the',
    'amount actually paid is not the field being compared. A payment_link can be part-paid while the',
    'link entity still reports the full amount. Reproduce it first with a test that FAILS, then fix so',
    'that green requires the SETTLED amount to equal intent.amount_paise exactly, and a partial or',
    'unknown-amount payload abstains rather than greens. Add amount_paid vs amount reasoning.',
    '',
    'DEFECT 2 (low-medium): REPLAY-STORE POISONING / DENIAL-OF-GREEN.',
    'The untrusted, un-HMAC-covered X-Razorpay-Event-Id HEADER is written into the replay store. An',
    'attacker who can guess or observe a future event id can pre-poison the store so the genuine webhook',
    'is later rejected as a replay -- money lands and the counter never goes green.',
    'Fix: derive the replay key ONLY from HMAC-VERIFIED body content (e.g. the event id inside the signed',
    'payload, or a hash of the raw verified body), never from an unauthenticated header. Write the',
    'attack as a test that fails before the fix and passes after.',
    '',
    'ALSO: the verifier found mutation M12 -- deleting parse_float=str from json.loads fails ZERO tests.',
    'Add a test that pins it (a JSON body with 214.50 as a bare number must not become a float amount).',
    'ALSO: the currency and entity-status gates fail OPEN when the field is absent (webhook.py:403 style',
    '`if currency is not None`). Decide deliberately: either fail closed, or document why open is right,',
    'and pin the decision with a test either way.',
  ].join('\n') },

  { k: 'sellevent-abstain', files: 'gawaah/sellevent.py + tests/test_sellevent.py', brief: [
    'DEFECT (INVARIANT 7, rated HIGH and MONEY-LOSING by the verifier):',
    'The re-identification path guesses instead of abstaining. When a track is lost and a new centroid',
    'appears nearby, the tracker silently re-associates it to the old id, which can convert an uncounted',
    'crossing into a counted one, or mask a genuine second item as the same item. That is a silent',
    'money-losing guess in exactly the place invariant 7 forbids one.',
    'Fix: when re-identification is ambiguous (more than one candidate inside the gate, or the gap',
    'exceeded a confidence window), the tracker must emit a NAMED abstention rather than binding an id.',
    'Route it to the existing exception surface so it becomes an amber exception row, not a silent bind.',
    '',
    'ALSO (INVARIANT 5, partial): LineZone.mat_exit_line()`s docstring claims it can run on a camera-less',
    'server, but it lazy-imports takhti, which imports cv2. Either make the import genuinely optional so',
    'the predicate really does run server-side with no cv2, or correct the docstring. paisa MUST be able',
    'to re-run the crossing predicate without OpenCV -- that is invariant 5. Prove it with a test that',
    'imports the predicate path with cv2 blocked from sys.modules.',
  ].join('\n') },

  { k: 'bench-mutants', files: 'tools/bench.py + tests/test_bench.py', brief: [
    'DEFECT (rated HIGH): THE FIVE BENCHMARK BODIES HAVE ZERO SEMANTIC TEST COVERAGE.',
    'The verifier ran 26 mutants against tools/bench.py. 16 were killed; 10 SURVIVED. Every surviving',
    'mutant was inside a benchmark body -- meaning the numbers the README publishes could be silently',
    'wrong and no test would notice. This directly threatens invariant 9.',
    'Fix: write semantic tests for each benchmark body so that mutating its computation is DETECTED.',
    'For each benchmark, assert on a known-answer case: construct an input whose correct metric value you',
    'can derive independently, run the benchmark, assert the number. A benchmark that cannot be given a',
    'known-answer test should say so loudly rather than silently publishing.',
    '',
    'ALSO FIX, all named by the verifier:',
    ' - worst_seed is unpinned: inverting _worst_seed to return the BEST seed changes published output and',
    '   no test fails. Pin it.',
    ' - a benchmark that measured NOTHING reports STATUS_OK. Empty per_seed must report NOT MEASURED.',
    ' - the "p95" column is actually p5 for higher-is-better benchmarks. Either rename the column per',
    '   direction or compute the true p95. Publishing p5 under a p95 header is a labelling lie.',
    'Finally: run your own mutation pass over the benchmark bodies and REPORT the kill rate before/after.',
  ].join('\n') },

  { k: 'chilla-dead', files: 'gawaah/chilla.py + tests/test_chilla.py', brief: [
    'THREE DEFECTS, all reproduced by the verifier.',
    '',
    '1. DEAD ABSTENTION BRANCH: the "too_small" reason is PROVABLY UNREACHABLE, because detect()',
    '   pre-filters contours by contourArea >= MIN_AREA_MM2 * PX_PER_MM_X * PX_PER_MM_Y before the check',
    '   that would emit it. Either make it reachable or delete it -- a published abstention reason that',
    '   can never fire is a false claim about the system\'s behaviour.',
    '2. MUTATION SURVIVOR M15: the rectangularity gate (chilla.py:450) can be DELETED ENTIRELY and all 60',
    '   tests still pass. Add a test that fails without it (a non-rectangular bright blob must be rejected).',
    '3. THE "EMISSIVE" GATE DOES NOT DISTINGUISH EMISSIVE FROM MERELY REFLECTIVE. min_emissive_delta only',
    '   asks whether the quad is >=18 grey levels brighter than the mat -- a white paper under a lamp',
    '   passes. Either find a real discriminator (a phone screen is bright AND has a different local',
    '   contrast/colour structure than diffuse paper) or RENAME it honestly to what it measures',
    '   (brightness delta) and state the limitation in the abstention list.',
    'ALSO: the builder claimed 16 distinct named abstention reasons; the verifier instrumented detect()',
    'and found several UNTESTED. Add a test that exercises every reason the code can emit, and delete any',
    'that cannot be reached.',
  ].join('\n') },

  { k: 'mudra-saaf', files: 'gawaah/mudra.py + tests/test_mudra.py + gawaah/saaf.py + tests/test_saaf.py', brief: [
    'TWO MODULES, ONE AGENT (they share the enrolment/gesture boundary).',
    '',
    'MUDRA DEFECT 1: tests/test_mudra.py test_every_reason_the_code_can_emit_is_published is THE ONE',
    'GENUINELY WEAKENED TEST in the repo. Its docstring claims it checks completeness, but it does not.',
    'Make it actually enumerate every reason string the module can emit (AST-walk the source or a',
    'registry) and assert each is published AND reachable.',
    'MUDRA DEFECT 2: test_shape_metrics_is_immutable uses a bare `pytest.raises(Exception)`, which would',
    'pass on ANY exception including AttributeError from a typo. Narrow it to the specific exception.',
    '',
    'SAAF DEFECT (INVARIANT 7, measured blind spot): the blur floor is a relative max()-based threshold,',
    'so a UNIFORMLY out-of-focus burst has no frame rejected -- every frame is equally bad, so the',
    'relative gate passes them all and returns a confidently sharp-looking stack of mush. Fix: add an',
    'ABSOLUTE floor as well as the relative one, so a burst that is bad everywhere ABSTAINS with a named',
    'reason rather than returning a degraded image. Prove it with a synthetically defocused burst.',
    '',
    'For both modules, re-run the full suite to confirm no regressions.',
  ].join('\n') },

  { k: 'kernel-paisa', files: 'gawaah/kernel.py + tests/test_kernel.py + gawaah/paisa.py + tests/test_paisa.py', brief: [
    'THREE GAPS the verifier named as real (none are money-safety violations, all are operational risk).',
    '',
    '1. KERNEL: THE ABSTENTION LOOP IS UNBOUNDED. There is no cap on retrieve_attempts and no escalation.',
    '   An intent stuck INDETERMINATE will be swept forever. Add a bounded retry with a terminal',
    '   ESCALATED state that requires human resolution, and an audit line when it escalates. Nothing may',
    '   auto-charge on escalation.',
    '2. KERNEL: Ledger.append keeps an in-memory head, so TWO PROCESSES sharing one ledger file corrupt',
    '   the chain. The RLock only covers threads in one process. Either add file-level locking (fcntl) so',
    '   multi-process append is safe, or make the single-writer requirement STRUCTURAL by taking an',
    '   exclusive lock at open and failing loudly if a second writer attaches. Prove it with a test that',
    '   spawns a second process and asserts the chain still verifies.',
    '3. PAISA: THE PII-STRIPPING PATH IS UNGUARDED BY TESTS. rzp_sim documents that paisa strips',
    '   vpa/email/contact/card before persisting, but no test asserts it. Write a test that pushes a',
    '   webhook containing email, contact, vpa and a card object and asserts NONE of them are persisted,',
    '   logged, or returned by any endpoint. Also assert secrets never appear in any response body.',
    '   ALSO: INVARIANT 1 at the price-book boundary is untested -- removing the paise() call in',
    '   DictPriceBook.__init__ so int(214.507) silently truncates fails no test. Pin it.',
  ].join('\n') },

  { k: 'web-robust', files: 'web/app.js + web/selftest.mjs', brief: [
    'THREE DEFECTS in the browser client, all named by the verifier.',
    '',
    '1. STALE LOCK ON DETECTOR EXCEPTION (invariant 7): web/app.js does `lock = detector(els.raw)`.',
    '   If detector() throws, the assignment never happens and the PREVIOUS lock silently persists --',
    '   so the UI keeps painting glyphs on a plane it can no longer see. Fix: clear the lock and enter a',
    '   visible MAT_LOST state on any detector exception. Fail closed.',
    '2. ABSTENTION-BY-THROW GAP: adjudicateLock guards NaN correctly (the `!(x <= MAX)` form fails closed)',
    '   but does NOT guard non-finite corner coordinates -- Infinity propagates into the homography.',
    '   Guard every corner for Number.isFinite before use.',
    '3. selftest.mjs swallows ALL exceptions in the money fuzz: `try { ... } catch { /* MoneyError is fine */ }`',
    '   catches everything, so a TypeError in the reducer would read as a pass. Narrow it to the specific',
    '   expected error and re-throw anything else.',
    'ALSO: the verifier flagged that selftest.mjs contains a block of HARDCODED transcribed Python golden',
    'values (PY = {...}) for the homography. That is fine as a cross-language pin, but it must be',
    'GENERATED, not transcribed -- add a small generator so a Python-side change cannot silently diverge',
    'from the JS pin, or at minimum add a test that regenerates and compares.',
    'Run: cd ' + ROOT + ' && node web/selftest.mjs   and report the real output.',
  ].join('\n') },

  { k: 'placement-ident', files: 'gawaah/placement.py + tests/test_placement.py + gawaah/ident_sticker.py + tests/test_ident_sticker.py', brief: [
    'TWO MODULES.',
    '',
    'PLACEMENT DEFECT 1 (the verifier\'s most substantive finding): morphologyEx OPEN+CLOSE is named',
    'explicitly by the contract, is present in the code, but has NO test that fails if it is removed.',
    'Add a test with salt-and-pepper noise and a thin bridge between two objects, such that deleting',
    'OPEN or CLOSE changes the measured result and fails.',
    'PLACEMENT DEFECT 2: test_border_px_constant_is_a_real_margin_not_a_disguised_one asserts only on',
    'module constants (0 <= BORDER_PX <= something) -- a tautology. Replace with a behavioural test:',
    'an object straddling the border must be REFUSED, and one just inside must be measured.',
    'PLACEMENT GAP 3: two touching objects merge into one oversized contour and are reported as ONE',
    'placement. The PRD requires an "alag alag rakhiye" two-component refusal. fill_ratio is already',
    'exposed as the signal. WIRE THE POLICY: a blob whose fill_ratio indicates two merged components must',
    'abstain with a named reason rather than reporting one oversized item. This is a money bug -- a merged',
    'contour bills two items as one.',
    '',
    'IDENT_STICKER DEFECT: two weak tests. (a) test_SAFETY_module_namespace_exposes_no_qr_capability',
    'asserts `"QR" in type(...)`-style tautology; make it a REAL safety test that AST-walks the module and',
    'asserts no QR encoder/decoder symbol is imported or defined. (b) a redundant assert at line ~831 is',
    'strictly implied by the line above it; replace it with something that adds coverage.',
  ].join('\n') },
]

phase('Fix')
log('8 agents fixing named, reproduced defects. Money bugs first.')

const fixed = (await parallel(FIXES.map(f => () =>
  agent(BASE + '\n\n=== YOUR FILES (you own these and ONLY these) ===\n' + f.files +
    '\n\n=== YOUR TASK ===\n' + f.brief +
    '\n\nFor EVERY defect: first write a test that FAILS demonstrating it, then fix, then show the test',
    { label: 'fix:' + f.k, phase: 'Fix', schema: S }
  )
))).filter(Boolean)

log('Fix phase: ' + fixed.length + '/' + FIXES.length + '. Now integrating the islands.')

const INTEGRATE = [
  { k: 'brain', files: 'gawaah/brain.py + tests/test_brain.py', brief: [
    'THE MISSING PIECE. 17 modules exist as ISLANDS. Nothing wires them together. Build the brain.',
    '',
    'gawaah/brain.py is the process that owns the pipeline on the laptop:',
    '  frame -> PlaneEngine.detect -> rectify -> PlacementDetector.update -> CentroidTracker ->',
    '  LineZone.update -> Identifier.identify -> Session transitions -> Kernel intent -> paisa mint',
    '  -> RazorpaySim webhook -> GreenPredicate -> Session PAID',
    '',
    'API: class Brain(config). .ingest_frame(frame, ts) -> BrainState ; .done() ; .on_webhook(raw, sig)',
    'BrainState carries: mat_lock, placements, basket lines (integer paise), total_paise, amber items',
    '(EXCLUDED from total), session state, exceptions, and the audit head hash.',
    '',
    'REQUIREMENTS:',
    ' - every module is INJECTED so the brain is testable without a camera',
    ' - a VirtualClock makes a whole run byte-reproducible',
    ' - one Ledger threads through everything; ledger.verify() must pass after every scenario',
    ' - AMBER items never reach the total',
    ' - the ONLY path to PAID is a signature-verified webhook via GreenPredicate',
    ' - a WebSocket server on 8787 for the PWA (fastapi/uvicorn), sending BrainState as JSON',
    'TESTS: drive a complete synthetic sale end to end -- render a mat, paste 3 known items, move them',
    'across the exit line, identify them, mint via RazorpaySim, pay, verify the webhook, reach PAID, and',
    'assert the total is exactly right in paise and the ledger verifies. Then the amber path, the offline',
    'path, and a wrong-amount webhook landing in AMOUNT_MISMATCH.',
  ].join('\n') },
  { k: 'e2e', files: 'tests/test_end_to_end.py + tools/e2e_scenarios.py', brief: [
    'THE TRUE END-TO-END SUITE. Do NOT import gawaah/brain.py (another agent is writing it concurrently);',
    'instead wire the EXISTING modules together yourself in tools/e2e_scenarios.py as a scenario driver,',
    'so this suite is independent of the brain and would catch a brain regression.',
    '',
    'Build scenario functions that compose the real modules: takhti -> placement -> sellevent ->',
    'identity -> session -> kernel -> rzp_sim -> webhook. Each returns a structured result.',
    'SCENARIOS (all must be real, none mocked):',
    ' 1. HAPPY PATH: 3 known items cross the exit line, DONE, mint, pay, webhook, PAID, exact paise total.',
    ' 2. AMBER: an unknown item is excluded from the total; the total is correct WITHOUT it.',
    ' 3. REVERT: an item crosses, then is reverted; the total decrements exactly; human_override logged.',
    ' 4. WRONG AMOUNT: webhook amount off by 1 paisa -> AMOUNT_MISMATCH, never PAID.',
    ' 5. TAMPERED WEBHOOK: flip one byte of the body -> rejected, never PAID.',
    ' 6. REPLAY: the same webhook twice -> idempotent, one settlement.',
    ' 7. OFFLINE: network down at DONE -> PENDING_OFFLINE, billing continues, nothing authorised;',
    '    reconnect -> queue drains -> PAID.',
    ' 8. CRASH: kill the kernel between intent-commit and gateway call -> recover -> exactly one charge.',
    ' 9. MAT LOST mid-basket -> total FREEZES, no further billing.',
    '10. CONCURRENCY: 50 threads racing DONE on one session -> exactly one intent, one charge.',
    'Every scenario must assert ledger.verify() passes at the end. Report the real pass count.',
  ].join('\n') },
  { k: 'demorunner', files: 'tools/demo.py + tools/README_DEMO.md', brief: [
    'THE DEMO RUNNER — what makes this filmable and what a judge runs first.',
    '',
    'tools/demo.py must run on a clean clone with NO camera, NO credentials, NO network:',
    '  ' + ROOT + '/.venv/bin/python tools/demo.py',
    'It should drive a complete synthetic counter session using the real modules and print a live,',
    'readable terminal rendering of what the counter would show: the mat lock, items appearing, prices,',
    'the running total in rupees, an AMBER item visibly excluded, the QR mint, the webhook arriving, and',
    'the counter going GREEN -- with the audit-chain head hash printed at each step.',
    'Flags: --seed N (byte-reproducible), --scenario happy|amber|offline|mismatch|attack, --slow (pace it',
    'for filming), --json (machine-readable for CI).',
    'It must END by printing: the exact paise total, the ledger verification result, and a one-line',
    'summary a judge can screenshot.',
    'Also write tools/README_DEMO.md: how to run it, what each scenario proves, and what it does NOT',
    'prove (no camera, no real Razorpay -- the simulator signs the webhooks).',
    'This is the single highest-value artefact for a reviewer with six minutes. Make it beautiful in a',
    'terminal: box drawing, colour via ANSI, aligned columns, tabular numbers. Degrade to plain text when',
    'not a TTY. Test it: a test that runs the demo for each scenario and asserts exit 0 and the expected',
    'final state.',
  ].join('\n') },
]

phase('Integrate')
log('Wiring the islands: brain + independent end-to-end suite + demo runner.')

const integrated = (await parallel(INTEGRATE.map(f => () =>
  agent(BASE + '\n\n=== YOUR FILES (you own these and ONLY these) ===\n' + f.files +
    '\n\n=== YOUR TASK ===\n' + f.brief +
    '\n\nRun everything you write. Report real output.',
    { label: 'integrate:' + f.k, phase: 'Integrate', schema: S }
  )
))).filter(Boolean)

phase('Harden')
log('Mutation testing and property-based invariants across the repo.')

const hardened = (await parallel([
  () => agent(BASE + '\n\n=== YOUR FILES ===\ntools/mutate.py + tests/test_mutation.py' +
    '\n\n=== YOUR TASK: MUTATION TESTING HARNESS ===\n' +
    'Build a real mutation tester for this repo and USE it to find weak tests.\n' +
    'tools/mutate.py: AST-mutate a target module (flip comparison operators, swap and/or, change integer\n' +
    'constants by one, negate booleans, delete a statement, replace a return with a constant), run that\n' +
    "module's test file, and record whether the mutant was KILLED or SURVIVED.\n" +
    'Run it against the money-critical modules: money.py, ledger.py, kernel.py, webhook.py, session.py,\n' +
    'sellevent.py. Report the REAL kill rate per module.\n' +
    'Then WRITE TESTS that kill the most dangerous survivors -- prioritising anything on the money path.\n' +
    'A surviving mutant on the green predicate or the paise arithmetic is a genuine hole.\n' +
    'Report before/after kill rates as real measured numbers. Be honest if a survivor is equivalent\n' +
    '(semantically identical) rather than a real hole -- equivalent mutants are expected and must be\n' +
    'named as such, not counted as failures.',
    { label: 'harden:mutation', phase: 'Harden', schema: S }),
  () => agent(BASE + '\n\n=== YOUR FILES ===\ntests/test_properties.py' +
    '\n\n=== YOUR TASK: PROPERTY-BASED INVARIANTS (Hypothesis) ===\n' +
    'Write property tests that hold for ANY input, across module boundaries. These catch what\n' +
    'example-based tests miss.\n' +
    'Properties to establish:\n' +
    ' - MONEY: for any sequence of add/revert operations, the total equals the sum of committed lines,\n' +
    '   exactly, in integer paise. No sequence produces a float or a rounding drift.\n' +
    ' - LEDGER: for any sequence of appends, verify() passes; for any single-byte mutation anywhere in\n' +
    '   the file, verify() FAILS. (This is the strong one -- tamper-evidence as a property.)\n' +
    ' - SESSION: from any reachable state, an AMBER item never enters the total; PAID is reachable ONLY\n' +
    '   via a green verdict. Model it as a Hypothesis RuleBasedStateMachine.\n' +
    ' - KERNEL: for any interleaving of create_intent calls with the same key, exactly one intent exists.\n' +
    ' - WEBHOOK: for any body and any secret, verify_signature is true iff the signature was computed\n' +
    '   over those exact bytes with that exact secret.\n' +
    ' - SELLEVENT: for any centroid path, net crossings equal (outward crossings - inward crossings), and\n' +
    '   replaying the identical script gives byte-identical results.\n' +
    'Use Hypothesis RuleBasedStateMachine where a sequence matters. Report any counterexample Hypothesis\n' +
    'finds -- a shrunk counterexample is the single most valuable output here, and if you find one, FIX\n' +
    'the bug (in your test file, report it; do not edit modules you do not own -- report it instead).',
    { label: 'harden:properties', phase: 'Harden', schema: S }),
])).filter(Boolean)

return {
  fixes: fixed.map(f => ({ task: f.task, defects: f.defects_fixed, passing: f.total_passing, limits: f.honest_limits })),
  integration: integrated.map(f => ({ task: f.task, files: f.files_changed, passing: f.total_passing, proof: f.proof_of_fix })),
  hardening: hardened.map(f => ({ task: f.task, passing: f.total_passing, measured: f.measured_numbers })),
  all_measured: [...fixed, ...integrated, ...hardened].flatMap(x => (x.measured_numbers || []).map(m => (x.task || '').slice(0, 22) + ': ' + m)),
}
