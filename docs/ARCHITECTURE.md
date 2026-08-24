# Architecture

## The load-bearing decision

**The model never decides where money goes.**

Claude appears in exactly two stages: reading scanned delivery proofs, and
writing the rebuttal letter. Triage, CE 3.0 qualification, the expected-value
arithmetic, the autonomy gate and the audit chain are ordinary Python with unit
tests. Nothing in `vakil.decide` or `vakil.rules` can hallucinate, and the one
place prose is generated is verified by code before anything is filed.

Everything below follows from that.

## The pipeline

```
                    ┌──────────────────────────────────────┐
  Razorpay          │  1 INGEST                            │
  Disputes API ────►│  webhook, HMAC verified before parse │  vakil.api
  (test mode)       │  or corpus loader for offline runs   │  vakil.ingest
                    └───────────────┬──────────────────────┘
                                    ▼
                    ┌──────────────────────────────────────┐
                    │  2 TRIAGE            deterministic    │  vakil.rules
                    │  reason_code → rulebook requirements  │  .deadlines
                    │  respond_by  → SLA tier               │
                    └───────────────┬──────────────────────┘
                                    ▼
                    ┌──────────────────────────────────────┐
   mock connectors  │  3 EVIDENCE HARVEST        MODEL      │  vakil.evidence
   ┌── orders   ───►│  parallel fetch; VLM parses scanned   │
   ├── courier  ───►│  PODs and invoices, keeping the span  │
   ├── support  ───►│  each extracted value came from       │
   └── devicefp ───►└───────────────┬──────────────────────┘
                                    ▼
                    ┌──────────────────────────────────────┐
                    │  4 CE 3.0 QUALIFIER  deterministic    │  vakil.rules.ce3
                    │  2 priors, 120-365d, ≥2 identifiers   │
                    └───────────────┬──────────────────────┘
                                    ▼
                    ┌──────────────────────────────────────┐
                    │  5 FIGHT-OR-FOLD     deterministic    │  vakil.decide
                    │  win model → EV in ₹ → verdict        │  .win / .ev
                    └───────────────┬──────────────────────┘
                          FIGHT     ▼
                    ┌──────────────────────────────────────┐
                    │  6 DRAFT + PROVENANCE GATE  MODEL     │  vakil.draft
                    │  every claim cites a span, or is cut  │  (gate is code)
                    └───────────────┬──────────────────────┘
                                    ▼
                    ┌──────────────────────────────────────┐
                    │  7 FILE              deterministic    │  vakil.file
                    │  Documents API → PATCH → contest      │
                    │  auto-file gated on amount+confidence │
                    └───────────────┬──────────────────────┘
                                    ▼
                    ┌──────────────────────────────────────┐
                    │  8 LEDGER            deterministic    │  vakil.ledger
                    │  hash-chained, replayable, verifiable │
                    └──────────────────────────────────────┘
```

## Stage notes

### 1 · Ingest
A dispute is a *pointer*, not a case file: it carries an id, an amount, a reason
code and a deadline, and nothing about the order, the customer, or the delivery.
Building the case file is stages 3-4. Webhook signatures are verified over the
raw body before the payload is parsed — an unsigned dispute event is an
instruction from a stranger to spend money.

### 2 · Triage
Reason code to requirements is a **deterministic lookup, not a retrieval.** The
mapping is twenty cited rows; putting a vector search between a dispute and the
evidence the network demands would place approximation where a table is exact.
Semantic search exists (BM25, `rulebook/search.py`) but serves only the drafting
stage's open-ended questions, and filters by reason code rather than boosting,
because a citation to the wrong dispute condition is worse than no citation.

Gap analysis then compares what the condition requires against what the harvest
found, mapping both onto Razorpay evidence field names. Gaps inform the drafting
stage and the console; they do not gate the decision, because a human cannot
conjure a missing delivery receipt either.

