import type { Table } from './en';

/* ===========================================================================
   বাংলা — the same counter, in Bengali
   ---------------------------------------------------------------------------
   Shop Bengali, not literary Bengali: খদ্দের rather than ক্রেতা, দাম rather
   than মূল্য, মজুত rather than পণ্য-তালিকা, বাকি for credit. The loanwords a
   Bengali shopkeeper actually says — স্টক, অফার, অর্ডার, ক্যাশ, সেটিং, বিল —
   stay, spelled in Bengali script.

   REGISTER: আপনি throughout. A till is somebody's own machine, but the person
   standing at it is the shopkeeper, and Bengali UI that addresses them as তুমি
   reads as a stranger being familiar. The verbs are therefore করুন / দেখুন /
   ধরুন, and they stay short enough for a button.

   The same two rules as the Hindi file: every {placeholder} survives, and a
   refusal stays a refusal — where the counter says it cannot charge, the
   Bengali says so in one plain sentence and does not soften it into a request.
   =========================================================================== */

export const bn: Table = {
  /* ------------------------------------------------------------ the shell -- */

  'nav.tab.counter': 'কাউন্টার',
  'nav.tab.counter.blurb': 'যা তাক থেকে বেরিয়ে যায়',
  'nav.tab.shop': 'দোকান',
  'nav.tab.shop.blurb': 'যে খদ্দেররা সামনে দাঁড়িয়ে নেই',
  'nav.tab.books': 'খাতা',
  'nav.tab.books.blurb': 'চেইন থেকে তৈরি, আলাদা কোনও কপি নয়',

  'nav.till': 'ক্যাশ',
  'nav.till.sub': 'কাউন্টারে যা আছে তার বিল',
  'nav.waapsi': 'ফেরত',
  'nav.waapsi.sub': 'ক্যামেরায় ফেরত, Razorpay-তে রিফান্ড',
  'nav.products': 'জিনিসপত্র',
  'nav.products.sub': 'শিখিয়ে দিন কোনটা কী জিনিস',
  'nav.categories': 'ধরন',
  'nav.categories.sub': 'তাকের কোথায় কী থাকে',
  'nav.stock': 'স্টক',
  'nav.stock.sub': 'কী এল, কী গেল',
  'nav.offers': 'অফার',
  'nav.offers.sub': 'দাম থেকে কতটা ছাড়',
  'nav.assistant': 'জিজ্ঞেস করুন',
  'nav.assistant.sub': 'বলুন, হয়ে যাবে',

  'nav.shop': 'অনলাইন দোকান',
  'nav.shop.sub': 'খদ্দের কী দেখে',
  'nav.orders': 'অর্ডার',
  'nav.orders.sub': 'লোকে যা পাঠাতে বলেছে',
  'nav.customers': 'খদ্দের',
  'nav.customers.sub': 'কে কেনে, কত খরচ করে',
  'nav.shopitems': 'আপনার জিনিস',
  'nav.shopitems.sub': 'নতুন যোগ করুন, দাম ঠিক করুন, ছবি দিন',
  'nav.shopprofile': 'আপনার দোকান',
  'nav.shopprofile.sub': 'নাম, ঠিকানা, খোলার সময়',

  'nav.expiry': 'মেয়াদ',

  'nav.expiry.sub': 'কী কখন নষ্ট হবে',

  'nav.weighed': 'ওজনে',

  'nav.weighed.sub': 'চাল, ডাল, আটা বস্তা থেকে',

  'nav.shelf': 'তাক',

  'nav.shelf.sub': 'ক্যামেরায় সামনের সারি গোনো',

  'nav.labels': 'লেবেল',

  'nav.labels.sub': 'তাকের জন্য দাম ছাপাও',

  'nav.khata': 'খাতা',
  'nav.khata.sub': 'খাতায় ধার, আদায় Razorpay-র মাধ্যমে',
  'nav.loyalty': 'পয়েন্ট',

  'nav.loyalty.sub': 'যে টাকা এসেছে, তাতে পয়েন্ট',

  'nav.insights': 'হিসাব',

  'nav.insights.sub': 'কী বাড়ছে, কী কমছে',

  'nav.po': 'অর্ডার',

  'nav.po.sub': 'যা কম, তার অর্ডার',

  'nav.gst': 'জিএসটি',

  'nav.gst.sub': 'করের হিসাব, ফাইলিং নয়',

  'nav.advisor': 'পরামর্শদাতা',

  'nav.advisor.sub': 'বলে জিজ্ঞেস করো',

  'nav.salaahkaar': 'সালাহকার',
  'nav.salaahkaar.sub': 'যা খুশি জিজ্ঞেস করুন — বলে, বা লিখে',

  'nav.display': 'ক্রেতার পর্দা',

  'nav.display.sub': 'যে পর্দা ক্রেতার দিকে',

  'nav.today': 'আজ',
  'nav.today.sub': 'আজ কত হল — চেইন থেকে বার করা',
  'nav.history': 'পুরনো বিল',
  'nav.history.sub': 'প্রতিটি বিল, আর তাতে কী বাদ গেল',
  'nav.expenses': 'খরচ',
  'nav.expenses.sub': 'খরচ, আর ক্যাশ বাক্স',
  'nav.purchases': 'কেনাকাটা',
  'nav.purchases.sub': 'কী কিনলেন, আর কত থাকল',
  'nav.dayclose': 'দিন বন্ধ করুন',
  'nav.dayclose.sub': 'ক্যাশ গুনুন, হিসেব পাকা করুন',
  'nav.inventory': 'মজুত',
  'nav.inventory.sub': 'কী বিক্রি হয়, কী পড়ে থাকে',
  'nav.settings': 'সেটিং',
  'nav.settings.sub': 'এই কাউন্টারকে কী করতে বলা আছে',

  'nav.signin': 'সাইন ইন',
  'nav.signin.sub': 'কাউন্টারে কে দাঁড়িয়ে আছেন',

  'nav.menu': 'মেনু',
  'nav.sections': 'ভাগ',
  'nav.switchSection': 'ভাগ বদলান',
  'nav.brandline': 'মুদির দোকান চলে কারও কথার উপর।<br><b>এটাই তার সাক্ষী।</b>',

  /* --------------------------------------------------------- the top bar -- */

  'app.ask': 'জিজ্ঞাসা',
  'app.ask.title': 'কাউন্টারকে জিজ্ঞাসা করুন',
  'app.salaahkaar': 'সালাহকার',
  'app.salaahkaar.title': 'সালাহকারকে যা খুশি জিজ্ঞেস করুন — বলে, বা লিখে',
  'app.salaahkaar.close': 'বন্ধ করুন',
  'app.orders.one': '{n}টা নতুন অর্ডার',
  'app.orders.other': '{n}টা নতুন অর্ডার',
  'app.orders.title': 'দোকানের পাতা থেকে আসা নতুন অর্ডার',
  'app.opening': 'খুলছে…',

  /* ---------------------------------------------------------- the picker -- */

  'lang.label': 'ভাষা',
  'lang.choose': 'ভাষা বাছুন',

  /* =========================================================== THE TILL == */

  'till.head.title': 'ক্যাশ কাউন্টার',
  'till.head.sub':
    'প্যাকেটটা এমনভাবে ধরুন যাতে কোডটা ক্যামেরার দিকে থাকে। সামনে যত কোড আছে সব একসঙ্গে পড়া হয় '
    + 'আর দাম বসে যায় — স্ক্যানার গান ছাড়াই সুপারমার্কেটের লাইন।',

  /* ---- the camera gate --------------------------------------------------- */

  'till.cam.off.title': 'ক্যামেরা চালু নেই',
  'till.cam.off.body':
    'আপনি চালু না করা পর্যন্ত কিছুই উপরে পাঠানো হয় না। কোড পড়ার সময় গোটা ক্যামেরার ছবিটাই যায়, '
    + 'যাতে কোড প্যাকেটের যেখানেই থাকুক ধরা পড়ে। ছোট করতে চাইলে একটা চৌকো টেনে নিন — আপনার আঁকা '
    + 'চৌকোর বাইরের সবটাই এই ব্রাউজারেই, পাঠানোর আগেই, বাদ দেওয়া হয়।',
  'till.cam.start': 'ক্যামেরা চালু করুন',
  'till.cam.failed': 'ক্যামেরা চালু হল না',

  /* ---- the instrument bar over the stage --------------------------------- */

  'till.stage.live': 'লাইভ',
  'till.stage.looks': '{n} বার দেখল',
  'till.stage.whole': 'গোটা ছবি',
  'till.stage.cropped': 'কাটা অংশ',

  /* ---- what the loop can see right now ----------------------------------- */

  'till.readout.symbols.one': '{n} কোড',
  'till.readout.symbols.other': '{n} কোড',
  'till.readout.distinct': '{n} আলাদা',
  'till.readout.untaught': '{n} শেখানো নেই',
  'till.readout.cooling': 'বিলে উঠে গেছে · {s} সেকেন্ড',
  'till.readout.nothing': 'সামনে পড়ার মতো কিছু নেই',
  'till.readout.cameraOff': 'ক্যামেরা বন্ধ',

  /* ---- reading the whole counter in one press ---------------------------- */

  'till.sweep.button': 'গোটা কাউন্টার পড়ুন',
  'till.sweep.reading': 'কাউন্টার পড়ছে…',
  'till.sweep.title':
    'সব জিনিস রেখে একবার চাপুন — যার যার দাম বসতে পারে, সব বিলে চলে যাবে',
  'till.sweep.mixed': '{named}-এর দাম বসল · {unnamed} চিনতে পারিনি',
  'till.sweep.allPriced': '{named}টা জিনিস, সবেরই দাম বসেছে',
  'till.sweep.unnamed.one':
    'কাউন্টারে <b>একটা</b> জিনিস আছে যা শেখানো কোনও জিনিসের সঙ্গে দাম বসানোর মতো করে মেলে না, তাই '
    + 'সেটা বিলে নেই। হয় ছাপা কোড দেখান, নয়তো জিনিসটার এই দিকটা শিখিয়ে দিন। এটা দোষারোপ নয় — '
    + 'কাউন্টার বলছে, কোনটা সে এত স্পষ্ট দেখতে পাচ্ছে না যে তার দাম নিতে পারে।',
  'till.sweep.unnamed.other':
    'কাউন্টারে <b>{n}</b>টা জিনিস আছে যা শেখানো কোনও জিনিসের সঙ্গে দাম বসানোর মতো করে মেলে না, তাই '
    + 'সেগুলো বিলে নেই। হয় ছাপা কোড দেখান, নয়তো জিনিসগুলোর এই দিকটা শিখিয়ে দিন। এটা দোষারোপ নয় — '
    + 'কাউন্টার বলছে, কোনগুলো সে এত স্পষ্ট দেখতে পাচ্ছে না যে তার দাম নিতে পারে।',
  'till.sweep.allBody':
    'যতগুলো জায়গা সে পেয়েছে, সবের দাম বসেছে। {byCode} ছাপা কোড থেকে পড়া, {byLook} দেখে চেনা।',
  'till.sweep.gapNote':
    'এক আঙুলের চেয়ে কম ফাঁকে রাখা প্যাকেট একটাই জিনিস হিসেবে পড়ে — মাঝে ফাঁক রাখুন। '
    + '{ms} মিলিসেকেন্ডে পড়া।',

  /* ---- the two ways of reading ------------------------------------------- */

  'till.mode.code': 'কোড দেখে',
  'till.mode.code.title': 'সামনের প্রতিটি ছাপা বারকোড বা QR পড়ুন',
  'till.mode.look': 'চেহারা দেখে',
  'till.mode.look.title': 'একটা জিনিসকে তার চেহারা দেখে চিনুন',
  'till.hint.code':
    'কোড পড়ার সময় <b>গোটা ক্যামেরার ছবি</b> পাঠানো হয়, যাতে কোড প্যাকেটের যেখানেই থাকুক ধরা পড়ে। '
    + 'কাউন্টারের একটা অংশে সীমিত রাখতে চাইলে একটা চৌকো টেনে নিন।',
  'till.hint.look':
    'চেহারা দেখে পড়ার সময় কেবল চৌকোটুকুই পাঠানো হয় — তার বাইরের সবটাই এই ব্রাউজারেই, পাঠানোর '
    + 'আগেই বাদ দেওয়া হয়। দেখে চেনা একটা অনুমান, তাই বিলে ওঠার আগে সেটাকে তিনটে ফ্রেম ধরে স্থির '
    + 'থাকতে হয়।',

  /* ---- the counter's own noises ------------------------------------------ */

  'till.sound.muted': '🔇 আওয়াজ বন্ধ — চাপলে চালু',
  'till.sound.on': '🔊 আওয়াজ চালু',
  'till.sound.muted.title': 'কাউন্টার চুপ করে আছে',
  'till.sound.on.title': 'কাউন্টার আওয়াজ করছে',
  'till.sound.test': 'আওয়াজ পরখ করুন',
  'till.sound.test.title': 'চিনল, তারপর জানি-না, তারপর আগে-থেকেই-বিলে',
  'till.redraw': 'জায়গাটা আবার আঁকুন',
  'till.stop': 'বন্ধ করুন',

  /* ---- the bill ----------------------------------------------------------- */

  'till.bill.title': 'বিল',
  'till.bill.clear': 'মুছুন',
  'till.bill.empty.1': 'কাউন্টারে এখনও কিছু নেই।',
  'till.bill.empty.2': 'প্যাকেটটা এমনভাবে ধরুন যাতে কোডটা ক্যামেরার দিকে থাকে।',
  'till.bill.total': 'মোট',
  'till.bill.toPay': 'দিতে হবে',
  'till.bill.oneFewer': '{name} একটা কম',
  'till.bill.oneMore': '{name} আরও একটা',
  'till.bill.drop': '{name} বিল থেকে বাদ দিন',
  'till.bill.drop.title': 'এটা বিল থেকে বাদ দিন',

  /* ---- the charge button and what it refuses ------------------------------ */

  'till.charge.witnessing': 'কাউন্টারের সাক্ষী নেওয়া হচ্ছে…',
  'till.charge.nothing': 'কাউন্টারে কিছু নেই',
  'till.charge.startCamera': 'টাকা নিতে ক্যামেরা চালু করুন',
  'till.charge.show.one': 'এটা ক্যামেরাকে দেখান',
  'till.charge.show.other': 'এগুলো ক্যামেরাকে দেখান',
  'till.charge.pay': '{amount} নিন',

  'till.book.action': 'খাতায় লেখো',
  'till.book.title': 'এই বিল খাতায় লেখো',
  'till.book.sub':
    'এটা ধার, কোনও রঙ ছাড়া — সবুজ নয়, হলুদ নয়, লাল নয়। এটা তখনই কমবে যখন গেটওয়ের সই করা '
    + 'webhook বলবে এই বাড়ি থেকে টাকা এসেছে।',
  'till.book.name': 'কার খাতা',
  'till.book.phone': 'ফোন নম্বর',
  'till.book.phone.sub': 'এক বাড়ি, এক নম্বর। মনে করাবে Razorpay; আপনি কিছু পাঠান না।',
  'till.book.confirm': 'খাতায় {amount}',
  'till.book.cancel': 'এখন নয়',
  'till.book.working': 'খাতায় লেখা হচ্ছে…',
  'till.book.done': 'খাতায় {amount} · {name} · {phone}',
  'till.book.done.body':
    'কিছু আসেনি, কিছু ফেরানোও হয়নি। এই বাড়ির এখন <b>{outstanding}</b> বাকি। খাতা স্ক্রিনে '
    + 'COLLECT চাপুন, একটা লিংক হবে; বাকি তখনই কমবে যখন সই করা webhook আসবে।',
  'till.book.open': 'খাতা খুলুন',
  'till.book.new': 'নতুন বাড়ি',
  'till.book.proposed': 'সলাহকার বলছে: <b>{name}</b> ({phone})-র খাতায়',
  'till.book.proposed.unknown':
    'সলাহকার বলছে: <b>{name}</b>-র খাতায় — এই নামে খাতা এখনও নেই, নম্বর চাওয়া হবে।',
  'till.book.accept': 'হ্যাঁ',
  'till.book.drop': 'বাদ',
  'till.book.needsWitness':
    'খাতায় লেখার আগে কাউন্টার বিলের ছবি তোলে — টাকা নেওয়ার জন্য যে প্রমাণ লাগে, সেটাই।',
  'till.charge.notInView': 'সামনে নেই: {names}',
  'till.charge.missing.one':
    'ক্যামেরা এখন {names} দেখতে পাচ্ছে না। টাকা নেওয়ার প্রমাণ হিসেবে সে কাউন্টারের ছবি তোলে, তাই '
    + 'এই বিলের জিনিসটা তাকে একসঙ্গে <b>একবার</b> দেখতে হবে — এটা লেন্সের সামনে ধরুন, বোতাম তৈরি '
    + 'হয়ে যাবে আর তৈরিই থাকবে। তারপর নামিয়ে রাখতে পারেন।',
  'till.charge.missing.other':
    'ক্যামেরা এখন {names} দেখতে পাচ্ছে না। টাকা নেওয়ার প্রমাণ হিসেবে সে কাউন্টারের ছবি তোলে, তাই '
    + 'এই বিলের সব জিনিস তাকে একসঙ্গে <b>একবার</b> দেখতে হবে — এগুলো লেন্সের সামনে ধরুন, বোতাম '
    + 'তৈরি হয়ে যাবে আর তৈরিই থাকবে। তারপর নামিয়ে রাখতে পারেন।',
  'till.charge.ready':
    'কাউন্টার এই বিলের ছবি তুলে প্রমাণ রেখে দিয়েছে। সব নামিয়ে রাখতে পারেন — টাকা নেওয়ার বোতাম তৈরি।',

  'till.refuse.notPhotographed': 'কাউন্টারের ছবি তোলা গেল না',
  'till.refuse.cannotCharge': 'এখনই এই কাউন্টার থেকে টাকা নেওয়া যাবে না',
  'till.refuse.putBack':
    'বোতাম চাপার মুহূর্তেই কাউন্টারের ছবি তোলা হয়। যেগুলোর বিল করছেন সেগুলো আবার ক্যামেরার সামনে '
    + 'রেখে ফের চাপুন।',
  'till.refuse.disagree': 'কাউন্টার দেখছে {seen}, বিল বলছে {bill}',
  'till.refuse.disagree.detail':
    'ক্যামেরা এখন যা দেখতে পাচ্ছে কেবল তারই টাকা নেওয়া হয়। বাদ পড়া প্যাকেটগুলো আবার সামনে রেখে '
    + 'ফের চাপুন, নয়তো নতুন করে বিল শুরু করতে মুছুন চাপুন।',

  /* ---- what the counter wrote down --------------------------------------- */

  'till.witness.heading': 'কাউন্টার কী দেখল',
  'till.witness.headingPay': 'কাউন্টারে যা দেখা গেল',
  'till.witness.notTaught': 'শেখানো নেই',

  /* ---- the pay screen ----------------------------------------------------- */

  'till.pay.title': 'টাকার অপেক্ষা',
  'till.pay.sub':
    'খদ্দের যেকোনও UPI অ্যাপ দিয়ে এটা স্ক্যান করবেন। Razorpay-র নিজের সই করা ওয়েবহুক টাকা আসার '
    + 'কথা না বলা পর্যন্ত এখানে কিছুই সবুজ হয় না।',
  'till.pay.qrTitle': 'টাকা দিতে স্ক্যান করুন',
  'till.pay.scanWithUpi': 'যেকোনও UPI অ্যাপ দিয়ে স্ক্যান করুন',
  'till.pay.qrAlt': '{amount}-এর পেমেন্ট QR',
  'till.pay.renderNote':
    'এই ছবিটা গেটওয়ের দেওয়া পেমেন্ট লিঙ্কেরই ছাপ। এখানে কোনও UPI পেলোড বানানো হয় না।',
  'till.pay.link': 'লিঙ্ক',
  'till.pay.session': 'সেশন',
  'till.pay.cancel': 'থাক — কাউন্টারে ফিরুন',
  'till.pay.waiting': 'গেটওয়ের অপেক্ষা',
  'till.pay.stopped': 'দেখা বন্ধ করলাম — লিঙ্কটা এখনও চালু আছে',
  'till.pay.noRecord': 'টাকার সার্ভিসে এই সেশনের কোনও রেকর্ড নেই — বাতিল করে আবার চান',
  'till.pay.qrRefused': 'পেমেন্ট QR তৈরি করা গেল না',
  'till.pay.qrRefused.detail': 'কাউন্টার নিজের সার্ভারেই পৌঁছতে পারল না যে কারণটা জানবে।',

  /* ---- nothing is reaching this counter ---------------------------------- */

  'till.inbound.title': 'এই কাউন্টারে গেটওয়ের কোনও খবর আসছে না',
  'till.inbound.never': 'এই কাউন্টারে আজ পর্যন্ত কোনও রকম কলব্যাক পৌঁছয়নি।',
  'till.inbound.last': 'এই কাউন্টারে পৌঁছনো শেষ কলব্যাক এসেছিল <b>{ago}</b>।',
  'till.inbound.none': 'সম্প্রতি এই কাউন্টারে কোনও কলব্যাক পৌঁছয়নি।',
  'till.inbound.since': 'এই লিঙ্ক তৈরি হওয়ার পর {s} সেকেন্ডে কিছুই আসেনি।',
  'till.inbound.mayHavePaid':
    '<b>খদ্দের হয়তো টাকা দিয়েও দিয়েছেন।</b> শুধু তাতে এই স্ক্রিন সবুজ হতে পারে না, কারণ একে সবুজ '
    + 'করে কেবল Razorpay-র নিজের সই করা কলব্যাক — আর সেই কলব্যাক এখানে পৌঁছচ্ছে না। Razorpay '
    + 'ড্যাশবোর্ডে ওয়েবহুকের ঠিকানাটা দেখুন, সেটা এখনও এই কাউন্টারের টানেলে লাগানো আছে কি না; '
    + 'কুইক টানেল প্রতিবার চালু হলে নতুন ঠিকানা নেয়।',
  'till.inbound.stillPayable':
    'উপরের লিঙ্কটা দু-ভাবেই চালু থাকে, আর দেরিতে আসা কলব্যাকও এই সেশন মিটিয়ে দেবে। অপেক্ষা করলে '
    + 'কিছু যায় না — তবে রাস্তা না খুললে কিছু বদলাবেও না।',

  /* ---- the paid moment ---------------------------------------------------- */

  'till.paid.word': 'টাকা এসে গেছে',
  'till.paid.body':
    'সই যাচাই করা ওয়েবহুক এই সেশন আর এই অঙ্ক মিলিয়েছে। এই স্ক্রিন কেবল এতেই আসতে পারে।',
  'till.return.fromPaid': 'এই বিলের একটি জিনিস ফেরত নাও',

  /* ---- how this counter decides ------------------------------------------- */

  'till.decides.title': 'এই কাউন্টার কীভাবে ঠিক করে',
  'till.decides.code': 'কোড বিলে তোলে',
  'till.decides.code.v': 'প্রথম পরিষ্কার পড়াতেই',
  'till.decides.look': 'দেখে চেনা জিনিস তোলে',
  'till.decides.look.v': '3টে স্থির ফ্রেমের পরে',
  'till.decides.forget': 'প্যাকেট ভুলে যায়',
  'till.decides.forget.v': '4টে ফ্রেমে না দেখলে',
  'till.decides.rate': 'প্রতি সেকেন্ডে কতবার দেখে',
  'till.decides.note':
    'চেনা কেবল দাম <b>প্রস্তাব</b> করে। সেশনকে মেটানো বলতে পারে কেবল সই যাচাই করা ওয়েবহুক — এই '
    + 'পাতা পেমেন্ট আটকাতে পারে, দিতে কখনও পারে না।',

  /* ------------------------------------------------------- the teach screen --
     `routes/Products.tsx`. These were the last English literals left in a
     translating file: 35 of them, all on the screen where a shopkeeper does
     the hardest thing this program asks — deciding what a packet IS. A till
     that speaks Hindi at the counter and English at the moment of teaching
     is a till that speaks Hindi decoratively. */
  'products.teach.title': 'নতুন জিনিস শেখান',
  'products.teach.nameEg': 'পারলে-জি বিস্কুট ১০০ গ্রাম',
  'products.draw': 'জিনিসটার চারপাশে বাক্স আঁকুন',
  'products.captured': 'তোলা হয়েছে',
  'products.show': 'প্যাকেটটা ক্যামেরার সামনে ধরুন',
  'products.show.sub': 'শেখানোর আগে ঝলকই বলে দেয় কোডটা পড়া যাচ্ছে কি না।',
  'products.camera.dead': 'ক্যামেরা চালু হয়নি',
  'products.checking': 'দেখা হচ্ছে',
  'products.retake': 'আবার তুলুন',
  'products.stopCamera': 'ক্যামেরা বন্ধ করুন',
  'products.checkingBox': 'আপনার আঁকা বাক্সটা দেখা হচ্ছে',
  'products.backToLive': 'চালু ক্যামেরায় ফিরুন',
  'products.catalogue': 'জিনিসের তালিকা',
  'products.reading': 'তালিকা পড়া হচ্ছে',
  'products.tryAgain': 'আবার চেষ্টা করুন',
  'products.emptyCatalogue': 'এখনও কিছু শেখানো হয়নি',
  'products.codeOnly': 'শুধু কোড',
  'products.noMm': 'মাপ নেই',
  'products.corrected': 'ঠিক করা হয়েছে।',
  'products.viewAdded': 'নতুন চেহারা যোগ হয়েছে।',
  'products.legend': 'বোতাম আর চিহ্নের মানে',
  'products.bar': 'এটুকু পার করতেই হবে।',
  'products.views': 'চেহারা',
  'products.edit': 'বদলান',
  'products.f.name': 'নাম',
  'products.f.price': 'দাম টাকায়',
  'products.f.code': 'ছাপা কোড',
  'products.cancel': 'থাক',
  'products.chain.reading': 'হিসেবের শিকল পড়া হচ্ছে',
  'products.chain.empty': 'শিকলে এখনও কিছু নেই',
  'products.choosePicture': 'একটা ছবি বাছুন',
  'products.noPhoto':
    '<b>ছবি লাগবে না।</b> আপনি যে কোডটা লিখলেন, সেটাই এই জিনিসের পরিচয় হবে। ক্যামেরা দিয়ে প্যাকেট থেকে কোড পড়াতে চাইলে ঘরটা ফাঁকা করে দিন।',
  'products.gate.camera':
    'ক্যামেরা থেকে শেখাতে <b>আটটা ফ্রেম</b> লাগে আর প্রত্যেকটার ঝলক, ঝাপসা ভাব আর ফোকাস দেখা হয়। যেটা সবচেয়ে পরিষ্কার সেটাই রাখা হয়, আর একটাও টিকল না মানে কিছুই রাখা হয় না — জোর করে পার করার উপায় নেই, কারণ হাত নেড়ে পার হওয়া যায় এমন পাহারা পাহারা নয়, সাজসজ্জা।',
  'products.gate.upload':
    'একটামাত্র আপলোড করা ফাইল এভাবে যাচাই করা <b>যায় না</b>: পাহারাটা ফ্রেমগুলোকে একে অপরের সঙ্গে মিলিয়ে দেখে, আর একটা ফাইলের মেলানোর মতো কিছুই নেই। এই সুরক্ষা চাইলে ক্যামেরা থেকে শেখান।',
  'products.pill.views.one': '১ চেহারা',
  'products.pill.views.other': '{n} চেহারা',
  'products.addView': '+ চেহারা',
  'products.adding': 'যোগ করা হচ্ছে…',
  'products.forget': 'ভুলে যান',
  'products.legend.views': 'চেহারা',
  'products.legend.edit': 'বদলান',
  'products.legend.addView':
    '<b>+ চেহারা</b> আগে শেখানো জিনিসের আর একটা কোণ থেকে ছবি তুলে সেটাও মনে রাখে। প্যাকেটের একাধিক দিক থাকে আর একটা শেখানো চেহারা মানে একটাই দিক, তাই দ্বিতীয় আর তৃতীয় চেহারাই কাউন্টারকে সেটা কাত হয়ে পড়ে থাকলে বা ঘোরানো থাকলে চিনতে দেয়। প্যাকেট ঘোরান, আবার চাপুন। দাম আর নাম কখনও বদলায় না।',
  'products.legend.gates':
    'এই কাউন্টার দেখে <n>{phi}</n> কোসাইনে চেনে, আর শুধু ছবি থেকে শেখানো জিনিসকে <n>{appearance}</n>-এর উঁচু মাপকাঠিতে বিচার করে — তাতে একটা পরিচায়ক, মিলিমিটারে তার আসল মাপ, থাকে না। যেটা এগিয়ে, তাকে দ্বিতীয়টার চেয়ে <n>{theta}</n> এগিয়ে থাকতেও হয়, নইলে কাউন্টার দুটোরই নাম বলে জিজ্ঞেস করে। এই সংখ্যাগুলো এই কাউন্টার থেকেই পড়া, এখানে ছাপা নয়: যে পাতা মাপকাঠি মনে রাখে, সে একদিন যে যন্ত্রের জানলা সেই যন্ত্রকেই মিথ্যে বলবে।',
  'products.legend.gates.loading':
    'এই কাউন্টার থেকে মাপকাঠি পড়া হচ্ছে…',
  'products.legend.gates.none':
    'এই কাউন্টার তার মাপকাঠি জানায়নি{why}, তাই এখানে কোনও সংখ্যা ছাপা হল না। সেটিংসও একই জায়গা থেকে একই সংখ্যা পড়ে।',
  'products.legend.codeOnly':
    '<b>শুধু কোড</b> মানে জিনিসটা তার ছাপা নম্বর থেকে শেখানো, তাই দেখতে কেমন সে বিষয়ে কিছুই রাখা হয়নি আর ক্যামেরা সেটা দেখে চিনতে পারে না — তার কোড দেখান, বা ছবি থেকে আবার শেখান। <b>চেহারা</b> বলে কাউন্টার সেটাকে কতগুলো কোণ থেকে দেখেছে; একটা চেহারা কেবল যে দিকটার ছবি তুলেছিলেন সেটাই চেনে।',
  'products.legend.edits':
    '<b>বদলান</b> নাম, দাম আর ছাপা কোড বদলায়, আর কিছুতে হাত দেয় না — শেখানো চেহারায় নয়, মিলিমিটারে নয়, ছবিতে নয়, আর SKU পরিচয়ে তো কখনওই নয়, যেটাকে পুরনো বিল আর অর্ডার দেখায়। দামের বদল দোকানের নিজের হিসেবের শিকলে পুরনো আর নতুন দুই দাম সমেত লেখা হয়, যাতে গত সপ্তাহের বিলও আজ ব্যাখ্যা করা যায়।',
  'products.legend.forget':
    'কোনও জিনিস ভুলে গেলে যা দিয়ে তার দাম বসানো যেত সবই মুছে যায় — বাঁধন, ভেক্টর আর দাম — তাই যে কোড আগে তার নাম বলত সে আর কারও নাম বলবে না। যদি শুধু দাম বা নামটাই ভুল হয় তবে সেটা <b>বদলান</b>: একটা নতুন ছবি থেকে আবার শেখাতে গেলে দুটো অক্ষর ঠিক করার জন্য প্রতিটা চেহারা, প্রতিটা মিলিমিটার আর ছবিটাই ফেলে দেওয়া হয়, আর জিনিসটার অনেক দিকের বদলে একটাই দিক পড়ে থাকে।',
  'products.mode.code':
    'কোড দিয়ে',
  'products.mode.code.t':
    'বারকোড বা QR — লেখা, বা প্যাকেট থেকে পড়া',
  'products.mode.photo':
    'ছবি দিয়ে',
  'products.mode.photo.t':
    'সাধারণ ছবি, ম্যাট ছাড়া: শুধু চেহারা',
  'products.mode.mat':
    'ম্যাটের উপর',
  'products.mode.mat.t':
    'ছাপা TAKHTI ম্যাট: আসল মিলিমিটারও যোগ হয়',
  'products.f.sku':
    'SKU পরিচয়',
  'products.f.name.sub':
    'দোকানি বিলে যা পড়ে',
  'products.f.price.sub':
    'পূর্ণ পয়সায় রাখা হয়; দশমিক নেওয়া হয় না, গোল করাও হয় না',
  'products.f.code.label':
    'বারকোড বা QR নম্বর',
  'products.f.code.optional':
    'বারকোড বা QR নম্বর (ইচ্ছে হলে)',
  'products.f.code.sub':
    'দাগের নিচের সংখ্যাগুলো লিখুন, বা ফাঁকা রেখে ক্যামেরাকে পড়তে দিন',
  'products.f.code.read':
    'ক্যামেরা প্যাকেট থেকে পড়েছে — {code}',
  'products.src.file':
    'ফাইল দিন',
  'products.src.camera':
    'ক্যামেরা ব্যবহার করুন',
  'products.pic.none':
    'কোনও ছবি বাছা হয়নি',
  'products.pic.alt':
    'যে জিনিসটা শেখাতে হবে',
  'products.teach.go':
    'এই জিনিসটা শেখান',
  'products.teach.busy':
    'শেখানো হচ্ছে…',
  'products.fine.code':
    'কোড এই SKU-কে একটা পরিচয়ে বেঁধে দেয়। এর চেহারা নিয়ে কিছুই রাখা হয় না, দরকারও নেই।',
  'products.fine.photo':
    'সাধারণ ছবি কেবল চেহারা শেখায়: মিলিমিটার নেই, মাপ যাচাই নেই, আর তার পুষিয়ে দিতে মিলের মাপকাঠি আরও কড়া।',
  'products.fine.mat':
    'ছাপা TAKHTI ম্যাট কাউন্টারকে আসল মাপকাঠি দেয়, তাই জিনিসটা তার সত্যিকারের মাপ নিয়ে মিলিমিটারে রাখা হয়।',
  'products.close':
    'বন্ধ করুন',
  'products.forgetting':
    'ভোলা হচ্ছে…',
  'products.pill.codes.one': 'কোড',
  'products.pill.codes.other': '{n} কোড',
  'products.burst': 'ছবি তোলা হচ্ছে — স্থির থাকুন · {n}-এর {at}',
  'products.lookingForCode': 'কোড খোঁজা হচ্ছে…',
  'products.stock.onhand': 'তাকে',
  'products.stock.notCounted': 'গোনা হয়নি',
  'products.stock.online': 'অনলাইন',
  'products.stock.noFigure': 'স্টকের হিসেব নেই — সীমা ছাড়া বিক্রি হয়',
  'products.stock.available': '{n} বিক্রি করা যাবে',
  'products.stock.out': 'অনলাইনে স্টক শেষ',
  'products.stock.held': '{open} খোলা অর্ডারে · {delivered} গোনার পরে ডেলিভারি',
  'products.stock.heldOpen': '{open} খোলা অর্ডারে',
  'products.stock.heldDelivered': '{delivered} গোনার পরে ডেলিভারি',
  'products.stock.floorIs': '{n} আটকে রাখা',
  'products.stock.count': 'এখন গোনা',
  'products.stock.count.go': 'লিখুন',
  'products.stock.count.empty': 'তাকে কটা আছে, গোটা প্যাকেটে লিখুন।',
  'products.stock.floor': 'আটকে রাখুন',
  'products.stock.floor.go': 'ঠিক করুন',
  'products.stock.floor.same': 'এটাই আগে থেকে সীমা।',
  'products.stock.floor.sub':
    'আটকে রাখুন: তাকে এতগুলো থাকলেই অনলাইন বিক্রি থেমে যায়, যাতে কাউন্টারের জন্য থাকে। 0 মানে শেষ প্যাকেটও বিক্রি হবে।',
  'products.stock.saving': 'সেভ হচ্ছে…',
  'products.stock.noFigures': 'স্টকের হিসেব পড়া যায়নি, তাই অনলাইনে কোনও সীমা নেই: {why}',
  'products.stock.reserve':
    'অনলাইন অর্ডার দেওয়া মাত্র তার প্যাকেট আটকে যায়, কিন্তু আপনি প্যাক না করা পর্যন্ত গোনা থেকে কিছু কমে না। ডেলিভারি হওয়া অর্ডার আপনি আবার তাক না গোনা পর্যন্ত বাদ থাকে।',
  'auth.chip.none': 'এখানে সাইন-ইন নেই',
  'auth.chip.none.short': 'সাইন-ইন নেই',
  'auth.chip.create': 'অ্যাকাউন্ট খুলুন',
  'auth.chip.create.short': 'অ্যাকাউন্ট',
  'auth.chip.signIn': 'সাইন ইন করুন',
  'auth.chip.out': 'সাইন ইন করা নেই',
  'till.cam.off.lead':
    'আপনি শুরু না করা পর্যন্ত কিছুই পাঠানো হয় না।',
  'till.cam.off.upload':
    '<b>এই যন্ত্র থেকে যা বাইরে যায়:</b> কোড পড়তে ক্যামেরার গোটা ছবিটা পাঠানো হয়, যাতে কোড প্যাকেটের যেখানেই থাকুক পাওয়া যায়। ছোট করতে চাইলে একটা চৌকো এঁকে দিন — আপনার আঁকা চৌকোর বাইরের সবটা এই ব্রাউজারেই, পাঠানোর আগেই, বাদ দেওয়া হয়।',
  'till.redraw.title.on':
    'কাউন্টারের এলাকা আবার গোটা ছবি করে দিন',
  'till.redraw.title.off':
    'ক্যামেরা না চললে আবার আঁকার মতো কোনও এলাকা নেই।',
  'till.sweep.title.off':
    'আগে ক্যামেরা চালু করুন — কাউন্টার পড়ার মতো কিছুই নেই।',
  'till.sweep.title.busy':
    'এই কাউন্টার আগে থেকেই পড়া হচ্ছে। একবারে একটাই।',

  /* ------------------------------------------------------- books · today -- */

  'today.recon.title': 'ক্যাশ আর গেটওয়ের হিসাব যেখানে মেলে না',
  'today.recon.clear': 'মিলেছে',
  'today.recon.nothing': 'আজ এখনও মেলানোর মতো কিছুই নেই',
  'today.recon.nothing.detail':
    'মাঝরাত থেকে একটাও বিল বন্ধ হয়নি, একটাও ওয়েবহুক আসেনি, তাই কোনও পরীক্ষাই হয়নি। এর মানে এই নয় যে হিসাব মেলে — আজ টাকার পথটার কাছে কিছুই চাওয়া হয়নি, তাই সেটা নিয়ে কিছু জানাও নেই।',
  'today.recon.none': 'আজ ক্যাশ আর গেটওয়ের হিসাব মেলে',
  'today.recon.none.detail':
    'আজ যত বিল বন্ধ হয়েছে, সবক’টির জন্যই গেটওয়ের কাছে লিংক চাওয়া হয়েছে, কোনও ওয়েবহুক অগ্রাহ্য হয়নি, আর ওয়েবহুক ছাড়া কিছুই মেটানো বলে লেখা নেই। যে টাকা এখনও আসেনি তা নিচে দেওয়া আছে — সেটা গোলমাল নয়, লাইন।',
  'today.recon.unavailable': 'এই কাউন্টার এখন নিজের হিসাব মিলিয়ে দেখতে পারছে না',
  'today.recon.unavailable.detail':
    'মিলিয়ে দেখার উত্তর আসেনি, তাই হিসাব মেলে কি না সে বিষয়ে কিছুই বলা হচ্ছে না। উপরের সংখ্যাগুলো দিনের হিসাবের, সেগুলোয় কিছু বদলায়নি। যেটা নেই সেটা পরীক্ষা, বিক্রি নয়।',
  'today.recon.split': 'আজ, আসলে যা ঘটেছে সেই অনুযায়ী',
  'today.recon.billed': 'বিল হয়েছে',
  'today.recon.settled': 'মেটানো — যাচাই করা ওয়েবহুকে',
  'today.recon.settled.none': 'আজ কিছুই নয়',
  'today.recon.settled.never': 'কোনওদিনই নয়',
  'today.recon.linksent': 'লিংক গেছে, টাকা আসেনি',
  'today.recon.nolink': 'বন্ধ হয়েছে, লিংকই তৈরি হয়নি',
  'today.recon.refused': 'কাউন্টার টাকা নিতে রাজি হয়নি',
  'today.recon.unwitnessed': 'মেটানো, কিন্তু ওয়েবহুকের লাইন নেই',
  'today.recon.owed': 'এখনও বাকি',
  'today.recon.channel': 'কোথা থেকে হয়েছে',
  'today.recon.ch.till': 'দোকানের ক্যাশ',
  'today.recon.ch.storefront': 'অনলাইন দোকান',
  'today.recon.ch.unnamed': 'দুটোর কোনওটাই নয় — এই কাউন্টার বলতে পারে না',
  'today.recon.lifetime': 'এই কাউন্টারে আজ পর্যন্ত সব মিলিয়ে',

  'today.empty.title': 'আজকের হিসাব',
  'today.empty.head': 'আজ এখনও কোনও বিল হয়নি',
  'today.empty.body':
    'মাঝরাত থেকে একটাও ঝুড়ি বন্ধ হয়নি, তাই যোগ করার মতো কিছুই নেই। প্রথম বিল বন্ধ হওয়ামাত্র সংখ্যাগুলো এখানে চলে আসবে — ততক্ষণ কোনও শূন্য দেখানো হয় না, কারণ এখানে শূন্য দেখতে ঠিক সেই দিনের মতোই লাগে যেদিন কিছুই বিক্রি হয়নি।',
  'today.empty.action': 'ক্যাশ খুলুন',
  'today.empty.yesterday.one':
    'কাল দিন শেষ হয়েছিল {amount}-এ, একটি বিলে।',
  'today.empty.yesterday.other':
    'কাল দিন শেষ হয়েছিল {amount}-এ, {n}টি বিলে।',

  /* ------------------------------------------------ আপনার জিনিস (দোকান) -- */

  'shopitems.title': 'আপনার জিনিস',
  'shopitems.blurb':
    'দোকানে যা কিছু বিক্রি হয়, সব এখানে। ক্যামেরা ছাড়াই নতুন জিনিস যোগ করুন, নাম বা দাম ঠিক করুন, ছবি দিন, আর বলুন তাকে কটা আছে।',

  'shopitems.stat.products': 'জিনিস',
  'shopitems.stat.nophoto': 'ছবি নেই',
  'shopitems.stat.nophoto.sub': 'খদ্দেরের দেখার মতো কিছু নেই',
  'shopitems.stat.unseen': 'কখনও ছবি তোলা হয়নি',
  'shopitems.stat.unseen.sub': 'ক্যামেরা এগুলো চিনতে পারে না',
  'shopitems.stat.counted': 'গোনা হয়েছে',
  'shopitems.stat.counted.sub': 'তাকের হিসেব আছে',

  'shopitems.list.title': 'তাক',
  'shopitems.list.sub': 'বদলাতে জিনিসটায় চাপ দিন',
  'shopitems.search': 'নাম, আইডি বা বারকোড খুঁজুন',
  'shopitems.filter.all': 'সব',
  'shopitems.filter.nophoto': 'ছবি নেই',
  'shopitems.filter.unseen': 'কখনও দেখা হয়নি',
  'shopitems.load.failed': 'তালিকা পড়া গেল না',
  'shopitems.retry': 'আবার চেষ্টা করুন',
  'shopitems.empty.title': 'তাকে এখনও কিছু নেই',
  'shopitems.empty.body':
    'দাম দিতে এখানে জিনিস যোগ করুন, বা Products পর্দায় ছবি তুলুন যাতে ক্যামেরাও চিনতে পারে।',
  'shopitems.nomatch.title': 'কিছু মিলল না',
  'shopitems.nomatch.body':
    'আপনি যা লিখেছেন তার সঙ্গে এখানে কোনও জিনিস মেলে না। সব দেখতে ঘরটা ফাঁকা করুন।',
  'shopitems.notcounted': 'গোনা হয়নি',
  'shopitems.onhand': 'তাকে {n}',
  'shopitems.nophoto': 'ছবি নেই',

  'shopitems.how.mat': 'ম্যাটে',
  'shopitems.how.look': 'দেখে',
  'shopitems.how.code': 'কোড দিয়ে',
  'shopitems.how.typed': 'লিখে দেওয়া',

  'shopitems.add.title': 'নতুন জিনিস যোগ করুন',
  'shopitems.add.sub': 'ক্যামেরা নয়, ম্যাট নয় — শুধু নাম আর দাম',
  'shopitems.add.open': 'জিনিস যোগ করুন',
  'shopitems.add.lead':
    'রাত এগারোটায় তাকে রাখা চালের বস্তা বিক্রি করতে আগে ছবি তুলতে হবে, এমন হওয়া উচিত নয়। নাম আর দাম লিখুন, সেটা দোকানে চলে আসবে।',
  'shopitems.add.go': 'তাকে রাখুন',
  'shopitems.add.fine':
    'এভাবে যোগ করলে কাউন্টার শুধু নাম আর দাম শেখে, জিনিসটা দেখতে কেমন তা নয় — ক্যামেরা একে চিনবে না। সময় পেলে Products পর্দায় ছবি তুলে নিন।',
  'shopitems.add.done': '{name} তাকে উঠল',
  'shopitems.add.derived': 'আইডি নাম থেকে তৈরি',
  'shopitems.add.filed': '{name}-এ রাখা হল।',
  'shopitems.add.notfiled': 'যোগ হয়েছে, কিন্তু কোনও ভাগে রাখা গেল না: {why}',
  'shopitems.add.counted': 'গোনা হল: তাকে {n}।',
  'shopitems.add.notcounted': 'যোগ হয়েছে, কিন্তু গোনা লেখা গেল না: {why}',
  'shopitems.cancel': 'থাক',

  'shopitems.f.name': 'নাম',
  'shopitems.f.name.sub': 'যা বিলে আপনি পড়েন আর দোকানে খদ্দের পড়ে',
  'shopitems.f.name.eg': 'বাসমতি চাল 5 কেজি',
  'shopitems.f.price': 'দাম',
  'shopitems.f.price.sub': 'টাকা, যেমন আপনি লেখেন: 12 বা 12.50',
  'shopitems.f.category': 'কোথায় থাকে',
  'shopitems.f.category.sub': 'যে তাকে খদ্দের খুঁজবে',
  'shopitems.f.category.none': 'কোথাও রাখা নেই',
  'shopitems.f.stock': 'এখন তাকে',
  'shopitems.f.stock.sub': 'গোটা প্যাকেট, যদি গুনে থাকেন',
  'shopitems.f.code': 'বারকোড',
  'shopitems.f.code.sub': 'দাগের নিচের সংখ্যা, প্যাকেটে থাকলে',
  'shopitems.f.code.edit': 'এই ঘর ফাঁকা করলে এই জিনিসের সব কোড খুলে যাবে',
  'shopitems.f.id': 'এর আইডি',
  'shopitems.f.id.sub':
    'ফাঁকা রাখলে নাম থেকে তৈরি হবে। পরে আর কখনও বদলানো যায় না।',
  'shopitems.f.id.auto': 'নাম থেকে তৈরি হবে',
  'shopitems.f.photo': 'ছবি',
  'shopitems.f.photo.sub': 'দোকানে দেখা যায়। ক্যামেরাকে এতে কিছু শেখানো হয় না।',
  'shopitems.f.photo.alt': 'এই জিনিসের জন্য বাছা ছবি',
  'shopitems.f.count': 'গোনা',

  'shopitems.photo.toobig':
    'ছবিটা প্রায় {n} MB, আর সীমা 8 MB। ছোট মাপে আবার তুলুন।',
  'shopitems.photo.unreadable': 'ফাইলটা ছবি হিসেবে পড়া গেল না।',
  'shopitems.photo.stored': 'ছবিটা রাখা হল।',
  'shopitems.photo.removed': 'ছবিটা সরানো হল।',
  'shopitems.photo.choose': 'ছবি বাছুন',
  'shopitems.photo.remove': 'ছবি সরান',
  'shopitems.photo.fine':
    'কাউন্টার যার সঙ্গে মেলায় ছবি তার অংশ নয় — ছবি বদলালে টিলের কোনও সিদ্ধান্ত বদলায় না।',

  'shopitems.g.basics': 'নাম, দাম আর বারকোড',
  'shopitems.g.photo': 'ছবি',
  'shopitems.g.category': 'কোথায় থাকে',
  'shopitems.g.stock': 'তাকে কটা আছে',

  'shopitems.edit.permanent':
    'আইডি কখনও বদলায় না — এ পর্যন্ত ছাপা প্রতিটি বিল ওটাকেই দেখায়।',
  'shopitems.edit.save': 'রাখুন',
  'shopitems.edit.nochange': 'আলাদা কিছু ছিল না, তাই কিছু লেখা হয়নি।',
  'shopitems.edit.saved':
    'রাখা হল: {what}। এটা দোকানের নিজের চেনে আছে, পুরনো আর নতুন দুই মান সহ।',
  'shopitems.edit.unbound': 'এই কোডগুলো আর এই জিনিসের দাম বলে না: {codes}।',
  'shopitems.history.show': 'এর দাম কী কী ছিল',
  'shopitems.history.none': 'এই জিনিসে এখনও কিছু বদলায়নি।',

  'shopitems.cat.file': 'এখানে রাখুন',
  'shopitems.cat.filed': '{name}-এ রাখা হল।',
  'shopitems.cat.cleared': 'যে তাকে ছিল, সেখান থেকে সরানো হল।',
  'shopitems.cat.none':
    'এখনও কোনও ভাগ নেই। Categories পর্দায় একটা বানান, তারপর এটাকে তাতে রাখা যাবে।',

  'shopitems.stock.record': 'গোনা লিখুন',
  'shopitems.stock.fine':
    'এটা আবার গোনা: এটা হিসেব বদলে দেয় আর তার আগের সব আনা-নেওয়া বাতিল করে। শুধু গোটা প্যাকেট।',
  'shopitems.close': 'বন্ধ করুন',
  'till.pay.qrRefused.stillPayable':
    'পেমেন্ট লিঙ্ক তৈরি হয়েছে আর তা দিয়ে টাকা দেওয়া যায় — শুধু এই কাউন্টার তার QR আঁকতে পারেনি। CANCEL চেপে কাউন্টারে ফিরে আবার চার্জ করুন।',
  'till.pay.qrRefused.notGateway':
    'এই ঠিকানা গেটওয়ের নয়, তাই এর মাধ্যমে কোনও টাকা দেওয়া যাবে না। এই কাউন্টার সিমুলেটরে চললে (RZP_MODE=sim) এটাই হওয়ার কথা: নকল লিঙ্ক ইচ্ছে করেই অচল রাখা হয়। আসল লিঙ্কের জন্য RZP_MODE=live দিন। CANCEL চেপে কাউন্টারে ফিরুন।',

  /* ---- কাউন্টারে সালাহকার ------------------------------------------------ */

  'till.sk.title': 'সালাহকার',
  'till.sk.sub': 'অর্ডার বলুন, বা দাম জিজ্ঞেস করুন',
  'till.sk.state.idle': 'কাউন্টারে',
  'till.sk.state.listening': 'শুনছে',
  'till.sk.state.thinking': 'ভাবছে',
  'till.sk.state.speaking': 'বলছে',
  'till.sk.state.voicing': 'গলা আনছে',
  'till.sk.listen': '🎤 শোনো',
  'till.sk.stop': 'শোনা বন্ধ',
  'till.sk.placeholder': 'লিখুন — "দুটো ম্যাগি আর একটা পার্লে জি" — বা জিজ্ঞেস করুন: "পার্লে জি-র দাম?"',
  'till.sk.send': 'ওকে বলুন',
  'till.sk.langs': 'ভাষা',
  'till.sk.idle':
    'শোনো চেপে অর্ডার বলুন, বা লিখে দিন। জিনিসের আগে সংখ্যা থাকলে সেটা বিলে প্রস্তাব হয়ে আসে; '
    + 'প্রশ্ন হলে সে মুখে উত্তর দেয়।',
  'till.sk.listening': 'শুনছে। অর্ডার বলুন — "দুটো ম্যাগি আর একটা পার্লে জি" — বা দাম জিজ্ঞেস করুন।',
  'till.sk.heard': 'শুনেছে',
  'till.sk.typed': 'লেখা',
  'till.sk.route.order': 'অর্ডার',
  'till.sk.route.advice': 'প্রশ্ন',
  'till.sk.route.order.v': 'বিলে প্রস্তাব হিসেবে রাখা, আপনার মেনে নেওয়ার জন্য',
  'till.sk.route.advice.v': 'মুখে উত্তর দেওয়া হয়েছে; বিল যেমন ছিল তেমনই',
  'till.sk.route.refused.v': 'নাম করে ফিরিয়ে দিয়েছে; বিলে কিছু নেই',
  'till.sk.why.shop_word': 'দাম বা দোকানের শব্দ',
  'till.sk.why.question_word': 'প্রশ্নের শব্দ',
  'till.sk.why.nothing': 'এতে কোনও জিনিস নেই',
  'till.sk.why.add_verb': '"যোগ করো" গোছের শব্দ',
  'till.sk.why.weight': 'জিনিসের আগে ওজন',
  'till.sk.why.count': 'জিনিসের আগে সংখ্যা',
  'till.sk.why.several': 'দুটো বা বেশি জিনিস, কোনও প্রশ্ন নেই',
  'till.sk.why.one_bare': 'একা একটা নাম',
  'till.sk.reread.as_question': 'সে এটাকে অর্ডার নয়, প্রশ্ন হিসেবে পড়েছে — বিলে কিছু রাখা হয়নি।',
  'till.sk.reread.as_order': 'কল এটাকে অর্ডার বলে ফিরিয়ে দিয়েছে, তাই সে এটা কাউন্টারে রেখেছে।',
  'till.sk.put.one':
    'বিলে <b>প্রস্তাব</b> হিসেবে রাখা: 1টা লাইন, {total}। আপনি ওখানে মেনে না নেওয়া পর্যন্ত কিছু বিল হয়নি।',
  'till.sk.put.other':
    'বিলে <b>প্রস্তাব</b> হিসেবে রাখা: {n}টা লাইন, {total}। আপনি ওখানে মেনে না নেওয়া পর্যন্ত কিছু বিল হয়নি।',
  'till.sk.check': 'এটা দেখে নিন',
  'till.sk.answer': 'ওর উত্তর',
  'till.sk.saying': 'বলছে',
  'till.sk.refused': 'সে এটা পারেনি',
  'till.sk.byVoice': 'নিজের গলায়',
  'till.sk.byBrowser': 'এই ব্রাউজারের গলায়',
  'till.sk.voiceRefused': 'ওর গলা আনা যায়নি, তাই এই ব্রাউজার পড়েছে: {why}',
  'till.sk.muted': 'কাউন্টার মিউট করা, তাই ওকে দেখা যায়, শোনা যায় না।',
  'till.sk.noMic': 'এই ব্রাউজার শুনতে পারে না',
  'till.sk.noMic.hint': 'অর্ডার লিখে দিন — বাকি সব এখানে মাইক ছাড়াই চলে।',
  'till.sk.micStopped': 'মাইক থেমে গেছে',
  'till.sk.disclose':
    'ব্রাউজার কথা নিজের সার্ভিসে লেখে, তাই <b>আওয়াজ এই মেশিন থেকে বাইরে যায়</b>। ওর বলা উত্তর '
    + 'কাউন্টারের গলা-সার্ভিস থেকে এক-একটা বাক্য করে আনা হয় (সেটা বন্ধ থাকলে এই ব্রাউজার পড়ে)। '
    + 'কাউন্টারের ছবি, জিনিসের তালিকা আর দাম কখনও বাইরে যায় না।',
  'till.sk.never':
    'সে প্রস্তাব দেয়, আপনি মানেন। CHARGE আপনার বোতাম — এখানে বলা বা লেখা কিছুই টাকার সার্ভিসে '
    + 'পৌঁছোয় না।',

  /* ---- বিলে প্রস্তাব ------------------------------------------------------ */

  'till.bill.proposed.pill': 'প্রস্তাব',
  'till.bill.proposed.count.one': '{n}টা লাইন আপনার অপেক্ষায়',
  'till.bill.proposed.count.other': '{n}টা লাইন আপনার অপেক্ষায়',
  'till.bill.proposed.acceptAll': 'সব মানুন',
  'till.bill.proposed.dropAll': 'সব বাদ',
  'till.bill.proposed.accept': 'মানুন',
  'till.bill.proposed.drop': '{name} প্রস্তাব থেকে বাদ দিন',
  'till.bill.proposed.heard': 'শুনেছে “{heard}”',
  'till.bill.proposed.respelt': 'শুনেছে “{heard}” — কাউন্টার ইংরেজি অক্ষরে লিখে এটা খুঁজে পেয়েছে',
  'till.bill.proposed.weighed': 'ওজনে: {weight}',
  'till.bill.proposed.onBill': 'এটা বিলে প্যাকেটে আগে থেকেই আছে। এর ওজন মানার আগে সেগুলো বাদ দিন।',
  'till.bill.proposed.notCounted': '+ {amount} প্রস্তাবে, মোটে নয়',
  'till.bill.proposed.hint':
    'হলুদ মানে থেমে থাকা: সালাহকার এগুলো রেখেছে, এখনও কেউ মানেনি। মানুন চাপলে লাইন বিলে যায়; '
    + 'CHARGE-এর আগে ক্যামেরাকে সেটা দেখতেই হবে।',
  'till.bill.held.title':
    'সলাহকার এই কাউন্টারের জন্য লাইন ধরে রেখেছিল',
  'till.bill.held.arrived':
    'অন্য জায়গায় ধরে রাখা {n}টি লাইন বিলে প্রস্তাবিত — নিচে মেনে নিন বা বাদ দিন।',
  'till.bill.held.moved':
    'প্রস্তাবের পর দাম বদলেছে — {list}। বিল আজকের দাম নেয়।',
  'till.bill.held.gone':
    'বাদ গেল, তালিকায় আর নেই: {list}।',
  'till.bill.held.noCatalogue':
    'সে {n}টি লাইন ধরে রেখেছিল, কিন্তু দাম বসাতে তালিকা পড়া গেল না। সেগুলো ধরা থাকবে।',
  'till.bill.held.heard':
    'সলাহকার রেখেছে',
  'till.bill.held.repriced':
    'সলাহকার রেখেছে · আজকের দাম',
  'till.bill.held.ok':
    'ঠিক আছে',
};