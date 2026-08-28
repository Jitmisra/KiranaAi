# RUKJA — DECISION DOCUMENT
### Razorpay AI Buildathon 2026 · solo builder · 10 days · one submission

---

## 0. VERIFICATION PASS — read this before §1

All 15 repos cited in §12 were cloned to `reference/`. **All 15 exist.** Two corrections to the body of this document, found by reading the code rather than the search results:

### 0.1 — `saiprasad4/aadesh` is much closer prior art than §5 says. This is good news, handled correctly.

§5 describes it as "the deterministic half." Reading the source, it is more than that. It already ships:

| What aadesh already has | Evidence on disk |
|---|---|
| A pinned failure-code table, **~298 codes** across eNACH + UPI Autopay, each row with a `verified` flag and a `source` | `src/codes/data.ts` (329 lines), `src/codes/handling.ts` (213) |
| **Primary-source provenance already cited**: NPCI UPI Error & Response Codes v2.9, NPCI circular NACH-006-FY-24-25, **NPCI/UPI/OC-151A** (the ₹1L no-AFA list), RBI E-mandate Framework 2026 | `README.md` § Data provenance |
| Two-tier confidence grading (`verified: true` = corroborated by authority; `false` = single-vendor Razorpay overlay) | same |
| `decideRetry` with the rail attempt cap, terminal-code stop, and **"unrecognized code is NOT retried — we refuse to guess with money"** | `src/retry/policy.ts` (92) |
| AFA ceiling check that flags rather than blocks | `src/compliance/limits.ts` (107) |
| Contact-window scheduling + pre-debit notification | `src/compliance/schedule.ts` (175), `notification.ts` (87) |
| Mandate/debit state machines that throw on illegal transitions; injectable `Clock`; pure and deterministic | `src/state/machine.ts`, tests for all of it |

**Consequence: the citation-graded code table is NOT the moat. It is a dependency.** Building your own worse version of a 298-code table in ten days, without crediting the one that exists, is the single fastest way to lose this. §6's `codes/` directory should become a thin wrapper over `aadesh` plus your citation-CI layer.

**What genuinely remains yours** — verified absent from aadesh by grep:

1. **Measurement. Entirely.** `grep -c 'http' src/compliance src/retry` → **0**. No URLs in the compliance code, so no citation CI. No eval harness, no control arm, no metrics, no batch, no numbers of any kind anywhere in the repo. The prevented ledger, the compliance tax and the three-tier claim structure remain 100% unbuilt by anyone.
2. **Exactly-once under concurrency.** aadesh is a pure library — no DB, no nonce, no write-ahead intent, no pool. It defines an idempotency *key* in `src/reconcile/types.ts` and stops there. It cannot provide exactly-once, structurally. Your kernel is untouched ground.
3. **Driving the real Razorpay sandbox.** aadesh never calls Razorpay.

**And it is a gift.** §11 Q3 asks where you get an opponent you did not write. Here it is: real, public, MIT, well-written, credited by name, with a genuine hole (no concurrency control) that is a *missing default* and not an author error — exactly the framing Beat 5b already scripts. **1 commit**, created 2026-07-06 — almost certainly a single AI-assisted dump, which is also why the hole is there. Answer Q3 as **(a) Fork**, target `aadesh`, and say all of this out loud.

### 0.2 — The MCC correction in §6 is partly wrong. Verify before you say it on camera.

§6 says "MCC 7322 is Collection Agencies… **delete 7322 and 6529 from every list**" and that G04 must key off a merchant-declared `mandate_purpose` enum, not MCC. But aadesh cites **NPCI/UPI/OC-151A** as *"the ₹1L no-AFA MCC list"* — i.e. the carve-out mechanism **is** MCC-based on the UPI rail, even if those two specific codes are wrong.

Most likely both are right about different things: the RBI E-mandate Framework names *use cases*, while NPCI's operating circular implements them as an **MCC allow-list**. Do not assert either version until you have quoted OC-151A directly. **Add this to §14 Q5 as a third contested number.** Getting a confident correction wrong in the one area you claim as expertise is worse than never raising it.

### 0.3 — Unchanged and confirmed

`razorpay/razorpay-mcp-server` has the documented **Remote Server Support** column, and `create_refund`, `close_qr_code`, `create_instant_settlement`, `create_registration_link` are all in it as remote-unsupported — run the local Docker image. It has no policy engine, no spend caps, no approval gate, no audit trail. `Shopify/toxiproxy`, `ethz-spylab/agentdojo`, `sierra-research/tau2-bench`, `grip-unina/TruFor`, `ZeqinYu/STFL-Net` all present as described.

---

## 1. THE CALL

**Track 03 — AI Revenue Recovery.**
**Project: Rukja** (रुक जा — "stop").
**Tagline, used verbatim everywhere: "Rukja — the recovery agent whose first decision is whether to charge at all."**
Build a subscription/mandate recovery agent whose primary screen is the ledger of debits that **did not happen**, each row carrying the rule that stopped it, the primary-source URL, the exact quoted sentence, and an honest grade of how strong that source is.
Headline metrics are counts of refused actions (facts, no model) plus a within-system A/B "compliance tax" — never a recovery-parity claim.

**The one-line reason:** every other Track 03 submission optimises the one direction the merchant P&L can see (retry more, contact more), and the cost of that direction lands on the customer's bank statement where nobody measures it — so the only defensible position left against a company that already shipped a Subscription Recovery Agent is the brake, priced and cited.

---

## 2. WHY THIS TRACK AND NOT THE OTHERS

| Track | Likely competition | What ~everyone submits | Call |
|---|---|---|---|
| **01 — AI Growth & Agentic Commerce** | **Highest.** This is where the press release is: Razorpay + NPCI launched agentic UPI payments on Claude on 20 Feb 2026 with Zomato, Swiggy, Zepto on UPI Reserve Pay (razorpay.com/blog/agentic-payments-and-npci/). | Claude Desktop or Cursor pointed at the hosted MCP server, `create_payment_link` + `create_order` wrapped in a Next.js chat UI (vercel/ai-chatbot template → ~40% of entries look pixel-identical), a system prompt saying "always confirm before payment", and the chat confirmation called an audit trail. | **No.** You compete with the judges' own live product on the one axis (UI shine) where a solo builder has no edge. The genuinely open ground here is the safety/authority layer under the MCP server, which is a Track 05 project wearing a Track 01 badge and reads as rubric-gaming. |
| **02 — AI Risk Manager** | **High, and mostly wasted.** | Chargeback evidence responders (a thinner version of Razorpay's shipped Dispute Responder Agent) and fraud scorers trained on the ULB `creditcard.csv` Kaggle set (284,807 rows, 492 frauds, 0.172% base rate, PCA'd features V1–V28) reporting "99.9% accuracy". | **No, as primary.** Two hard blockers: disputes **cannot be created in Razorpay test mode by any documented path**, so 100% of the data is synthetic and the oracle is your own generator; and any from-scratch fraud scorer sits next to Vulcan (launched 18 Aug 2026, ~3 trillion data points from 4 billion payments, ~3,000 signals/txn) and Thirdwatch (300+ params, <200ms). **Kept as the backup** — see §11 — because one sub-direction is genuinely empty. |
| **03 — AI Revenue Recovery** | **High volume, low ceiling.** A GitHub sweep in the dossier reported 91 repos for `razorpay revenue recovery agent` — **flag: that count is not independently reproducible; a hostile judge running the obvious query gets a different number. Never say it out loud.** | An LLM writing dunning emails inside a LangGraph pipeline, "recovered ₹X" as the headline, no control arm, no compliance layer, no cost side. | **YES.** It is the only loss loop Razorpay lets you drive end-to-end in test mode with real webhooks (Plans + Subscriptions + the Dashboard charge-outcome toggle), and the entire field pushes one direction, so inverting the objective function is genuinely differentiating rather than cosmetic. The Indian regulatory layer is a real, citable, machine-checkable moat nobody else will build. |
| **04 — AI Finance Controller** | **Most contested, and the median is already good.** Same-day GitHub search: `razorpay buildathon` = **287 repos**; `"AI Finance Controller" razorpay` = **30 repos**, nearly all created 20–26 Aug 2026. | "Deterministic matcher + LLM tail + refusal semantics + honest metrics" is now the *baseline*, not the differentiator. Named competitors already publish multi-seed evals with refusal counts: aviralgarg05/milaan, poreddynarendra2006-debug/cash-application-agent, niy-ati/recon-engine. | **No.** Razorpay's Agentic Dashboard already does bank-statement → UTR extraction → settlement cross-reference (FTX'26, March 2026). To win you'd need a second axis (the statutory tax leg, or provable optimality) — and the statutory leg has a trap: a plain PG settlement carries fee + GST-on-fee only, not 194-O or s.52 TCS, because those bind the e-commerce operator, not the aggregator (CBDT Circular 17/2020). |
| **05 — Open Track** | Unknown, probably thin. | Anything. | **No.** Same execution bar with none of the track-specific scaffolding. Track 03's stated bar — "measured money recovered across a BATCH, compliant escalation, stopping rules, audit trail" — is a checklist that Rukja hits literally, four for four. Filing this in Open forfeits that alignment. |

**The decisive asymmetry:** Track 03's sandbox affordances are the best in the competition. Razorpay's own docs (razorpay.com/docs/payments/subscriptions/test/) describe a test-mode "Charge this now" button that **prompts you to choose success or failure**, driving `active → pending → (4 consecutive failures) → halted` with real `subscription.charged` / `.pending` / `.halted` / `.activated` webhooks and a real attempt counter. Nothing else in the sandbox lets you drive a money-loss loop end to end. Disputes cannot be created at all. Downtime records almost certainly return empty. Settlements are community-reported not to generate against a 0.00 test balance.

---

## 3. THE PROJECT

### What it is

A subscription/mandate recovery agent that ingests live Razorpay webhooks, classifies each failure against a **pinned, checksum-verified, citation-graded** failure-code table, and runs every proposed money action through seven deterministic gates before anything executes. Its primary interface is the **prevented ledger** — one row per action refused, with the reason code, the instrument that stopped it, the exact quoted sentence from the primary source, and a strength grade (`STATUTE` / `REGULATOR_CIRCULAR` / `NETWORK_RULE` / `VENDOR_DOC` / `INDUSTRY_NORM` / `SELF_IMPOSED`).

Underneath sits an exactly-once kernel: a debit for a given `(subscription_id, cycle, amount_paise, attempt_ordinal)` executes once or never, and a gateway timeout **never retries blind** — it transitions to `RETRIEVE` and reconciles by `fetch_payment` first.

### The problem, with sourced numbers

- **UPI AutoPay is broken at scale.** Business declines average **~74% across the top 50 banks**, and **20 million+ mandates are revoked every month** because the customer's account is short at debit time — Business Standard, 7 Sep 2025, citing NPCI data and unnamed industry sources: https://www.business-standard.com/finance/news/upi-autopay-revocations-hit-20-mn-monthly-over-low-customer-balances-125090700500_1.html
  > ⚠ **FLAG — VERIFY BEFORE THE VIDEO.** The 74% figure very likely misreads *BD-share-of-declines* as a *decline rate*; UPI AutoPay execution success is nowhere near 26%. Pull NPCI's Autopay Ecosystem Statistics table (https://www.npci.org.in/what-we-do/autopay/ecosystem-statistics) by hand, screenshot the column header, and quote whatever it literally says. npci.org.in returns **HTTP 403** to automated fetchers — snapshot it into the repo with a checksum. The safe neighbouring claim from the same article is the 20M revocations.
- **NACH e-mandate rejection has roughly doubled.** Share of mandates rejected out of total processed through NPCI's Mandate Management System: **28% (FY2017-18) → 45% (FY2020-21) → 55% (FY2025-26 to Nov 2025)**. NACH debit volume nearly doubled over the same period: 99.3 crore txns (FY22) → 197 crore (FY25); value ₹9.5 lakh cr → ₹21.9 lakh cr. FACTLY on NPCI data, 16 Jan 2026: https://factly.in/nach-e-mandates-scale-up-but-rejections-rise/
- **Sponsor-bank decline rates, July 2025:** DCB 53.2%, Indian Bank 44.3%, IndusInd 42.0%, Federal 41.0%, RBL 40.8%. YES Securities via ETBFSI, 10 Sep 2025: https://bfsi.economictimes.indiatimes.com/articles/rising-loan-repayment-decline-rates-signal-financial-strain-for-dcb-bank-and-others/123797689
- **Razorpay publishes its own leak:** "Nearly 30% of subscribers drop off before registration is completed. Around 20% of subsequent debits fail... close to 18% of active subscribers cancel mandates" and "involuntary churn accounts for nearly 30% of subscriber attrition." https://razorpay.com/blog/upi-autopay-with-intelligent-revenue-protect/ (12 Mar 2026)
- **The cost nobody prices:** every failed NACH debit triggers a bank bounce charge **borne by the customer**. SBI ₹250, ICICI ₹500, + 18% GST (₹590 all-in on ICICI). **RBI does not fix this amount — the customer's bank does.** Cite each bank's own published Schedule of Charges PDF, never a blog, and always say the RBI-doesn't-set-it sentence out loud.
- **Chasing money the bank must return is itself a liability.** RBI/2019-20/67 (DPSS.CO.PD No.629/02.01.014/2019-20, 20 Sep 2019, effective 15 Oct 2019) mandates auto-reversal — UPI merchant T+5, UPI P2P T+1, IMPS T+1, card POS T+5 — with **₹100/day compensation paid suo moto**, no complaint required. https://www.rbi.org.in/Scripts/NotificationUser.aspx?Id=11693
  > ⚠ **SCOPE IT.** That circular's T+5 row covers *"account debited but transaction confirmation not received at merchant location"* and defines a failed transaction as one not completed *"due to any reason not attributable to the customer."* Insufficient funds **is** attributable to the customer, and a declined mandate debits nothing. Scope the gate strictly to debited-but-unconfirmed states, print the scope on screen, and ship a passing **negative test** proving it doesn't fire on `insufficient_funds`.

### What it does, end to end

