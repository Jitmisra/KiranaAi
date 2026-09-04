/* ===========================================================================
   ENGLISH — THE SOURCE OF TRUTH
   ---------------------------------------------------------------------------
   Every string the shell and the till can show, keyed once. Hindi and Bengali
   are OVERLAYS on this table: a key missing from them falls back to the line
   below, so a half-finished translation shows English words rather than an
   empty button. `i18n.test.ts` asserts that neither overlay is missing a key
   and that neither has invented one.

   THE KEYS ARE THE CONTRACT. `nav.<route id>` and `nav.<route id>.sub` are
   named after the route ids in components/shell.tsx, so the sidebar can look
   its own labels up by the id it already has. The rest are `till.*`, `app.*`
   and `lang.*` — the screen that owns them, then what they are.

   TWO CONVENTIONS INSIDE A STRING:

     {name}     a value the screen substitutes. The braces are part of the
                key's contract: a translation that drops one loses a number.
     <b>…</b>   emphasis, and <br> a line the design breaks on purpose. `t()`
                strips the tags and returns plain text (right for a title= or
                an aria-label; a <br> becomes the space it stands for), while
                `rich()` renders them as real <b> and <br> elements. One entry
                serves both, so a translator never has to think about which
                call site uses it.

   PLURALS are two keys, `.one` and `.other`, chosen by `tn()`. That is the
   right shape for all three languages here: English, Hindi and Bengali all
   distinguish exactly one from everything else, and a sentence about "1 thing
   the counter could not name" reads differently enough in Hindi from one about
   four that stitching it out of fragments would not survive translation.

   VOICE: what a shopkeeper would say, in the shorter word. Numbers, refusals
   and limits are stated, never softened — a translated sentence that implies a
   payment settled when it did not is a worse bug than an untranslated one.
   =========================================================================== */

