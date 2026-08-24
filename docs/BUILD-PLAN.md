# Razorpay AI Buildathon 2026 — Build Plan

**Deadline:** 5 September 2026 · **Today:** 24 August 2026 · **Days left: 12**

---

## 1. Competitive intelligence (measured, not guessed)

Public GitHub repos mentioning "razorpay buildathon", queried 24 Aug 2026 via the
GitHub search API. Counts are keyword matches in name + description + README, so
they are approximate, but the *ratios* are the signal:

| Query term | Repos | Maps to track |
|---|---:|---|
| `razorpay buildathon` (total) | **177** | all |
| `recovery` | **60** | Track 3 — Revenue Recovery |
| `risk` | 32 | Track 2 (generic word, inflated) |
| `reconciliation` | 16 | Track 4 — Finance Controller |
| `commerce` | 14 | Track 1 — Agentic Commerce |
| `fraud` | 12 | Track 2 — Risk Manager |
| `checkout` | 10 | Track 1 |
| `settlement` | 7 | Track 4 |
| **`chargeback`** | **1** | Track 2, chargeback sub-problem |

The repo list is churning by the minute — 10 repos updated within 20 minutes of
each other. Expect 400–700 submissions by 5 Sept.

**Conclusion: Track 3 (Revenue Recovery) is a bloodbath. Track 2's chargeback
sub-problem is nearly empty.**

Why Track 2 is under-subscribed:
1. The brief says *"Strictly defense-only: anything offense-capable is
   disqualified"* — that scares people off.
2. It demands **precision/recall on a held-out test set**, which most students
   avoid because it means building an eval harness, not just a demo.
3. "Fraud detection" sounds like an ML track, so LLM-first builders self-select out.

All three are advantages for you: the barrier is *discipline*, not *ML depth*, and
the chargeback sub-problem is heavily LLM-native (document understanding, rulebook
reasoning, argument drafting) — which is exactly your background.

---

## 2. Decision

> **Track 2 — AI Risk Manager.**
> Sub-problem: **chargeback representment** (the "chargeback response" example
> direction named in the official brief).

Official framing to satisfy:
- Problem: *"Stop the merchant losing money to fraud, returns and chargebacks."*
- Deliverable: *"a working detector, verifier or auto-responder for one class of
  loss, with measured precision and recall on a held-out test set."*
- The bar: *"Honest metrics including false-positive cost. Strictly defense-only."*

We are building an **auto-responder** — the least-attempted of the three verbs.

---

## 3. The product

**Working name: `Vakil`** (Hindi: *advocate* — one who argues your case).
Alternatives: `Represent`, `Rebut`, `CaseFile`.

> Vakil is an autonomous chargeback defence agent for Razorpay merchants. It
> decides **whether a dispute is worth fighting**, assembles a network-compliant
> evidence pack, drafts a rebuttal letter where every factual claim is traceable
> to a source document, files it through the Razorpay Disputes API in test mode,
> and logs the whole thing to a tamper-evident ledger.

### Why this wins on the judging bar

| Bar | How Vakil answers it |
|---|---|
| "auto-responder" | Files real contest requests via Razorpay's Contest Dispute API |
| "measured precision and recall" | Held-out test set of 300 synthetic disputes with ground-truth outcomes; P/R/F1 + calibration reported by CI on every commit |
| "honest metrics including false-positive cost" | The **Fight-or-Fold engine** prices false positives in ₹ explicitly — this *is* the centrepiece, not an afterthought |
| "strictly defense-only" | A `THREAT-MODEL.md` proving the system has no offensive capability; the synthetic data generator is documented as an eval fixture, never a fraud tool |
| "one failure handled gracefully" | Deadline-miss and missing-evidence paths are first-class, demoed live |

---

## 4. The four features judges have not seen before

These are the differentiators. Everything else is table stakes.

### 4.1 Fight-or-Fold expected-value engine ⭐ the headline

Nobody else will build this. Most submissions will assume "always fight". Fighting
is not free:

```
EV(fight) = P(win) × dispute_amount
          − representment_cost          (ops + gateway fee)
          − P(lose) × arbitration_risk
          − vamp_ratio_penalty          (Visa VAMP: too many disputes = fines/T&C review)
```

The agent outputs one of `FIGHT` / `FOLD` / `PRE-EMPTIVE REFUND`, with the money
shown. This is *precisely* what "honest metrics including false-positive cost"
is asking for — a false positive here is a case you fought and lost, and you can
put a rupee number on it.

