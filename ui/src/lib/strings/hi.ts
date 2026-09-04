import type { Table } from './en';

/* ===========================================================================
   हिन्दी — the counter as it is actually spoken over
   ---------------------------------------------------------------------------
   This is not a translation of the English sentences; it is the same counter
   said in Hindi. The words are the ones used across a kirana counter — दाम,
   बिल, गल्ला, ख़र्चा, उधार, माल — and not their textbook equivalents. Where
   English is already what a shopkeeper says (UPI, QR, स्टॉक, ऑफ़र, ऑर्डर,
   सेटिंग), the loanword stays in Devanagari rather than being replaced by a
   Sanskritised word nobody uses at a counter.

   TWO RULES THIS FILE KEEPS:

   1. EVERY {placeholder} SURVIVES. A dropped brace is a dropped number, and
      the numbers are the product. i18n.test.ts checks each one.
   2. A REFUSAL STAYS A REFUSAL. Hindi has a very polite register that turns
      "this cannot be charged" into "this may perhaps be a little difficult".
      None of that here: where the counter says it cannot do something, the
      Hindi says it plainly, in the same breath length.

   Register: the imperative used is the तुम/plain form ("दिखाओ", "दबाओ") for
   the shopkeeper's own controls, because that is how a person talks to their
   own till; the customer-facing sentences on the pay screen use the neutral
   third person. Nuqta is written where the word carries it (ख़र्च, फ़्रेम,
   क़ीमत) — a shop's own screen is worth spelling correctly.
   =========================================================================== */

