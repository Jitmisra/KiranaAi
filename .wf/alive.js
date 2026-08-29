export const meta = {
  name: 'gawaah-make-it-alive',
  description: 'Make every panel actually work with no camera and no printed mat: sim drives all six, enrolment UI, image upload, scripted demo run',
  phases: [
    { title: 'Alive', detail: '6 agents: sim feed, enrolment UI, upload path, ledger wiring, demo runner, panel polish' },
    { title: 'Verify', detail: 'headless browser proof that every panel leaves I-DO-NOT-KNOW' },
  ],
}

const ROOT = '/Users/agnik/Desktop/razor'

const BASE = [
  'GAWAAH — a camera-native kirana counter. Repo: ' + ROOT,
  'Python:  ' + ROOT + '/.venv/bin/python        Tests: ./.venv/bin/python -m pytest tests/ -q',
  'JS:      node web/selftest.mjs | node web/panels/panels.test.mjs | node web/panels/panels2.test.mjs',
  'Browser: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless --disable-gpu \\',
  '           --virtual-time-budget=25000 --dump-dom "http://127.0.0.1:8787/?v=1" > /tmp/dom.html',
  '         YOU CAN AND MUST USE THIS. Screenshots: add --screenshot=/tmp/x.png --window-size=1500,1900',
  '',
  'STATE: 1734 Python + 628 JS tests pass. Real money already works: a genuine Razorpay test-mode',
  'Payment Link was paid and the counter went PAID off a signature-verified webhook, with an unknown',
  'item correctly excluded from the total.',
  '',
  '*** THE PROBLEM YOU ARE FIXING ***',
  'The web client is running, OpenCV is loaded, the brain is connected -- and EVERY capability panel',
  'sits on an honest but useless abstention, because nothing ever feeds it:',
  '  MUDRA   mudra_no_reference_frame     "no reference frame has been taken"',
  '  PEEL    peel_no_sticker_enrolled     "nothing has been enrolled"',
  '  CHILLA  chilla_no_screen_found       "no screen found, no ledger mirror watched"',
  '  SAAF    saaf_no_burst_captured       "no burst captured"',
  '  LEDGER  ledger_no_audit_head         "no audit head received from the brain"',
  '  CORE    no mat lock (there is no printed mat and no camera pointed at one)',
  'The abstentions are CORRECT and must remain reachable. The bug is that there is no path by which a',
  'user without a camera, without a printed mat and without a phone can ever see any of it work.',
  '',
  'gawaah/brain_server.py has a --sim flag that is supposed to drive synthetic frames. It is clearly',
  'not feeding the panels. Currently run as:',
  '  RZP_MODE=live ./.venv/bin/python -m gawaah.brain_server --sim --host 0.0.0.0 --port 8787',
  '',
  'MODULES YOU CAN DRIVE (all real, all tested):',
  '  gawaah/takhti.py       render_takhti(px_per_mm) -> the mat image; PlaneEngine().detect/.rectify',
  '                         BUF_W=840 BUF_H=1188 PX_PER_MM_X=2.82828 MARKER_IDS=(0,1,2,3)',
  '  gawaah/placement.py    PlacementDetector(ref).update(rectified) -> [Placement(centre_mm,',
  '                         long_edge_mm, short_edge_mm, area_mm2, angle_deg, stable, frames_seen)]',
  '  gawaah/sellevent.py    LineZone(p1_mm,p2_mm,min_crossing_frames).update(tracks); CentroidTracker',
  '  gawaah/identity.py     Gallery, Identifier(gallery, embed_fn, theta, phi, tau_mm).identify()',
  '  gawaah/mudra.py        OccluderGesture(ref).update(rect) -> GestureState(state, solidity,',
  '                         defects, compactness, area_mm2)  states NONE/OPEN/FIST/GOODS/AMBIGUOUS',
  '  gawaah/ident_sticker.py StickerRegistry(dir).enrol(name, crop) / .compare(name, crop)',
  '                         -> StickerVerdict(ignited_fraction, verdict, ecc_ok, reason)',
  '  gawaah/chilla.py       ScreenFinder.detect(rect); LedgerMatcher(mirror, window_s).match()',
  '  gawaah/saaf.py         BurstStacker.stack(frames) -> StackResult(used, rejected,',
  '                         sharpness_gain, warning)',
  '  gawaah/ledger.py       Ledger(path).append/.head/.count; verify(path)',
  '  gawaah/brain.py        Brain(config).ingest_frame(frame, ts) -> BrainState; .done(); .on_webhook()',
  '  gawaah/paisa.py        FastAPI money service, running separately on 127.0.0.1:8788 in LIVE mode',
  '  tools/make_takhti.py   build_a3_page/build_a4_page/render_page/emit -> build/takhti/*.png',
  '',
  'BRAIN -> CLIENT PROTOCOL (already implemented on both sides):',
  '  {"type":"state", ...BrainState...}   {"type":"ledger", head, count}',
  '  {"type":"mudra", ...}  {"type":"peel", ...}  {"type":"chilla", ...}  {"type":"saaf", ...}',
  '  {"type":"refused", reason}   {"type":"keepalive"}',
  'CLIENT -> BRAIN: {"type":"frame", rect, ts} {"type":"done"} {"type":"revert", item_id}',
  '  {"type":"ack"} {"type":"enrol_sticker", name} {"type":"select_panel", id}',
  'web/app.js routes every one of these to PANEL_REGISTRY.get(id).onState already. The panels render',
  'whatever they are given. The pipe is connected; nothing is being pushed through it.',
  '',
  'INVARIANTS THAT MUST HOLD:',
  '1. Money is integer paise. ./.venv/bin/python tools/lint_no_float.py must stay green.',
  '2. GREEN only from a signature-verified webhook: valid HMAC over RAW BYTES before any JSON parse,',
  '   AND event in the green set, AND notes.session_id matches an OPEN intent, AND amount == intent',
  '   exactly. NO simulated or panel result may ever produce green.',
  '3. Zero model weights in the browser. Geometry only.',
  '4. Only the rectified 840x1188 mat crop survives a frame grab.',
  '7. Abstain rather than guess. Every I-DO-NOT-KNOW state must REMAIN REACHABLE and must still be',
  '   shown when it genuinely applies. You are adding a path to the working state, NOT deleting the',
  '   honest one. Anything simulated must be VISIBLY LABELLED as simulated on screen.',
  '',
  'RULES: own ONLY your files -- other agents work concurrently. No git write commands. No pip install.',
  'RUN what you write, in the browser where it is a browser thing. Report real output. Never report a',
  'test you did not run. Do not weaken a test to make it pass.',
].join('\n')