Ship a slider in the UI: as the merchant's dispute ratio approaches the VAMP
threshold, the agent gets more selective. Live, on stage, in five seconds.

### 4.2 Claim-level provenance gate (hallucination firewall)

The rebuttal letter is LLM-generated — but every factual sentence must carry a
pointer to a span in a source document (order record, courier POD, chat log). A
post-generation verifier re-checks each claim against its cited span and **strips
or flags anything unverifiable**.

Demo moment: delete the delivery proof, regenerate — the letter visibly loses the
delivery paragraph instead of inventing one. That single interaction tells a
payments judge you understand why LLMs are dangerous near money.

### 4.3 Network rulebook RAG with citations

Reason code → the exact evidence the network requires. Visa CE 3.0 (automatic
qualification, enforced globally through 2026), Visa VCR reason codes (10.4, 13.1,
13.3…), Mastercard's consolidated codes, RBI/NPCI dispute norms.

The **CE 3.0 qualifier is deterministic, not LLM**: 2+ prior undisputed
transactions, 120+ days old, matching ≥2 of {device ID, IP, shipping address,
account ID}. Binary, auditable, cited. Hybrid rules-plus-LLM is exactly the
architecture a payments company respects.

### 4.4 Hash-chained audit ledger with deterministic replay

Every decision — retrieval, rule hit, model call, EV computation, submission —
appended to a hash-chained log (`hash_n = H(hash_{n-1} ‖ event_n)`). Tamper-evident.
`vakil replay <case_id>` reconstructs any past decision exactly.

Demo moment: edit one row in the ledger, run `vakil verify` → chain breaks, loud
red failure. Auditability is a *payments* value, not a generic one.

---

## 5. Architecture

```
                    ┌──────────────────────────────────────┐
  Razorpay          │  INGEST                              │
  Disputes API ────►│  webhook payment.dispute.created     │
  (test mode)       │  + synthetic corpus loader           │
                    └───────────────┬──────────────────────┘
                                    ▼
                    ┌──────────────────────────────────────┐
                    │  TRIAGE (deterministic)              │
                    │  reason_code → rulebook requirements │
                    │  respond_by → SLA clock              │
                    └───────────────┬──────────────────────┘
                                    ▼
                    ┌──────────────────────────────────────┐
   mock connectors  │  EVIDENCE HARVEST (parallel tools)   │
   ┌── orders   ───►│  order · POD · comms · device/IP ·   │
   ├── courier  ───►│  prior-txn history · refund policy   │
   ├── support  ───►│  VLM parses scanned PODs + invoices  │
   └── devicefp ───►└───────────────┬──────────────────────┘
                                    ▼
                    ┌──────────────────────────────────────┐
                    │  CE 3.0 QUALIFIER (pure rules)       │
                    │  + WIN-PROBABILITY MODEL (calibrated)│
                    └───────────────┬──────────────────────┘
                                    ▼
                    ┌──────────────────────────────────────┐
                    │  FIGHT-OR-FOLD  →  EV in ₹           │
                    │  FIGHT / FOLD / PRE-EMPTIVE REFUND   │
                    └───────────────┬──────────────────────┘
                          FIGHT     ▼
                    ┌──────────────────────────────────────┐
                    │  DRAFT (LLM) → PROVENANCE GATE       │
                    │  every claim ↔ source span or cut    │
                    └───────────────┬──────────────────────┘
                                    ▼
                    ┌──────────────────────────────────────┐
                    │  FILE  Documents API → Contest API   │
                    │  confidence-gated: auto vs human-ILP │
                    └───────────────┬──────────────────────┘
                                    ▼
                        HASH-CHAINED AUDIT LEDGER
```

### Agent design notes
- **State machine, not a free-running loop.** LangGraph with a Postgres
  checkpointer. Every money-adjacent node is bounded and resumable.
- **Autonomy is confidence-gated.** High confidence + low amount → auto-file.
  Low confidence or high amount → queued for human review with a pre-filled draft.
  Show both paths in the demo.
- **Deterministic where it must be.** CE 3.0 qualification, EV maths, deadline
  logic: plain Python, unit-tested. The LLM does language and judgement only.

---

## 6. Tech stack