export const hi: Table = {
  /* ------------------------------------------------------------ the shell -- */

  'nav.tab.counter': 'काउंटर',
  'nav.tab.counter.blurb': 'जो शेल्फ़ से बाहर जाता है',
  'nav.tab.shop': 'दुकान',
  'nav.tab.shop.blurb': 'वो ग्राहक जो यहाँ खड़े नहीं हैं',
  'nav.tab.books': 'बही',
  'nav.tab.books.blurb': 'चेन से बनती है, दूसरी कॉपी कभी नहीं',

  'nav.till': 'गल्ला',
  'nav.till.sub': 'काउंटर पर जो रखा है, उसका बिल',
  'nav.waapsi': 'वापसी',
  'nav.waapsi.sub': 'कैमरे से वापसी, Razorpay से रिफ़ंड',
  'nav.products': 'सामान',
  'nav.products.sub': 'सिखाओ कि कौन-सी चीज़ क्या है',
  'nav.categories': 'क़िस्में',
  'nav.categories.sub': 'शेल्फ़ पर चीज़ कहाँ रखी है',
  'nav.stock': 'स्टॉक',
  'nav.stock.sub': 'क्या आया, क्या गया',
  'nav.offers': 'ऑफ़र',
  'nav.offers.sub': 'दाम में से क्या कटेगा',
  'nav.assistant': 'पूछो',
  'nav.assistant.sub': 'बोलो, और हो जाएगा',

  'nav.shop': 'ऑनलाइन दुकान',
  'nav.shop.sub': 'ग्राहक को क्या दिखता है',
  'nav.orders': 'ऑर्डर',
  'nav.orders.sub': 'लोगों ने जो मँगवाया है',
  'nav.customers': 'ग्राहक',
  'nav.customers.sub': 'कौन ख़रीदता है, कितना ख़र्च करता है',
  'nav.shopitems': 'आपका सामान',
  'nav.shopitems.sub': 'नया जोड़ें, दाम ठीक करें, फोटो लगाएँ',
  'nav.shopprofile': 'आपकी दुकान',
  'nav.shopprofile.sub': 'नाम, पता, खुलने का समय',

  'nav.expiry': 'एक्सपायरी',

  'nav.expiry.sub': 'क्या कब ख़राब होगा',

  'nav.weighed': 'तौल से',

  'nav.weighed.sub': 'चावल, दाल, आटा बोरी से',

  'nav.shelf': 'शेल्फ़',

  'nav.shelf.sub': 'कैमरे से आगे की क़तार गिनो',

  'nav.labels': 'लेबल',

  'nav.labels.sub': 'शेल्फ़ के लिए दाम छापो',

  'nav.khata': 'खाता',
  'nav.khata.sub': 'खाते पर उधार, वसूली Razorpay से',
  'nav.loyalty': 'पॉइंट',

  'nav.loyalty.sub': 'जो पैसा आया, उस पर पॉइंट',

  'nav.insights': 'हिसाब',

  'nav.insights.sub': 'क्या बढ़ रहा, क्या घट रहा',

  'nav.po': 'ऑर्डर',

  'nav.po.sub': 'जो कम है उसका ऑर्डर',

  'nav.gst': 'जीएसटी',

  'nav.gst.sub': 'टैक्स का हिसाब, फ़ाइलिंग नहीं',

  'nav.advisor': 'सलाहकार',

  'nav.advisor.sub': 'बोल के पूछो',

  'nav.salaahkaar': 'सलाहकार',
  'nav.salaahkaar.sub': 'कुछ भी पूछो — बोल के, या लिख के',

  'nav.display': 'ग्राहक स्क्रीन',

  'nav.display.sub': 'जो स्क्रीन ग्राहक की तरफ़ है',

  'nav.today': 'आज',
  'nav.today.sub': 'आज कितना हुआ — चेन से निकाला हुआ',
  'nav.history': 'पुराने बिल',
  'nav.history.sub': 'हर बिल, और उसमें क्या नहीं गया',
  'nav.expenses': 'ख़र्चा',
  'nav.expenses.sub': 'ख़र्चा, और गल्ले का कैश',
  'nav.purchases': 'ख़रीद',
  'nav.purchases.sub': 'क्या ख़रीदा, और कितना बचा',
  'nav.dayclose': 'दिन बंद करो',
  'nav.dayclose.sub': 'कैश गिनो, हिसाब पक्का करो',
  'nav.inventory': 'माल',
  'nav.inventory.sub': 'क्या बिकता है, क्या पड़ा रहता है',
  'nav.settings': 'सेटिंग',
  'nav.settings.sub': 'यह काउंटर क्या करने पर लगा है',

  'nav.signin': 'साइन इन',
  'nav.signin.sub': 'काउंटर पर कौन खड़ा है',

  'nav.menu': 'मेन्यू',
  'nav.sections': 'हिस्से',
  'nav.switchSection': 'हिस्सा बदलो',
  'nav.brandline': 'किराने की दुकान किसी की बात पर चलती है।<br><b>यह उसका गवाह है।</b>',

  /* --------------------------------------------------------- the top bar -- */

  'app.ask': 'पूछो',
  'app.ask.title': 'काउंटर से पूछो',
  'app.salaahkaar': 'सलाहकार',
  'app.salaahkaar.title': 'सलाहकार से कुछ भी पूछो — बोल के, या लिख के',
  'app.salaahkaar.close': 'बंद करो',
  'app.orders.one': '{n} नया ऑर्डर',
  'app.orders.other': '{n} नए ऑर्डर',
  'app.orders.title': 'दुकान के पन्ने से आए नए ऑर्डर',
  'app.opening': 'खुल रहा है…',

  /* ---------------------------------------------------------- the picker -- */

  'lang.label': 'भाषा',
  'lang.choose': 'भाषा चुनो',

  /* =========================================================== THE TILL == */

  'till.head.title': 'गल्ला',
  'till.head.sub':
    'पैकेट ऐसे पकड़ो कि उसका कोड कैमरे के सामने रहे। सामने जितने कोड हैं, सब एक साथ पढ़े जाते हैं '
    + 'और दाम लग जाता है — बिना स्कैनर गन वाली सुपरमार्केट लाइन।',

  /* ---- the camera gate --------------------------------------------------- */

  'till.cam.off.title': 'कैमरा चालू नहीं है',
  'till.cam.off.body':
    'जब तक आप चालू नहीं करते, कुछ भी ऊपर नहीं भेजा जाता। कोड पढ़ने में पूरी कैमरा तस्वीर जाती है, '
    + 'ताकि कोड पैकेट पर कहीं भी हो, मिल जाए। इसे छोटा करना हो तो एक चौकोर खींच लो — आपके खींचे '
    + 'चौकोर के बाहर का सब कुछ इसी ब्राउज़र में, भेजने से पहले ही, हटा दिया जाता है।',
  'till.cam.start': 'कैमरा चालू करो',
  'till.cam.failed': 'कैमरा चालू नहीं हुआ',

  /* ---- the instrument bar over the stage --------------------------------- */

  'till.stage.live': 'लाइव',
  'till.stage.looks': '{n} बार देखा',
  'till.stage.whole': 'पूरी तस्वीर',
  'till.stage.cropped': 'कटा हुआ हिस्सा',

  /* ---- what the loop can see right now ----------------------------------- */

  'till.readout.symbols.one': '{n} कोड',
  'till.readout.symbols.other': '{n} कोड',
  'till.readout.distinct': '{n} अलग',
  'till.readout.untaught': '{n} सिखाया नहीं',
  'till.readout.cooling': 'बिल में लग चुका · {s} सेकंड',
  'till.readout.nothing': 'सामने पढ़ने लायक़ कुछ नहीं',
  'till.readout.cameraOff': 'कैमरा बंद',

  /* ---- reading the whole counter in one press ---------------------------- */

  'till.sweep.button': 'पूरा काउंटर पढ़ो',
  'till.sweep.reading': 'काउंटर पढ़ रहा है…',
  'till.sweep.title':
    'सारा सामान रख दो और एक बार दबाओ — जिस-जिस का दाम लग सकता है, सब बिल में चला जाएगा',
  'till.sweep.mixed': '{named} का दाम लगा · {unnamed} को पहचान नहीं पाया',
  'till.sweep.allPriced': '{named} चीज़ें, सब का दाम लग गया',
  'till.sweep.unnamed.one':
    'काउंटर पर <b>एक</b> चीज़ ऐसी है जो किसी सिखाई हुई चीज़ से इतनी नहीं मिलती कि उसका दाम लगाया '
    + 'जा सके, इसलिए वो बिल में नहीं है। या तो छपा हुआ कोड दिखाओ, या उस चीज़ का यह रुख़ सिखा दो। '
    + 'यह इल्ज़ाम नहीं है — काउंटर बता रहा है कि वो किसे इतना साफ़ नहीं देख पा रहा कि उसके पैसे ले सके।',
  'till.sweep.unnamed.other':
    'काउंटर पर <b>{n}</b> चीज़ें ऐसी हैं जो किसी सिखाई हुई चीज़ से इतनी नहीं मिलतीं कि उनका दाम '
    + 'लगाया जा सके, इसलिए वो बिल में नहीं हैं। या तो छपा हुआ कोड दिखाओ, या उन चीज़ों का यह रुख़ '
    + 'सिखा दो। यह इल्ज़ाम नहीं है — काउंटर बता रहा है कि वो किसे इतना साफ़ नहीं देख पा रहा कि उसके '
    + 'पैसे ले सके।',
  'till.sweep.allBody':
    'जितने हिस्से मिले, सब का दाम लग गया। {byCode} छपे कोड से पढ़े, {byLook} देखकर पहचाने।',
  'till.sweep.gapNote':
    'एक उँगली से कम फ़ासले पर रखे पैकेट एक ही चीज़ पढ़े जाते हैं — बीच में जगह छोड़ो। {ms} मि.से. में पढ़ा।',

  /* ---- the two ways of reading ------------------------------------------- */

  'till.mode.code': 'कोड से',
  'till.mode.code.title': 'सामने के हर छपे बारकोड या QR को पढ़ो',
  'till.mode.look': 'देखकर',
  'till.mode.look.title': 'एक चीज़ को उसकी शक्ल से पहचानो',
  'till.hint.code':
    'कोड से पढ़ने में <b>पूरी कैमरा तस्वीर</b> भेजी जाती है, ताकि कोड पैकेट पर कहीं भी हो, मिल जाए। '
    + 'इसे काउंटर के एक हिस्से तक सीमित करना हो तो एक चौकोर खींच लो।',
  'till.hint.look':
    'शक्ल से पढ़ने में सिर्फ़ चौकोर भेजा जाता है — उसके बाहर का सब कुछ इसी ब्राउज़र में, भेजने से '
    + 'पहले ही हटा दिया जाता है। देखकर पहचानना एक अंदाज़ा है, इसलिए बिल में डालने से पहले उसे तीन '
    + 'फ़्रेम तक टिकना पड़ता है।',

  /* ---- the counter's own noises ------------------------------------------ */

  'till.sound.muted': '🔇 आवाज़ बंद — दबाकर चालू करो',
  'till.sound.on': '🔊 आवाज़ चालू',
  'till.sound.muted.title': 'काउंटर चुप है',
  'till.sound.on.title': 'काउंटर आवाज़ कर रहा है',
  'till.sound.test': 'आवाज़ जाँचो',
  'till.sound.test.title': 'पहचाना, फिर पता-नहीं, फिर पहले-से-बिल-में',
  'till.redraw': 'इलाक़ा दोबारा खींचो',
  'till.stop': 'बंद करो',

  /* ---- the bill ----------------------------------------------------------- */

  'till.bill.title': 'बिल',
  'till.bill.clear': 'मिटाओ',
  'till.bill.empty.1': 'काउंटर पर अभी कुछ नहीं है।',
  'till.bill.empty.2': 'पैकेट ऐसे पकड़ो कि उसका कोड कैमरे के सामने रहे।',
  'till.bill.total': 'कुल',
  'till.bill.toPay': 'देने हैं',
  'till.bill.oneFewer': '{name} एक कम',
  'till.bill.oneMore': '{name} एक और',
  'till.bill.drop': '{name} को बिल से हटाओ',
  'till.bill.drop.title': 'इसे बिल से हटाओ',

  /* ---- the charge button and what it refuses ------------------------------ */

  'till.charge.witnessing': 'काउंटर की गवाही ले रहा है…',
  'till.charge.nothing': 'काउंटर पर कुछ नहीं है',
  'till.charge.startCamera': 'पैसे लेने के लिए कैमरा चालू करो',
  'till.charge.show.one': 'इसे कैमरे को दिखाओ',
  'till.charge.show.other': 'इन्हें कैमरे को दिखाओ',
  'till.charge.pay': '{amount} लो',

  'till.book.action': 'खाते में लिखो',
  'till.book.title': 'यह बिल खाते में लिखो',
  'till.book.sub':
    'यह उधार है, बिना रंग का — न हरा, न पीला, न लाल। यह तभी घटेगा जब गेटवे का दस्तख़त वाला '
    + 'webhook कहेगा कि इस घर से पैसा आया।',
  'till.book.name': 'किसका खाता',
  'till.book.phone': 'फ़ोन नंबर',
  'till.book.phone.sub': 'एक घर, एक नंबर। याद Razorpay दिलाएगा; तुम कुछ नहीं भेजते।',
  'till.book.confirm': 'खाते में {amount}',
  'till.book.cancel': 'अभी नहीं',
  'till.book.working': 'खाते में लिख रहा है…',
  'till.book.done': 'खाते में {amount} · {name} · {phone}',
  'till.book.done.body':
    'कुछ नहीं आया, कुछ मना नहीं हुआ। इस घर पर अब <b>{outstanding}</b> बाकी है। खाता स्क्रीन पर '
    + 'COLLECT दबाओ, एक लिंक बनेगा; बाकी तभी घटेगा जब दस्तख़त वाला webhook आएगा।',
  'till.book.open': 'खाता खोलो',
  'till.book.new': 'नया घर',
  'till.book.proposed': 'सलाहकार कहती है: <b>{name}</b> ({phone}) के खाते में',
  'till.book.proposed.unknown':
    'सलाहकार कहती है: <b>{name}</b> के खाते में — इस नाम का खाता अभी नहीं है, नंबर पूछा जाएगा।',
  'till.book.accept': 'हाँ',
  'till.book.drop': 'छोड़ो',
  'till.book.needsWitness':
    'खाते में लिखने से पहले काउंटर बिल की तस्वीर लेता है — वही सबूत जो पैसे लेने के लिए चाहिए।',
  'till.charge.notInView': 'सामने नहीं है: {names}',
  'till.charge.missing.one':
    'कैमरे को अभी {names} नहीं दिख रहा। पैसे लेने का सबूत बनाने के लिए वो काउंटर की तस्वीर लेता है, '
    + 'इसलिए इस बिल की चीज़ उसे एक साथ <b>एक बार</b> दिखनी चाहिए — इसे लेंस के सामने कर दो, बटन '
    + 'तैयार हो जाएगा और तैयार ही रहेगा। उसके बाद इसे नीचे रख सकते हो।',
  'till.charge.missing.other':
    'कैमरे को अभी {names} नहीं दिख रहे। पैसे लेने का सबूत बनाने के लिए वो काउंटर की तस्वीर लेता है, '
    + 'इसलिए इस बिल की सारी चीज़ें उसे एक साथ <b>एक बार</b> दिखनी चाहिए — इन्हें लेंस के सामने कर दो, '
    + 'बटन तैयार हो जाएगा और तैयार ही रहेगा। उसके बाद इन्हें नीचे रख सकते हो।',
  'till.charge.ready':
    'काउंटर ने इस बिल की तस्वीर ले ली और सबूत रख लिया। अब सब नीचे रख दो — पैसे लेने का बटन तैयार है।',

  'till.refuse.notPhotographed': 'काउंटर की तस्वीर नहीं ली जा सकी',
  'till.refuse.cannotCharge': 'अभी इस काउंटर से पैसे नहीं लिए जा सकते',
  'till.refuse.putBack':
    'बटन दबाते ही काउंटर की तस्वीर ली जाती है। जिन चीज़ों का बिल बना रहे हो, उन्हें वापस कैमरे के '
    + 'सामने रखो और दोबारा दबाओ।',
  'till.refuse.disagree': 'काउंटर को {seen} दिख रहे हैं, बिल {bill} कह रहा है',
  'till.refuse.disagree.detail':
    'पैसे सिर्फ़ उसी के लिए जाते हैं जो कैमरा अभी देख पा रहा है। छूटे हुए पैकेट वापस सामने रखकर '
    + 'दोबारा दबाओ, या बिल नए सिरे से शुरू करने के लिए मिटाओ दबाओ।',

  /* ---- what the counter wrote down --------------------------------------- */

  'till.witness.heading': 'काउंटर ने क्या देखा',
  'till.witness.headingPay': 'काउंटर पर जो देखा गया',
  'till.witness.notTaught': 'सिखाया नहीं',

  /* ---- the pay screen ----------------------------------------------------- */

  'till.pay.title': 'पैसे का इंतज़ार',
  'till.pay.sub':
    'ग्राहक इसे किसी भी UPI ऐप से स्कैन करेगा। जब तक रेज़रपे का अपना दस्तख़त वाला वेबहुक यह न कह दे '
    + 'कि पैसा आ गया, यहाँ कुछ भी हरा नहीं होता।',
  'till.pay.qrTitle': 'पैसे देने के लिए स्कैन करें',
  'till.pay.scanWithUpi': 'किसी भी UPI ऐप से स्कैन करें',
  'till.pay.qrAlt': '{amount} का पेमेंट QR',
  'till.pay.renderNote':
    'यह तस्वीर उसी पेमेंट लिंक की छवि है जो गेटवे ने बनाया। यहाँ कोई UPI पेलोड नहीं बनाया जाता।',
  'till.pay.link': 'लिंक',
  'till.pay.session': 'सेशन',
  'till.pay.cancel': 'रहने दो — काउंटर पर वापस',
  'till.pay.waiting': 'गेटवे का इंतज़ार',
  'till.pay.stopped': 'जाँचना बंद किया — यह लिंक अब भी चालू है',
  'till.pay.noRecord': 'पैसे वाली सेवा के पास इस सेशन का कोई रिकॉर्ड नहीं — रद्द करके दोबारा माँगो',
  'till.pay.qrRefused': 'पेमेंट QR नहीं बन सका',
  'till.pay.qrRefused.detail': 'काउंटर अपने ही सर्वर तक नहीं पहुँच पाया कि वजह पूछ सके।',

  /* ---- nothing is reaching this counter ---------------------------------- */

  'till.inbound.title': 'इस काउंटर तक गेटवे की कोई ख़बर नहीं आ रही',
  'till.inbound.never': 'इस काउंटर तक आज तक किसी क़िस्म का कॉलबैक नहीं पहुँचा।',
  'till.inbound.last': 'इस काउंटर तक पहुँचा आख़िरी कॉलबैक <b>{ago}</b> आया था।',
  'till.inbound.none': 'हाल में इस काउंटर तक कोई कॉलबैक नहीं पहुँचा।',
  'till.inbound.since': 'यह लिंक बने {s} सेकंड हो गए, इस बीच कुछ नहीं आया।',
  'till.inbound.mayHavePaid':
    '<b>हो सकता है ग्राहक ने पैसे दे भी दिए हों।</b> इतने से यह स्क्रीन हरी नहीं हो सकती, क्योंकि '
    + 'इसे हरा सिर्फ़ रेज़रपे का अपना दस्तख़त वाला कॉलबैक करता है — और वो कॉलबैक यहाँ तक पहुँच नहीं '
    + 'रहा। रेज़रपे डैशबोर्ड में वेबहुक का पता देखो कि वो अब भी इस काउंटर की टनल पर लगा है या नहीं; '
    + 'क्विक टनल हर बार चालू होने पर नया पता ले लेती है।',
  'till.inbound.stillPayable':
    'ऊपर वाला लिंक दोनों हाल में चालू रहता है, और देर से आया कॉलबैक भी इस सेशन को निपटा देगा। '
    + 'इंतज़ार करने में कुछ नहीं जाता — पर रास्ता खुले बिना कुछ बदलेगा भी नहीं।',

  /* ---- the paid moment ---------------------------------------------------- */

  'till.paid.word': 'पैसे मिल गए',
  'till.paid.body':
    'दस्तख़त जाँचे हुए वेबहुक ने इसी सेशन और इसी रक़म को मिलाया। यह स्क्रीन सिर्फ़ इसी से आती है।',
  'till.return.fromPaid': 'इस बिल का कोई सामान वापस लो',

  /* ---- how this counter decides ------------------------------------------- */

  'till.decides.title': 'यह काउंटर कैसे तय करता है',
  'till.decides.code': 'कोड बिल में डालता है',
  'till.decides.code.v': 'पहली साफ़ पढ़त पर',
  'till.decides.look': 'देखी हुई चीज़ डालता है',
  'till.decides.look.v': '3 टिके हुए फ़्रेम के बाद',
  'till.decides.forget': 'पैकेट भूल जाता है',
  'till.decides.forget.v': '4 फ़्रेम न दिखने पर',
  'till.decides.rate': 'हर सेकंड कितनी बार देखता है',
  'till.decides.note':
    'पहचान सिर्फ़ दाम <b>सुझाती</b> है। सेशन को चुकता सिर्फ़ दस्तख़त जाँचा हुआ वेबहुक कर सकता है — '
    + 'यह पन्ना पेमेंट रोक सकता है, दे कभी नहीं सकता।',

  /* ------------------------------------------------------- the teach screen --
     `routes/Products.tsx`. These were the last English literals left in a
     translating file: 35 of them, all on the screen where a shopkeeper does
     the hardest thing this program asks — deciding what a packet IS. A till
     that speaks Hindi at the counter and English at the moment of teaching
     is a till that speaks Hindi decoratively. */
  'products.teach.title': 'नया सामान सिखाएँ',
  'products.teach.nameEg': 'पारले-जी बिस्कुट 100 ग्राम',
  'products.draw': 'सामान के चारों ओर डिब्बा खींचें',
  'products.captured': 'खींच ली',
  'products.show': 'पैकेट कैमरे के सामने रखें',
  'products.show.sub': 'सिखाने से पहले झलक बता देती है कि कोड पढ़ा जा रहा है या नहीं।',
  'products.camera.dead': 'कैमरा चालू नहीं हुआ',
  'products.checking': 'जाँच हो रही है',
  'products.retake': 'दोबारा लें',
  'products.stopCamera': 'कैमरा बंद करें',
  'products.checkingBox': 'आपका खींचा डिब्बा जाँचा जा रहा है',
  'products.backToLive': 'वापस चालू कैमरे पर',
  'products.catalogue': 'सामान की सूची',
  'products.reading': 'सूची पढ़ी जा रही है',
  'products.tryAgain': 'फिर कोशिश करें',
  'products.emptyCatalogue': 'अभी कुछ नहीं सिखाया',
  'products.codeOnly': 'सिर्फ़ कोड',
  'products.noMm': 'नाप नहीं',
  'products.corrected': 'सुधार हो गया।',
  'products.viewAdded': 'नया रूप जुड़ गया।',
  'products.legend': 'गोलियों और बटनों का मतलब',
  'products.bar': 'इतना पार करना ज़रूरी है।',
  'products.views': 'रूप',
  'products.edit': 'बदलें',
  'products.f.name': 'नाम',
  'products.f.price': 'दाम रुपये में',
  'products.f.code': 'छपा हुआ कोड',
  'products.cancel': 'रहने दें',
  'products.chain.reading': 'हिसाब की कड़ी पढ़ी जा रही है',
  'products.chain.empty': 'कड़ी पर अभी कुछ नहीं',
  'products.choosePicture': 'एक तस्वीर चुनें',
  'products.noPhoto':
    '<b>फ़ोटो की ज़रूरत नहीं।</b> आपने जो कोड लिखा है, यही इस सामान की पहचान होगी। कैमरे से पैकेट पर का कोड पढ़वाना हो तो यह ख़ाना ख़ाली कर दें।',
  'products.gate.camera':
    'कैमरे से सिखाने में <b>आठ फ़्रेम</b> लगते हैं और हर एक की चमक, धुँधलापन और फ़ोकस पर जाँच होती है। जो सबसे साफ़ बचता है वही रखा जाता है, और अगर कोई नहीं बचा तो कुछ भी नहीं रखा जाता — इसे ज़बरदस्ती पार करने का कोई रास्ता नहीं, क्योंकि जिस जाँच को हाथ हिलाकर पार किया जा सके वह जाँच नहीं, सजावट है।',
  'products.gate.upload':
    'एक अकेली अपलोड की हुई फ़ाइल की इस तरह जाँच <b>नहीं</b> हो सकती: जाँच फ़्रेमों को आपस में मिलाकर देखती है, और अकेली फ़ाइल के पास मिलाने को कुछ है ही नहीं। यह हिफ़ाज़त चाहिए तो कैमरे से सिखाएँ।',
  'products.pill.views.one': '1 रूप',
  'products.pill.views.other': '{n} रूप',
  'products.addView': '+ रूप',
  'products.adding': 'जोड़ा जा रहा है…',
  'products.forget': 'भुला दें',
  'products.legend.views': 'रूप',
  'products.legend.edit': 'बदलें',
  'products.legend.addView':
    '<b>+ रूप</b> पहले से सिखाए हुए सामान की दूसरे कोण से तस्वीर लेकर उसे भी याद रखता है। पैकेट के कई चेहरे होते हैं और एक सिखाया रूप एक ही चेहरा है, इसलिए दूसरा और तीसरा रूप ही काउंटर को उसे बग़ल में पड़ा या घुमा हुआ पहचानने देते हैं। पैकेट घुमाइए, फिर दबाइए। दाम और नाम कभी नहीं बदलते।',
  'products.legend.gates':
    'यह काउंटर देखकर <n>{phi}</n> कोसाइन पर पहचानता है, और सिर्फ़ तस्वीर से सिखाए सामान को <n>{appearance}</n> की ऊँची कसौटी पर परखता है — उसमें एक पहचान, मिलीमीटर में उसका असली नाप, मौजूद नहीं होता। आगे वाले को दूसरे नंबर से <n>{theta}</n> आगे भी रहना पड़ता है, वरना काउंटर दोनों के नाम लेकर पूछता है। ये आँकड़े इसी काउंटर से पढ़े जाते हैं, यहाँ छपे हुए नहीं हैं: जो पन्ना कसौटी याद रखता है वह एक दिन उसी मशीन को झुठलाएगा जिसकी वह खिड़की है।',
  'products.legend.gates.loading':
    'इस काउंटर से कसौटियाँ पढ़ी जा रही हैं…',
  'products.legend.gates.none':
    'इस काउंटर ने अपनी कसौटियाँ नहीं बताईं{why}, इसलिए यहाँ कोई आँकड़ा नहीं छापा गया। सेटिंग्स भी वही आँकड़े उसी जगह से पढ़ती है।',
  'products.legend.codeOnly':
    '<b>सिर्फ़ कोड</b> का मतलब है कि यह सामान उसके छपे नंबर से सिखाया गया, इसलिए उसकी शक्ल के बारे में कुछ नहीं रखा गया और कैमरा उसे देखकर नहीं पहचान सकता — उसका कोड दिखाइए, या तस्वीर से दोबारा सिखाइए। <b>रूप</b> बताता है कि काउंटर ने उसे कितने कोणों से देखा है; एक रूप सिर्फ़ उसी चेहरे को पहचानता है जिसकी तस्वीर आपने ली थी।',
  'products.legend.edits':
    '<b>बदलें</b> से नाम, दाम और छपा कोड बदलते हैं, और कुछ नहीं छूता — न सिखाए हुए रूप, न मिलीमीटर, न तस्वीर, और SKU पहचान तो कभी नहीं, जिस पर पुराने बिल और ऑर्डर टिके हैं। दाम का बदलाव दुकान की अपनी हिसाब-कड़ी में पुराने और नए दोनों दाम के साथ लिखा जाता है, ताकि पिछले हफ़्ते का बिल आज भी समझाया जा सके।',
  'products.legend.forget':
    'सामान भुलाने से वह सब मिट जाता है जिससे उसका दाम लग सकता था — बंधन, वेक्टर और दाम — इसलिए जो कोड पहले उसका नाम लेता था अब किसी का नाम नहीं लेगा। अगर सिर्फ़ दाम या नाम ग़लत है तो उसे <b>बदलें</b>: एक नई तस्वीर से दोबारा सिखाने में दो अक्षर ठीक करने के लिए हर रूप, हर मिलीमीटर और ख़ुद तस्वीर फेंक दी जाती है, और सामान के पास कई चेहरों की जगह एक ही चेहरा बचता है।',
  'products.mode.code':
    'कोड से',
  'products.mode.code.t':
    'बारकोड या QR — लिखा हुआ, या पैकेट से पढ़ा हुआ',
  'products.mode.photo':
    'तस्वीर से',
  'products.mode.photo.t':
    'सादी तस्वीर, बिना तख़्ती: सिर्फ़ शक्ल',
  'products.mode.mat':
    'तख़्ती पर',
  'products.mode.mat.t':
    'छपी हुई तख़्ती: असली मिलीमीटर भी जुड़ते हैं',
  'products.f.sku':
    'SKU पहचान',
  'products.f.name.sub':
    'जो दुकानदार बिल पर पढ़ता है',
  'products.f.price.sub':
    'पूरे पैसे में रखा जाता है; दशमलव मंज़ूर नहीं, गोल भी नहीं किया जाता',
  'products.f.code.label':
    'बारकोड या QR नंबर',
  'products.f.code.optional':
    'बारकोड या QR नंबर (चाहें तो)',
  'products.f.code.sub':
    'लकीरों के नीचे के अंक लिखें, या ख़ाली छोड़ दें और कैमरे को पढ़ने दें',
  'products.f.code.read':
    'कैमरे ने पैकेट से पढ़ा — {code}',
  'products.src.file':
    'फ़ाइल चढ़ाएँ',
  'products.src.camera':
    'कैमरा इस्तेमाल करें',
  'products.pic.none':
    'कोई तस्वीर नहीं चुनी',
  'products.pic.alt':
    'जो सामान सिखाना है',
  'products.teach.go':
    'यह सामान सिखाएँ',
  'products.teach.busy':
    'सिखाया जा रहा है…',
  'products.fine.code':
    'कोड इस SKU को एक पहचान से बाँध देता है। इसकी शक्ल के बारे में कुछ नहीं रखा जाता, और न ही ज़रूरत है।',
  'products.fine.photo':
    'सादी तस्वीर सिर्फ़ शक्ल सिखाती है: न मिलीमीटर, न नाप की जाँच, और इसकी भरपाई के लिए मिलान की कसौटी ज़्यादा सख़्त।',
  'products.fine.mat':
    'छपी हुई तख़्ती काउंटर को असली पैमाना देती है, इसलिए सामान अपने सही नाप के साथ मिलीमीटर में रखा जाता है।',
  'products.close':
    'बंद करें',
  'products.forgetting':
    'भुलाया जा रहा है…',
  'products.pill.codes.one': 'कोड',
  'products.pill.codes.other': '{n} कोड',
  'products.burst': 'तस्वीरें ली जा रही हैं — स्थिर रखें · {n} में से {at}',
  'products.lookingForCode': 'कोड ढूँढा जा रहा है…',
  'products.stock.onhand': 'शेल्फ़ पर',
  'products.stock.notCounted': 'गिना नहीं',
  'products.stock.online': 'ऑनलाइन',
  'products.stock.noFigure': 'स्टॉक का आँकड़ा नहीं — बिना सीमा के बिकता है',
  'products.stock.available': '{n} बिक सकते हैं',
  'products.stock.out': 'ऑनलाइन स्टॉक ख़त्म',
  'products.stock.held': '{open} खुले ऑर्डर में · {delivered} गिनती के बाद डिलीवर',
  'products.stock.heldOpen': '{open} खुले ऑर्डर में',
  'products.stock.heldDelivered': '{delivered} गिनती के बाद डिलीवर',
  'products.stock.floorIs': '{n} रोक कर रखे हैं',
  'products.stock.count': 'अभी गिनती',
  'products.stock.count.go': 'दर्ज करें',
  'products.stock.count.empty': 'शेल्फ़ पर कितने हैं, पूरे पैकेट में लिखें।',
  'products.stock.floor': 'रोक कर रखें',
  'products.stock.floor.go': 'तय करें',
  'products.stock.floor.same': 'यही सीमा पहले से है।',
  'products.stock.floor.sub':
    'रोक कर रखें: शेल्फ़ पर इतने बचते ही ऑनलाइन बिक्री रुक जाती है, ताकि काउंटर के लिए बचे रहें। 0 का मतलब आख़िरी पैकेट भी बिकेगा।',
  'products.stock.saving': 'सहेजा जा रहा है…',
  'products.stock.noFigures': 'स्टॉक के आँकड़े पढ़े नहीं जा सके, इसलिए ऑनलाइन कोई सीमा नहीं लगी: {why}',
  'products.stock.reserve':
    'ऑनलाइन ऑर्डर रखते ही उसके पैकेट आरक्षित हो जाते हैं, पर जब तक आप पैक न करें गिनती से कुछ नहीं घटता। डिलीवर हुआ ऑर्डर तब तक घटा रहता है जब तक आप शेल्फ़ दोबारा न गिनें।',
  'auth.chip.none': 'यहाँ साइन-इन नहीं',
  'auth.chip.none.short': 'साइन-इन नहीं',
  'auth.chip.create': 'खाता बनाएँ',
  'auth.chip.create.short': 'खाता',
  'auth.chip.signIn': 'साइन इन करें',
  'auth.chip.out': 'साइन इन नहीं',
  'till.cam.off.lead':
    'जब तक आप शुरू न करें, कुछ भी नहीं भेजा जाता।',
  'till.cam.off.upload':
    '<b>इस मशीन से क्या बाहर जाता है:</b> कोड पढ़ने पर कैमरे की पूरी तस्वीर भेजी जाती है, ताकि कोड पैकेट पर कहीं भी हो, मिल जाए। इसे छोटा करना हो तो एक चौकोर खींच दें — आपके खींचे चौकोर के बाहर का सब कुछ इसी ब्राउज़र में, भेजने से पहले ही, हटा दिया जाता है।',
  'till.redraw.title.on':
    'काउंटर का इलाक़ा वापस पूरी तस्वीर कर दें',
  'till.redraw.title.off':
    'जब तक कैमरा नहीं चल रहा, दोबारा खींचने को कोई इलाक़ा नहीं है।',
  'till.sweep.title.off':
    'पहले कैमरा चालू करें — काउंटर को पढ़ने के लिए कुछ है ही नहीं।',
  'till.sweep.title.busy':
    'यह काउंटर पहले से पढ़ा जा रहा है। एक बार में एक ही बार।',

  /* ------------------------------------------------------- books · today -- */

  'today.recon.title': 'गल्ले और गेटवे का हिसाब जहाँ नहीं मिलता',
  'today.recon.clear': 'मिल गया',
  'today.recon.nothing': 'आज अभी मिलाने को कुछ है ही नहीं',
  'today.recon.nothing.detail':
    'आधी रात से न कोई बिल बंद हुआ है, न कोई वेबहुक आई है, इसलिए कोई जाँच हुई ही नहीं। इसका मतलब यह नहीं कि हिसाब मिलता है — आज पैसे के रास्ते से कुछ माँगा ही नहीं गया, तो उसके बारे में कुछ पता भी नहीं है।',
  'today.recon.none': 'आज गल्ले और गेटवे का हिसाब मिलता है',
  'today.recon.none.detail':
    'आज जो भी बिल बंद हुआ, उसके लिए गेटवे से लिंक माँगा गया, कोई वेबहुक नामंज़ूर नहीं हुआ, और बिना वेबहुक के कुछ भी चुकता दर्ज नहीं है। जो पैसा अभी आना बाकी है वह नीचे लिखा है — वह गड़बड़ नहीं, कतार है।',
  'today.recon.unavailable': 'यह काउंटर अभी खुद की जाँच नहीं कर पा रहा',
  'today.recon.unavailable.detail':
    'मिलान का जवाब नहीं आया, इसलिए हिसाब मिलता है या नहीं, इस बारे में कुछ नहीं कहा जा रहा। ऊपर के आँकड़े दिन के ब्यौरे के हैं और उन पर कोई फ़र्क़ नहीं पड़ा। जो नहीं है वह जाँच है, बिक्री नहीं।',
  'today.recon.split': 'आज, असल में जो हुआ उसके हिसाब से',
  'today.recon.billed': 'बिल बना',
  'today.recon.settled': 'चुकता — पक्की वेबहुक पर',
  'today.recon.settled.none': 'आज कुछ नहीं',
  'today.recon.settled.never': 'आज तक कुछ नहीं',
  'today.recon.linksent': 'लिंक गया, पैसा नहीं आया',
  'today.recon.nolink': 'बंद हुआ, लिंक बना ही नहीं',
  'today.recon.refused': 'काउंटर ने पैसा लेने से मना किया',
  'today.recon.unwitnessed': 'चुकता, पर वेबहुक की लाइन नहीं',
  'today.recon.owed': 'अभी बाकी',
  'today.recon.channel': 'कहाँ से बना',
  'today.recon.ch.till': 'गल्ला',
  'today.recon.ch.storefront': 'ऑनलाइन दुकान',
  'today.recon.ch.unnamed': 'दोनों में से कोई नहीं — यह काउंटर बता नहीं सकता',
  'today.recon.lifetime': 'इस काउंटर पर आज तक का पूरा हिसाब',

  'today.empty.title': 'आज का हिसाब',
  'today.empty.head': 'आज अभी तक कोई बिल नहीं बना',
  'today.empty.body':
    'आधी रात से अब तक कोई टोकरी बंद नहीं हुई, इसलिए जोड़ने को कुछ है ही नहीं। पहला बिल बंद होते ही आँकड़े यहाँ आ जाएँगे — तब तक कोई शून्य नहीं दिखाया जाता, क्योंकि यहाँ का शून्य बिल्कुल उस दिन जैसा दिखता जिस दिन कुछ बिका ही न हो।',
  'today.empty.action': 'गल्ला खोलें',
  'today.empty.yesterday.one':
    'कल का हिसाब {amount} पर बंद हुआ, एक बिल में।',
  'today.empty.yesterday.other':
    'कल का हिसाब {amount} पर बंद हुआ, {n} बिलों में।',

  /* ------------------------------------------------ आपका सामान (दुकान) -- */

  'shopitems.title': 'आपका सामान',
  'shopitems.blurb':
    'दुकान में जो कुछ बिकता है, सब यहाँ। कैमरे के बिना नया सामान जोड़ें, नाम या दाम ठीक करें, फोटो लगाएँ, और बताएँ कि शेल्फ़ पर कितने हैं।',

  'shopitems.stat.products': 'सामान',
  'shopitems.stat.nophoto': 'फोटो नहीं',
  'shopitems.stat.nophoto.sub': 'ग्राहक को देखने के लिए कुछ नहीं',
  'shopitems.stat.unseen': 'कभी फोटो नहीं खिंची',
  'shopitems.stat.unseen.sub': 'कैमरा इन्हें नहीं पहचान सकता',
  'shopitems.stat.counted': 'गिने हुए',
  'shopitems.stat.counted.sub': 'शेल्फ़ का आँकड़ा है',

  'shopitems.list.title': 'शेल्फ़',
  'shopitems.list.sub': 'बदलने के लिए सामान पर दबाएँ',
  'shopitems.search': 'नाम, आईडी या बारकोड खोजें',
  'shopitems.filter.all': 'सब',
  'shopitems.filter.nophoto': 'फोटो नहीं',
  'shopitems.filter.unseen': 'कभी नहीं देखा',
  'shopitems.load.failed': 'सूची पढ़ी नहीं जा सकी',
  'shopitems.retry': 'फिर कोशिश करें',
  'shopitems.empty.title': 'शेल्फ़ पर अभी कुछ नहीं',
  'shopitems.empty.body':
    'दाम लगाने के लिए यहाँ सामान जोड़ें, या Products स्क्रीन पर फोटो खींचें ताकि कैमरा भी उसे पहचान सके।',
  'shopitems.nomatch.title': 'कुछ नहीं मिला',
  'shopitems.nomatch.body':
    'जो आपने लिखा, उससे यहाँ कोई सामान नहीं मिलता। सब देखने के लिए बॉक्स ख़ाली करें।',
  'shopitems.notcounted': 'गिना नहीं',
  'shopitems.onhand': 'शेल्फ़ पर {n}',
  'shopitems.nophoto': 'फोटो नहीं',

  'shopitems.how.mat': 'मैट पर',
  'shopitems.how.look': 'देखकर',
  'shopitems.how.code': 'कोड से',
  'shopitems.how.typed': 'टाइप किया',

  'shopitems.add.title': 'नया सामान जोड़ें',
  'shopitems.add.sub': 'न कैमरा, न मैट — बस नाम और दाम',
  'shopitems.add.open': 'सामान जोड़ें',
  'shopitems.add.lead':
    'रात ग्यारह बजे शेल्फ़ पर रखी चावल की बोरी बेचने के लिए पहले फोटो खींचना ज़रूरी नहीं होना चाहिए। नाम और दाम लिखिए, वह दुकान पर आ जाएगी।',
  'shopitems.add.go': 'शेल्फ़ पर रखें',
  'shopitems.add.fine':
    'ऐसे जोड़ने पर काउंटर सिर्फ़ नाम और दाम सीखता है, यह नहीं कि सामान दिखता कैसा है — कैमरा इसे नहीं पहचानेगा। समय मिले तो Products स्क्रीन पर फोटो खींच लें।',
  'shopitems.add.done': '{name} शेल्फ़ पर आ गया',
  'shopitems.add.derived': 'आईडी नाम से बनी है',
  'shopitems.add.filed': '{name} में रखा गया।',
  'shopitems.add.notfiled': 'जुड़ गया, पर किसी श्रेणी में नहीं रखा जा सका: {why}',
  'shopitems.add.counted': 'गिनती दर्ज: शेल्फ़ पर {n}।',
  'shopitems.add.notcounted': 'जुड़ गया, पर गिनती दर्ज नहीं हुई: {why}',
  'shopitems.cancel': 'रहने दें',

  'shopitems.f.name': 'नाम',
  'shopitems.f.name.sub': 'जो बिल पर आप पढ़ते हैं और दुकान पर ग्राहक',
  'shopitems.f.name.eg': 'बासमती चावल 5 किलो',
  'shopitems.f.price': 'दाम',
  'shopitems.f.price.sub': 'रुपये, जैसे आप लिखते हैं: 12 या 12.50',
  'shopitems.f.category': 'कहाँ रखा है',
  'shopitems.f.category.sub': 'वह शेल्फ़ जहाँ ग्राहक ढूँढेगा',
  'shopitems.f.category.none': 'कहीं नहीं रखा',
  'shopitems.f.stock': 'अभी शेल्फ़ पर',
  'shopitems.f.stock.sub': 'पूरे पैकेट, अगर आपने गिने हों',
  'shopitems.f.code': 'बारकोड',
  'shopitems.f.code.sub': 'लकीरों के नीचे का नंबर, अगर पैकेट पर हो',
  'shopitems.f.code.edit': 'यह बॉक्स ख़ाली करने पर इस सामान के सारे कोड हट जाएँगे',
  'shopitems.f.id': 'इसकी आईडी',
  'shopitems.f.id.sub':
    'ख़ाली छोड़ें तो नाम से बन जाएगी। बाद में यह कभी नहीं बदली जा सकती।',
  'shopitems.f.id.auto': 'नाम से बनेगी',
  'shopitems.f.photo': 'फोटो',
  'shopitems.f.photo.sub': 'दुकान पर दिखती है। इससे कैमरे को कुछ नहीं सिखाया जाता।',
  'shopitems.f.photo.alt': 'इस सामान के लिए चुनी गई तस्वीर',
  'shopitems.f.count': 'गिनती',

  'shopitems.photo.toobig':
    'यह तस्वीर लगभग {n} MB की है और सीमा 8 MB है। छोटी साइज़ में दोबारा खींचें।',
  'shopitems.photo.unreadable': 'यह फ़ाइल तस्वीर के रूप में पढ़ी नहीं जा सकी।',
  'shopitems.photo.stored': 'तस्वीर रख ली गई।',
  'shopitems.photo.removed': 'तस्वीर हटा दी गई।',
  'shopitems.photo.choose': 'तस्वीर चुनें',
  'shopitems.photo.remove': 'तस्वीर हटाएँ',
  'shopitems.photo.fine':
    'काउंटर जिस चीज़ से मिलान करता है, तस्वीर उसका हिस्सा नहीं — इसे बदलने से टिल का कोई फ़ैसला नहीं बदलता।',

  'shopitems.g.basics': 'नाम, दाम और बारकोड',
  'shopitems.g.photo': 'फोटो',
  'shopitems.g.category': 'कहाँ रखा है',
  'shopitems.g.stock': 'शेल्फ़ पर कितने',

  'shopitems.edit.permanent':
    'आईडी कभी नहीं बदलती — अब तक छपा हर बिल उसी की ओर इशारा करता है।',
  'shopitems.edit.save': 'सहेजें',
  'shopitems.edit.nochange': 'कुछ अलग नहीं था, इसलिए कुछ लिखा नहीं गया।',
  'shopitems.edit.saved':
    'सहेजा गया: {what}। यह दुकान की अपनी चेन पर है, पुराना और नया दोनों मान लिखे हुए।',
  'shopitems.edit.unbound': 'ये कोड अब इस सामान का दाम नहीं बताते: {codes}।',
  'shopitems.history.show': 'इसका दाम क्या-क्या रहा',
  'shopitems.history.none': 'इस सामान में अभी तक कुछ नहीं बदला।',

  'shopitems.cat.file': 'यहाँ रखें',
  'shopitems.cat.filed': '{name} में रखा गया।',
  'shopitems.cat.cleared': 'जिस शेल्फ़ पर था, वहाँ से हटा दिया।',
  'shopitems.cat.none':
    'अभी कोई श्रेणी नहीं है। Categories स्क्रीन पर एक बनाएँ, फिर इसे उसमें रखा जा सकेगा।',

  'shopitems.stock.record': 'गिनती दर्ज करें',
  'shopitems.stock.fine':
    'यह दोबारा गिनती है: यह आँकड़ा बदल देती है और उससे पहले की सारी आवाजाही रद्द कर देती है। सिर्फ़ पूरे पैकेट।',
  'shopitems.close': 'बंद करें',
  'till.pay.qrRefused.stillPayable':
    'पेमेंट लिंक बन तो गया है और उससे पैसा दिया जा सकता है — बस यह काउंटर उसका QR नहीं बना सका। CANCEL दबाकर काउंटर पर लौटें और दोबारा चार्ज करें।',
  'till.pay.qrRefused.notGateway':
    'यह पता गेटवे का नहीं है, इसलिए इससे कोई पैसा नहीं दिया जा सकता। अगर यह काउंटर सिम्युलेटर पर चल रहा है (RZP_MODE=sim) तो यही होना था: नकली लिंक जान-बूझकर देने लायक नहीं बनाए जाते। असली लिंक के लिए RZP_MODE=live रखें। CANCEL दबाकर काउंटर पर लौटें।',

  /* ---- काउंटर पर सलाहकार ------------------------------------------------- */

  'till.sk.title': 'सलाहकार',
  'till.sk.sub': 'ऑर्डर बोलो, या दाम पूछो',
  'till.sk.state.idle': 'काउंटर पर',
  'till.sk.state.listening': 'सुन रही है',
  'till.sk.state.thinking': 'सोच रही है',
  'till.sk.state.speaking': 'बोल रही है',
  'till.sk.state.voicing': 'आवाज़ ला रही है',
  'till.sk.listen': '🎤 सुनो',
  'till.sk.stop': 'सुनना बंद',
  'till.sk.placeholder': 'लिखो — "दो मैगी और एक पारले जी" — या पूछो: "पारले जी का दाम?"',
  'till.sk.send': 'उसे बताओ',
  'till.sk.langs': 'भाषा',
  'till.sk.idle':
    'सुनो दबाकर ऑर्डर बोलो, या लिख दो। सामान से पहले गिनती हो तो वह बिल पर प्रस्ताव बनकर आता है; '
    + 'सवाल हो तो वह बोलकर जवाब देती है।',
  'till.sk.listening': 'सुन रही है। ऑर्डर बोलो — "दो मैगी और एक पारले जी" — या दाम पूछो।',
  'till.sk.heard': 'सुना',
  'till.sk.typed': 'लिखा',
  'till.sk.route.order': 'ऑर्डर',
  'till.sk.route.advice': 'सवाल',
  'till.sk.route.order.v': 'बिल पर प्रस्ताव की तरह रखा है, आपके मानने के लिए',
  'till.sk.route.advice.v': 'बोलकर जवाब दिया; बिल जैसा था वैसा है',
  'till.sk.route.refused.v': 'नाम लेकर मना किया; बिल पर कुछ नहीं',
  'till.sk.why.shop_word': 'दाम या दुकान का शब्द',
  'till.sk.why.question_word': 'सवाल का शब्द',
  'till.sk.why.nothing': 'इसमें कोई सामान नहीं',
  'till.sk.why.add_verb': '"जोड़ो" जैसा शब्द',
  'till.sk.why.weight': 'सामान से पहले वज़न',
  'till.sk.why.count': 'सामान से पहले गिनती',
  'till.sk.why.several': 'दो या ज़्यादा सामान, कोई सवाल नहीं',
  'till.sk.why.one_bare': 'अकेला एक नाम',
  'till.sk.reread.as_question': 'उसने इसे ऑर्डर नहीं, सवाल पढ़ा — बिल पर कुछ नहीं रखा।',
  'till.sk.reread.as_order': 'कॉल ने इसे ऑर्डर कहकर मना किया, तो उसने इसे गल्ले पर रख दिया।',
  'till.sk.put.one':
    'बिल पर <b>प्रस्ताव</b> की तरह रखा: 1 लाइन, {total}। जब तक आप वहाँ मानें नहीं, कुछ बिल नहीं हुआ।',
  'till.sk.put.other':
    'बिल पर <b>प्रस्ताव</b> की तरह रखा: {n} लाइनें, {total}। जब तक आप वहाँ मानें नहीं, कुछ बिल नहीं हुआ।',
  'till.sk.check': 'यह देख लो',
  'till.sk.answer': 'उसका जवाब',
  'till.sk.saying': 'बोल रही है',
  'till.sk.refused': 'वह यह नहीं कर सकी',
  'till.sk.byVoice': 'अपनी आवाज़ में',
  'till.sk.byBrowser': 'इस ब्राउज़र की आवाज़ में',
  'till.sk.voiceRefused': 'उसकी आवाज़ नहीं आ सकी, इसलिए इस ब्राउज़र ने पढ़ा: {why}',
  'till.sk.muted': 'काउंटर म्यूट है, इसलिए वह दिखती है, सुनाई नहीं देती।',
  'till.sk.noMic': 'यह ब्राउज़र सुन नहीं सकता',
  'till.sk.noMic.hint': 'ऑर्डर लिख दो — बाकी सब यहाँ बिना माइक के चलता है।',
  'till.sk.micStopped': 'माइक रुक गया',
  'till.sk.disclose':
    'ब्राउज़र बोली को अपनी सेवा से लिखता है, इसलिए <b>आवाज़ इस मशीन से बाहर जाती है</b>। उसका बोला हुआ '
    + 'जवाब गल्ले की आवाज़-सेवा से एक-एक वाक्य लाया जाता है (वह बंद हो तो यह ब्राउज़र पढ़ता है)। काउंटर की '
    + 'तस्वीर, सामान की सूची और दाम कभी बाहर नहीं जाते।',
  'till.sk.never':
    'वह प्रस्ताव रखती है, आप मानते हैं। CHARGE आपका बटन है — यहाँ बोला या लिखा कुछ भी पैसे की सेवा तक '
    + 'नहीं पहुँचता।',

  /* ---- बिल पर प्रस्ताव --------------------------------------------------- */

  'till.bill.proposed.pill': 'प्रस्ताव',
  'till.bill.proposed.count.one': '{n} लाइन आपके इंतज़ार में',
  'till.bill.proposed.count.other': '{n} लाइनें आपके इंतज़ार में',
  'till.bill.proposed.acceptAll': 'सब मानो',
  'till.bill.proposed.dropAll': 'सब हटाओ',
  'till.bill.proposed.accept': 'मानो',
  'till.bill.proposed.drop': '{name} को प्रस्ताव से हटाओ',
  'till.bill.proposed.heard': 'सुना “{heard}”',
  'till.bill.proposed.respelt': 'सुना “{heard}” — काउंटर ने इसे अंग्रेज़ी अक्षरों में लिखकर यह ढूँढा',
  'till.bill.proposed.weighed': 'तौला: {weight}',
  'till.bill.proposed.onBill': 'यह बिल पर पैकेट में पहले से है। इसका वज़न मानने से पहले वे हटाओ।',
  'till.bill.proposed.notCounted': '+ {amount} प्रस्ताव में, कुल में नहीं',
  'till.bill.proposed.hint':
    'पीला रंग मतलब रुकना: सलाहकार ने ये रखे हैं, अभी किसी ने माने नहीं। मानो दबाने से लाइन बिल में जाती '
    + 'है; CHARGE से पहले कैमरे को वह दिखनी ज़रूरी है।',
  'till.bill.held.title':
    'सलाहकार ने इस काउंटर के लिए लाइनें रोक रखी थीं',
  'till.bill.held.arrived':
    'उसने कहीं और रोकी हुई {n} लाइन(ें) बिल पर प्रस्तावित हैं — नीचे मंज़ूर करें या हटाएँ।',
  'till.bill.held.moved':
    'प्रस्ताव के बाद दाम बदल गया — {list}। बिल आज का दाम लेता है।',
  'till.bill.held.gone':
    'छोड़ दी गईं, अब सूची में नहीं: {list}।',
  'till.bill.held.noCatalogue':
    'उसने {n} लाइन(ें) रोकी थीं, पर दाम लगाने के लिए सूची पढ़ी नहीं जा सकी। वे रुकी रहेंगी।',
  'till.bill.held.heard':
    'सलाहकार ने रखी',
  'till.bill.held.repriced':
    'सलाहकार ने रखी · आज का दाम',
  'till.bill.held.ok':
    'ठीक है',
};