export const en = {
  /* ------------------------------------------------------------ the shell --
     Three tabs and the sidebar under each. Ids match components/shell.tsx. */

  'nav.tab.counter': 'Counter',
  'nav.tab.counter.blurb': 'what leaves the shelf',
  'nav.tab.shop': 'Shop',
  'nav.tab.shop.blurb': 'the customers who are not standing here',
  'nav.tab.books': 'Books',
  'nav.tab.books.blurb': 'derived from the chain, never a second copy',

  'nav.till': 'Till',
  'nav.till.sub': 'bill what is on the counter',
  'nav.waapsi': 'Returns',
  'nav.waapsi.sub': 'a return by camera, refunded by Razorpay',
  'nav.products': 'Products',
  'nav.products.sub': 'teach it what things are',
  'nav.categories': 'Categories',
  'nav.categories.sub': 'where things sit on the shelf',
  'nav.stock': 'Stock',
  'nav.stock.sub': 'what came in, what went out',
  'nav.offers': 'Offers',
  'nav.offers.sub': 'what comes off the price',
  'nav.assistant': 'Ask',
  'nav.assistant.sub': 'say it, and it does it',

  'nav.shop': 'The storefront',
  'nav.shop.sub': 'what a customer sees',
  'nav.orders': 'Orders',
  'nav.orders.sub': 'what people asked you to send',
  'nav.customers': 'Customers',
  'nav.customers.sub': 'who buys, and what they spend',
  'nav.shopitems': 'Your products',
  'nav.shopitems.sub': 'add one, fix a price, put a photo on it',
  'nav.shopprofile': 'Your shop',
  'nav.shopprofile.sub': 'name, address, hours',

  'nav.expiry': 'Expiry',

  'nav.expiry.sub': 'what goes off, and when',

  'nav.weighed': 'By weight',

  'nav.weighed.sub': 'rice, dal, atta from the sack',

  'nav.shelf': 'Shelf',

  'nav.shelf.sub': 'count the front row with the camera',

  'nav.labels': 'Labels',

  'nav.labels.sub': 'print a price for the shelf',

  'nav.loyalty': 'Loyalty',

  'nav.loyalty.sub': 'points on money that settled',

  'nav.khata': 'Khata',

  'nav.khata.sub': 'udhaar on the book, collected by Razorpay',

  'nav.insights': 'Insights',

  'nav.insights.sub': 'what is rising, what is falling',

  'nav.po': 'Reorder',

  'nav.po.sub': 'a purchase order from what is short',

  'nav.gst': 'GST',

  'nav.gst.sub': 'tax-ready records, not a filing',

  'nav.advisor': 'Salaahkaar',

  'nav.advisor.sub': 'talk it through, out loud',

  /* THE MERGED SCREEN. `assistant` (Ask) and `advisor` (Salaahkaar) were two
     sidebar rows doing overlapping things; #/salaahkaar is the one row now, and
     the two old ids redirect to it. Their keys stay: a bookmark, a translation
     table and a test each still name them. */
  'nav.salaahkaar': 'Salaahkaar',
  'nav.salaahkaar.sub': 'ask it anything, out loud or typed',

  'nav.display': 'Customer display',

  'nav.display.sub': 'the screen that faces the other way',

  'nav.today': 'Today',
  'nav.today.sub': 'aaj kitna hua — the day, from the chain',
  'nav.history': 'History',
  'nav.history.sub': 'every bill, and what it excluded',
  'nav.expenses': 'Expenses',
  'nav.expenses.sub': 'kharcha, and the cash drawer',
  'nav.purchases': 'Purchases',
  'nav.purchases.sub': 'what you bought, and the margin',
  'nav.dayclose': 'Close the day',
  'nav.dayclose.sub': 'count the cash, freeze the figures',
  'nav.inventory': 'Inventory',
  'nav.inventory.sub': 'what sells and what sits',
  'nav.settings': 'Settings',
  'nav.settings.sub': 'what this counter is set to do',

  /* `signin` is a RouteId with no sidebar row today. Kept so the table covers
     every route, and so a command palette or a sign-out redirect has a name
     for it in all three languages. */
  'nav.signin': 'Sign in',
  'nav.signin.sub': 'who is standing at this counter',

  /* Landmarks and controls in the chrome itself. */
  'nav.menu': 'Menu',
  'nav.sections': 'Sections',
  'nav.switchSection': 'Switch section',
  'nav.brandline': 'A kirana counter runs on somebody’s word.<br><b>This is the witness.</b>',

  /* --------------------------------------------------------- the top bar --
     WHAT IS LEFT IN THE BAR IS WHAT CANNOT WAIT: the account, the assistant,
     and a customer who is standing in the future waiting for an order.

     `app.taught` and `app.gateway.*` used to live here, for two chips that were
     removed from the bar. Neither fact went with them — the taught count is the
     pill on /#/products and gateway reachability is the Payments section of
     /#/settings, both of which say more than a chip could — so the keys are
     gone rather than kept warm for a bar that will not show them again.

     `app.ask` is the WORD on the trigger; `app.ask.title` is the sentence the
     tooltip says, and it is also all a screen reader gets at the width where
     the word is dropped and only the glyph remains. */

  'app.ask': 'Ask',
  'app.ask.title': 'Ask the counter',
  /* The round button at the bottom-right of every shopkeeper screen. The
     first is what a screen reader says for a button that is a face; the
     second is its tooltip, and the modal's own heading. */
  'app.salaahkaar': 'Salaahkaar',
  'app.salaahkaar.title': 'Ask Salaahkaar anything — say it, or type it',
  'app.salaahkaar.close': 'Close',
  'app.orders.one': '{n} new order',
  'app.orders.other': '{n} new orders',
  'app.orders.title': 'New orders from the storefront',
  'app.opening': 'Opening…',

  /* ---------------------------------------------------------- the picker -- */

  'lang.label': 'Language',
  'lang.choose': 'Choose a language',

  /* =========================================================== THE TILL == */

  'till.head.title': 'The till',
  'till.head.sub':
    'Hold a packet up so its code faces the camera. Every code in view is read at once and '
    + 'priced — the supermarket lane, without the scanner gun.',

  /* ---- the camera gate --------------------------------------------------- */

  'till.cam.off.title': 'The camera is not running',
  'till.cam.off.body':
    'Nothing is uploaded until you start it. Reading codes uploads the whole camera image, so a '
    + 'code is found wherever it sits on the packet. Drag a rectangle to narrow that — everything '
    + 'outside a rectangle you draw is discarded in this browser, before the request.',
  'till.cam.start': 'START CAMERA',
  'till.cam.failed': 'The camera did not start',

  /* ---- the instrument bar over the stage --------------------------------- */

  'till.stage.live': 'LIVE',
  'till.stage.looks': '{n} looks',
  'till.stage.whole': 'WHOLE FRAME',
  'till.stage.cropped': 'CROPPED',

  /* ---- what the loop can see right now ----------------------------------- */

  'till.readout.symbols.one': '{n} SYMBOL',
  'till.readout.symbols.other': '{n} SYMBOLS',
  'till.readout.distinct': '{n} DISTINCT',
  'till.readout.untaught': '{n} NOT TAUGHT',
  'till.readout.cooling': 'ALREADY ON THE BILL · {s}s',
  'till.readout.nothing': 'nothing readable in view',
  'till.readout.cameraOff': 'camera off',

  /* ---- reading the whole counter in one press ---------------------------- */

  'till.sweep.button': 'READ THE WHOLE COUNTER',
  'till.sweep.reading': 'READING THE COUNTER…',
  'till.sweep.title':
    'Lay the shopping out and press once — every product that can be priced goes on the bill',
  'till.sweep.mixed': '{named} priced · {unnamed} I could not name',
  'till.sweep.allPriced': '{named} products, all priced',
  'till.sweep.unnamed.one':
    'There is <b>1</b> thing on this counter that does not match anything taught closely enough '
    + 'to price, so it is not on the bill. Show a printed code, or teach that view of the '
    + 'product. This is not an accusation — it is the counter saying what it cannot see well '
    + 'enough to charge for.',
  'till.sweep.unnamed.other':
    'There are <b>{n}</b> things on this counter that do not match anything taught closely enough '
    + 'to price, so they are not on the bill. Show a printed code, or teach that view of the '
    + 'product. This is not an accusation — it is the counter saying what it cannot see well '
    + 'enough to charge for.',
  'till.sweep.allBody':
    'Every region it found was priced. {byCode} read from a printed code, {byLook} recognised by '
    + 'appearance.',
  'till.sweep.gapNote':
    'Packets closer together than about a finger’s width read as one item — leave a gap between '
    + 'them. Read in {ms} ms.',

  /* ---- the two ways of reading ------------------------------------------- */

  'till.mode.code': 'By code',
  'till.mode.code.title': 'Read every printed barcode or QR in the frame',
  'till.mode.look': 'By look',
  'till.mode.look.title': 'Recognise one product by appearance',
  'till.hint.code':
    'Reading codes: the <b>whole camera image</b> is uploaded, so a code is found wherever it '
    + 'sits on the packet. Drag a rectangle if you want to narrow that to part of the counter.',
  'till.hint.look':
    'Reading by appearance: only the rectangle is uploaded — everything outside it is discarded '
    + 'in this browser, before the request. A look is a guess, so it must hold steady for three '
    + 'frames before it bills.',

  /* ---- the counter's own noises ------------------------------------------ */

  'till.sound.muted': '🔇 MUTED — TAP FOR SOUND',
  'till.sound.on': '🔊 SOUND ON',
  'till.sound.muted.title': 'The counter is silent',
  'till.sound.on.title': 'The counter is chiming',
  'till.sound.test': 'TEST SOUND',
  'till.sound.test.title': 'Named, then I-do-not-know, then already-on-the-bill',
  'till.redraw': 'REDRAW AREA',
  'till.stop': 'STOP',

  /* ---- the bill ----------------------------------------------------------- */

  'till.bill.title': 'The bill',
  'till.bill.clear': 'CLEAR',
  'till.bill.empty.1': 'Nothing on the counter yet.',
  'till.bill.empty.2': 'Hold a packet up so its code faces the camera.',
  'till.bill.total': 'Total',
  'till.bill.toPay': 'To pay',
  'till.bill.oneFewer': 'One fewer {name}',
  'till.bill.oneMore': 'One more {name}',
  'till.bill.drop': 'Take {name} off the bill',
  'till.bill.drop.title': 'Take this off the bill',

  /* ---- the charge button and what it refuses ------------------------------ */

  'till.charge.witnessing': 'WITNESSING THE COUNTER…',
  'till.charge.nothing': 'NOTHING ON THE COUNTER',
  'till.charge.startCamera': 'START THE CAMERA TO CHARGE',
  'till.charge.show.one': 'SHOW IT TO THE CAMERA',
  'till.charge.show.other': 'SHOW THEM TO THE CAMERA',
  'till.charge.pay': 'CHARGE {amount}',
  'till.charge.notInView': 'Not in view: {names}',
  'till.charge.missing.one':
    'The camera cannot see {names} right now. It photographs the counter as evidence for the '
    + 'charge, so it needs to see everything on this bill together <b>once</b> — show it to the '
    + 'lens and the button arms itself and stays armed. You can put it down again.',
  'till.charge.missing.other':
    'The camera cannot see {names} right now. It photographs the counter as evidence for the '
    + 'charge, so it needs to see everything on this bill together <b>once</b> — show them to the '
    + 'lens and the button arms itself and stays armed. You can put them down again.',
  'till.charge.ready':
    'The counter photographed this bill and kept the evidence. You can put everything down — '
    + 'CHARGE is ready.',

  /* ---- ON THE BOOK: the bill closes onto a customer's khata, no colour --- */

  'till.book.action': 'ON THE BOOK',
  'till.book.title': 'Put this bill on the book',
  'till.book.sub':
    'A debt in neutral ink. It is not green, not amber, not red — and it drops only when the '
    + 'gateway\'s signed webhook says money arrived against this household.',
  'till.book.name': 'Whose book',
  'till.book.phone': 'Phone number',
  'till.book.phone.sub': 'One household, one number. Razorpay sends the reminders to it; you send nothing.',
  'till.book.confirm': 'ON THE BOOK {amount}',
  'till.book.cancel': 'Not now',
  'till.book.working': 'WRITING IT ON THE BOOK…',
  'till.book.done': 'ON THE BOOK {amount} · {name} · {phone}',
  'till.book.done.body':
    'Nothing settled and nothing was refused. This household now owes <b>{outstanding}</b>. '
    + 'Press COLLECT on the Khata screen for one payment link; the balance drops only on a '
    + 'signed webhook.',
  'till.book.open': 'Open the khata',
  'till.book.new': 'new household',
  'till.book.proposed': 'Salaahkaar proposes: on the book for <b>{name}</b> ({phone})',
  'till.book.proposed.unknown':
    'Salaahkaar proposes: on the book for <b>{name}</b> — no book by that name yet, so you '
    + 'will be asked for the number.',
  'till.book.accept': 'ACCEPT',
  'till.book.drop': 'DROP',
  'till.book.needsWitness':
    'The counter photographs the bill before it goes on the book, the same evidence a charge needs.',

  'till.refuse.notPhotographed': 'The counter could not be photographed',
  'till.refuse.cannotCharge': 'This counter cannot be charged yet',
  'till.refuse.putBack':
    'CHARGE photographs the counter at the moment you press it. Put everything you are billing '
    + 'back in front of the camera and press again.',
  'till.refuse.disagree': 'The counter sees {seen}, the bill says {bill}',
  'till.refuse.disagree.detail':
    'Only what the camera can see right now is charged. Put the missing packets back in view and '
    + 'press again, or press CLEAR to start the bill over.',

  /* ---- what the counter wrote down --------------------------------------- */

  'till.witness.heading': 'What the counter witnessed',
  'till.witness.headingPay': 'Witnessed on the counter',
  'till.witness.notTaught': 'not taught',

  /* ---- the pay screen ----------------------------------------------------- */

  'till.pay.title': 'Waiting for payment',
  'till.pay.sub':
    'The customer scans this with any UPI app. Nothing here turns green until Razorpay’s own '
    + 'signed webhook says the money arrived.',
  'till.pay.qrTitle': 'Scan to pay',
  'till.pay.scanWithUpi': 'Scan with any UPI app',
  'till.pay.qrAlt': 'Payment QR for {amount}',
  'till.pay.renderNote':
    'This image is a render of the payment link the gateway issued. Nothing here constructs a UPI '
    + 'payload.',
  'till.pay.link': 'link',
  'till.pay.session': 'session',
  'till.pay.cancel': 'CANCEL — back to the counter',
  'till.pay.waiting': 'waiting for the gateway',
  'till.pay.stopped': 'stopped checking — this link is still payable',
  'till.pay.noRecord': 'the money service has no record of this session — cancel and re-charge',
  'till.pay.qrRefused': 'the payment QR could not be produced',
  'till.pay.qrRefused.detail': 'The counter could not reach its own server to find out why.',

  /* ---- nothing is reaching this counter ---------------------------------- */

  'till.inbound.title': 'This counter is not hearing from the gateway',
  'till.inbound.never': 'No callback of any kind has ever reached this counter.',
  'till.inbound.last': 'The last callback to reach this counter arrived <b>{ago}</b>.',
  'till.inbound.none': 'No callback has reached this counter recently.',
  'till.inbound.since': 'Nothing has arrived in the {s} seconds since this link was minted.',
  'till.inbound.mayHavePaid':
    '<b>The customer may well have paid.</b> This screen cannot turn green on that, because the '
    + 'only thing that turns it green is Razorpay’s own signed callback — and that callback is '
    + 'not getting through. Check that the webhook URL in the Razorpay dashboard still points at '
    + 'this counter’s tunnel; a quick tunnel gets a new address every time it restarts.',
  'till.inbound.stillPayable':
    'The link above stays payable either way, and a callback that arrives late will still settle '
    + 'this session. Nothing is lost by waiting — but nothing will change until the path is open.',

  /* ---- the paid moment ---------------------------------------------------- */

  'till.paid.word': 'Paid',
  'till.paid.body':
    'A signature-verified webhook matched this session and this amount. That is the only thing '
    + 'that can produce this screen.',
  'till.return.fromPaid': 'Return an item from this bill',

  /* ---- how this counter decides ------------------------------------------- */

  'till.decides.title': 'How this counter decides',
  'till.decides.code': 'commits a code',
  'till.decides.code.v': 'on its first clean read',
  'till.decides.look': 'commits a look',
  'till.decides.look.v': 'after 3 steady frames',
  'till.decides.forget': 'forgets a packet',
  'till.decides.forget.v': 'after 4 missed frames',
  'till.decides.rate': 'looks per second',
  'till.decides.note':
    'Recognition <b>proposes</b> a price. Only a signature-verified webhook can mark a session '
    + 'paid — this page can refuse a payment, never grant one.',

  /* ------------------------------------------------------- the teach screen --
     `routes/Products.tsx`. These were the last English literals left in a
     translating file: 35 of them, all on the screen where a shopkeeper does
     the hardest thing this program asks — deciding what a packet IS. A till
     that speaks Hindi at the counter and English at the moment of teaching
     is a till that speaks Hindi decoratively. */
  'products.teach.title': 'Teach a product',
  'products.teach.nameEg': 'Parle-G biscuit 100g',
  'products.draw': 'DRAW A BOX AROUND THE PRODUCT',
  'products.captured': 'CAPTURED',
  'products.show': 'Show the packet to the camera',
  'products.show.sub': 'The preview tells you whether the code is legible before you teach it.',
  'products.camera.dead': 'The camera did not start',
  'products.checking': 'CHECKING',
  'products.retake': 'RETAKE',
  'products.stopCamera': 'STOP CAMERA',
  'products.checkingBox': 'Checking the box you drew',
  'products.backToLive': 'BACK TO THE LIVE VIEW',
  'products.catalogue': 'The catalogue',
  'products.reading': 'Reading the catalogue',
  'products.tryAgain': 'TRY AGAIN',
  'products.emptyCatalogue': 'Nothing taught yet',
  'products.codeOnly': 'CODE ONLY',
  'products.noMm': 'NO MM',
  'products.corrected': 'Corrected.',
  'products.viewAdded': 'View added.',
  'products.legend': 'What the pills and the buttons mean',
  'products.bar': 'The bar it has to clear.',
  'products.views': 'VIEWS',
  'products.edit': 'EDIT',
  'products.f.name': 'Name',
  'products.f.price': 'Price in rupees',
  'products.f.code': 'Printed code',
  'products.cancel': 'CANCEL',
  'products.chain.reading': 'Reading the audit chain',
  'products.chain.empty': 'Nothing on the chain yet',
  'products.choosePicture': 'CHOOSE A PICTURE',
  'products.noPhoto':
    '<b>No photograph needed.</b> The code you typed is the identifier this product will answer to. Clear the field to read it from the packet with the camera instead.',
  'products.gate.camera':
    'Teaching from the camera takes <b>eight frames</b> and scores each on glare, blur and focus. The sharpest survivor is what gets stored, and if none survive nothing is stored at all — there is no override, because a gate you can wave through is decoration.',
  'products.gate.upload':
    'A single uploaded file <b>cannot</b> be checked this way: the gate compares frames against each other, and one still has nothing to be compared with. Teach from the camera to get that protection.',
  'products.pill.views.one': '1 VIEW',
  'products.pill.views.other': '{n} VIEWS',
  'products.addView': '+ VIEW',
  'products.adding': 'ADDING…',
  'products.forget': 'FORGET',
  'products.legend.views': 'VIEWS',
  'products.legend.edit': 'EDIT',
  'products.legend.addView':
    '<b>+ VIEW</b> photographs a product you have already taught from another angle and remembers that too. A packet has more than one face and one taught view is one face, so a second and a third are what let the counter recognise it lying on its side or turned round. Turn the packet, press again. Price and name never change.',
  'products.legend.gates':
    'This counter recognises by sight at a cosine of <n>{phi}</n>, and judges a product taught from a plain photograph at the higher bar of <n>{appearance}</n> — one discriminator, its real size in millimetres, is missing from that one. The leader must also be ahead of second place by <n>{theta}</n>, or the counter names both and asks. These are read from this counter, not printed here: a page that remembers a gate is a page that will one day contradict the machine it is a window on.',
  'products.legend.gates.loading':
    'Reading the gates from this counter…',
  'products.legend.gates.none':
    'This counter did not report its gates{why}, so no number is printed here. Settings reads the same figures from the same place.',
  'products.legend.codeOnly':
    '<b>CODE ONLY</b> means this product was taught from its printed number, so nothing about what it looks like was stored and the camera cannot recognise it by sight — show its code, or teach it again from a photograph. <b>VIEWS</b> is how many angles of it the counter has seen; one view recognises the face you photographed and little else.',
  'products.legend.edits':
    '<b>EDIT</b> changes the name, the price and the printed code, and touches nothing else — not the taught views, not the millimetres, not the photograph, and never the SKU id, which is what past bills and orders point at. A price change is written to the shop’s own audit chain with the old value and the new one, so a bill from last week can still be explained.',
  'products.legend.forget':
    'Forgetting a product removes everything that could still price it — the binding, the vectors and the price — so a code that used to name it will name nothing. If only the price or the name is wrong, <b>EDIT</b> it: teaching it again from one fresh photograph throws away every view, every millimetre and the photograph itself to fix two characters, and leaves the product with one face where it had several.',
  'products.mode.code':
    'By code',
  'products.mode.code.t':
    'A barcode or QR — typed, or read from the packet',
  'products.mode.photo':
    'By photo',
  'products.mode.photo.t':
    'A plain photograph, no mat: appearance only',
  'products.mode.mat':
    'On the mat',
  'products.mode.mat.t':
    'The printed TAKHTI mat: adds real millimetres',
  'products.f.sku':
    'SKU id',
  'products.f.name.sub':
    'what the shopkeeper reads on the bill',
  'products.f.price.sub':
    'stored as integer paise; a float is refused, never rounded',
  'products.f.code.label':
    'Barcode or QR number',
  'products.f.code.optional':
    'Barcode or QR number (optional)',
  'products.f.code.sub':
    'type the digits under the bars, or leave blank and let the camera read it',
  'products.f.code.read':
    'read from the packet by the camera — {code}',
  'products.src.file':
    'Upload a file',
  'products.src.camera':
    'Use the camera',
  'products.pic.none':
    'no picture chosen',
  'products.pic.alt':
    'the product to teach',
  'products.teach.go':
    'TEACH THIS PRODUCT',
  'products.teach.busy':
    'TEACHING…',
  'products.fine.code':
    'A code binds this SKU to an identifier. Nothing about its appearance is stored, and nothing about its appearance is needed.',
  'products.fine.photo':
    'A plain photograph teaches appearance only: no millimetres, no size check, and a stricter similarity bar to compensate.',
  'products.fine.mat':
    'The printed TAKHTI mat gives the counter a real scale, so the product is stored with its true footprint in millimetres.',
  'products.close':
    'CLOSE',
  'products.forgetting':
    'FORGETTING…',
  'products.pill.codes.one': 'CODE',
  'products.pill.codes.other': '{n} CODES',
  'products.burst': 'CAPTURING — HOLD STILL · {at} OF {n}',
  'products.lookingForCode': 'LOOKING FOR A CODE…',
  /* The shelf as the storefront sells against it — components/StockOnline.tsx,
     on every Products card and in the Your-products editor. */
  'products.stock.onhand': 'on the shelf',
  'products.stock.notCounted': 'not counted',
  'products.stock.online': 'online',
  'products.stock.noFigure': 'no stock figure — sold with no cap',
  'products.stock.available': '{n} can be sold',
  'products.stock.out': 'OUT OF STOCK online',
  'products.stock.held': '{open} in open orders · {delivered} delivered since the count',
  'products.stock.heldOpen': '{open} in open orders',
  'products.stock.heldDelivered': '{delivered} delivered since the count',
  'products.stock.floorIs': 'keeping back {n}',
  'products.stock.count': 'Count now',
  'products.stock.count.go': 'RECORD',
  'products.stock.count.empty': 'Type how many are on the shelf, in whole packets.',
  'products.stock.floor': 'Keep back',
  'products.stock.floor.go': 'SET',
  'products.stock.floor.same': 'This is already the floor.',
  'products.stock.floor.sub':
    'Keep back: the storefront stops selling this at that many on the shelf, so the counter keeps them. 0 sells the last packet.',
  'products.stock.saving': 'SAVING…',
  'products.stock.noFigures': 'The stock figures could not be read, so nothing is capped online: {why}',
  'products.stock.reserve':
    'An online order reserves its packets the moment it is placed and takes nothing off the count until you pack it. A delivered order stays subtracted until you count the shelf again.',
  'auth.chip.none': 'no sign-in here',
  'auth.chip.none.short': 'no sign-in',
  'auth.chip.create': 'create an account',
  'auth.chip.create.short': 'account',
  'auth.chip.signIn': 'sign in',
  'auth.chip.out': 'not signed in',
  'till.cam.off.lead':
    'Nothing is uploaded until you start it.',
  'till.cam.off.upload':
    '<b>What leaves this machine:</b> reading codes uploads the whole camera image, so a code is found wherever it sits on the packet. Drag a rectangle to narrow that — everything outside a rectangle you draw is discarded in this browser, before the request.',
  'till.redraw.title.on':
    'Put the counter area back to the whole frame',
  'till.redraw.title.off':
    'There is no counter area to redraw until the camera is running.',
  'till.sweep.title.off':
    'Start the camera first — there is nothing to read the counter from.',
  'till.sweep.title.busy':
    'Already reading this counter. One sweep at a time.',

  /* ------------------------------------------------------- books · today --
     The reconciliation panel and the empty day. The DISAGREEMENTS themselves
     are not keyed: `gawaah/daybook.py` writes each one from the figures it
     found, so the sentence is derived and there is no fixed string to
     translate. Only the frame around them is here. */

  'today.recon.title': 'What the till and the gateway do not agree on',
  'today.recon.clear': 'AGREED',
  'today.recon.nothing': 'There is nothing to reconcile yet today',
  'today.recon.nothing.detail':
    'No bill has closed and no webhook has arrived since midnight, so no check has been made. This is not the same as the books agreeing — nothing has been asked of the payment path today, so nothing is known about it.',
  'today.recon.none': 'The till and the gateway agree on today',
  'today.recon.none.detail':
    'Every bill that closed today was asked of the gateway, no webhook was refused, and nothing is recorded settled without one. Money still waiting to settle is listed below — that is not a disagreement, it is a queue.',
  'today.recon.unavailable': 'This counter cannot check itself right now',
  'today.recon.unavailable.detail':
    'The reconciliation did not answer, so nothing is claimed about whether the books agree. The figures above are the day brief’s and are unaffected. What is missing is the check, not the takings.',
  'today.recon.split': 'today, split by what actually happened',
  'today.recon.billed': 'billed',
  'today.recon.settled': 'settled — a verified webhook',
  'today.recon.settled.none': 'nothing today',
  'today.recon.settled.never': 'nothing, ever',
  'today.recon.linksent': 'link sent, not settled',
  'today.recon.nolink': 'closed with no link at all',
  'today.recon.refused': 'the counter refused to charge',
  'today.recon.unwitnessed': 'settled with no webhook line',
  'today.recon.owed': 'still owed',
  'today.recon.channel': 'where it was rung up',
  'today.recon.ch.till': 'the till',
  'today.recon.ch.storefront': 'the storefront',
  'today.recon.ch.unnamed': 'neither — this counter cannot say',
  'today.recon.lifetime': 'everything this counter has ever billed',

  'today.empty.title': 'The day so far',
  'today.empty.head': 'Nothing has been billed today',
  'today.empty.body':
    'No basket has closed since midnight, so there is nothing to total. Figures appear here the moment the first bill closes — none is drawn as a zero in the meantime, because a zero here would look exactly like a day that took nothing.',
  'today.empty.action': 'OPEN THE TILL',
  'today.empty.yesterday.one':
    'Yesterday finished on {amount}, across one bill.',
  'today.empty.yesterday.other':
    'Yesterday finished on {amount}, across {n} bills.',

  /* ------------------------------------------------ your products (Shop) --
     The shopkeeper's own side of the catalogue: add a product with no camera,
     correct a name, change a price, replace a photograph, count the shelf.
     Every sentence that says what this path CANNOT do is here on purpose — a
     shopkeeper who does not know they added the weak kind cannot choose the
     other one, and that has to be true in all three languages. */

  'shopitems.title': 'Your products',
  'shopitems.blurb':
    'Everything this shop sells. Add one without the camera, fix a name or a price, put a photograph on it, and say how many are on the shelf.',

  'shopitems.stat.products': 'products',
  'shopitems.stat.nophoto': 'no photograph',
  'shopitems.stat.nophoto.sub': 'nothing for a customer to look at',
  'shopitems.stat.unseen': 'never photographed',
  'shopitems.stat.unseen.sub': 'the camera cannot name these',
  'shopitems.stat.counted': 'counted',
  'shopitems.stat.counted.sub': 'the shelf has a figure',

  'shopitems.list.title': 'The shelf',
  'shopitems.list.sub': 'tap a product to change it',
  'shopitems.search': 'search a name, an id or a barcode',
  'shopitems.filter.all': 'ALL',
  'shopitems.filter.nophoto': 'NO PHOTO',
  'shopitems.filter.unseen': 'NEVER SEEN',
  'shopitems.load.failed': 'The catalogue could not be read',
  'shopitems.retry': 'TRY AGAIN',
  'shopitems.empty.title': 'Nothing on the shelf yet',
  'shopitems.empty.body':
    'Add a product here to price it, or photograph one on the Products screen so the camera can name it too.',
  'shopitems.nomatch.title': 'Nothing matches',
  'shopitems.nomatch.body':
    'No product here matches what you typed. Clear the box to see all of them.',
  'shopitems.notcounted': 'not counted',
  'shopitems.onhand': '{n} on the shelf',
  'shopitems.nophoto': 'no photograph',

  'shopitems.how.mat': 'ON THE MAT',
  'shopitems.how.look': 'BY LOOK',
  'shopitems.how.code': 'BY CODE',
  'shopitems.how.typed': 'TYPED IN',

  'shopitems.add.title': 'Add a product',
  'shopitems.add.sub': 'no camera, no mat — a name and a price',
  'shopitems.add.open': 'ADD A PRODUCT',
  'shopitems.add.lead':
    'A sack of rice going on the shelf at eleven at night should not need a photograph before the shop can sell it. Type a name and a price and it is on the storefront.',
  'shopitems.add.go': 'PUT IT ON THE SHELF',
  'shopitems.add.fine':
    'Added this way, the counter learns a name and a price and nothing about what the product looks like — the camera cannot recognise it. Photograph it on the Products screen when there is time.',
  'shopitems.add.done': '{name} is on the shelf',
  'shopitems.add.derived': 'the id was made from the name',
  'shopitems.add.filed': 'Filed under {name}.',
  'shopitems.add.notfiled': 'It was added, but not filed under a category: {why}',
  'shopitems.add.counted': 'Counted: {n} on the shelf.',
  'shopitems.add.notcounted': 'It was added, but the count was not recorded: {why}',
  'shopitems.cancel': 'CANCEL',

  'shopitems.f.name': 'Name',
  'shopitems.f.name.sub': 'what you read on the bill and a customer reads on the storefront',
  'shopitems.f.name.eg': 'Basmati rice 5kg',
  'shopitems.f.price': 'Price',
  'shopitems.f.price.sub': 'rupees, as you would write them: 12 or 12.50',
  'shopitems.f.category': 'Where it sits',
  'shopitems.f.category.sub': 'the shelf a customer would look on',
  'shopitems.f.category.none': 'not filed anywhere',
  'shopitems.f.stock': 'On the shelf now',
  'shopitems.f.stock.sub': 'whole packets, if you have counted them',
  'shopitems.f.code': 'Barcode',
  'shopitems.f.code.sub': 'the number under the bars, if the packet has one',
  'shopitems.f.code.edit': 'emptying this box unbinds every code on this product',
  'shopitems.f.id': 'Its id',
  'shopitems.f.id.sub':
    'left empty, one is made from the name. It can never be changed afterwards.',
  'shopitems.f.id.auto': 'made from the name',
  'shopitems.f.photo': 'Photograph',
  'shopitems.f.photo.sub': 'shown on the storefront. It teaches the camera nothing.',
  'shopitems.f.photo.alt': 'the picture chosen for this product',
  'shopitems.f.count': 'the count',

  'shopitems.photo.toobig':
    'That picture is about {n} MB and the limit is 8 MB. Take it again at a smaller size.',
  'shopitems.photo.unreadable': 'That file could not be read as a picture.',
  'shopitems.photo.stored': 'The picture is stored.',
  'shopitems.photo.removed': 'The picture has been removed.',
  'shopitems.photo.choose': 'choose a picture',
  'shopitems.photo.remove': 'REMOVE THE PICTURE',
  'shopitems.photo.fine':
    'A photograph is not part of what the counter compares against, so changing one changes no decision the till makes.',

  'shopitems.g.basics': 'Name, price and barcode',
  'shopitems.g.photo': 'Photograph',
  'shopitems.g.category': 'Where it sits',
  'shopitems.g.stock': 'How many on the shelf',

  'shopitems.edit.permanent':
    'The id never changes — every bill already printed points at it.',
  'shopitems.edit.save': 'SAVE',
  'shopitems.edit.nochange': 'Nothing was different, so nothing was written.',
  'shopitems.edit.saved':
    'Saved: {what}. It is on the shop’s own chain, with the old value and the new one.',
  'shopitems.edit.unbound': 'These codes no longer price this product: {codes}.',
  'shopitems.history.show': 'WHAT THIS PRICE HAS BEEN',
  'shopitems.history.none': 'Nothing has been changed on this product yet.',

  'shopitems.cat.file': 'FILE IT',
  'shopitems.cat.filed': 'Filed under {name}.',
  'shopitems.cat.cleared': 'Taken off the shelf it was on.',
  'shopitems.cat.none':
    'No categories yet. Make one on the Categories screen and this can be filed under it.',

  'shopitems.stock.record': 'RECORD THE COUNT',
  'shopitems.stock.fine':
    'This is a re-count: it replaces the figure and supersedes the movements before it. Whole packets only.',
  'shopitems.close': 'CLOSE',
  'till.pay.qrRefused.stillPayable':
    'The payment link was still minted and is still payable — this counter could not draw it, that is all. Press CANCEL to go back to the counter and charge again.',
  'till.pay.qrRefused.notGateway':
    'That address is not the gateway’s, so nothing can be paid through it. If this counter is running the simulator (RZP_MODE=sim) that is expected: simulated links are made unpayable on purpose. Set RZP_MODE=live for a real link. Press CANCEL to go back to the counter.',

  /* ---- Salaahkaar at the counter ----------------------------------------
     The "Say the order" card, with the advisor's presenter in it. She puts
     lines on the bill as PROPOSED and answers questions out loud; a person
     accepts, and CHARGE stays the shopkeeper's button. */

  'till.sk.title': 'Salaahkaar',
  'till.sk.sub': 'say the order, or ask a price',
  'till.sk.state.idle': 'AT THE COUNTER',
  'till.sk.state.listening': 'LISTENING',
  'till.sk.state.thinking': 'THINKING',
  'till.sk.state.speaking': 'SPEAKING',
  'till.sk.state.voicing': 'FETCHING HER VOICE',
  'till.sk.listen': '🎤 LISTEN',
  'till.sk.stop': 'STOP LISTENING',
  'till.sk.placeholder': 'Type it — "do Maggi aur ek Parle-G" — or ask: "Parle-G ka daam?"',
  'till.sk.send': 'TELL HER',
  'till.sk.langs': 'Language',
  'till.sk.idle':
    'Press LISTEN and say the order, or type it. A count before a product goes on the bill as a '
    + 'proposal; a question is answered out loud.',
  'till.sk.listening': 'Listening. Say the order — "do Maggi aur ek Parle-G" — or ask a price.',
  'till.sk.heard': 'Heard',
  'till.sk.typed': 'Typed',
  'till.sk.route.order': 'ORDER',
  'till.sk.route.advice': 'QUESTION',
  'till.sk.route.order.v': 'proposed on the bill, for you to accept',
  'till.sk.route.advice.v': 'answered out loud; the bill is untouched',
  'till.sk.route.refused.v': 'refused by name; nothing on the bill',
  'till.sk.why.shop_word': 'a price or shop word',
  'till.sk.why.question_word': 'a question word',
  'till.sk.why.nothing': 'no product in it',
  'till.sk.why.add_verb': 'an add verb',
  'till.sk.why.weight': 'a weight before a product',
  'till.sk.why.count': 'a count before a product',
  'till.sk.why.several': 'two or more products, no question',
  'till.sk.why.one_bare': 'one product name alone',
  'till.sk.reread.as_question': 'She read it as a question, not an order — nothing was put on the bill.',
  'till.sk.reread.as_order': 'The call refused it as an order, so she put it to the till instead.',
  'till.sk.put.one':
    'Put on the bill as <b>PROPOSED</b>: 1 line, {total}. Nothing is billed until you accept it there.',
  'till.sk.put.other':
    'Put on the bill as <b>PROPOSED</b>: {n} lines, {total}. Nothing is billed until you accept them there.',
  'till.sk.check': 'Check this',
  'till.sk.answer': 'Her answer',
  'till.sk.saying': 'Saying',
  'till.sk.refused': 'She could not do that',
  'till.sk.byVoice': 'in her own voice',
  'till.sk.byBrowser': 'in this browser’s voice',
  'till.sk.voiceRefused': 'Her voice could not be fetched, so this browser read it: {why}',
  'till.sk.muted': 'The counter is muted, so she is shown and not heard.',
  'till.sk.noMic': 'This browser cannot listen',
  'till.sk.noMic.hint': 'Type the order instead — everything else here works without a microphone.',
  'till.sk.micStopped': 'The microphone stopped',
  'till.sk.disclose':
    'The browser transcribes speech with its own service, so the <b>audio leaves this machine</b>. '
    + 'Her spoken reply is fetched from the till’s voice service one sentence at a time (or read by '
    + 'this browser when that is off). The counter image, the catalogue and the prices never leave.',
  'till.sk.never':
    'She proposes and you accept. CHARGE is your button — nothing said or typed here reaches the '
    + 'money service.',

  /* ---- proposed lines on the bill ---------------------------------------- */

  'till.bill.proposed.pill': 'PROPOSED',
  'till.bill.proposed.count.one': '{n} line waiting for you',
  'till.bill.proposed.count.other': '{n} lines waiting for you',
  'till.bill.proposed.acceptAll': 'ACCEPT ALL',
  'till.bill.proposed.dropAll': 'DROP ALL',
  'till.bill.proposed.accept': 'ACCEPT',
  'till.bill.proposed.drop': 'Take {name} off the proposals',
  'till.bill.proposed.heard': 'heard “{heard}”',
  'till.bill.proposed.respelt': 'heard “{heard}” — spelt in latin letters by the counter to find this',
  'till.bill.proposed.weighed': 'weighed: {weight}',
  'till.bill.proposed.onBill':
    'Already on the bill as packets. Take those off before accepting a weight of it.',
  'till.bill.proposed.notCounted': '+ {amount} proposed, not in the total',
  'till.bill.proposed.hint':
    'Amber is an abstention: Salaahkaar put these here and nobody has agreed to them yet. ACCEPT '
    + 'moves a line into the bill; the camera still has to see it before CHARGE.',
  'till.bill.held.title':
    'Salaahkaar held lines for this till',
  'till.bill.held.arrived':
    '{n} line(s) she held elsewhere are on the bill as PROPOSED — accept or drop them below.',
  'till.bill.held.moved':
    'Price changed since they were proposed — {list}. The bill uses today’s price.',
  'till.bill.held.gone':
    'Left off, no longer in the catalogue: {list}.',
  'till.bill.held.noCatalogue':
    'She held {n} line(s) for this till, but the catalogue could not be read to price them. They stay held.',
  'till.bill.held.heard':
    'held by Salaahkaar',
  'till.bill.held.repriced':
    'held by Salaahkaar · re-priced today',
  'till.bill.held.ok':
    'OK',
} as const;

/** Every key that exists. A `t()` call with anything else does not compile. */
export type StringKey = keyof typeof en;

/**
 * A translation of the table above.
 *
 * Deliberately PARTIAL. A key that has not been translated yet must fall back
 * to English rather than fail the build for whoever added it — the fallback is
 * the design, and `i18n.test.ts` is what keeps the gaps from being permanent.
 */
export type Table = Readonly<Partial<Record<StringKey, string>>>;