| Layer | Choice | Why it reads as current |
|---|---|---|
| Orchestration | **LangGraph** + Postgres checkpointer | durable, replayable agent state |
| Model | **Claude (`claude-sonnet-5`, `claude-opus-5` for drafting)** via Anthropic SDK | tool use + structured outputs |
| Cost control | **prompt caching** on rulebook context | show tokens/₹ per case in the README |
| Doc parsing | **Docling** + VLM fallback for scanned PODs | real multimodal, not a toy |
| Retrieval | Postgres + **pgvector**, hybrid BM25 + dense | rulebook citations |
| API | **FastAPI** + Pydantic v2 | typed contracts everywhere |
| UI | **Next.js 15** + Tailwind + shadcn/ui | merchant console + case viewer |
| Tracing | **OpenTelemetry** + Langfuse | every agent step visible |
| Evals | pytest + custom harness, **run in CI** | P/R/F1, calibration, Brier score |
| Infra | Docker Compose, `make demo` one-liner | judge can run it in 60s |
| CI | GitHub Actions running the eval suite per push | badge in README showing live metrics |

The CI-runs-the-eval-suite detail is worth an unreasonable amount. A README badge
reading `precision 0.87 · recall 0.79 · n=300` regenerated on every commit is the
single most credible artefact you can ship.

---

## 7. The dataset (do this first — it gates everything)

You cannot get real chargeback data. Build a **synthetic corpus generator** and be
loud and honest about it:

- 300 disputes, stratified across reason codes (10.4 fraud, 13.1 non-delivery,
  13.3 not-as-described, 12.x processing errors) and India-specific patterns
  (COD/RTO abuse, UPI-adjacent disputes, subscription friendly fraud).
- Each case: order record, courier events, support transcript, device/IP history,
  prior transactions, refund policy — some **deliberately incomplete**.
- **Ground-truth label** = whether representment should have won, generated from
  the rule fixture (not from the model being tested).
- Frozen **held-out split** (`test/` 100 cases), committed with a hash, never
  looked at while iterating.
- Documented in `DATA-CARD.md` — generation method, distribution, known biases,
  and an explicit statement that no real customer data is used.

A frozen, hashed, honestly-documented held-out set will put you ahead of 95% of
submissions on its own.

---

## 8. Twelve-day schedule

| Day | Date | Deliverable | Done when |
|---|---|---|---|
| 1 | Aug 24 | Repo, Docker Compose, FastAPI skeleton, Razorpay test keys, `DATA-CARD.md` drafted | `make up` boots clean |
| 2 | Aug 25 | Synthetic corpus generator; 300 cases; train/test split frozen + hashed | `make data` reproducible from seed |
| 3 | Aug 26 | Rulebook corpus ingested (Visa CE3.0/VCR, Mastercard, RBI/NPCI); pgvector retrieval with citations | reason code → cited requirements |
| 4 | Aug 27 | Deterministic core: CE 3.0 qualifier + deadline logic, fully unit-tested | 100% branch coverage on rules |
| 5 | Aug 28 | Evidence harvest connectors + Docling/VLM parsing of PODs | full evidence bundle per case |
| 6 | Aug 29 | Win-probability model + **Fight-or-Fold EV engine**; calibration curve | EV in ₹ printed per case |
| 7 | Aug 30 | LLM drafting + **provenance gate**; unverifiable claims stripped | delete-POD demo works |
| 8 | Aug 31 | Razorpay Documents + Contest API integration (test mode); confidence gating | a dispute actually moves to `under_review` |
| 9 | Sep 1 | **Eval harness**: P/R/F1, calibration, false-positive cost in ₹; wired into GitHub Actions | README badge live |
| 10 | Sep 2 | Next.js console: case list, evidence viewer, EV panel, ledger verifier | clickable end to end |
| 11 | Sep 3 | Hash-chained ledger + `replay` + `verify`; `THREAT-MODEL.md`; failure paths | tamper demo breaks the chain |
| 12 | Sep 4 | README, architecture doc, **5-min pitch video**, rehearse | submitted |
| — | Sep 5 | Buffer. Submit early in the day. | — |

Hard rule: **days 1–2 and day 9 are non-negotiable.** If you slip, cut the Next.js
console (day 10) down to a CLI + static report. Metrics beat UI with these judges.

---

## 9. Repo layout