Two pure functions. Reason code determines which evidence the network will
accept, and getting it wrong wastes the case. SLA tier determines how much work
the pipeline is allowed to do: under 12 hours it skips optional document parsing
and files a reduced pack, because a thin filing beats a perfect one that arrives
after the window closes. Expired disputes short-circuit before any model call is
paid for.

### 3 · Evidence harvest
Six connectors in parallel. The courier POD is the interesting one — a scan or a
photograph, from which a VLM extracts `{delivered_at, signed_by, address,
tracking_id}` **along with the span each value came from**. That provenance is
not decoration; stage 6 cannot function without it.

Incomplete bundles are the normal case, not an error path.

### 4 · CE 3.0
Visa's Compelling Evidence 3.0 lets a merchant rebut a card-absent fraud claim
by proving a prior relationship: two earlier undisputed transactions, 120-365
days old, sharing at least two of {device id, IP, shipping address, account id}.
When it qualifies, the issuer is obliged to accept the evidence.

Forty lines, nine tests, and every rejection names the clause it failed. A
missing identifier never counts as a match — absence of evidence must not be
scored as agreement, or the system would manufacture qualifications out of
missing data.

### 5 · Fight-or-Fold
Covered in detail in [BUILD-PLAN.md](BUILD-PLAN.md) and D4/D5 of
[DECISIONS.md](DECISIONS.md). Two lanes:

- **post-dispute** — FIGHT / FOLD / ESCALATE. Folding nets zero; the money is
  already debited. Network dispute ratio is not a term, because the dispute is
  already counted whether or not it is contested.
- **pre-dispute** — a payment likely to be disputed but not yet. Refunding now
  costs the same rupees as losing later, but the dispute never enters the ratio.
  This is the only lane where VAMP pressure legitimately moves the decision.

### 6 · Draft and gate
The letter is generated, then decomposed into atomic claims. Each claim must
resolve to a span in a source document; unverifiable claims are **removed**, not
flagged for later review. Delete the POD from a case and the delivery paragraph
disappears rather than being invented.

The generator is a model. The gate is code. That asymmetry is the design.

### 7 · File
`POST /v1/documents` per file (`purpose=dispute_evidence`), then `PATCH
/v1/disputes/{id}` with the evidence object, then `POST
/v1/disputes/{id}/contest`. Status moves `open → under_review`.

Auto-filing requires high verdict confidence *and* an amount below a configured
ceiling. Everything else queues for a human with the pack already assembled.

Real disputes cannot be raised on demand in Razorpay test mode, so
`vakil.file.mock_razorpay` provides something to file against. The real client
ships either way.

### 8 · Ledger
`hash_n = sha256(hash_{n-1} ‖ canonical_json(event_n))`, appended per stage.
`verify()` names the first broken link; `replay()` refuses to run on a broken
chain, because replaying tampered history would launder it.

## Why LangGraph and not a loop

The pipeline is a **checkpointed state machine**, not a free-running agent loop.
Every node's inputs and outputs persist to Postgres, so a crash at stage 6
resumes at stage 6, and replay is nearly free because the state is already on
disk. An agent that decides its own next step is the wrong shape for a process
where every step is known in advance and the stakes are financial.

## Services

```
docker compose up
├── postgres    cases, pgvector rulebook index, ledger      :5432
├── api         FastAPI: webhooks, case + ledger endpoints  :8000
├── mock-rzp    Razorpay Disputes/Documents stand-in        :8080
└── web         Next.js merchant console (day 10)           :3000
```

## Module map

| module | contains | model? |
|---|---|:--:|
| `vakil.models` | domain types; money is always integer paise | no |
| `vakil.ingest` | webhook parsing, corpus loader | no |
| `vakil.rules` | deadlines, CE 3.0 qualifier | no |
| `vakil.rulebook` | cited requirements lookup + BM25 search + gap analysis | no |
| `vakil.evidence` | connectors, document extraction | yes |
| `vakil.decide` | win model, EV engine, pipeline | no |
| `vakil.draft` | letter generation; provenance gate | generation only |
| `vakil.file` | Razorpay client, mock server | no |
| `vakil.ledger` | hash chain, verify, replay | no |