1. **Ingest.** Webhook receiver on a real host verifies HMAC-SHA256 over the **raw bytes** (never parse before verifying — Razorpay's docs say this in bold), dedupes on `x-razorpay-event-id`, and folds events into a state machine that tolerates `payment.captured` arriving before `payment.authorized` (Razorpay documents that order is not guaranteed).
2. **Classify.** Failure reason → one of six classes via a pinned YAML table with a checksum, a citation and a pytest case per row: `RETRIABLE_LIQUIDITY` (NACH 04) · `RETRIABLE_TECHNICAL` (NACH 59) · `MANDATE_DEAD` (14, 61, reject 21) · `ACCOUNT_DEAD` (01, 02, 60) · `DISPUTE` (06) · `UNKNOWN`. Unknown → never guess → human queue.
3. **Gate.** Seven pure functions return `ALLOW | DEFER | NEVER` with a citation, a `strength` grade, a `binds_whom` party and an `enforcement_locus` (`RUKJA` / `UPSTREAM` / `ADVISORY`).
4. **Price.** A flat conjugate Beta-Binomial per `(bank, code_class, attempt)`, primed from the NPCI bank-wise Autopay table, decides ALLOW-now vs DEFER-to-window. Integer paise arithmetic; no floats anywhere in the money path (CI-enforced).
5. **Execute exactly once.** Write-ahead intent + nonce under a `UNIQUE` index → **commit and release the connection** → call the gateway → on indeterminate result, `RETRIEVE` and reconcile.
6. **Record.** Append-only hash-chained event log with a `verify-ledger` command that recomputes from genesis.
7. **Parse replies.** One LLM call, temperature 0, strict Pydantic schema, deterministic regex+dateparser fallback that is counted in the metrics.
8. **Report.** Prevented ledger, compliance tax, exception list, and a sensitivity sweep with its failure region printed.

### The single wow moment

`make attack SEED=8471293`. Fifty concurrent identical debit requests for the same billing cycle, against a gateway sitting behind toxiproxy's `timeout` toxic at `timeout=0` — a silent black hole that accepts and never answers.

Left pane: a **real, public, third-party Razorpay dunning agent, credited by name, unmodified**, racking up 4 duplicate debits on one cycle. Right pane: the same repo with Rukja's 20-line adapter — **1 charge, 49 `nonce_burned`**, and a live connection-pool gauge that **stays flat at ~1/64** while 50 calls hang in the void. That flat gauge is the proof the kernel commits the intent, burns the nonce, releases the connection, *then* calls the gateway. Then the toxic is removed, the gateway reveals the charge actually succeeded, the reconciler confirms one payment ID, zero duplicates, `debits − reversals − ledger_net = 0 paise`, and the independent auditor prints `WRONGFUL_ACTIONS: 0`.

Then attempt 5 is refused, with the honest caveat on the same row: *"scope: merchant-driven retry loops on the Payments API. Razorpay Subscriptions halts at 4 on its own rail."*

---

## 4. WHY IT WINS — mapped to the four stated criteria

### Criterion 1 — Problem taste: did you pick something that actually matters

- **It attacks a cost that is structurally invisible.** The bounce charge lands on the *customer's* bank statement. The merchant P&L never shows it, so no recovery agent has ever had a reason to compute it. Being the first person to put a number on it is problem taste demonstrated, not asserted.
- **It refuses the collision.** Razorpay shipped four production agents on the Claude Agent SDK at FTX'26 on 12 March 2026 — Abandoned Cart Conversion, **Dispute Responder**, **Subscription Recovery** (voice, with ElevenLabs), and Cashflow Forecaster — plus Intelligent Revenue-Protect for UPI Autopay with a configurable retry engine (razorpay.com/blog/upi-autopay-with-intelligent-revenue-protect/). Rukja names their agent, concedes it optimises the right thing, and differentiates on the axis they demonstrably did not ship: what recovery cost the customer and what compliance cost the merchant.
- **The IPO framing is real and citable.** Razorpay filed confidential draft IPO papers in June 2026. A pre-IPO payment aggregator does not need an agent that recovers more; it needs one that can prove, per decision, that it did not break a rule.

### Criterion 2 — Build quality: does it run, is it structured, would you trust it

- `make judge` runs on a clean clone in **under 4 minutes, with zero credentials and zero network** — it replays committed fixtures. That is the single highest-value build-quality signal available, because the biggest measured score differentiator across every hackathon-judging source is simply *did the demo run*.
- Money is `int` paise end to end with a CI lint that fails the build on a `float` in the money module.
- The kernel design answers the crash question rather than hiding it: intent row committed and nonce burned **before** the gateway call, reconciler heals in-flight rows on startup, `kill -9` mid-refund produces exactly one charge.
- Webhooks: HMAC over raw bytes, `x-razorpay-event-id` dedupe, out-of-order-tolerant projection with a Hypothesis commutativity test over shuffled event sequences. All three hazards are documented by Razorpay; handling them correctly is an unmistakable signal you read their docs.
- Hash-chained append-only ledger with an independent `verify-ledger` that recomputes from genesis and prints the head hash and checkpoint count.
- Kill switch writes an auditable event **and** a `HALT` sentinel file; the process refuses to start ALLOW-capable if the file exists. A killed system cannot restart into ALLOW.
- **`make verify-claims`** regenerates every number in the README from `results/CLAIMS.json` and fails on drift, which makes the sentence *"no number in this submission was typed by hand"* literally true and one command away from being checked.
- Razorpay's own internal bar for "Agent Ready" is 80%+ across Context (AGENTS.md, current docs), Testing (unit/integration/e2e, contract docs), CI/CD. Ship an AGENTS.md and a green CI badge where the check is `verify-citations`.

### Criterion 3 — AI judgment, including where you deliberately did NOT use one

This is the criterion almost nobody answers well, and Rukja answers it with an **ablation, not a paragraph**.

**The LLM does exactly one job in the runtime path:** free-text or Hinglish customer reply → strict JSON. `claude-haiku-4-5`, temperature 0, structured outputs, one retry, then a deterministic `regex + dateparser` fallback whose firing rate is **printed in the metrics table**.

**The schema is the security boundary.** Its five fields are `{intent, promise_to_pay_date, hardship_flag, dispute_flag, opt_out_flag}` — **none of which can authorise, schedule, or size a debit.** Model output can only *stop* or *delay* money movement. So the worst outcome of a successful prompt injection is a wrongful stop, whose cost is measured; the probability of an injection causing a wrongful *debit* is 0 by construction, not by prompt hygiene.

**Nine places with no model, each with a reason:**

| # | Decision | Mechanism | Why not an LLM |
|---|---|---|---|
| 1 | Failure reason → class | Pinned YAML, checksum-verified | Classification over a contested corpus must never be a generation task. This is the NACH-01 incident. |
| 2 | Retry timing | Beta-Binomial + integer arithmetic | A model that computes EV can be wrong by 10× and sound confident. |
| 3 | Compliance gating | Seven pure functions | A system prompt can be argued into calling at 21:00. An arithmetic gate cannot. |
| 4 | Message content | DLT template ID + declared variables; renderer raises on unknown var | TCCCPR requires a pre-registered template — free-form copy is made *structurally impossible*, not discouraged. |
| 5 | "Did the gateway call succeed?" | Ledger + `fetch_payment` | Never summarise a money outcome. Read it. |
| 6 | Account prioritisation | Deterministic EV sort | Reproducible ordering; replays byte-identical. |
| 7 | Money arithmetic, ceilings, dates | integer paise, `zoneinfo` | Floats and LLMs are both banned from money. |
| 8 | **The entire eval harness** | Pure functions on a frozen tape | **Zero LLM calls in the measurement path** — that's what makes `make judge` byte-reproducible. |
| 9 | Unknown reason code | `UNKNOWN` → NEVER → human queue | Guessing is the failure mode that caused the incident. |

**And the killer row in the ablation table:** `− code table → LLM classifies`. Replacing the pinned table with retrieval + generation dunned *K* closed accounts out of *M* — a wrongful-action rate of *X* per thousand. That converts the war story into a measured number.

Two citations do the load-bearing work here. arXiv **2512.13040** measured direct LLM prompting on tabular fraud rows at **F1 = 0.00, MCC = −0.01** on ULB. arXiv **2402.08115** (ICLR 2025) found LLM self-critique *collapses* performance on verification tasks while a sound external verifier produces large gains. Together they are the empirical licence for "the LLM proposes, deterministic code disposes."

**Declared openly, not hidden:** Claude Code wrote most of this repo, and a script drafts candidate citation rows — but every row must be human-confirmed by pasting the exact quoted span, and CI verifies that span is still live at that URL. LLM proposes; CI disposes.

### Criterion 4 — Failure recovery

They read this first, so it is engineered to be three layers deep and unfakeable — see §10 for the full text of all three candidates. The structure:

1. **A gate I built was wrong, and my own control arm caught it** (the T+5 misapplication). Self-incriminating on the exact axis the project claims as its moat; the measurement apparatus paid for itself; the fix is a negative test a judge watches go green.
2. **My classifier learned a wrong fact from a well-ranked blog and dunned closed accounts** (NACH 01). Architecture-causing, with a dated deletable commit a judge can click.
3. **The exactly-once kernel was a textbook anti-pattern and the black-hole test found it in nine seconds** (transaction held across a network call, pool exhausted). The fix is visible as a flat line on a gauge.

Plus the operationally-ordinary one in `FAILURES.md`: the token-TTL schedule collision and the webhook domain blacklist. One clean architectural arc reads as written-after-the-fact; one clean arc plus one dumb operational scar reads true.

---

## 5. THE COMPETITIVE PICTURE

### What everyone else submits

- **The AI-slop signature.** A judge with 20+ events named the 2025-26 patterns: every project's design looks identical because of vibe coding; "feature stuffing" — 8-10 features that don't connect; "no one does machine learning anymore, everything is LLM-based" with no justification for non-determinism; and "it could have been a form."
- **Real 2026 Indian fintech hackathon specimens** to calibrate against: `akshat333-debug/FinTwin-Lite` ("10 autonomous AI agents", claims ₹58,409/month savings and +25% survival probability from 7,000 Monte Carlo runs, a "Cinematic AI Terminal"); `Pulkit7070/TrustAI` ("AI Agent Swarm", GNN on a 21-node graph, latency table where 5 of 6 agents run at "~1ms" — i.e. they are if-statements in agent costumes).
- **The median Track 03 entry:** CrewAI (~5.2M monthly downloads) financial-crew template with invoices swapped for tickers, an LLM writing dunning emails, one number at the end, no control arm, no compliance layer, no cost side, 3–14 commits all in one night.

### What already exists in this space, and what each does NOT do

| Repo | Stars | What it is | What it does NOT do |
|---|---|---|---|
| **razorpay/razorpay-mcp-server** https://github.com/razorpay/razorpay-mcp-server | 230 ⭐, 35 forks, Go, MIT, pushed 2026-08-25 | Official MCP server, ~45 tools: `create_order`, `create_payment_link`, `create_payment_link_upi`, `capture_payment`, `create_refund`, `fetch_settlement_recon_details`, `fetch_tokens`, `revoke_token`, `detect_stack`, `integrate_razorpay_checkout` | **No policy engine, no spend caps, no approval gate, no idempotency layer, no audit trail. Zero dispute tools** despite the Disputes API existing. `create_refund`, `close_qr_code`, `create_instant_settlement`, `create_registration_link` are **blocked on the hosted remote server** — you must run the local Docker image. |
| **saiprasad4/aadesh** https://github.com/saiprasad4/aadesh | 0 ⭐, created 2026-07-06, MIT, TypeScript | The only real prior art on the primitive: normalised eNACH/UPI AutoPay error dictionary, mandate + debit state machines that throw on illegal transitions, a `decideRetry` respecting the 1+3 cap, and a `reconcile()` that catches the async-return-vs-retry race | **No agent, no batch, no measurement, no outreach, no audit UI, no LLM anywhere, zero adoption.** It is the deterministic half. Rukja is the other half plus the measurement. |
| **tonbistudio/leakplug-public** https://github.com/tonbistudio/leakplug-public | 5 ⭐, created 2026-06-29 | Stripe-native revenue-recovery operator: audits leaked revenue, designs A/B plays, gates on deterministic guardrails + human approval, executes real Stripe test-mode actions | Stripe-only, US-shaped. No UPI, no mandates, no NACH, no RBI/TRAI constraints, **no control group or uplift measurement**, no voice. Messages are drafts only — nothing is ever sent, so it never faces the compliance problem. |
| **merrttopal/recoup** https://github.com/merrttopal/recoup | 0 ⭐, created 2026-07-29, MIT | Java/Spring dunning engine on Postgres only (Postgres as DB + job queue via `FOR UPDATE SKIP LOCKED` + scheduler). Gateway-agnostic behind a port; provider decline codes normalised | Retry schedule is **static per-merchant config**; smart retry timing is explicitly still on the roadmap. No intelligence, no uplift, no outreach, no Indian rails, no measured numbers, one Turkish gateway adapter. |
| **ajithmanmu/dunning-system** https://github.com/ajithmanmu/dunning-system | 0 ⭐ | Tiered Stripe dunning on AWS Step Functions. Hard declines cancel immediately with no retries; VIP retries Day 1/3/7. Ships a script that creates real Stripe test customers and triggers genuine declines | Tier is a hand-seeded DynamoDB row, not a model. Fixed day-offsets, no per-decline-code timing, no salary-cycle logic, **no control arm**, no India. |
| **ZERO34802/paysentinel** https://github.com/ZERO34802/paysentinel | 0 ⭐, Google Cloud hackathon entry | Autonomous payment-failure *investigation*: Gemini plans, calls Elastic MCP over JSON-RPC, builds ES\|QL against live telemetry, compares to a 7-day baseline, files a pre-diagnosed Jira ticket | **Diagnosis only — it never recovers a rupee.** No execution, no bounded workflow, no stopping rules, no batch metric, no compliance. |
| **Pracheta007/priya-sarvam-collections-agent** https://github.com/Pracheta007/priya-sarvam-collections-agent | 0 ⭐ | Multilingual BFSI collections voice bot, 100% no-code n8n, Sarvam TTS/STT + sarvam-105b, 6-scenario playbook, JSON extraction to Google Sheets | **Telephony is simulated** — customer audio is a pre-recorded Drive clip. Single exchange per call, no identity verification, no real payment execution, no batch, no measured recovery. |
| **stevenfackley/syzm** https://github.com/stevenfackley/syzm | 0 ⭐ | ML-driven retry-window prediction, FastAPI inference service, Supabase ingest, Next.js portal, pg_cron loop | Author states it is "implementation-ready but not production-complete": **webhook signature verification is a TODO and processor retry calls are stubs.** No measured outcomes, US timezone assumptions. |
| **uber/causalml** https://github.com/uber/causalml | 5,961 ⭐ | S/T/X/R-learners, uplift trees, AUUC/Qini | A library. No payments domain, no policy layer, no notion of contact fatigue or compliance windows. |
| **Shopify/toxiproxy** https://github.com/Shopify/toxiproxy | 12,077 ⭐, MIT, Go | TCP fault injection. Control API on 8474. Toxics: `latency` (with jitter), `bandwidth`, `timeout` (**`timeout=0` = silent black hole**), `reset_peer`, `slicer`, `limit_data`, `slow_close`, global `toxicity`. <100µs overhead when idle. In production at Shopify since Oct 2014 | **It only breaks the network.** It cannot corrupt semantics — duplicate webhooks, out-of-order settlement rows, a replayed mandate token. Rukja adds a semantic injector on top; nobody in the 287-repo field has attempted that. |

### The five specific ways Rukja is not that

1. **The headline is a count of refusals, not an estimate of recovery.** Counts of things the code refused to do are facts; estimates of money you'd otherwise have lost are not. Nobody else will draw that line.
2. **A within-system A/B that makes the simulator cancel.** Kernel ON vs OFF on the *identical* tape and seeds. The compliance tax survives the "your simulator is your prior" objection by construction.
3. **Citation CI.** Every rule stores the exact quoted sentence; CI asserts it is still present at that URL. Presence of a URL is a formality; presence of the sentence is a control.
4. **Self-graded evidence.** `strength` / `binds_whom` / `enforcement_locus` rendered in the UI, including rules graded `SELF_IMPOSED` and gates badged `REDUNDANT-ON-THIS-RAIL`. Volunteering "which of my gates actually prevents anything" pre-empts the hostile question.
5. **A real forked baseline, not a strawman.** Beating an opponent you wrote is worth nothing. If no usable public agent exists, that null finding gets published as a table — which is a better slide than a fork.

---

## 6. ARCHITECTURE

### Stack (chosen for demo reliability, not novelty)

| Layer | Choice | Why |
|---|---|---|
| Language | Python **3.12** (not 3.13 — wheel availability) | Razorpay SDK, Pydantic, Hypothesis all stable |
| Deps | **uv**, `uv.lock` committed | Sub-second reproducible installs from a clean container |
| DB | **PostgreSQL** `postgres:16.4-alpine`, digest-pinned | Real transactional isolation + `UNIQUE` index; makes the connection-pool gauge story real (SQLite can't show pool pressure) |
| Driver | SQLAlchemy 2.0 + psycopg 3.2, `pool_size=10, max_overflow=10` | Explicit sizing so the gauge means something |
| API | FastAPI + uvicorn | |
| HTTP | httpx, `Timeout(connect=3, read=10)`, **no transport-level retry on POST** | |
| Money | integer **paise**, `NewType('Paise', int)`; `float` banned by CI lint | |
| Time | `zoneinfo` + a `Clock` protocol (`RealClock` / `VirtualClock`) | All gates take `now` as an argument |
| Property tests | Hypothesis | Kernel invariants + projection commutativity |
| Faults | `ghcr.io/shopify/toxiproxy:2.12.0` | `timeout=0` black hole |
| Browser | Playwright (chromium) | Hosted checkout auth + Dashboard charge-outcome |
| UI | Jinja2 + **HTMX 2.0 (vendored)** + SSE + **uPlot (vendored)** | **Zero node build step. Nothing to break on demo day.** |
| LLM | `claude-haiku-4-5`, temperature 0, one call site | |
| Exec substrate | `razorpay/mcp` Docker image (**local, not remote**) | Wrapped, not replaced. Fallback: direct REST. |

### Repo tree

```
rukja/
├── README.md                     # see §10
├── FAILURES.md                   # they read this FIRST
├── EVIDENCE.md                   # every number → source + repro command
├── LIMITATIONS.md                # where this loses, with numbers
├── PREREGISTRATION.md            # committed BEFORE the D7 run
├── BASELINES.md                  # fork selection protocol + rejected candidates
├── Makefile                      # judge, eval, attack, replay, real-leg, verify-*
├── docker-compose.yml            # postgres + toxiproxy + app, digest-pinned
├── pyproject.toml  uv.lock  alembic.ini  .env.example
├── seeds.txt                     # committed BEFORE any results exist
│
├── rukja/
│   ├── clock.py                  # Clock protocol; RealClock, VirtualClock
│   ├── money.py                  # Paise NewType; no floats
│   ├── ids.py                    # UUIDv7 + canonical JSON (RFC 8785-style)
│   ├── config.py                 # env → typed settings; refuses HALT sentinel
│   │
│   ├── codes/                    # ── THE MOAT
│   │   ├── model.py  loader.py  classes.py
│   │   ├── razorpay_reasons.yaml # the 106 documented reason strings
│   │   ├── nach_return_codes.yaml
│   │   ├── upi_decline_codes.yaml
│   │   └── snapshots/            # cached primary-source HTML + .sha256 (403 sources)
│   │
│   ├── gates/                    # ── seven pure functions
│   │   ├── base.py  registry.py
│   │   ├── g01_dead_code.py            g02_attempt_cap.py
│   │   ├── g03_tat_suppression.py      g04_afa_ceiling.py
│   │   ├── g05_contact_window.py       g06_consent_and_template.py
│   │   └── g07_predebit_notice.py      # ADVISORY ONLY — never blocks
│   │
│   ├── authority/                # mandate.py budgets.py killswitch.py approval.py
│   │
│   ├── kernel/                   # ── exactly-once
│   │   ├── intent.py             # write-ahead intent + nonce (UNIQUE)
│   │   ├── executor.py           # protocol
│   │   ├── exec_rest.py  exec_mcp.py  exec_localsim.py
│   │   ├── retrieve.py           # RETRIEVE reconciler worker
│   │   ├── gateway_semaphore.py  # bounded concurrency OUTSIDE the DB pool
│   │   └── pool_metrics.py       # samples pool every 100ms → SSE
│   │
│   ├── ledger/                   # events.py store.py verify.py projections.py prevented.py
│   ├── ev/                       # posterior.py policy.py priors/{npci csv,sha256,SOURCE.md}
│   ├── nlu/                      # schema.py llm.py fallback.py router.py   ← the ONLY LLM
│   ├── dlt/                      # registry.yaml render.py
│   ├── adjudicator.py            # POST /adjudicate
│   ├── api/                      # app.py adjudicate.py webhooks.py admin.py stream.py ui/
│   ├── harness/                  # real_leg.py pw_*.py tape.py replay.py fidelity.py
│   └── adapter/sidecar.py        # the ~20-line drop-in
│
├── audit/                        # INDEPENDENT auditor — forbidden to import gates/
│   ├── rulebook.yaml             # one row per rule, with quoted_span
│   └── checker.py
│
├── third_party/<forked-repo>/    # git submodule, credited by name
├── tapes/  fixtures/  results/  scripts/  tests/
```

### Deterministic core vs LLM edges

```
                        ┌─────────────────────────────────────────┐
   Razorpay webhooks ──▶ │  HMAC over RAW bytes → dedupe on        │
   (order-tolerant)      │  x-razorpay-event-id → event log        │  DETERMINISTIC
                        └────────────────┬────────────────────────┘
                                         │
                        ┌────────────────▼────────────────────────┐
   reason string ─────▶ │  codes/  pinned YAML + sha256           │  DETERMINISTIC
                        │  → RETRIABLE_* | *_DEAD | DISPUTE |     │  (no model, ever —
                        │    UNKNOWN → human queue                │   test asserts the
                        └────────────────┬────────────────────────┘   LLM is never called)
                                         │
                        ┌────────────────▼────────────────────────┐
                        │  gates/  7 pure functions               │  DETERMINISTIC
                        │  ALLOW | DEFER | NEVER + citation +     │  (arithmetic,
                        │  strength + binds_whom + locus          │   set membership,
                        └────────────────┬────────────────────────┘   date compare)
                                         │
                        ┌────────────────▼────────────────────────┐
                        │  ev/  Beta-Binomial(bank, class, attempt)│ DETERMINISTIC
                        │  integer paise, conformal lower bound    │ (statistics,
                        └────────────────┬────────────────────────┘  not generation)
                                         │
                        ┌────────────────▼────────────────────────┐
                        │  kernel/  intent → nonce → COMMIT →     │  DETERMINISTIC
                        │  RELEASE CONN → gateway → RETRIEVE      │  (a UNIQUE index
                        └────────────────┬────────────────────────┘   is the guarantee)
                                         │
                        ┌────────────────▼────────────────────────┐
                        │  ledger/  hash-chained, verify-ledger   │  DETERMINISTIC
                        └─────────────────────────────────────────┘

   ═══════════════════════ THE ONLY LLM EDGE ═══════════════════════

   customer free text ──▶  nlu/  claude-haiku-4-5, temp 0
                           ↓
                     ReplyIntent {intent, promise_to_pay_date,
                                  hardship_flag, dispute_flag,
                                  opt_out_flag}
                           ↓
                     Pydantic strict validate ──fail──▶ regex+dateparser
                           ↓                             (counted in metrics)
                     TWO-KEY RULE: LLM and fallback must agree
                     on action-relevant fields, else escalate

   ⚠ THE SCHEMA HAS NO ACTION FIELD.
     Model output can only STOP or DELAY money movement.
     Worst case of a successful injection = a wrongful STOP (measured, S3).
     P(injection → wrongful DEBIT) = 0 by construction.

   ═══════════════════ NON-RUNTIME LLM USE (declared) ═══════════════
   · Claude Code wrote most of this repo.
   · scripts/citation_draft.py drafts candidate codes/ rows.
     Human confirms the quoted span; CI verifies it is still live.
```

### The seven gates, corrected

| ID | Rule | Verdict | strength | binds_whom | locus |
|---|---|---|---|---|---|
| **G01** DEAD_CODE | class ∈ {`MANDATE_DEAD`, `ACCOUNT_DEAD`} → refuse permanently | NEVER | NETWORK_RULE | MERCHANT | **RUKJA** |
| **G02** ATTEMPT_CAP | 1 original + 3 retries per cycle | NEVER | see flag ↓ | SPONSOR_BANK | **UPSTREAM** |
| **G03** TAT_SUPPRESSION | **only** for debited-but-unconfirmed reasons → hold until reconciliation | DEFER | REGULATOR_CIRCULAR | SPONSOR_BANK | **RUKJA** |
| **G04** AFA_CEILING | >₹15,000 (or ₹1,00,000 for declared `mandate_purpose ∈ {INSURANCE_PREMIUM, MUTUAL_FUND, CREDIT_CARD_BILL}`) without AFA → AFA flow | NEVER | REGULATOR_CIRCULAR | ISSUER/MERCHANT | **RUKJA** |
| **G05** CONTACT_WINDOW | outbound contact 08:00–19:00 IST only | DEFER | **SELF_IMPOSED** | — | **RUKJA** |
| **G06** CONSENT_AND_TEMPLATE | registered DLT template + live consent required | NEVER | REGULATOR_CIRCULAR | TELCO | **UPSTREAM** |
| **G07** PREDEBIT_NOTICE | "unverifiable — issuer obligation; merchant cannot observe" | **FLAG only** | REGULATOR_CIRCULAR | **ISSUER** | **ADVISORY** |

Three corrections that are load-bearing and said out loud:

- **G04 keys off a merchant-declared `mandate_purpose` enum, not MCC.** RBI's E-mandate Framework 2026 (RBI/DPSS/2026-27/396, 21 Apr 2026, https://www.rbi.org.in/scripts/NotificationUser.aspx?Id=13374) para 8(b) names *use cases* — insurance premiums, mutual fund subscriptions, credit card bills. **MCC 7322 is Collection Agencies** (Visa, Oct 2022, mandatory Apr 2023) and has nothing to do with credit-card bill payment. **Delete 7322 and 6529 from every list.** Keep MCC only as a corroborating signal graded `SELF_IMPOSED`.
- **G05 is `SELF_IMPOSED`.** RBI/2022-23/108 (DOR.ORG.REC.65/21.04.158/2022-23, 12 Aug 2022) binds **recovery agents acting for regulated lenders**. A SaaS merchant collecting a subscription is not a lender. Saying "I chose to be stricter than the law here" is stronger than pretending it is the law.
- **G07 does not block.** Para 6(a)'s 24-hour pre-debit notification binds the **issuer**. A merchant-side sidecar can neither observe compliance nor discharge the duty. Refusing to block on a duty you cannot verify shows you read the applicability section, not just the operative sentence.

> ⚠ **G02 flag.** Public sources on the NPCI recurring retry cap conflict — "one attempt and three retries" vs "no hard cap per billing cycle" appear in the same search result set. If no primary NPCI circular span can be quoted, **downgrade `strength` to `INDUSTRY_NORM` and say so on screen.** Razorpay's own documented Subscriptions ladder (T+1, T+2, T+3, then `halted` after 4 consecutive failures) is separately citable and solid: https://razorpay.com/docs/payments/subscriptions/payment-retries/

---

## 7. MEASUREMENT

### Three tiers of claim, one provenance enum

Every number in the UI, README and video carries a tag from a closed six-value set, in the same column, same font:

`REAL` · `PLATFORM` · `PUBLIC-STAT` · `MODELLED` · `SIMULATED` · `SELF-IMPOSED`

**Tier 1 — FACTS.** Counts of money actions refused, itemised by instrument. A deterministic function of gate code over an immutable event log. Depends on no prior, no counterfactual, no outcome model. **This is the headline.**

**Tier 2 — WITHIN-SYSTEM A/B.** The compliance tax: kernel ON vs OFF on the *identical* tape and seeds. The simulator's realism enters both arms equally and cancels in the difference. **Second headline.**

**Tier 3 — SENSITIVITY, never a headline.** Recovery parity / attempts saved, swept across a 3×3 prior grid, with the failure region printed.

> The sentence that must be said on camera: *"Counts of things my code refused to do are facts. Estimates of money I would otherwise have lost are not. Here is which is which."*

### Data: two provenances, same table, same font

| | REAL | SIMULATED |
|---|---|---|
| N | **40 subscriptions** | ~4,000 account-cycles (10 tapes × 400) |
| Source | Razorpay test mode: Plans + Subscriptions API, hosted-checkout auth via Playwright, outcome chosen in the Dashboard "Charge this now" modal via Playwright | Frozen exogenous outcome tape, banks held out |
| Proves | The plumbing: real webhooks, real HMAC, real state machine, real token lifecycle | Batch-scale policy behaviour |
| Does **not** prove | Batch economics | Anything about real bank behaviour |

**The README/video sentence, verbatim:**
> "40 subscriptions were driven fully end-to-end through Razorpay's sandbox — that validates the state machine against live webhooks. Everything above 40 is a replay harness seeded from those 40. Razorpay's charge trigger is a Dashboard control, not an API, so a batch of 4,000 real charges is not physically available to me. Here is the boundary, and here is the fidelity check between the two."

Not Subscription Links, not Payment Links — both capped at **30 per business in test mode**, and UPI Payment Links are unsupported in test mode entirely.

**The real leg's ceiling, stated:** the platform halts at 4 consecutive failures, so the real leg **can never observe attempt ordinal ≥ 5**. Any claim about attempt 5+ is `SIMULATED` by construction.

### Tape generator — six layers

| Layer | What | Provenance |
|---|---|---|
| A | Payer liquidity: latent `salary_day`, periodic modulation of success probability. **The EV model cannot represent this** — deliberate misspecification. | MODELLED |
| B | Bank behaviour: per-remitter approval / BD / TD from the NPCI Autopay table, snapshotted with `fetch_npci.sh`, sha256, retrieval date **and a screenshot of the column headers** | PUBLIC-STAT |
| C | Outage bursts: Markov-modulated Poisson, emitted in the exact shape of Razorpay's `payment.downtime` entity (severity high/medium/low, typed `instrument` union: bank/issuer/network/psp/vpa_handle/card_type/flow). **Creates correlated failures** — the realism property a naive Bernoulli tape misses. | PLATFORM schema |
| D | Code emission: empirical mixture over the 106 documented reason strings | — |
| E | Messiness injectors (below) | PLATFORM |
| F | Adversarial: parameters chosen to **minimise** Rukja's advantage, producing a pessimistic bound | — |

**Layer E injectors, each traceable to documented platform behaviour:**

| Injector | Rate | Basis |
|---|---|---|
| duplicate webhook delivery | 0.05 | Razorpay: duplicate delivery expected, dedupe on `x-razorpay-event-id` |
| out-of-order delivery | 0.08 | Razorpay: `authorized → captured` order "may not be followed at all times" |
| late reconciliation | 0.03 | `fetch_payment` returns `created`, then `captured` 40s later |
| unknown reason string | 0.02 | codes outside the 106 → abstention test |
| casing/whitespace drift | 0.02 | `insufficient_funds` / `INSUFFICIENT_FUNDS` / `insufficient funds` |
| rail-collided numeric codes | 0.02 | NACH `01` vs UPI `01` — the NACH-01 incident, permanently regression-tested |
| AFA boundary amounts | fixed | 1 499 900 / 1 500 000 / 1 500 100 paise |
| IST boundary timestamps | fixed | 18:59:59 / 19:00:00 / 07:59:59 IST |
| mandate revoked mid-flight | 0.02 | `MANDATE_DEAD` arriving after an in-flight attempt |
| injected instruction in reply | 0.03 | prompt injection in the free-text field |

**Tape grid — headline is the WORST cell, pre-registered:**

| | Standard | Shift (held-out banks) | Adversarial |
|---|---|---|---|
| Base | T-STD | T-LBO | T-ADV |
| + outage bursts | T-STD-B | T-LBO-B | T-ADV-B |

All 6 cells × 10 seeds printed. Headline = worst cell, never the mean, never the best.

### Splits and leakage prevention

**Unit of split is the payer, never the attempt.** Attempts within a subscription are correlated; splitting by attempt is the classic leak.

`FIT` (EV prior) · `CAL` (conformal, payers disjoint) · `HELD-LBO` (banks disjoint from FIT) · `HELD-TIME` (drift) · `HELD-PHRASE` (parser templates from an unseen bank).

**Five guards enforced by code, not discipline:**

1. **Import firewall** (`import-linter`): `tape/` may not import `ev/`, `gates/`, `policy/`; `audit/` may not import `gates/`. `make firewall` prints PASSED in 4 seconds on camera.
2. **Runtime leakage guard**: `ev/prior.json` records the bank list it was fit on; the evaluator raises `LeakageError` if a tape's bank set intersects it. Demonstrated live by flipping a flag.
3. **Split manifest**: sha256 per partition; CI asserts payer-ID intersection is empty.
4. **Temporal seal**: `make verify-seal` asserts every held-out tape's git commit **predates** the last commit touching `ev/` or `policy/`. Prints two dates side by side.
5. **Parser blindness test**: asserts the LLM prompt template contains no account-state fields.

**Named limitation, stated first:** LBO holds out *parameters*, not *model family*. Layer A exists precisely because it is a family the EV model cannot fit.

### The metrics table format

**PRIMARY — Wrongful-Action Rate (WAR).** Out-of-policy money actions executed per 1,000 money-moving attempts, adjudicated by an **independent auditor** that re-derives violations from raw events with a separately-written checker, forbidden by import-linter from importing `gates/`. CI runs a **differential fuzz**: Hypothesis generates event sequences and asserts `enforcer_decision == auditor_verdict`; disagreement fails the build. Target 0, reported for every arm.

> Honesty note printed next to it: *"The auditor catches implementation bugs, not misreadings. Misreadings are caught only by the quoted-span test and the applicability tests — that is the weakest control in this system."*

**The main table:**

| Variant | WAR /1k | Recovery vs CONTROL | Attempts /100 | Compliance tax | Exceptions /100 | Wrongful debits @50-concurrent |
|---|---|---|---|---|---|---|
| **Rukja (full)** | | | | | | |
| − EV model (uniform prior) | | | | | | |
| − bank pooling | | | | | | |
| − gates (kernel OFF) | | | | | | |
| **− code table → LLM classifies** | | | | | | |
| − parser (regex only) | | | | | | |
| − exactly-once nonce | | | | | | |
| − RETRIEVE (blind retry) | | | | | | |
| B0 `RZP-DEFAULT` (documented T+1/T+2/T+3 → halted) | | | | | | |
| B1 `MAXRETRY` | | | | | | |
| B2 `FORK` (real public repo, credited) | | | | | | |
| B3 `CONTROL` (~30% untreated) | | | | | | |
| B4 `ORACLE` (sees the tape) | | | | | | |

**Secondary metrics:**

- **S1 — Customer-borne cost avoided (₹, band).** Reported as **two columns in the same table**: `MEASURED (card) = ₹0` and `MODELLED (NACH) = ₹250–500 band`. Say out loud: *"On the rail I actually measured — cards — this number is zero. The bounce-charge argument is modelled for NACH and I am not going to pretend otherwise."*
- **S2 — Compliance tax (% of gross recovery)**, cluster-bootstrap CI over payers.
- **S3 — False-Brake Cost (₹ per 100 accounts).** Lawful attempts Rukja deferred/killed that would have succeeded on the tape. Swept across the threshold. Explicitly `SIMULATED`.
- **S4 — Exception load + abstention risk–coverage curve**, with the human priced.
- **S5 — Ledger integrity:** `debits − reversals − ledger_net` in paise, asserted == 0 across every replay.
- **S6 — Gate bindingness census:** `BINDING` / `ADVISORY` / `REDUNDANT-WITH-UPSTREAM`, printed per gate.

**Deliberately NOT computed (a printed section):** involuntary churn / LTV saved (no defensible number for a synthetic merchant); agent-attributed chargeback rate (the circulating "24% growth to ~324m disputes" is *total* chargebacks across all commerce, not agent-attributed); absolute rupees recovered on real data (structurally unobtainable when you choose the outcomes); precision/recall of a fraud score (not this system's job, and Vulcan owns that ground). **Refusing to compute four metrics you could easily have faked is itself the argument.**

### Uncertainty and abstention

Split conformal, **Mondrian (class-conditional) by (rail × code class)**, calibrated on `CAL`. ~40 lines hand-rolled. Act only when the **lower** conformal bound clears the EV threshold; interval straddling → `DEFER` with `reason_code = EV_UNCERTAIN` and an exception row.

**Report empirical coverage on held-out banks at α = 0.10, and print the degradation.** It will almost certainly under-cover under bank shift (e.g. 0.90 in-distribution → 0.83 LBO). Print the inflated α actually shipped and the coverage plot. A conformal coverage number that honestly degrades is worth more than one that suspiciously doesn't. Justification for Mondrian over marginal: arXiv **2607.27143** (15 datasets, 7 models, 3 calibrators, 10 seeds = 3,150 runs) found marginal CP under-covers the minority class to **below 1%**, while Mondrian restores it by **+61.7 percentage points on average (p < 1e-80)**.

### Reproduction

```
make reproduce         # hermetic, no network, ~12 min
  ├─ make firewall     # import-linter contracts
  ├─ make verify-seal  # held-out tapes predate policy commits (prints both dates)
  ├─ make leakage      # split manifests, bank disjointness, parser blindness
  ├─ make replay-all   # 6 cells × 10 seeds × 9 ablations × 5 baselines
  ├─ make real-leg     # re-derives real-leg stats from the archived evidence bundle
  └─ make verify-claims

make attack SEED=8471293   # the toxiproxy race (needs docker)
make citations             # THE ONLY networked target: re-fetch, assert quoted spans
make unblind               # reveals arm labels; writes a timestamped commit
```

- **Seeds are not shopped:** `seed_i = int(sha256("rukja-batch-" + str(i)).hexdigest()[:8], 16)`, `i ∈ 0..9`, derivation string committed in a dated commit before the first run.
- **`--strict` mode fails the run on any outbound socket** during replay — replay cannot silently depend on the internet.
- **`results/NEGATIVE.md` is auto-appended by the harness** whenever any segment shows treatment worse than control at 95%. Bad news is written by the machine, not curated by the builder.
- **`make verify-claims`** regenerates every number from `results/CLAIMS.json` and fails on drift. Final line: `ALL 47 CLAIMS REPRODUCED — 0 DRIFT`.
- **Optional blinding (~45 min, high value):** `analysis/` consumes arms as `arm_a/arm_b/arm_c`; `make unblind` reveals the mapping in a timestamped commit. Almost no hackathon submission blinds anything.

### Fidelity check (because they will ask)

A printed table comparing the 40-sub real leg against a matched tape on: terminal-state distribution (TVD), attempts-to-terminal (KS), code-class mix (TVD), inter-attempt delay (KS), webhook duplicate rate. **Report bootstrap CIs and state that at N=40 this is underpowered.** Show at least one statistic that visibly does *not* match, and explain it. A fidelity table that passes everything at N=40 is not believable.

### Exception list schema

```json
{
  "exception_id": "exc_01JX...",
  "batch_id": "T-LBO-B/seed_3",
  "provenance": "SIMULATED",
  "rail": "card|upi_autopay|nach",
  "payer_bank": "HDFC",
  "amount_paise": 149900,
  "class": "UNKNOWN_CODE",
  "observed": {"reason": "issuer_risk_hold_v2", "raw_event_id": "evt_…"},
  "action_taken": "NONE — NO MONEY MOVED",
  "human_decision_required": "Classify issuer_risk_hold_v2 as retriable-liquidity or account-dead.",
  "money_at_stake_paise": 149900,
  "reproduce": "make replay TAPE=T-LBO-B SEED=3 FOCUS=exc_01JX..."
}
```

**Completeness is enforced, not claimed.** CI asserts per batch:
```
attempts_in == allowed + deferred + never + exceptions          (no residual)
sum(money_at_stake) + sum(recovered) + sum(written_off) == sum(billed)   (paise-exact)
```

---

## 8. 10-DAY PLAN

**D1 — Skeleton + the two-hour spike that decides everything.**
Repo, `docker-compose` (postgres + toxiproxy, digest-pinned), event store + hash chain + `verify-ledger`, `Clock`, `Paise`, CI green. Razorpay signup (test mode needs **no KYC and no registered business**), generate `rzp_test_` keys.
**Two-hour timebox: is the charge outcome API-drivable?** Write the finding into `FAILURES.md` the same day either way. This decision cannot wait until D7.
Deploy the webhook receiver to a **real host** (Cloudflare Worker → Fly/Render). **Not ngrok.** Razorpay hard-blacklists ngrok.io, webhook.site, requestbin.com, beeceptor.com, hookbin.com, mockbin.org, loca.lt, localhost, `.local`, `.internal`; their own recommended workaround is zrok (https://docs.zrok.io). Test-mode webhook setup requires OTP **754081**.
Also on D1: **test the `create_refund` path** — community reports (razorpay-node issue #438) say test-mode refunds fail with a bare `BAD_REQUEST_ERROR` because test balance is 0.00. Rukja's happy path deliberately does not terminate in a refund, but confirm it now, not on D9.

**D2 — `codes/` v1 + citation CI.**
The 106 Razorpay reason strings (scrape https://razorpay.com/docs/build/llm-docs/errors/payments/list.md — the `.md` route is the only reliable programmatic path; the HTML site is a JS SPA that returns near-empty HTML to non-browsers), NACH return codes, UPI codes. `strength` / `binds_whom` / `enforcement_locus` on every row. `verify_citations.py` asserts each quoted span is still live. **CI fails on a missing citation, a missing strength grade, or a drifted quote.**
**Adversarial citation pass:** delete MCC 7322/6529; fix the NPCI Autopay figure to whatever the table literally says and screenshot the column header into `EVIDENCE.md`; downgrade G02 to `INDUSTRY_NORM` if no primary span exists.

**D3 — Gates G01–G07 + adjudicator + fork selection.**
Seven pure functions, `POST /adjudicate`, decision rows with full `gate_results`. **Write `test_g03_negative.py` first.**
Run the fork-selection protocol *today*: a stated GitHub query on a stated date, every candidate recorded in `BASELINES.md` with the reason it was accepted or rejected. **If no usable public agent exists, that null finding is publishable** — "I evaluated 9 public Razorpay dunning repos; 0 handle concurrent duplicate charge attempts; here is the table" is a better slide than a fork. **Never write the losing agent yourself.**

**D4 — Kernel.**
Intent/nonce, corrected commit-then-call sequence, gateway semaphore, RETRIEVE reconciler, `Executor` abstraction (REST / MCP / LocalSim), pool metrics. Hypothesis invariants.

**D5 — Webhooks + replay.**
HMAC over raw bytes, event-id dedupe, order-tolerant projection with a commutativity property test, fixtures captured. Tape generator with bank holdout. `make replay SEED=` byte-identical assertion.

**D6 — Real leg + the adapter.**
`make real-leg N=40` (~25 min end-to-end). **Do this on D6, not D1** — test-mode card tokens are valid **3 days**, so creation must bracket both the D7 run and the D10 recording. Fidelity check. Wire the 20-line adapter into the forked submodule against LocalSim.

**D7 — Eval.**
Three tiers, 10 committed seeds, compliance tax, 3×3 prior sweep. `make judge` from a clean container, <4 min, no credentials. Commit `PREREGISTRATION.md` **before** the run.

**D8 — UI + attack.**
Prevented ledger with clickable citations (one click straight through to rbi.org.in), decision drawer showing all seven verdicts + `RULE`/`MODEL` provenance + enforcement locus, live SSE, pool gauge, nonce table. `scripts/attack.py`.

**D9 — LLM edge + writing.**
`nlu/` + fallback + injection demo. `FAILURES.md` written properly. `EVIDENCE.md`. README. **Code freeze 20:00.**

**D10 — Record.**
Morning: fresh **12-account** real leg (token TTL). Rehearse the 85-second take 8+ times. Record five beats. Cut. Upload **unlisted** (never Private). Submit.

### Cut list, in this order

1. Voice, entirely. (A stalled call on camera reads as broken, and the published ceiling for a cascaded Indic stack is ~700ms–1.2s per turn anyway.)
2. Real WhatsApp/SMS sends — the DLT registry is real, the channel is simulated and **labelled in the UI**.
3. Hierarchical Beta-Binomial → **already cut** to flat conjugate. Bought a day, produced the most contestable number.
4. Uplift / Qini curves.
5. Fork-and-adapt demo → falls back to a recorded terminal clip of the adapter.
6. MCP executor → falls back to direct REST.
7. Prior sweep beyond 3×3.
8. UI beyond the prevented-ledger screen.

**Never cut:** the control arm · the prevented ledger · citation CI · the kill switch · `FAILURES.md`.

### Risk register

| Risk | Prob | Fallback |
|---|---|---|
| Webhook domain blacklist | **Certain if unplanned** | Real host on D1. Never ngrok. |
| Charge outcome not API-drivable | High | Playwright the Dashboard modal; cap N at 40; label everything above as replay. **Decided D1.** |
| Playwright flakes on hosted checkout | Medium | Retry with backoff; `real-leg` idempotent per subscription; a partial batch (N=31) is reported as N=31, **not padded**. |
| 3-day token TTL | Certain | Two real-leg runs (D6, D10 morning). One command, run twice. |
| Test-mode refund failure (0.00 balance) | Medium | No happy path terminates in a refund. Out of scope by design, stated in `LIMITATIONS.md`. |
| MCP Docker stdio flakes on camera | Medium | `EXECUTOR=rest` env flip; both paths in CI. |
| Undocumented rate limits (429) | Medium | Deliberate throttle at 4 rps + jittered backoff. **The throttle is visible in the UI as a design choice.** Razorpay publishes no numeric limit, only that 429 exists. |
| Anthropic API down during recording | Low | `nlu_call` fixture replay; the UI says `FIXTURE` vs `LIVE`. |
| Forked repo won't reproduce the bug | Medium | Recorded terminal clip; if it doesn't double-charge under concurrency, **say so** — a null result beats a manufactured one. |

---

## 9. THE 5-MINUTE VIDEO

**Anchor sentence (written first, never changed, spoken at 0:30 and 4:45):**
> "Rukja is a subscription recovery agent whose first decision on every account is whether to act at all."

**The sentence you want repeated to the panel an hour later (spoken once, 4:52):**
> "A pre-IPO payments company doesn't need an agent that recovers more. It needs one that can prove, per decision, that it didn't break a rule."

**VO budget: 618 words / 300s = 124 wpm average, ~148 wpm inside speaking blocks, ~62s of deliberate silence.** Do not exceed. Silence is where the counters do the talking. (Calibration: industry planning rate is 140 wpm for a clear explainer, 120–130 for dense technical content.)

---

### BEAT 1 — COLD OPEN | 0:00–0:14
**No logo. No name. No face. No music. No title card.**

**SCREEN:** Frame 1 is already mid-run. Locked 2-pane layout that will not change for 2 minutes.
- LEFT (50%): `agent-a` — a real public third-party dunning agent, unmodified. HUD above: `DUPLICATE DEBITS THIS CYCLE: 0 → 1 → 2 → 3 → 4`, red, ticking.
- RIGHT (50%): same repo, same seed, same 50 requests, Rukja's adapter bolted on. HUD: `CHARGES SENT: 1` / `NONCE BURNED: 49`, green, frozen.
- BOTTOM STRIP (fixed, ~90px): `tail -f webhooks.log`, quiet for now.
- TOP-RIGHT: elapsed timer `00:00`, burned in, stays until 2:20.

**0:00–0:04 — TOTAL SILENCE.** Let the left counter climb 1→4 with no narration. *(Wistia: videos 5–10 min lose 17.3% of engagement in the first 2% — for a 5:00 video that is the first ~6 seconds.)*

**VO 0:04–0:14** *(30 words)*
> "Same agent. Same fifty requests. Same dead gateway. On the left, four debits on one billing cycle that should never have existed. On the right — one."

**CAPTION:** `Same agent. Same 50 requests. Same dead gateway.` → `Left: 4 debits. Right: 1.`

---

### BEAT 2 — THE VIEWER'S PROBLEM | 0:14–0:30
**SCREEN:** Hold. Slow 1.2× punch-in on the LEFT counter at 4. Overlay bottom-left: `Rs 250–500 + GST. Per failed debit. Charged to the customer.`

**VO** *(36 words)*
> "Every failed NACH debit charges your customer two hundred and fifty to five hundred rupees, plus GST. Not you. Your customer. The recovery agent is billing the people you're trying to keep — for the privilege of failing."

---

### BEAT 3 — ANCHOR + POSITIONING | 0:30–0:44
**SCREEN:** Cut to a single still — the prevented ledger, populated, scrolling slowly. Rows read `BLOCKED`, `DEFERRED`, `ADVISORY`.

**VO** *(34 words)*
> "This is Rukja. A subscription recovery agent whose first decision on every account is whether to act at all. Razorpay already ships an agent that optimises recovery. Nobody publishes what recovery cost the customer."

> **Do not say "91 repos." Do not say any repo count.**

---

### BEAT 4 — THE CONTRACT | 0:44–0:55
**SCREEN:** One card, four lines, static, 11 seconds.
```
NEXT 4 MINUTES — ONE TAKE, TIMER ON SCREEN
1  A real public dunning agent, 50 concurrent debits, dead gateway
2  The ledger of debits that did not happen — with the citation
3  The numbers. REAL vs SIMULATED, labelled
4  The gate I got wrong, and the arm that caught it
```
**VO** *(28 words)*
> "Next four minutes. One real public dunning agent, fifty concurrent debits into a dead gateway, live, one take. Then the numbers — including the one where I lost."

---

### BEAT 5 — THE RUN | 0:55–2:20
**One unbroken 85-second take. No cut, no layout change, no alt-tab.**

**5a | 0:55–1:10 — Launch and the honesty banner.** One keystroke, `make attack SEED=8471293`. The harness prints first:
```
commit  a3f19c2  (github.com/<you>/rukja — public)
utc     2026-09-0? 14:0?:??Z
left    <real-repo-name> @ <sha>, unmodified — credit: <author/repo URL>
charge  LOCAL STUB. Razorpay test-mode charge trigger is Dashboard-only.
toxi    timeout toxic, timeout=0  (accepts, never answers)
```
**VO** *(37 words)*
> "One command. Line one is the commit you can clone. Line two is the honest part — this runs against a local charge path, because Razorpay's test-mode charge trigger is a Dashboard button, not an API."

**5b | 1:10–1:26 — The left side.** Left floods, counter 1→4. Cursor hovers the credit line and repo URL for 2 full seconds. **4 seconds of silence first.**
**VO** *(30 words)*
> "That's a real public repo, by name, unmodified. I picked it because it's well written and it still has this hole. The hole isn't a skill problem — it's a missing default."

**5c | 1:26–1:45 — The invariant and the pool gauge.** Punch-in right. `nonce_burned` × 49. Then the money shot: `POOL: 3/64 in use`, **flat as a table edge** while 50 calls hang. Then `timeout → state=RETRIEVE` and `reconcile via fetch_payment → single payment id confirmed`.
**VO** *(43 words)*
> "One charge accepted. Forty-nine nonces burned. Watch the connection pool — it stays flat, because the intent row commits, the nonce burns, and the connection is released *before* the gateway is called. A timeout never retries blind. It goes to RETRIEVE and reconciles."

**5d | 1:45–1:57 — The external event.** Bottom strip lights up with a real Razorpay test-mode webhook from one of the 40 real subscriptions: `[webhook] payment.failed sub_… reason=… sig=verified`
**VO** *(29 words)*
> "That just arrived from outside my process. I clicked Charge This Now in the Dashboard forty seconds before I hit record — because that's the only way to trigger it."

**5e | 1:57–2:12 — The gate and the pre-loaded objection.** Right: `attempt_ordinal=5 → REFUSED  NPCI_RETRY_CAP 1+3`. Same row, small: `scope: merchant-driven retry loops on Razorpay Payments API. Razorpay Subscriptions halts at 4 on its own rail.`
**VO** *(38 words)*
> "Attempt five, refused. And to be fair to Razorpay — on their own subscription rail the engine already halts at four. This gate exists for the long tail of merchants running their own retry loop on the payments API."

**5f | 2:12–2:20 — Freeze and shut up.** Timer stops at `01:25`. Final frame: `CHARGES: 1 | NONCE BURNED: 49 | POOL PEAK: 4/64 | DUPLICATE DEBITS: 0`. **VO: none. Hold silence for 3 full seconds.**

---

### BEAT 6 — THE LEDGER AND THE CITATION CLICK | 2:20–2:55

**2:20–2:32:** Prevented ledger full screen. Columns: `decision | reason_code | instrument | strength | cost_avoided`. Cursor picks `TAT_DEEMED_TRANSACTION` and clicks the citation. **A real browser opens rbi.org.in**, the actual circular, Cmd-F highlights the exact quoted sentence stored in the YAML.
**VO** *(26 words)*
> "Every row cites the instrument that stopped it, and the citation is a link. CI stores the quoted sentence and fails if that sentence is no longer at that URL."

**2:32–2:44:** Zoom the `strength` column across three adjacent rows:
```
TAT_DEEMED_TRANSACTION  — REGULATOR_CIRCULAR — scope: deemed_transaction / pending-debit ONLY
CONTACT_WINDOW 08–19    — SELF_IMPOSED       — not law for non-lenders
PRE_DEBIT_NOTICE_24H    — ADVISORY           — issuer duty. Flags, does not block.
```
**VO** *(32 words)*
> "I graded my own evidence. That window is not law for a SaaS merchant, so it's marked self-imposed. And the twenty-four-hour pre-debit notice is the issuer's duty, not mine — so it flags. It doesn't block."

**2:44–2:55:** Terminal, live: `pytest -k tat_gate -v` → `test_tat_gate_does_NOT_fire_on_insufficient_funds PASSED`
**VO** *(24 words)*
> "And the test I care most about is a negative one. The T-plus-five reversal rule doesn't apply to a decline — nothing was debited. So it must not fire."

---

### BEAT 7 — THE NUMBERS | 2:55–3:32
**2:55–3:02:** `make eval` typed live. The card that follows is rendered from `metrics.json` so it cannot drift.

**3:02–3:32:** One table. Two provenance columns, same font, same weight, no asterisks.
```
                                        REAL (n=40)     SIMULATED (n=[N])
                                        live sandbox    replay, seeded from the 40
attempts prevented                          [--]              [----]
out-of-policy actions blocked               [--]              [----]
  by instrument: NPCI 1+3 / dead code / window / TAT
customer-borne charges avoided (Rs)      [modelled band, Rs 250–500]
duplicate debits under concurrency            0                  0
LLM fallback fired                          [--]%             [--]%

COMPLIANCE TAX  (same batch, kernel ON vs OFF)          [--.-]% of gross recovery
SEGMENT WHERE TREATMENT LOST TO CONTROL     [name]      -Rs [----] per 100 accts
```
**VO** *(72 words)*
> "Two columns, one font. Forty subscriptions driven end to end through the real sandbox. Everything above that is a replay harness seeded from those forty, and it says so.
> The headline is a count, not an estimate: attempts my code refused to make, and out-of-policy actions blocked, itemised by instrument.
> The second headline is the ugly one. Obeying the regulator cost me [X] percent of gross recovery. And here is the segment where my treatment lost to the control arm."

**3:25–3:32:** Sensitivity strip: `parity-at-fewer-attempts holds for bank success curves p ∈ [__ , __]; breaks below __`.

> **Never say "recovers within a few percent using 40% fewer attempts" as a measured result.** It lives only here, labelled, with its failure region.

---

### BEAT 8 — AI JUDGMENT | 3:32–3:55
**3:32–3:44:** A nasty real-shaped reply typed live:
> `bhai abhi paisa nahi hai, 3 tarikh ko salary aayegi tab kar dunga — but yeh charge maine authorize hi nahi kiya tha`

Output: strict JSON, Pydantic-validated, temp 0 — `{intent: promise_to_pay, promise_to_pay_date: 2026-09-03, hardship_flag: true, dispute_flag: true, opt_out_flag: false}`. Then a second input where the model returns malformed JSON: `LLM_PARSE_FAIL → regex+dateparser fallback → counted in metrics`.
**VO** *(28 words)*
> "One model call in the whole system. Mixed script, relative date, an implied dispute. When it fails, a regex fallback catches it, and I count how often that happens."

**3:44–3:55:** Hard cut to three code snippets, 3 seconds each, no narration of the boxes: `gates/contact_window.py`, the `UNIQUE` index on the nonce table, the pinned `codes/nach.yaml`.
**VO** *(29 words)*
> "Three places I chose not to put a model: the compliance gates, the exactly-once kernel, the failure-code table. A system prompt can be argued into calling at nine p.m. An arithmetic gate cannot."

*(Land that last sentence clean, then stop for one beat.)*

---

### BEAT 9 — WHAT BROKE | 3:55–4:27
**3:55–4:12:** A real `git log` / `git diff` on screen — dated, clickable in the public repo.
**VO** *(43 words)*
> "The worst bug was mine. One of my own gates suppressed a legitimate retry — and I only found it because the control arm beat the treatment arm in one segment. A gate I built, that was wrong, that my own measurement caught. That's why the control arm exists."

**4:12–4:27:** Cut to `FAILURES.md` at the NACH-01 incident, then the dated commit `remove RAG over open web for code classification`, red → green.
**VO** *(41 words)*
> "Second one: version one retrieved failure codes from the open web, picked up a well-ranked blog saying NACH oh-one is insufficient funds — it's account closed — and dunned closed accounts. Classification over a contested corpus can never be a generation task. That commit is dated and it's in the repo."

> **Two failures. Not three.** Two, one of which makes you look operationally wrong, reads true; three reads written-after-the-fact.

---

### BEAT 10 — HONEST BOUNDS | 4:27–4:42
**SCREEN:** Plain text card, `LIMITS`, four lines.
**VO** *(35 words)*
> "What breaks first at scale: the nonce table is a single Postgres unique index, so it's one shard's write throughput. The EV model is a flat conjugate prior, not a real behavioural model. And the messaging channel is simulated — the DLT template registry is real, the send is not."

---

### BEAT 11 — CLOSE | 4:42–5:00
**SCREEN:** Prevented ledger, still. Final 15 seconds: the public repo URL in the largest type in the film, plus the two headline counts. Nothing moves.
**VO 4:45–4:57** *(46 words)*
> "Razorpay filed draft papers in June. A pre-IPO payments company doesn't need an agent that recovers more. It needs one that can prove, per decision, that it didn't break a rule.
> Rukja is a recovery agent whose first decision on every account is whether to act at all."

**HARD CUT TO BLACK at 5:00.** No outro, no thanks, no music swell.

---

### Production

| Element | Spec |
|---|---|
| Master | 1920×1080, 60fps, H.264, ~12 Mbps. Not 4K — reviewers scrub in a browser. |
| Beat 5 (the 85s run) | **OBS**, single Display Capture scene, zero scene switches. Must be provably continuous. |
| All other beats | **Screen Studio** (macOS, $108/yr, free until export, click-derived auto-zoom + cursor motion smoothing). Its zooms are continuous moves, not cuts — never let one land mid-run. |
| HUD (counters, pool gauge, timer) | **OBS Browser Source** on a WebSocket to the harness event stream. A real page, not a CSS animation, not post-production. Design at 1920×1080, ES2017-safe (OBS ships older embedded Chromium), 250ms fades. |
| Metrics card | **Remotion**, rendered from `metrics.json` at build time. It literally cannot show a number the repo doesn't have. |
| Terminal | tmux, 2 panes + 1 fixed bottom strip. JetBrains Mono 19pt, `#0d1117`, bare `$` prompt — no git-branch spam, no hostname, no venv. |
| Browser | Clean separate Chrome profile. No bookmarks bar, no extensions, no other tabs, 125% zoom. |
| Audio | Cardioid 15cm off-axis. Record VO **after** picture lock and **after** `make eval` freezes the numbers. −16 LUFS, high-pass 90Hz. **No music anywhere.** |
| Captions | **Burned in**, plus a separate `.srt`. Max 2 lines, 38 chars/line, sentence case, no emoji. Bottom-centre at 88% for talking beats; **top-centre during Beat 5** so the webhook strip is never occluded. Every spoken number must also exist as large on-screen type and match exactly. *(69% watch with sound off in public; 80% more likely to finish a captioned video; captions produced +7.32% lifetime views in a controlled 334-video study.)* |

**PRE-STAGED (fine, some said on camera):** the 40 real subscriptions authorised on **D6**; the Dashboard "Charge this now" click fired ~40s before recording so the webhook lands mid-run (**say this out loud at 1:48**); toxiproxy configured; forked repo pinned to a SHA; `metrics.json` committed; 8+ rehearsals of Beat 5.

**MUST BE LIVE:** `make attack` and everything it prints · the pool gauge across the storm · the webhook arriving · the citation click to rbi.org.in · `pytest -k tat_gate` · `make eval` · the Hinglish parse and the fallback firing.

### MUST NOT APPEAR — pre-flight checklist

**Security:** no API keys / `rzp_test_*` / merchant IDs / webhook secrets (including terminal scrollback, `.env` previews, browser autofill) · no real customer names, phones, emails, VPAs, PANs · no notifications, Slack, calendar, other tabs.

**Claims that get you killed:**
- [ ] The number **91** (or any repo count) — anywhere
- [ ] **MCC 7322 or 6529** in any AFA carve-out
- [ ] The **~74% business decline** figure unless the NPCI column header is on screen
- [ ] `SUPPRESSED — T+5` appearing on any **insufficient-funds** decline
- [ ] The 08:00–19:00 window presented as **law**
- [ ] The 24h pre-debit notice presented as a **merchant** obligation or as **blocking**
- [ ] "Within a few percent using 40% fewer attempts" as a measured result
- [ ] "300 / 400 real test-mode subscriptions" — the real N is 40 and it is on screen
- [ ] Any chart or number without a REAL / SIMULATED label
- [ ] Any hint that the toxiproxy attack ran against Razorpay's real charge rail

**Craft:** logo animation, title card, music sting, face-cam intro, "Hi, I'm ___" · the product name in the first spoken sentence · any cut or layout change between 0:55 and 2:20 · sped-up footage without an on-screen label · typing a URL on camera · any login screen · narration over the frozen counters at 2:12–2:15 · any disparagement of the forked repo's author · anything offense-capable · "Thanks for watching" or a fade.

### Alternate 60-second cut (social)

Recut from the same masters. 1:1 or 9:16 crop centred on the two HUD counters. Captions at 20% height (platform UI eats the bottom). Muted-first: every claim on screen as type.

| Time | Screen | VO |
|---|---|---|
| 0:00–0:05 | Split, left counter 1→4, right frozen at 1. Silence. | caption only |
| 0:05–0:13 | Punch-in, overlay ₹250–500 + GST | "Every failed NACH debit charges your customer two-fifty to five hundred rupees plus GST. Not you. Your customer." |
| 0:13–0:20 | Ledger still | "Rukja is a recovery agent whose first decision on every account is whether to act at all." |
| 0:20–0:36 | Beat 5c verbatim | "Fifty concurrent debits into a gateway that never answers. One charge. Forty-nine nonces burned. The pool stays flat, because the connection is released before the gateway is ever called." |
| 0:36–0:44 | Live click to rbi.org.in | "Every blocked action cites the instrument that stopped it. The citation is a link, and CI checks the sentence is still there." |
| 0:44–0:54 | Metrics card, 3 lines + losing segment | "Obeying the regulator cost me [X] percent of gross recovery. And here's the segment where my system lost to doing nothing." |
| 0:54–1:00 | Repo URL, static | "Repo's public. Numbers are in `metrics.json`." |

---

## 10. THE FORM

### The 12 fields, in order (from the live Google Form definition)

Email (auto) · Full Name · College Name · **Graduation Year [dropdown: 2027 / 2028 / 2029 ONLY]** · In-person availability from September [Yes/No] · Preferred Duration [6/12-Month] · Selected Track · Project Name · **Project Objectives — "What does it solve?"** [paragraph] · **GitHub Repository URL** · **5-min Pitch Video Link** · **Build Challenges & Technical Obstacles — "What issues did you face while building, and how did you solve them?"** [paragraph] · Final Submission Confirmation [checkbox: *"no further changes or edits can be made after submitting"*].

**There is no architecture field, no metrics field, no team field, no resume field.** Everything for criteria 2 and 3 must be carried by the README and the video alone.

### Project name — 5 candidates, ranked

| # | Name | Why it wins | Why it might not |
|---|---|---|---|
| **1** | **Rukja** *(रुक जा — "stop")* | One word, imperative, instantly legible to an Indian fintech panel, and the only name that is a **command to the machine**. On screen next to a debit that did not happen it is the whole product in one frame. | Says "stop", not "recover". Needs the tagline in the same breath, every time. |
| **2** | **Prevented** | The name *is* the metric. Forces every slide to be a count rather than an estimate. Zero ambiguity in English. | Passive and cold. No India texture; may read as an analytics dashboard. |
| **3** | **Hisaab** *(हिसाब — the reckoning)* | Warm, memorable, names the actual contribution: nobody publishes what recovery **cost**. Perfect for the compliance-tax slide. | Reads as accounting — risks being mentally filed into Track 04 by a judge skimming 200 entries. |
| **4** | **Lakshman Rekha** *(short: Rekha)* | Highest instant comprehension of the *concept* in India — the line you do not cross, drawn to protect. | Three syllables too long; mythological framing can read as gimmick; "Rekha" is a common first name; weak as a CLI binary. |
| **5** | **Deadman** | Engineer-legible; sets up the toxiproxy demo perfectly. | Morbid next to "debit", English-only, describes the mechanism not the job. |

**Ship: `Rukja — the recovery agent whose first decision is whether to charge at all.`** That exact tagline goes in the README `<h1>` subtitle, the video's first framing beat, and the project-name field if it accepts a subtitle.

### "What does it solve?" — three lengths

**30 words**
> Rs 250–500 plus GST: your customer's bank bills them for each failed auto-debit your agent triggers. Rukja is the recovery agent whose first decision is whether to charge at all.

**100 words**
> Rs 250–500 plus GST is what your customer's own bank charges **them** every time a mandate debit bounces. Recovery agents optimise for recovery, so they retry — and bill the customers they are trying to keep for the privilege of failing. Rukja is a recovery agent that decides, per account, per attempt, whether to act at all, and prints the ledger of debits it refused with the regulation or scheme rule that stopped each one. Two headline numbers: attempts prevented and out-of-policy actions blocked (exact counts, no model), and the compliance tax — what obeying the rules cost gross recovery, measured kernel-on versus kernel-off on the identical batch.

**250 words** *(use this one)*
> Rs 250–500 plus GST is what your customer's own bank charges **them** every time a mandate debit bounces. The merchant does not see that line. The recovery agent does not price it. So the whole field optimises in one direction — retry more, contact more — and the cost lands on the customer you are trying to retain.
>
> Razorpay shipped a Subscription Recovery Agent in Agent Studio in March 2026. It optimises for recovery, and it should. Nothing in the market publishes what recovery cost the customer, or what the regulator cost the merchant. That is the screen I built.
>
> Rukja is a recovery agent whose first decision on every account is whether to act at all. It ingests subscription and payment webhooks, classifies each failure against a pinned, citation-graded code table (not retrieval), and runs a bounded workflow through arithmetic gates that return `ALLOW | DEFER | NEVER` with a reason code, a primary-source URL, and the exact quoted sentence CI verifies is still live at that URL. Its main screen is the ledger of debits that did **not** happen.
>
> Two headline numbers, both chosen because they cannot be faked. **Attempts prevented and out-of-policy actions blocked, itemised by instrument** — exact counts, computed from gate decisions, dependent on no model or prior. And the **compliance tax**: obeying the rules cost `[N]%` of gross recovery, measured kernel-on versus kernel-off on the identical batch, so the simulator cancels out of both arms.
>
> Recovery parity is reported separately, as a labelled sensitivity analysis, with its failure region stated.

### "What broke, and how you got out" — three candidates

**Pick ONE for the form.** The other two live in `FAILURES.md`, linked from the answer.

---

#### Candidate A — *"A gate I built was wrong, and my own control arm caught it."* ← **RECOMMENDED**

I built seven compliance gates. One of them was wrong, and it was the one I put in the demo.

The RBI Harmonisation-of-TAT circular (RBI/2019-20/67) mandates auto-reversal by T+5 with ₹100/day compensation, paid suo moto. I read the operative row — "account debited but transaction confirmation not received" — and wired a gate that suppressed dunning inside that window, with the line "chasing money the bank must auto-reverse" on the ledger. Then I ran the three-arm batch and the treatment arm lost recovery in a segment it should have won. I traced it: the gate was firing on plain `insufficient_funds` declines.

The bug was that I read the table and not the definition. The same circular defines a failed transaction as one "not fully completed due to any reason **not attributable to the customer**." Insufficient funds is attributable to the customer. A declined mandate debits nothing, so no reversal is pending, no compensation clock is running, and suppressing that retry cost real money for a benefit that accrued to nobody.

Three things changed. The gate is now scoped strictly to debited-but-unconfirmed states and prints that scope on screen next to the citation. There is a **negative test** — `test_tat_gate_does_not_fire_on_insufficient_funds` — and I show it passing on camera. And every row in the rules table now carries a `strength` grade: STATUTE / REGULATOR_CIRCULAR / INDUSTRY_NORM / SELF_IMPOSED. CI fails on a missing grade, not just a missing URL.

*(≈245 words. Strongest: self-incriminating on the exact axis the project claims as its moat; the measurement apparatus paid for itself; the fix is a test a judge can watch go green.)*

---

#### Candidate B — *"My classifier learned a wrong fact from a well-ranked blog and dunned closed accounts."*

Version one classified failure codes by retrieving over the open web and letting the model decide. It worked, which is why it took two days to catch.

A well-ranked 2026 blog stated that NACH return code 01 is Insufficient Funds. It is not. NPCI's own return-code circular, and both Juspay's and Decentro's published tables, say **01 = Account Closed** and **04 = Balance Insufficient**. The retrieval layer picked the blog, the model agreed with it, and my harness spent a full run scheduling retries against accounts that no longer existed — the single worst class of error in dunning, because every one of those attempts is a guaranteed-fail presentment that in production charges the customer a bounce fee for a debit that could never have succeeded.

The diagnosis was uncomfortable: nothing had malfunctioned. The corpus was contested, the retriever ranked by relevance, and the model has no mechanism to prefer a scheme circular over a blog. Confidence stayed high the whole time.

I deleted the retrieval path — it is a dated commit, not a refactor — and replaced it with a pinned YAML table where every row carries the primary source URL, the exact quoted sentence from it, and a pytest case. CI asserts the quote is still present at that URL, so the control checks that the source **supports the rule**, not merely that a link exists.

The general rule I now build to: classification over a contested corpus must never be a generation task. Retrieval is for prose. Codes are a table you pin, cite, and test.

*(≈250 words.)*

---

#### Candidate C — *"My exactly-once kernel was a textbook anti-pattern and the black-hole test found it in nine seconds."*

The kernel's job is that one billing cycle can never be charged twice. My first version burned the idempotency nonce inside the same database transaction as the gateway call, so the burn and the charge committed atomically. On paper that is airtight.

Then I ran the attack: 50 concurrent identical debits with the gateway behind toxiproxy's timeout toxic at `timeout=0` — a silent black hole, connection accepted, nothing ever returned. The invariant held. The service died anyway. Every request held an open Postgres transaction across a network call that would never return; the pool was exhausted in about nine seconds and every unrelated request in the system queued behind it. I had built correctness that takes the process down under exactly the failure it exists to survive.

You cannot hold a transaction open across a call to a payment gateway. The rewrite splits it: commit a write-ahead intent row and burn the nonce, **release the connection**, then call the gateway, then reconcile. Reconciliation is where the real rule lives — a gateway timeout never retries blind. It transitions to `RETRIEVE` and calls `fetch_payment` first, because a timeout is not a failure, it is an unknown, and treating unknowns as failures is how systems double-debit real people.

I put a live connection-pool gauge on the demo screen. Under 50 hung calls it stays flat: one charge, 49 `nonce_burned`, timeout routed to `RETRIEVE`. The graph is now the part of the demo I most want to be asked about.

*(≈247 words.)*

---

**The fourth, which goes in `FAILURES.md` regardless:** the token-TTL schedule collision (test-mode card tokens expire in 3 days; my plan built the batch on D1–D2 and ran the experiment on D7, so every token would have been dead before it started), plus the discovery that Razorpay's charge trigger is a Dashboard control and not an API — which is why the honest ceiling on genuinely end-to-end subscriptions was never 300. Cut to 40 real, moved creation to D6, ran the batch arms on a replay harness seeded from those 40, labelled REAL and SIMULATED in the same table. Add the webhook domain blacklist too — it makes you look operationally ordinary, which is exactly what one clean architectural narrative needs beside it.

### README opening paragraph

> # Rukja
> **The recovery agent whose first decision is whether to charge at all.**
>
> Rs 250–500 plus GST is what your customer's own bank charges **them** when a mandate debit bounces. Your merchant P&L never shows that line, so every recovery agent on the market optimises the one direction it can see: retry more, contact more. Razorpay shipped a Subscription Recovery Agent in Agent Studio in March 2026 and it optimises for recovery, correctly. Nothing in the market publishes what the recovery cost the customer, or what the regulator cost the merchant. Rukja detects revenue at risk from live subscription webhooks, diagnoses it against a pinned and citation-graded failure-code table, and executes a bounded recovery workflow — and its main screen is the **ledger of debits that did not happen**, each row carrying the reason code, the primary-source URL, the exact quoted sentence CI verifies is still live at that URL, and how strong that source actually is: STATUTE, REGULATOR_CIRCULAR, INDUSTRY_NORM, or SELF_IMPOSED. Two headline numbers. **Attempts prevented and out-of-policy actions blocked, by instrument** — exact counts, no model, no prior. And the **compliance tax**: obeying the rules cost `[N]%` of gross recovery, measured kernel-on versus kernel-off on the identical batch. Recovery parity is reported below those, as a sensitivity analysis, with the range over which it survives and the region where it breaks. Forty subscriptions ran genuinely end-to-end through the Razorpay sandbox; everything above forty is a replay harness seeded from them, and every table says which is which.
>
> `make replay SEED=8471293` is byte-identical on your machine.

### Repo first-impression checklist (the first 90 seconds)

**Above the fold:** `<h1>` + tagline in one line, no badge wall · one ≤12s autoplaying GIF (prevented ledger filling, one row expanding to citation + quote + strength) · the two headline numbers as literal text, not only inside an image · **the REAL vs SIMULATED table above the fold** · three commands that work on a clean clone: `make judge`, `make attack SEED=`, `make verify-citations`.

**Structural:** `FAILURES.md` linked by name with "read this first" and the RAG-removal as a **real dated commit linked by SHA** (a judge will click it) · `rules/` rendered as a table with every column · a section titled **"Which gates actually bind"** listing the ones enforced upstream and why they still exist for merchants on the raw Orders API · `ARCHITECTURE.md`, one diagram, one page · green CI badge where the check is `verify-citations`, not `tests` · LICENSE, `uv.lock`, `.env.example`, seeded fixtures committed · **AGENTS.md**.

**Explicitly NOT on the first screen:** the EV model, the seven gates enumerated, the Hypothesis invariants, the adapter story, the sensitivity sweep. Strengths, not the hook.

### Three hostile panel questions and the answers

**Q1. "Your policy and your simulator share a prior. 'Parity at 40% fewer attempts' is arithmetic you assumed and then dressed as an experiment."**
You're right, and that is why it is not my headline. A policy that skips attempts its own model rates low will always look good against a simulator built from the same model — that result is a restatement of the prior. So I report three tiers, labelled. Tier one is counts: attempts prevented, out-of-policy actions blocked by instrument. Those come deterministically from gate decisions and depend on no prior. Tier two is the compliance tax — kernel-on versus kernel-off on the *identical* batch, same tape, same seeds; both arms share the simulator, so it cancels. Tier three is recovery parity, presented as a sensitivity analysis with the range where it holds and the region where it breaks. The tape holds out entire banks the EV model never saw, it is committed, and you can replay it. One sentence is on the slide: this is a simulation study, the policy was not fit on this tape.

**Q2. "Which of your gates actually prevents anything that would otherwise happen?"**
On the Razorpay subscriptions rail, fewer than you'd think, and the repo has a column that says so per rule. The 1+3 cap is enforced by your engine — my gate is a redundant assertion there and I've graded it that way rather than taking credit. DLT is enforced at send time, and I've labelled my channel simulated. Three things do bind. The merchants who aren't on your subscription engine — the long tail running their own retry logic on Orders and Payments, where nothing upstream stops attempt five, and that's who the 20-line adapter is for. The gates about *timing and targeting* rather than count: dead-code suppression, where the table says the mandate or account is gone and the correct number of retries is zero, not three — your engine will happily spend all four attempts on a closed account. And the exactly-once kernel, which isn't a compliance gate but is the thing that actually prevents double debits under concurrency, and no upstream system prevents that for you.

**Q3. "We already ship this. Agent Studio has a Subscription Recovery Agent."**
You shipped it on Claude, and it's the right product. I'm not competing with it — I'm the gate it should call before it acts. Two gaps. Your agent's objective function is recovery, and recovery is measured on the merchant's side of the ledger; the cost of a failed retry lands on the customer's bank statement where neither the agent nor the merchant ever sees it. Nobody publishes a compliance tax or a customer-borne cost of collection. That's not a feature you forgot; it's a number nobody has had a reason to compute, and it's the number an auditor will eventually ask for. Second, Agent Studio's guardrails are merchant-configured policy. Mine are arithmetic with a citation and a strength grade attached — including the ones marked SELF_IMPOSED because they're a voluntary norm and not law. A system prompt can be argued into calling at 21:00. An arithmetic gate cannot.

**Two more loaded, one breath each:**
- *"You can't hold a DB transaction across a gateway call."* — Correct, and I did it wrong first. Intent committed, nonce burned, connection released, *then* the gateway call, then reconcile. The live pool gauge is on the demo screen; under 50 hung calls it stays flat.
- *"For an AI buildathon, your total AI surface is one JSON extraction call."* — Deliberately. The one place that genuinely needs judgement is free-text Hinglish replies, and that call is temperature 0, Pydantic-validated, with a fallback that fires on camera and is counted. Message content is never generated — TCCCPR requires a pre-registered DLT template, so the model picks a template ID and fills declared variables. Free-form copy is structurally impossible. Criterion three asks where I chose *not* to use one; the answer is the failure-code table, the retry timing, and every gate.

---

## 11. BACKUP PLAN

**Runner-up: Pakka (Track 02 — AI Risk Manager).** A counter-side verifier for forged UPI "Payment Successful" screens.

1. Merchant photographs or drops in a customer's payment-confirmation screen.
2. **Layer 0 is deterministic and has no model:** OCR extracts the 12-digit UPI reference, amount, timestamp and payee VPA; a typed reference grammar rejects anything that doesn't parse; then an exact composite-key join against the merchant's own Razorpay ledger, mirrored locally from `payment.captured` / `qr_code.credited` webhooks. VERIFIED with certainty, ~400ms, **zero model calls**.
3. **Layer 1 only fires when the ledger says absent:** app-layout template match, ELA + resample-grid forensics, glyph-metric consistency, and a SegFormer head fine-tuned on **STFD** (ICASSP 2023, the only public screenshot-text-forgery dataset; scene list explicitly includes "Mobile Payment" and "Online Banking"; https://huggingface.co/datasets/Zegkim/STFD, code https://github.com/ZeqinYu/STFL-Net, 5 ⭐).
4. **Layer 2 is an explicit ABSTAIN lane** with a 90-second provisional re-poll window, calibrated by Mondrian conformal prediction.
5. Headline metric is **coverage**: what fraction of the batch the deterministic ledger path resolved with zero false accepts. Then precision/recall/abstain on the residual, with **entire tamper types held out** for open-set evaluation and a JPEG-Q60 robustness split.
6. The AI-judgment artifact is a **measured negative result**: run TruFor (https://github.com/grip-unina/TruFor, 271 ⭐, CVPR 2023) on your own screenshot set and report that it underperforms the OCR-semantic channel, because its Noiseprint++ extractor was trained on 24,757 photos from 1,475 camera models across 43 brands and a PNG screenshot has no camera pipeline, no CFA, no sensor noise.
7. **Why it's a credible backup:** GitHub search on 2026-08-26 — `razorpay buildathon` = 287 repos, `razorpay buildathon screenshot` = **0**, `razorpay buildathon vision OCR` = **0**. The one adjacent entry, https://github.com/Tannnnnnnnn-beep/Suraksha-Pay, is a README with 0 ⭐, no dataset, no held-out set, no metrics, no ledger path.
8. **The problem is vivid and dated:** ₹76,000 (Bengaluru mobile shop, Feb 2026) · ₹46,000 gold ring (Indore jeweller, Jul 2025) · ₹8,700 (Ludhiana garment store, Aug 2025) · five arrested in Kochi (Nov 2025) for an app built "to specifically target ordinary traders". Field study of 102 merchants in Srinagar and Anantnag (Jul 2026): **57.8% took direct financial losses, 36.3% faced spoofing FREQUENTLY, 90.2% did not know an official reporting channel existed, 60.8% displayed QR as loose paper stickers.**
9. **Its three known weaknesses, which you must pre-empt:** Razorpay sells **Bharat Soundbox** into exactly this merchant (razorpay.com/pos/bharat-soundbox) — answer it in the first 30 seconds; test-mode QR codes are documented as not scannable, so `qr_code.credited` is unreachable with a test key and you build against `payment.captured`; and test-mode UPI never renders a real PhonePe/GPay screen, so the genuine class must come from real ₹1 payments on your own phone.
10. **It is a genuinely different modality** in a field of 287 text agents, which is worth a lot on the memorability axis — but the CV is decoration by its own headline metric (~6% of traffic), so the pitch must lead with the ledger.

### The exact decision point

**End of Day 2. One gate, three conditions, all must be green:**

| Check | Green | Red → switch to Pakka |
|---|---|---|
| Can you create a Plan + Subscription via API and authenticate a card through hosted checkout, headless? | ≥1 subscription reaches `active` and you have the token | Playwright cannot drive checkout reliably |
| Can you drive a charge outcome (success **and** failure) and receive the resulting `subscription.pending` / `subscription.charged` webhook on your real host, HMAC-verified? | ≥1 of each, raw body archived | The Dashboard modal cannot be automated **and** manual clicking cannot reach N=40 inside the token TTL |
| Does `codes/` have ≥20 rows with a verified quoted span and a passing `verify-citations`? | Yes | The primary sources cannot be quoted reliably (this kills the moat) |

If any is red at 20:00 on D2, switch. Pakka's D1–D4 (ledger mirror + OCR + reference grammar) reuses the webhook receiver, the event store, the hash chain, the `Clock`, `Paise`, the CI scaffolding and the `make judge` harness verbatim — you lose roughly one day, not five. **Do not switch after D3.** After D3 you ship a 70%-complete Rukja, which is still a better submission than a 40%-complete Pakka.

---

## 12. REPOS TO CLONE AND READ FIRST

Prioritised. Read the first four before writing any code.

1. **https://github.com/razorpay/razorpay-mcp-server** (230 ⭐, Go, MIT, pushed 2026-08-25)
   *Take:* the exact tool surface and env format (`--key-id rzp_test_… --key-secret …`), the `AGENTS.md` tool-generator workflow, and — critically — the **Remote Server Support column** showing `create_refund`, `close_qr_code`, `create_instant_settlement`, `create_registration_link` unsupported on the hosted server. Run the **local Docker image**, wrap every call in your audit layer, and cite the absence of a policy engine as your opening.

2. **https://github.com/saiprasad4/aadesh** (0 ⭐, TypeScript, MIT, created 2026-07-06)
   *Take:* the closest existing thinking on the exact primitive. Read `getErrorCode` / `handlingFor` / `isRetriable` / `isTerminal`, the `MandateMachine` / `DebitMachine` throw-on-illegal-transition design, `decideRetry`'s 1+3 handling, the frozen rail profiles, and `reconcile()`'s async-return-vs-retry race. Credit it in the README. It validates the primitive and proves nobody wrapped it in measurement.

3. **https://github.com/Shopify/toxiproxy** (12,077 ⭐, Go, MIT) + `toxiproxy-python`
   *Take:* the HTTP control API on port 8474 and the `timeout` toxic with `timeout=0` as a silent black hole. This is the demo. Also `reset_peer`, `latency` with jitter, and the global `toxicity` probability for the semantic injector.

4. **https://github.com/tonbistudio/leakplug-public** (5 ⭐, created 2026-06-29)
   *Take:* the guardrails package layout (consent / discount-budget / safe-copy / sandbox-only checks), the "live keys rejected" adapter pattern, and the plays-gated-on-human-approval flow. Then note what it never does — send anything, measure uplift, or face compliance — and make that your delta.

5. **https://github.com/merrttopal/recoup** (0 ⭐, Java/Spring, MIT)
   *Take:* Postgres-as-everything (`FOR UPDATE SKIP LOCKED` as the job queue), the `PaymentGatewayPort` abstraction, Testcontainers with fault injection, ArchUnit for the import firewall idea. Its "unknown result never re-charges" invariant is the pattern to steal for `RETRIEVE`.

6. **https://github.com/ajithmanmu/dunning-system** (0 ⭐)
   *Take:* only one thing — the script that creates **real** Stripe test customers and triggers **genuine** declines rather than mocking webhooks. That posture (drive the real sandbox, don't mock it) is the right one and it's rare.

7. **https://github.com/ZERO34802/paysentinel** (0 ⭐)
   *Take:* the diagnosis-before-action structure and the baseline-comparison pattern (compare against a 7-day baseline). It's a hackathon entry from another hackathon — useful as a calibration of the demo bar you must clear.

8. **https://github.com/maks-sh/scikit-uplift** (813 ⭐, MIT) and **https://github.com/uber/causalml** (5,961 ⭐)
   *Take:* only if the uplift layer survives the cut list. `ClassTransformation`, `qini_auc_score`, `uplift_at_k`. Budget half a day, not two.

9. **https://github.com/HypothesisWorks/hypothesis** (8,913 ⭐)
   *Take:* `RuleBasedStateMachine` with `@rule` / `@invariant` / `@precondition`, and automatic **shrinking**. A shrunk minimal counterexample pasted into `FAILURES.md` is worth more than any prose.

10. **https://github.com/ethz-spylab/agentdojo** (771 ⭐, NeurIPS 2024)
    *Take:* the evaluation *discipline*, not the code — deterministic environment-state check functions instead of an LLM judge, and the injection-corpus structure. Its banking suite is the shape to copy for your injection fixtures.

11. **https://github.com/sierra-research/tau2-bench** (1,880 ⭐, MIT)
    *Take:* the `pass^k` metric and grading by final **database state** rather than text. Frontier `pass^1` on the original 165 tasks is 69.2% retail / 46.0% airline (Claude 3.5 Sonnet 20241022); GPT-4o 60.4% / 42.0%. Quote that when you publish your own `pass^8`.

12. **https://github.com/razorpay/razorpay-python** (172 ⭐, MIT, v2.0.1 released 2026-03-09)
    *Take:* `client.enable_retry(True)` and `client.utility.verify_webhook_signature`. Note the gap: **none of the 106 documented `reason` strings are represented in code anywhere**, which is exactly why `codes/` has to exist.

**Also fetch, not clone:** `https://razorpay.com/docs/llms.txt` (495 KB, 2,406 lines, "Last Updated: 30 April 2026") and the per-page markdown at `https://razorpay.com/docs/build/llm-docs/<path>.md` — the HTML docs are a JS-rendered SPA that returns near-empty HTML to non-browsers, so the `.md` route is the only reliable programmatic path.

---

## 13. PAPERS WORTH CITING

Twelve, each with the exact sentence-shaped job it does in your pitch.

| # | Paper | Link | How it is used |
|---|---|---|---|
| 1 | **On the Self-Verification Limitations of LLMs on Reasoning and Planning Tasks** (ICLR 2025) | https://arxiv.org/abs/2402.08115 | The empirical licence for the whole architecture: LLM self-critique **collapsed** performance while a sound **external verifier** produced large gains. Cite when a judge asks "why not add a reflection loop?" |
| 2 | **Understanding Structured Financial Data with LLMs: A Case Study on Fraud Detection (FinFRE-RAG)** | https://arxiv.org/html/2512.13040v2 | Direct LLM prompting on raw tabular fraud rows: **Qwen3-14B F1 = 0.00, MCC = −0.01** on ULB; even with their best RAG pipeline XGBoost still wins on 2 of 4 benchmarks. The reason the failure-code table is a lookup and not a generation. |
| 3 | **Protocol-Level Attacks on Agentic Commerce Platforms (AIP-Bench + PCAT)** | https://ar5iv.labs.arxiv.org/html/2607.21824 | The thesis that the consequential vulnerabilities are **structural and model-independent** — 33 vulns at 100% ASR regardless of model, "no model improvement removes them." Their benchmark uses only deterministic judges (HTTP codes, string matches, log regexes, event counts). Justifies enforcing constraints in code, and justifies your grader. |
| 4 | **Progent: Securing AI Agents with Privilege Control** | https://arxiv.org/abs/2504.11703 | The argument-level policy model: symbolic rules over tool name + arguments, checked deterministically, with narrowing auto-applied and expansion requiring a human. Cite as the design lineage of the gate contract. |
| 5 | **Design Patterns for Securing LLM Agents against Prompt Injections** | https://arxiv.org/abs/2506.08837 | Name your architecture after two of its six patterns (Plan-Then-Execute + Action-Selector) in the README. Core principle to quote: once an agent has ingested untrusted input it must be constrained so unsafe actions are **impossible, not merely unlikely.** |
| 6 | **AgentDojo** (NeurIPS 2024 D&B) | https://arxiv.org/abs/2406.13352 | 97 tasks, 629 security test cases, and — the load-bearing detail — success judged by **deterministic environment-state check functions, explicitly avoiding LLM-based evaluation.** Your grading discipline, with a citation. |
| 7 | **τ-bench: Tool-Agent-User Interaction in Real-World Domains** | https://arxiv.org/abs/2406.12045 | The `pass^k` metric. gpt-4o succeeds on <50% of tasks and `pass^8` falls **below 25%** in retail. Cite when you publish `pass^1` next to `pass^8` and explain that everyone else is showing you one lucky run. |
| 8 | **ST-WebAgentBench** (IBM Research) | https://arxiv.org/abs/2410.06703 | 222 tasks each paired with explicit policies; **Completion-under-Policy** counts a task successful only if zero applicable policies were violated, and agents average **less than two-thirds** of their nominal completion rate under it. Steal CuP wholesale for the gate metric. |
| 9 | **Cost-Sensitive Conformal Prediction and Human-in-the-Loop Abstention** (15 datasets, 3,150 runs) | https://arxiv.org/html/2607.27143 | Marginal CP under-covers the minority class to **below 1%**; Mondrian class-conditional CP restores it by **+61.7 pp on average (p < 1e-80)**. The reason the abstention layer is class-conditional, plus the break-even framing for deferring to a human. |
| 10 | **Consistent Estimators for Learning to Defer to an Expert** (Mozannar & Sontag, ICML 2020) | https://proceedings.mlr.press/v119/mozannar20b.html | Derive the escalation threshold from an explicit **cost matrix** (false accept vs false reject vs human review) instead of hand-tuning 0.8. Pair with the AISTATS 2023 follow-up as the reason you did *not* jointly optimise classifier + rejector (NP-hard even in the realizable linear case) — a clean "chose the boring correct thing" note. |
| 11 | **Agent Contracts: A Formal Framework for Resource-Bounded Autonomous AI Systems** (COINE 2026 @ AAMAS) | https://arxiv.org/abs/2601.08815 | Formalises budgets and stop conditions as contracts. Its cited anecdote — two agents in a recursive clarification loop for **eleven days** and a **$47,000** API bill, with no stop conditions, no budget limits, no cost monitoring — is a 15-second opener for the stopping-rules section. *(Note: read via the paper's summary of the incident, not a primary incident report.)* |
| 12 | **Synthetic Tabular Generators Fail to Preserve Behavioral Fraud Patterns** | https://arxiv.org/pdf/2604.13125 | Pre-empt "is your synthetic batch even hard?" by citing this and then showing you deliberately injected temporal, velocity, correlated-outage and multi-account structure — Layers A, C and E of the tape generator. |

**Two more if you touch injection on camera:**
- **Whispers of Wealth: Red-Teaming Google's AP2 via Prompt Injection** — https://arxiv.org/abs/2601.22569. A working AP2 shopping agent on Gemini-2.5-Flash + Google ADK, **100% success rate** manipulating product ranking, with the agent **fabricating a justification**, and no cryptographic mandate broken. Conclusion: AP2 "lacks effective mechanisms for mitigating prompt injection attacks" and injections **propagate across agents.**
- **StakeBench** — https://arxiv.org/html/2606.13385v2. Indirect prompt injection against deployable shopping web agents: **41.67%–68.59% ASR across 3,168 attacked runs**; under *direct* injection every configuration exceeds 79%. Also the "stealthy parasitism" finding — attacks on the user often succeed *while the delegated task completes normally*, so task-success is not a safety metric.

**Cite only what you implemented.** One implemented paper (Progent's argument-level checks, or Mondrian CP) beats twelve name-drops, and a judge at a payments company will spot a README that name-checks CaMeL while the code is a `while` loop around an LLM.

---

## 14. OPEN QUESTIONS FOR THE BUILDER

Five decisions only you can make. Answer them in this order; the first one is a gate on the other four.

---

### Q1 — Are you eligible? *(Answer within the hour. Nothing else matters until this is settled.)*

The Google Form's **Graduation Year is a dropdown with exactly three options: 2027, 2028, 2029.** There is no "other" and no free text. Razorpay staff stated publicly on LinkedIn: *"This is only for students pursuing their degree!"* Several job aggregators say "any degree / freshers" — **the aggregators are wrong or sloppy.**

**Choose:**
- **(a) I graduate in 2027, 2028 or 2029** → proceed.
- **(b) I graduate 2026 or earlier, or I'm not enrolled** → the form is un-submittable. Stop, and re-point the same 10 days at https://razorpay.com/ai-builders (not student-gated) or another target.

Second half of the same question: **the deadline.** 5 September 2026 appears only in third-party aggregators (velonx.in, fresherjobinfo.in) and one X post. It is **not on razorpay.com/buildathon and not in the form.** Assume it could close early. Target submission ~24 hours before the reported close, with README and video frozen 12 hours before that. **Submission is one-shot and irreversible** — the mandatory final checkbox says no further edits.

---

### Q2 — After the D1 spike: what is your real N, and how do you say it?

Two hours on D1 establishes whether the test-mode charge outcome is API-drivable. Razorpay's docs describe it as a Dashboard button. Choose your response *before* you know the answer:

- **(a) API-drivable** → N can go to 100–150 real subscriptions. Still label the batch arms SIMULATED if you seed them.
- **(b) Dashboard-only, Playwright works** → **N = 40 real, everything above is replay, labelled in the same table in the same font.** This is the recommended path and it is the honest one.
- **(c) Dashboard-only, Playwright unreliable** → N = 12–15 real, purely to validate the state machine on camera; everything else replay; say the number out loud.

**The trap:** the quiet path is to let "real" silently become "simulated" while the video still says 300. In a track whose bar is honest metrics, that specific substitution is worse than never claiming it. Write the boundary sentence into the README **on D1**, before you know which branch you're on.

---

### Q3 — Fork a real dunning repo, or publish the null finding?

The left half of your money shot needs an opponent you did not write.

- **(a) Fork.** Run a stated GitHub query on a stated date, record every candidate in `BASELINES.md` with accept/reject reasons, pick one, credit the author by name, bolt on the 20-line adapter. Removes the strawman objection entirely and gives you the visible agent the project otherwise lacks. **Risk:** it may not double-charge naturally under concurrency, or may not run at all.
- **(b) Null finding.** "I evaluated N public Razorpay/Stripe dunning repos; K handle concurrent duplicate charge attempts; here is the table." Then run the attack against Rukja alone and show the invariant holding under a race you cannot fake.
- **(c) Both** — fork if one works by D3 EOD, fall back to (b) otherwise. **Recommended.**

**Never write the losing agent yourself.** A naive agent you authored that conveniently double-charges is a strawman, and a judge will say so.

---

### Q4 — Which "what broke" answer goes in the form?

They read this field first, and they have read hundreds.

- **(a) Candidate A — the gate I got wrong, caught by my own control arm.** ← recommended. Self-incriminating on the exact axis you claim as your moat; the measurement apparatus justifies its own cost; the fix is a negative test the judge watches pass. **Requires that it actually happens** — instrument the control arm to detect it, and if it doesn't happen, fall back.
- **(b) Candidate B — the NACH-01 RAG hallucination.** Safest, architecture-causing, verifiable against primary sources, with a clickable dated commit. **Requires that the commit is real** — do not rewrite history to manufacture it.
- **(c) Candidate C — the kernel anti-pattern found by the black-hole test.** Best pure-engineering story; pairs perfectly with the demo's pool gauge; least "domain judgment."

Whichever you pick, the other two plus the token-TTL/webhook-blacklist entry go in `FAILURES.md`, linked from the answer's last line. **Keep a dated log from D1.** You cannot fabricate this on D10 and the panel will know.

---

### Q5 — Where do you draw the honesty line on the two contested numbers?

Your entire moat is that you are more precisely right about Indian payments regulation than the panel expects. Two numbers currently threaten it:

- **The "~74% business decline" figure.** It almost certainly misreads BD-share-of-declines as a decline rate. **Choose:** (a) pull the NPCI Autopay table by hand, quote the literal column, show the header on screen; (b) drop the number entirely and lead with "20 million+ AutoPay mandates revoked every month for insufficient balance," which is defensible from the same article; (c) keep it with an explicit "reported by Business Standard citing sources" attribution. **Recommendation: (a), falling back to (b).** Never (c) — the risk isn't attribution, it's that the number is wrong.
- **The NPCI 1+3 retry cap.** Public sources conflict inside a single search result set. **Choose:** (a) find and quote a primary NPCI circular span; (b) downgrade `strength` to `INDUSTRY_NORM` and say so on screen; (c) drop the gate. **Recommendation: (a), falling back to (b) — never (c),** because a gate graded `INDUSTRY_NORM` with an honest note is *stronger* evidence of judgment than a gate you deleted or a gate you overclaimed.

Related, already decided and non-negotiable: **delete MCC 7322 and 6529**, **scope the T+5 gate to debited-but-unconfirmed only**, **grade the 08:00–19:00 window `SELF_IMPOSED`**, **make the 24h pre-debit notice advisory-not-blocking**, and **never say a repo count.**

> The general principle, and the thing that converts this from a good project into a winning one: a panel trusts a table more when the author has visibly graded their own confidence. Adding a `strength` column and marking your weakest rows honestly is worth more than any additional feature you could build in the time it takes.