```
vakil/
├── README.md                  # problem, demo GIF, metrics badge, 60-second quickstart
├── ARCHITECTURE.md            # the diagram above + why each boundary exists
├── THREAT-MODEL.md            # defense-only proof; explicit non-capabilities
├── DATA-CARD.md               # synthetic corpus: method, distribution, biases
├── EVALUATION.md              # metrics, held-out protocol, calibration, FP cost in ₹
├── DECISIONS.md               # ADRs — what broke and how you fixed it
├── Makefile                   # make up | data | eval | demo | replay
├── docker-compose.yml
├── .github/workflows/eval.yml # runs the held-out eval on every push
├── src/vakil/
│   ├── ingest/                # webhooks, corpus loader
│   ├── rules/                 # CE 3.0 qualifier, deadlines — pure, deterministic
│   ├── rulebook/              # RAG over network rulebooks
│   ├── evidence/              # connectors + Docling/VLM parsing
│   ├── decide/                # win model + Fight-or-Fold EV engine
│   ├── draft/                 # LLM drafting + provenance gate
│   ├── file/                  # Razorpay Documents + Contest API
│   ├── ledger/                # hash chain, replay, verify
│   └── graph.py               # LangGraph state machine
├── data/{generator,train,test}
├── evals/
└── web/                       # Next.js console
```

`DECISIONS.md` matters: the application form explicitly asks **what broke and how
you solved it.** Keep it as you go — do not reconstruct it on day 12.

---

## 10. Metrics to report (and to put in the README)

1. **Fight-or-Fold classifier**: precision, recall, F1 on the held-out 100.
2. **Calibration**: reliability diagram + Brier score for predicted win probability.
   Almost nobody will do this; it is the mark of someone who takes probability seriously.
3. **False-positive cost in ₹**: total money burned fighting cases that lost.
4. **Net recovery**: ₹ recovered vs a naive "always fight" baseline *and* an
   "always fold" baseline. Beating both is the money shot.
5. **Throughput + unit cost**: cases/minute, tokens and ₹ per case.
6. **Honest exception list**: the cases it refused to decide, and why.

Report the numbers where you lose. A submission that says "we underperform on
13.3 not-as-described because subjective quality claims lack objective evidence"
is more convincing than one claiming 0.99 everywhere.

---

## 11. Five-minute pitch structure

| Time | Beat |
|---|---|
| 0:00–0:30 | The money. Card fraud up 25% per RBI; representment win rates sit at 8–20% manual, 50%+ with structured evidence. Merchants leave that on the table. |
| 0:30–1:00 | Insight: the hard question isn't *how* to fight — it's *whether* to. |
| 1:00–2:30 | Live: dispute arrives → evidence harvested → CE 3.0 qualified with citation → **EV says FOLD on one, FIGHT on another** → letter drafted → filed to Razorpay test mode. |
| 2:30–3:15 | **Delete the POD, regenerate.** The claim disappears instead of being invented. |
| 3:15–3:45 | **Tamper the ledger, run verify.** Chain breaks. |
| 3:45–4:30 | Metrics slide: P/R, calibration curve, ₹ recovered vs both baselines, exceptions. |
| 4:30–5:00 | Architecture in one breath: deterministic where money moves, LLM where language lives. What broke, what you'd build next. |

Rehearse until you can do it without notes. The panel round is *"present your
architecture"* — they will probe whether you actually built it. Be able to defend
every boundary in that diagram.

---

## 12. Risks

| Risk | Mitigation |
|---|---|
| Razorpay test mode won't produce real disputes | Disputes can't be self-triggered in test mode — build against the documented schema with a mock server, and be upfront in the README. Ship the real API client either way. |
| Synthetic data reads as fake | Own it loudly in `DATA-CARD.md`. Honest synthetic beats hand-waved "real". |
| Scope creep kills day 9 | Console is the cut line, not the eval harness. |
| Judged as "just prompt engineering" | The deterministic rules core + calibration + ledger are the defence. Keep them visible in the README. |
| Accidentally looks offense-capable | `THREAT-MODEL.md` up front; the corpus generator is framed and documented strictly as an eval fixture. |

---

## Sources

- [Razorpay AI Buildathon — official](https://razorpay.com/buildathon/)
- [Razorpay Disputes API — entity schema](https://razorpay.com/docs/api/disputes/entity/)
- [Razorpay Docs — Submit Evidence](https://razorpay.com/docs/payments/disputes/submit-evidence/)
- [Razorpay Docs — Contest a Dispute](https://razorpay.com/docs/api/disputes/contest/)
- [Visa Compelling Evidence 3.0 guide](https://corepay.net/articles/visa-compelling-evidence-3-0-ultimate-guide/)
- [Visa & Mastercard chargeback rules 2026](https://paymentbrief.com/articles/visa-vcr-mastercard-chargeback-rules-2026/)
- [VAMP 2026 merchant playbook](https://cside.com/blog/vamp-2026-merchant-playbook)
- [Chargeback representment win rates](https://beastinsights.com/blog/chargeback-representment)
- [Razorpay blog — payment gateways and fraud risk 2026](https://razorpay.com/blog/payment-gateways-reduce-fraud-risk)