const S = {
  type: 'object',
  properties: {
    task: { type: 'string' },
    files_changed: { type: 'array', items: { type: 'string' } },
    what_now_works: { type: 'string', description: 'concretely, what a user can now DO that they could not before' },
    proof: { type: 'string', description: 'the real command output or DOM evidence that it works' },
    tests_passing: { type: 'number' },
    abstentions_kept: { type: 'array', items: { type: 'string' }, description: 'the I-do-not-know states still reachable' },
    honest_limits: { type: 'array', items: { type: 'string' } },
  },
  required: ['task', 'files_changed', 'what_now_works', 'proof', 'tests_passing', 'abstentions_kept', 'honest_limits'],
}

const JOBS = [
  { k: 'simfeed', files: 'gawaah/sim_source.py + tests/test_sim_source.py', brief: [
    'THE CORE FIX. Build the synthetic source that makes every panel live with NO camera and NO mat.',
    '',
    'gawaah/sim_source.py: class SimSource(seed, clock) producing a scripted counter session as a',
    'stream of REAL rectified 840x1188 frames, by rendering the actual mat via takhti.render_takhti()',
    'and compositing objects onto it. Not fixtures -- real frames the real detectors then process.',
    '',
    'It must drive a full narrative that exercises every capability:',
    '  1. empty mat            -> mat locks, CORE goes OK',
    '  2. three known SKUs placed, then slid across the exit line -> priced, total climbs',
    '  3. one UNKNOWN item     -> AMBER, excluded from the total (invariant 7 on screen)',
    '  4. a hand-shaped occluder enters -> MUDRA sees OPEN / FIST / AMBIGUOUS',
    '  5. a sticker-like patch laid on the mat -> PEEL enrol then compare, GENUINE then TAMPERED',
    '  6. a bright rectangle (a phone screen) laid on the mat -> CHILLA finds it, matches the ledger',
    '  7. an enrolment burst with deliberate jitter and one blurred frame -> SAAF stacks and reports',
    '     used/rejected and a real measured sharpness gain',
    'API: .frames() yields (frame_bgr, ts, note) ; .script() returns the beat list so the UI can label',
    'the current beat. Deterministic from the seed: the same seed gives byte-identical frames.',
    '',
    'EVERYTHING IT PRODUCES MUST BE MARKED SIMULATED. Every message the brain later derives from a sim',
    'frame carries simulated: true so the UI can badge it. A simulated frame must never be able to',
    'produce GREEN -- that still requires a real signed webhook (invariant 2).',
    '',
    'TESTS: assert the mat locks on a rendered frame; assert a known object measures within 3mm of the',
    'size you composited; assert the unknown item is excluded from the total; assert determinism across',
    'two runs with one seed; assert every frame is exactly 840x1188.',
  ].join('\n') },

  { k: 'simwire', files: 'gawaah/brain_server.py', brief: [
    'WIRE THE SIM INTO THE BRAIN so --sim actually feeds all six panels.',
    '',
    'Today --sim exists but the panels sit on mudra_no_reference_frame / peel_no_sticker_enrolled /',
    'chilla_no_screen_found / saaf_no_burst_captured / ledger_no_audit_head forever. Find _sim_pump and',
    'make it real: pull frames from gawaah.sim_source.SimSource (another agent is writing it -- import',
    'it lazily and, if absent, fall back to rendering takhti frames yourself so YOUR work still runs),',
    'push each through the brain, and EMIT the per-panel messages the client already routes:',
    '  {"type":"mudra"|"peel"|"chilla"|"saaf", ..., "simulated": true}',
    '  {"type":"ledger", "head":..., "count":...}   <- LEDGER is currently never sent a head at all',
    '  {"type":"state", ...}',
    '',
    'ALSO ADD, because a demo with no controls is a video:',
    '  POST /sim/start /sim/stop /sim/step  and a client message {"type":"sim", "action":...}',
    '  so the operator can pause on a beat, step one frame, and read the numbers.',
    'Expose the current beat label in the state message so the UI can show what is happening and why.',
    '',
    'HARD RULES: a simulated frame may NEVER produce green -- that still needs a signature-verified',
    'webhook. Every simulated message carries simulated: true. The real camera path must be unchanged',
    'and must still work; --sim is additive.',
    'TESTS: extend tests/test_brain_server.py ONLY if you own it -- if another agent may be editing it,',
    'put your tests in tests/test_sim_wire.py instead and say so.',
  ].join('\n') },

  { k: 'enrol', files: 'web/panels/enrol.js + web/panels/enrol.test.mjs', brief: [
    'THE MISSING INTERACTION: a user cannot currently teach the counter anything from the browser.',
    '',
    'Build an ENROLMENT surface as a panel module (registerPanel seam, same shape as web/panels/*.js:',
    'a descriptor {id,title,attach(register),attached} published on globalThis.GAWAAH_PANELS, plus pure',
    'render functions so it unit-tests with no browser).',
    'It must let the operator, with only a mouse:',
    '  - ADD AN SKU: name + price typed in RUPEES, converted to INTEGER PAISE at the boundary and',
    '    REFUSED loudly if it is not exact (214.507 must be rejected, not rounded). Show the paise.',
    '  - see the enrolled SKU list with prices, and remove one',
    '  - ENROL A STICKER by name (sends {"type":"enrol_sticker", name})',
    '  - a CAPTURE BURST action for SAAF',
    'It sends client->brain messages and renders whatever comes back; it never computes money itself.',
    'The price entry is the important part: money crosses a boundary here, so it must be integer paise',
    'with a visible refusal for anything else. Test that specifically -- 214.507, "abc", "", -5, 1e3.',
    'Run: cd ' + ROOT + ' && node web/panels/enrol.test.mjs',
  ].join('\n') },

  { k: 'upload', files: 'tools/upload_app.py + tests/test_upload_app.py', brief: [
    'A file has been started at tools/upload_app.py -- READ IT FIRST, then finish and harden it.',
    '',
    'It serves a page on port 8790 where a user drops in a photograph and sees the REAL pipeline run:',
    'PlaneEngine.detect for the mat lock, then PlacementDetector for objects, with every measurement in',
    'millimetres and every refusal keeping its named reason. It also has a SAMPLE button that generates',
    'a synthetic photo of the mat with objects of KNOWN size, so results are checkable against truth',
    'rather than merely admired.',
    'Verify it actually runs: start it, curl /sample, confirm the JSON reports locked=true and item',
    'measurements within a few mm of the stated truth. Fix whatever is broken.',
    'THEN EXTEND IT so it is genuinely useful:',
    '  - report per-item measured-vs-truth error for the sample, as a table',
    '  - accept a camera photo taken on a phone (any orientation; handle EXIF rotation)',
    '  - if the mat does NOT lock, say precisely why and how many markers were found -- that is the',
    '    most common real failure and the message is the product',
    '  - a /health endpoint and a --port flag',
    'TESTS: sample locks and measures within tolerance; a blank image refuses with a named reason; a',
    'non-image body is refused; the endpoint never returns a 500.',
  ].join('\n') },

  { k: 'demorun', files: 'web/panels/demo.js + web/panels/demo.test.mjs', brief: [
    'THE ONE-CLICK PROOF. A user should be able to press one button and watch the whole product work.',
    '',
    'Build a DEMO surface (panel-module seam, pure render functions, unit-tested with no browser) that:',
    '  - has RUN DEMO / PAUSE / STEP / RESET controls driving the brain sim',
    '    ({"type":"sim", action:"start"|"stop"|"step"|"reset"})',
    '  - shows the SCRIPT as a beat list with the current beat highlighted, so a viewer knows what is',
    '    being demonstrated at each moment ("unknown item -> amber, excluded")',
    '  - shows a live running commentary line explaining WHY the counter is doing what it is doing',
    '  - has a prominent, permanent SIMULATED badge whenever sim frames are driving the session, and',
    '    that badge must be impossible to confuse with the real path',
    '  - shows the three headline numbers as they move: total in paise, amber count, ledger line count',
    'This is the surface a reviewer with six minutes will use, so it must be legible and calm: no',
    'animation for its own sake, tabular numerals, and a clear statement of what is simulated and what',
    'is real. Money still only goes green on a real webhook, and the demo must say so on screen.',
    'Run: cd ' + ROOT + ' && node web/panels/demo.test.mjs',
  ].join('\n') },

  { k: 'liveboxes', files: 'web/panels/scout.js + web/panels/scout.test.mjs', brief: [
    'THE MISSING VISUAL FEEDBACK. Right now the camera draws NOTHING until the mat locks, so the app',
    'looks dead even while it is working correctly. The reference the builder keeps pointing at is a',
    'pothole detector: boxes snap onto objects the instant the camera sees them, with a label.',
    '',
    'Build SCOUT: live boxes on the RAW feed, drawn immediately, with no mat and no model.',
    'OpenCV is ALREADY LOADED in the page (globalThis.cv, 4.11.0, verified) so this is pure classical',
    'CV in the browser -- invariant 3 holds, zero model weights:',
    '  grayscale -> GaussianBlur -> Canny or adaptiveThreshold -> morphologyEx CLOSE ->',
    '  findContours -> filter by area and solidity -> minAreaRect -> draw an ORIENTED box',
    'Track boxes frame to frame with a simple centroid tracker so each keeps a stable id, exactly like',
    'the reference: a persistent "id:36" label rather than numbers flickering every frame.',
    '',
    'LABEL HONESTLY, and this is the whole design problem. The reference prints "potholes 0.73" -- a',
    'class and a confidence. We have NEITHER: no classifier, and no mat, so no millimetres either.',
    'So the label is what we actually know:',
    '   locked:   "id:12  58.4 x 31.2 mm"          <- real measurement, mat present',
    '   unlocked: "id:12  object  (size unknown)"  <- a box we can see, a size we cannot state',
    'and a persistent banner while unlocked: "PREVIEW - boxes only. No mat, so no measurement and',
    'nothing billable." Never invent a class name and never invent a confidence number.',
    '',
    'This is the honest version of the effect the builder wants: something visibly happens the moment',
    'the camera opens, and the app still refuses to claim what it cannot know.',
    '',
    'API: a panel module on the registerPanel seam with an onFrame hook, plus pure exported functions',
    '(boxesFromContours, trackBoxes, labelFor) so scout.test.mjs unit-tests them with no browser.',
    'Also expose a DRAW-ON-RAW mode the shell can switch on for the CORE panel, so the boxes appear',
    'over the live raw feed and not only inside the SCOUT panel.',
    'Run: cd ' + ROOT + ' && node web/panels/scout.test.mjs',
  ].join('\n') },
  { k: 'shell', files: 'web/index.html + web/style.css', brief: [
    'MAKE THE SHELL HOLD THE NEW SURFACES AND STOP LOOKING EMPTY.',
    '',
    'Three new rail entries are arriving from other agents: ENROL (teach the counter an SKU, a sticker,',
    'a burst), DEMO (one-click scripted run) and SCOUT (live boxes on the raw feed, no mat needed). Add #panel-enrol, #panel-demo and #panel-scout containers with rail',
    'entries and status dots exactly like the existing six, keeping the CSS radio-group router so tabs',
    'work with JS disabled. DEMO should be the FIRST thing a new visitor sees -- reorder the rail so it',
    'leads, because a visitor with no camera currently lands on CORE and sees nothing happen.',
    '',
    'Then fix the emptiness. Right now every panel shows a large hatched I-DO-NOT-KNOW block and',
    'nothing else, which reads as broken rather than honest. For each panel add, ABOVE the abstention:',
    '  - one line saying what this capability IS, in plain words',
    '  - a WHAT WOULD MAKE THIS WORK line naming the concrete missing input',
    '    (e.g. "needs a mat in frame", "needs a sticker enrolled -- go to ENROL")',
    '  - where another panel provides that input, make it a link to that panel',
    'The abstention block stays exactly as it is. You are adding orientation around it, not softening',
    'it. A user must always be able to tell the difference between "refusing because it does not know"',
    'and "broken".',
    'Verify by screenshotting in headless Chrome and reporting what each panel shows.',
  ].join('\n') },
]

phase('Alive')
log('7 agents: sim source, sim wiring, enrolment UI, upload tool, one-click demo, LIVE BOXES, shell.')

const out = (await parallel(JOBS.map(j => () =>
  agent(BASE + '\n\n=== YOUR FILES (you own these and ONLY these) ===\n' + j.files +
    '\n\n=== YOUR TASK ===\n' + j.brief +
    '\n\nBuild it, RUN it, iterate until it genuinely works, then report real output.',
    { label: 'alive:' + j.k, phase: 'Alive', schema: S })
))).filter(Boolean)

phase('Verify')
log('Proving in a real browser that every panel can leave I-DO-NOT-KNOW.')

const verdict = await agent(
  BASE +
  '\n\n=== WHAT THE OTHER AGENTS REPORT ===\n' + JSON.stringify(out.map(o => ({
    task: o.task, works: o.what_now_works, limits: o.honest_limits })), null, 1) +
  '\n\n=== YOUR JOB: PROVE IT, IN A REAL BROWSER ===\n' +
  'You own NO source files. Do not edit the product. Verify it.\n' +
  '1. Restart the stack yourself:\n' +
  '     pkill -f brain_server; cd ' + ROOT + ' && set -a; . ./.env; set +a\n' +
  '     RZP_MODE=live ./.venv/bin/python -m gawaah.brain_server --sim --host 0.0.0.0 --port 8787 &\n' +
  '2. Load the page in headless Chrome and DUMP THE DOM. For each of the six capability panels plus\n' +
  '   the new ENROL and DEMO surfaces, report the ACTUAL text shown: is it still an I-DO-NOT-KNOW\n' +
  '   abstention, or is it showing live values?\n' +
  '3. Drive the demo (POST /sim/start or the client message) and re-dump. Report which panels changed\n' +
  '   and to what. Take a screenshot and describe it.\n' +
  '4. Run every suite: pytest, the three JS suites, tools/lint_no_float.py. Report real numbers.\n' +
  '5. Confirm the invariants still hold: no green without a real webhook; simulated content is\n' +
  '   visibly badged; the abstention states are STILL REACHABLE (verify at least one by starting the\n' +
  '   server WITHOUT --sim and confirming the panels go back to I-DO-NOT-KNOW).\n' +
  'Be adversarial. If a panel is faking liveness, or a simulated value is presented as real, or the\n' +
  'green rule was weakened to make the demo prettier, say so plainly and name the file and line.',
  { label: 'verify:browser', phase: 'Verify' }
)

return {
  built: out.map(o => ({ task: o.task, files: o.files_changed, works: o.what_now_works, limits: o.honest_limits })),
  verdict,
}
