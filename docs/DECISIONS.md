# Decisions

The application form asks what broke and how it was solved. This file is kept
as the work happens, not reconstructed afterwards. Newest last.

---

## D1 · Track selection driven by measured competition, not intuition

**24 Aug 2026**

The first instinct was Track 3, Revenue Recovery - the most obviously tractable
brief. A GitHub search API sweep on the day said otherwise:

| query | repos |
|---|---:|
| `razorpay buildathon` (all) | 177 |
| `recovery` | 60 |
| `reconciliation` | 16 |
| `commerce` | 14 |
| `fraud` | 12 |
| `chargeback` | **1** |

Revenue Recovery is the most crowded track by a factor of five. Chargeback
representment - named explicitly in the Track 2 brief as an example direction -
was essentially unclaimed.

**Decision:** Track 2, AI Risk Manager, chargeback auto-responder.

The track is under-subscribed because its bar is uncomfortable: precision and
recall on a held-out test set, and disqualification for anything offence-capable.
Both are discipline problems rather than capability problems, and the underlying
work - document understanding, rulebook reasoning, argument construction - is
LLM-shaped rather than ML-shaped.

---

## D2 · The corpus was measuring the calendar, not the model

**24 Aug 2026 · found by the eval harness on its first run**

The first held-out run escalated **58 of 100 cases**, nearly all with "response
deadline passed". The generator had been anchoring each dispute to its order
date - orders placed 10-75 days ago, disputes raised 5-60 days after that, and a
7-day response window - so most cases were long expired against the evaluation
clock.

This is not what a live dispute inbox looks like. Worse, it meant the headline
metric was dominated by date arithmetic in the fixture rather than by any
decision the system made.

**Fix:** disputes are now generated relative to a fixed `CORPUS_NOW`, landing
0-8 days back, with an explicit `EXPIRED_FRACTION = 0.08` so the expired path is
still exercised deliberately rather than accidentally. Escalations fell from 58
to 33.

**Lesson worth keeping:** the eval harness earned its cost on its first run, and
it did so by finding a bug in the *data*, not the model. Building it on day 1
instead of day 9 was the right call.

---

## D3 · Verdict confidence was penalising well-calibrated uncertainty

**24 Aug 2026 · found by a failing test**

`_confidence()` originally blended two terms: how far net EV sat from zero, and
how far `p_win` sat from 0.5. A test asserting that two cases with identical
odds and different amounts should produce opposite verdicts failed - the large
case escalated instead of fighting.

The second term was wrong on its own terms. A well-calibrated `p_win` of 0.45 is
a *precisely known* quantity, not an uncertain one. Treating mid-range
probabilities as low-confidence escalated cases whose arithmetic was in fact
unambiguous, and would have buried a human reviewer in cases the machine had
already answered correctly.

**Fix:** confidence is now a function of EV margin alone -
`min(|net_ev| / amount, 1)`. What makes a verdict shaky is expected value
sitting near zero relative to the money at stake, nothing else.

---

## D4 · Pre-emptive refund does not belong in the post-dispute lane

**24 Aug 2026**

The original pitch had one engine emitting FIGHT / FOLD / PRE-EMPTIVE REFUND,
with network dispute-ratio (VAMP) pressure as an input throughout. Writing it
down exposed the error: **once a chargeback exists, it is already counted in the
ratio numerator.** Refunding after the fact does not remove it, so VAMP pressure
cannot rationally change a post-dispute decision, and offering "pre-emptive
refund" on an already-raised dispute is incoherent.

**Fix:** two explicitly separated lanes.

- **Post-dispute** - FIGHT / FOLD / ESCALATE. Folding nets zero because the
  money is already debited. VAMP is not a term.
- **Pre-dispute** - a payment that looks likely to be disputed but has not been.
  Refunding now costs the same rupees as losing later, but the dispute never
  enters the ratio at all. This is the only lane where VAMP legitimately moves
  the decision, and it is where PRE-EMPTIVE REFUND lives.

A modelling assumption is stated in the module docstring and repeated in the
eval report: we assume a successfully represented dispute is **not** removed
from the ratio numerator. Programs differ. This is the conservative reading, so
the pre-dispute lane's advantage is a floor rather than a claim.

---

## D5 · The filing cost may be too small for Fight-or-Fold to matter

**24 Aug 2026 · resolved same day, see D6**

The first honest eval says Vakil **loses to always-fight** by ₹54,838 across 100
cases, with 0 true negatives - the EV engine essentially never folds.

The arithmetic explains why. Fighting is positive-EV whenever
`p_win > filing_cost / amount`. At the default ₹250 filing cost against disputes
averaging ~₹5,000, that threshold is about 5%. Almost nothing scores below 5%,
so almost nothing folds, and a strategy that never folds cannot beat one that
never folds *and* fights everything.

Two readings, and they lead to different products:

1. **₹250 is wrong.** It prices the gateway fee and ignores the analyst who
   spends 30-60 minutes assembling a pack. Fully loaded, ₹800-2,500 is closer to
   the truth for a merchant doing this by hand - and at ₹2,000 the fold
   threshold moves to ~40%, where the model's discrimination starts to pay.
   But an automated system's marginal filing cost genuinely *is* near ₹250,
   which is an argument that Vakil should fight nearly everything.
2. **Fight-or-Fold pays off on the tails, not the mean.** Its value concentrates
   in low-value disputes, cases with real arbitration exposure, and merchants
   under VAMP pressure. If so, the headline metric should be uplift on those
   segments, and the corpus needs a heavier small-ticket tail to show it.

Reading 2 is probably right, and reading 1 is probably also right about the
number. The next step is a cost-sensitivity sweep - net recovery as a function
of filing cost across the plausible ₹250-2,500 range - so the claim becomes
"here is the regime where choosing beats fighting everything" rather than an
unqualified win. That sweep is honest whichever way it comes out, and it is a
better story than a tuned constant.

**Not doing:** quietly raising `VAKIL_REPRESENTMENT_COST` until the baseline
comparison flips. That would be fitting the economics to the demo.

---

## D6 · Fight-or-Fold is worth exactly one thing, and it is not the headline

**24 Aug 2026 · resolves D5 ·
[`evals/cost_sweep.md`](../evals/cost_sweep.md)**

Ran the sweep D5 called for: net realised recovery against filing cost across
Rs 250-2,500 on the held-out split, plus a split by dispute size.

### A methodology problem found first

The initial sweep credited escalated cases with zero, the same as folds. That
silently rewards abstention: raise the filing cost, expected values drift toward
zero, verdict confidence drops, more cases escalate - and Vakil's "net" improves
because the hard cases quietly leave the accounting. At Rs 2,000 it was
escalating 66 of 100 and being scored on the 34 it kept.

Every comparison is now reported at **both bounds**: optimistic (the human folds
every escalated case) and pessimistic (the human fights them all). A win that
holds only at the optimistic bound is a result about abstention, not about
deciding well, and is labelled as such.

### The answer

| filing cost | uplift vs always-fight (opt) | (pess) |
|---:|---:|---:|
| Rs 250 | −Rs 54,838 | Rs 0 |
| Rs 1,200 | −Rs 19,781 | +Rs 1,200 |
| Rs 1,600 | −Rs 2,275 | +Rs 1,600 |
| **Rs 2,000** | **+Rs 26,330** | **+Rs 12,000** |
| Rs 2,500 | +Rs 62,331 | +Rs 22,404 |

**Robust crossover: Rs 2,000.** It holds at both bounds, so it is not an
abstention artefact.

Split by dispute size at Rs 2,000, terciles of this corpus:

| band | n | uplift (opt) | uplift (pess) | robust |
|---|---:|---:|---:|:--:|
| small (< Rs 4,999) | 26 | +Rs 16,616 | +Rs 12,000 | yes |
| mid | 36 | +Rs 11,209 | Rs 0 | no |
| large (> Rs 7,499) | 38 | −Rs 1,495 | Rs 0 | no |

The small band is the whole effect. There, fighting everything is **outright
loss-making** - always-fight nets −Rs 16,616, because a Rs 2,000 filing cost
against a Rs 3,000 dispute cannot pay for itself at achievable win rates. Vakil
folds all six it decides and nets zero, which is the correct answer.

### What this changes

Reading 2 in D5 was right, and more narrowly than expected.

1. **The headline metric is no longer "net recovery vs both baselines."** That
   comparison flatters or damns Vakil depending entirely on an assumed filing
   cost, which is a parameter, not a result. The headline becomes: *here is the
   regime in which deciding beats fighting everything, and here is its size.*
   The sweep is the deliverable, not a single number pulled from it.
2. **Small-ticket disputes are the product.** Which is a *better* story for the
   Indian market than the one we started with - low-value COD and subscription
   disputes are exactly where Indian merchants bleed, and exactly where no one
   can justify an analyst's hour.
3. **The corpus needs a heavier small-ticket tail.** Terciles of a corpus
   spanning Rs 1,899-24,998 put the "small" band at under Rs 5,000, which is not
   small. Real sub-Rs 1,000 disputes would sharpen this considerably, and their
   absence currently understates the effect. Next corpus change.
4. **Large disputes should just be fought.** No apology needed; the model says
   so and the model is right. Vakil's value on those is the evidence pack and
   the audit trail, not the verdict.

### Open, carried forward *(resolved same day in D7)*

The escalation rate at Rs 2,000 is 66%, which is too high to be useful - the
0.35 confidence floor is scaled by dispute amount, so raising the filing cost
pushes EV toward zero and floods the human queue. The floor should probably be
absolute (rupees of EV margin) rather than proportional. Worth fixing on day 6
alongside the model fit, because a system that abstains on two thirds of its
inbox has not automated anything.

**Not done:** raising `VAKIL_REPRESENTMENT_COST` to 200_000 so the README shows
a win. The default stays at Rs 250 - the honest marginal cost of an automated
filing - and the sweep reports what that implies.

---

## D7 · The escalation floor belonged in probability space, not rupees

**24 Aug 2026 · resolves the open item in D6**

D6 left the system abstaining on **66 of 100 cases** at a Rs 2,000 filing cost.
A system that hands two thirds of its inbox to a human has not automated
anything.

### Why it happened

Confidence was `min(|net_ev| / amount, 1)` with a floor of 0.35. Net EV shrinks
as the filing cost rises, so raising the cost dragged every case toward the
floor at once. The floor was measuring *the cost assumption*, not the strength
of the decision.

D3 had already moved this once - from a term that penalised mid-range `p_win` -
and landed on EV margin. That was better but still wrong on the same axis: it
expressed doubt in rupees, when the thing actually in doubt is a probability.

### The fix

Every input collapses into one number. Net EV is `p·A − C − (1−p)·X` for amount
A, filing cost C, arbitration exposure X. Set it to zero and solve:

```
p* = (C + X) / (A + X)
```

That is the win probability at which fighting exactly breaks even. The verdict
is just `p_win > p*`, and the *confidence* is the distance `|p_win − p*|`.

Escalate when that distance is under **8 percentage points**, which reads as:
*our estimate would have to be wrong by less than the model's own error for this
call to flip.* The floor is now a claim about model error, which is measurable.
Once the win model is fitted and calibrated on day 6, the 8 points should be set
from its observed error instead of chosen.

Three properties fall out for free:

- **Scale-free.** The same distance from break-even means the same confidence
  for a Rs 300 dispute and a Rs 300,000 one.
- **Cost-stable.** Raising the filing cost moves `p*` and flips verdicts, which
  is correct, instead of collapsing confidence, which was not.
- **`p* > 1` is meaningful.** The cost structure has priced the case out
  entirely; no win probability could justify filing. The engine folds with full
  confidence rather than deliberating.

### Effect

| | before | after |
|---|---:|---:|
| escalated at Rs 250 | 33 | **21** |
| escalated at Rs 2,000 | 66 | **34** |
| folds at Rs 2,000 | 6 | **14** |
| folds at Rs 2,500 | 12 | **21** |
| robust crossover | Rs 2,000 | Rs 2,000 |
| small-band uplift at Rs 2,000 (pess) | +Rs 12,000 | **+Rs 14,303** |

The engine now decides rather than abstains, and D6's conclusion survives with a
larger pessimistic margin than before.

**Single-cost precision fell 0.493 → 0.436.** That is the honest direction: the
abstention was inflating it. Cases the old floor quietly dropped are now being
decided and counted, including the ones it gets wrong.

One detail worth noting - at Rs 2,000 the *pessimistic* bound now scores higher
than the optimistic one (+Rs 16,304 vs +Rs 3,722). That inverts because escalated
cases now contain genuine winners a human would profitably fight, rather than the
systematically hopeless ones the old floor was dumping. Escalation has become a
signal about ambiguity instead of a symptom of the cost assumption.

---

## D8 · The rulebooks are proprietary, so the corpus is summaries with citations

**24 Aug 2026 · day 3**

The plan said "rulebook RAG with citations". Building it ran into the obvious
problem: **Visa Core Rules, the VCR dispute-condition guide and the Mastercard
chargeback guide are licensed documents.** They are not public, and reproducing
them in a public GitHub repository is not something a payments company would
want to see in a submission.

Three options were available:

1. Scrape whatever fragments are floating around and embed them. Fast, and
   wrong - it puts copyrighted text in the repo and cites summaries of summaries.
2. Skip the rulebook entirely and hard-code requirements in Python. Honest about
   provenance but unciteable, and a requirement without a source is an assertion
   this system is not entitled to make.
3. Author short requirement summaries, each carrying a citation to the rule it
   summarises, and mark every one unverified until checked against a licensed
   copy.

**Chose 3.** `data/rulebook/*.json` holds 20 entries across all six dispute
conditions plus India-specific context. Each has a `citation` with document,
section and URL, and a `verified` flag. Three are `verified=true` because they
come from Razorpay's public documentation; **17 are `verified=false`**, and
`vakil rules` and the eval report both print that count rather than burying it.

A real deployment licenses the rulebooks. Saying so is more credible than
pretending twenty hand-written summaries are the Visa Core Rules.

### The retrieval decision that matters more

Reason code to requirements is a **deterministic lookup, not a retrieval.**

Putting a vector search between a dispute and the evidence the network demands
would place approximation where a table is exact - and it is the same category
of error as letting the model decide the verdict. The mapping is 20 rows. It
does not need embeddings; it needs to be right every time and to cite its source.

Semantic search does exist, in `rulebook/search.py`, and it serves one narrow
purpose: the drafting stage asking open-ended questions like "what bears on a
delivery to a different address". It filters by reason code rather than boosting,
because a citation to the wrong dispute condition is worse than no citation.

The backend is **BM25 over twenty short entries**, in pure Python, no API key and
no database. That is not a placeholder for something better - on a corpus this
size lexical scoring is not obviously worse than dense retrieval, and Anthropic
ships no embeddings API, so "proper RAG" means adding a separate provider and a
real dependency for a corpus that fits on one screen. The `Retriever` protocol
makes it a drop-in when the corpus grows enough to justify it. The pgvector table
in `scripts/init.sql` is provisioned and deliberately unused.

Calling this "RAG" would be generous. It is a cited lookup table plus a small
lexical index, which is what the problem actually needs.

### Gaps inform, they do not gate

Gap analysis compares what a dispute condition requires against what the harvest
actually found, and maps both onto Razorpay's evidence field names -
`shipping_proof`, `refund_confirmation`, and so on. That bridge is the useful
part: a network requirement on one side, an API field on the other.

The temptation was to escalate any case missing required evidence. Resisted: a
human cannot conjure a delivery receipt either, and that would refill the queue
D7 just drained. A missing document lowers the win probability and the EV engine
folds on its own. Gaps go to the drafting stage, which must not claim what is not
held, and to the console, which can go and look.

Held-out metrics are unchanged by this commit, which is the correct outcome for a
change that adds information without moving a decision boundary.

### One bug worth recording

The first version marked both `visa-13.1-proof-of-delivery` and
`visa-13.1-proof-of-service` as `required` for non-delivery disputes. They are
mutually exclusive - a physical shipment cannot produce a service access log -
so every 13.1 case reported a gap no merchant could ever close.

Fixed with an alternative `group`: rules sharing a group satisfy each other, and
the group is closed as soon as any member is satisfied. Same mechanism now covers
the two 13.6 refund positions and the CE 3.0 pair.

Found by printing gap output for six real cases before writing any tests, which
is worth remembering - the test suite would have encoded the bug if written first.

---

## D9 · The scoreboard was not charging a cost the decision was paying

**24 Aug 2026 · day 6 · found by fitting the model**

Fitting the win model on `data/train` improved every classification number on
the held-out split:

| | prior (unfitted) | fitted |
|---|---:|---:|
| precision | 0.436 | **0.700** |
| recall | 1.000 | 0.903 |
| F1 | 0.607 | **0.789** |
| Brier | 0.259 | **0.175** |
| true negatives | 0 | **12** |

And made the money look *worse*: uplift versus always-fight fell from
-Rs 50,089 to -Rs 72,983, and the robust crossover moved the wrong way,
Rs 2,000 to Rs 2,500.

Better classification producing less money is the kind of contradiction that
usually means the measurement is wrong, so the measurement got read before the
model did.

### The bug

The EV engine prices arbitration exposure - Rs 800 expected on a lost
representment - into every decision. `evals/run_eval.py` charged **zero** for it
in realised results, on the reasoning that leaving it out was conservative.

That reasoning held only while the system fought almost everything. Once fitting
gave the engine the discrimination to actually fold, the omission stopped being
conservative and became a systematic penalty on folding: Vakil was declining
cases specifically because they carried arbitration risk, and then being scored
on a board where arbitration risk was free. Always-fight was getting 57 lost
representments' worth of exposure written off; Vakil, fighting 40 cases, got 12.

A strategy scored against a cost it was told to avoid will always look worse
than one that ignores the cost.

### The fix

Both accountings are now reported side by side, in `evals/report.md` and in the
sweep. Not one replacing the other - **both** - because switching to the
accounting that flatters the system, immediately after seeing it flatter the
system, is indistinguishable from moving the goalposts even when the reasoning
is sound. Showing the pair lets a reader check the reasoning instead of trusting
it.

| | no arbitration | arbitration charged |
|---|---:|---:|
| uplift vs always-fight at Rs 250 | -Rs 72,983 | -Rs 36,983 |
| robust crossover | Rs 2,500 | **Rs 1,200** |

Charging the cost consistently halves the gap at Rs 250 and moves the robust
crossover down to Rs 1,200 - which is inside the plausible range for a merchant
doing this by hand, rather than at the top of it. D6's conclusion survives
either way: at a genuinely automated Rs 250 marginal cost, fighting everything
is still hard to beat.

### What fitting actually produced

Calibration method chosen by 5-fold cross-validation **inside** the training
set. Isotonic lost, which is the expected outcome on 200 rows with 8% label
noise - it had the flexibility to carve steps around individual cases and did:

| calibrator | out-of-fold Brier |
|---|---:|
| Platt | **0.1613** |
| none | 0.1625 |
| isotonic | 0.1662 |

The learned coefficients agree with the domain, which is the reassuring part:
`ce3_qualified` is the strongest evidence signal at +2.15, `address_match`
+1.88, and `rc_13_3` is the most negative dispute condition at -1.36 - "not as
described" really is close to unwinnable on documents alone.

### The escalation margin is now measured

D7 moved the abstention floor into probability space and set it to 8 points,
which was a chosen number. It is now derived: the fitted model's expected
calibration error, **0.089**, recorded in the artefact and read by the pipeline.
Refitting moves it automatically - a better-calibrated model earns the right to
decide more cases, a worse one loses it.

The chosen 0.08 turned out to be within a thousandth of the measured value.
That is reassuring but it was luck, and luck is not a method.

### Provenance

`data/model/win_model.json` is committed so evaluation is reproducible without
a training step, and CI refits and asserts the artefact is unchanged. If the
training data moves and the artefact does not, every reported number would
silently describe a model that no longer exists. The model also carries its own
`source` field into the ledger and the eval report, so a run that quietly fell
back to the unfitted prior is identifiable after the fact rather than assumed
away.

---

## D10 · A second extraction backend, because the account has no credit

**24 Aug 2026 · day 5**

The extraction stage was built against Claude and verified as far as the wire:
the request reached Anthropic and returned a **billing** error rather than a
validation error, which confirms the request shape is right. The account has no
credit balance, and buying some was declined.

Track 2's bar is *"measured precision and recall"*. An unmeasured stage is worse
than one measured on a different vendor's model, so `GeminiExtractor` was added
behind the same `Extractor` protocol - free tier, reads PDFs, returns structured
JSON against a supplied schema.

Recorded plainly rather than presented as an architectural preference: **a mixed
stack is not desirable here, it is a funding constraint.** The system is
Claude-first and the Claude backend ships either way; Gemini is what makes the
number exist today. `evals/extraction_gemini.md` and `evals/extraction_claude.md`
are written separately so neither run silently overwrites the other, and the
comparison is the point.

Three details worth keeping:

- **Free-tier requests are rate limited**, so the extractor throttles to a
  configured RPM instead of discovering the limit through a wall of 429s. On 175
  documents that is the difference between finishing and being throttled into
  failure. 429 and 5xx are retried with wide spacing (the limit is per minute, so
  a fast retry just burns another slot); 400 and 403 are not, because a bad key
  will not improve by waiting.
- **The response schema is hand-written**, not derived from the Pydantic model.
  Gemini accepts an OpenAPI subset - no `$defs`, no `$ref`, and nullability is a
  flag rather than a union - so a generated schema would emit constructs the API
  rejects.
- **A blocked candidate raises rather than returning an empty document.** A
  safety-blocked response has no `parts`; reading that as "the page was blank"
  would quietly downgrade real evidence to none.

Raw HTTP via httpx rather than the `google-genai` SDK: this is a secondary
backend behind a two-method protocol, and one REST call is a smaller thing to
own than another dependency.

### Default extraction model

Separately, the Claude extraction default moved from `claude-sonnet-5` to
`claude-opus-5`. The sonnet default was chosen for cost without anyone asking
for it - the wrong way round for a stage whose job is reading damaged scans. On
175 documents the difference is roughly $3 against $1.20, which is not a reason
to silently pick the weaker model. The trade is now a documented config line.

---

## D11 · A four-hour run that produced nothing

**24 Aug 2026 · day 5**

The 175-document extraction run was estimated at ~33 minutes. It was still
going at four hours and was killed. It produced **zero output**.

The diagnosis, once a single request was allowed to finish: the free-tier daily
quota was exhausted, and Google holds each request for roughly **170 seconds**
before returning 429. Three compounding mistakes turned that into four hours:

1. **The retry ladder had no circuit breaker.** `RETRY_DELAYS = (20, 45, 90)`
   applied per document. With every call taking 170s and failing, each document
   cost about 11 minutes, and there were 175 of them. The first three failures
   had already established the fact; the code went on to prove it another 172
   times.
2. **429 was treated as one thing.** A per-minute rate limit clears on its own
   and is worth waiting out. An exhausted daily quota does not clear for hours.
   The status code cannot tell them apart, so classification now reads the
   response body - not lovely, but the difference is hours of wall-clock.
3. **Nothing was written until the end.** Results accumulated in memory and were
   serialised once, at completion. An end that never arrived meant four hours of
   real API calls left no trace.

### Fixes

- **`GeminiUnavailable`**, distinct from `GeminiError`. One malformed document
  is worth skipping; a network or quota wall is not going to improve over the
  next 174 documents, and the runner stops on it immediately.
- **Circuit breaker**: three consecutive failures of any kind aborts the run.
  The counter resets on success, so isolated bad pages do not trip it - a
  breaker that fires on noise becomes a breaker people disable.
- **Checkpointing**: every scored document is appended to a JSONL file as it
  completes. A killed run keeps everything it finished, and re-running resumes
  rather than restarting. Resumed rows count toward the totals, so the report
  describes the whole set rather than only what was re-sent.
- **The report says when it is partial.** An aborted run states how many
  documents were not attempted, above the metrics. A partial run's numbers are
  a different claim from a complete run's and a reader should not have to infer
  which they are looking at.
- **Granular timeouts.** A bare `timeout=120` did not stop a request that hung;
  connect now has its own 10-second ceiling, read keeps 90 seconds because a
  large PDF genuinely takes time. The client is also constructed once rather
  than per call, which was throwing away every kept-alive connection.

### What the estimate should have been

"~33 minutes" assumed throttling was the only per-document cost and that
failures would be fast. Both were assumptions stated as fact. The honest
version was "about 33 minutes if nothing goes wrong, and I have not measured
what happens when something does."

### Standing result

The 10-document run completed before the quota ran out and remains valid: 50
fields, 100% correct, zero fabrication, spot-checked against actual values on a
photo-tier document. Whether that holds across 875 fields is still unmeasured -
and if it does hold, the likely reading is that the fixtures are too easy rather
than that extraction is solved.

---

## D12 · "Reproducible" needed defining

**25 Aug 2026 · first CI runs**

The repository was pushed public and CI ran for the first time. It failed three
times, and every failure was worth having.

### Run 1 - an undeclared dependency

`ModuleNotFoundError: No module named 'PIL'`. The fixture generator imports
Pillow; `pyproject.toml` never declared it. It had been pip-installed by hand
on day 5 and never written down, so **every fresh clone would have failed** at
`make fixtures` - and nobody would have known until a judge tried to run the
repo.

This is the argument for pushing before the work is finished rather than after.
The machine had the dependency; the project did not, and only a second machine
could tell the difference.

### Run 2 - two broken guards, and the silent one was worse

The model-staleness check failed with `fatal: n: no such path`. A shell line
continuation had been written as a literal `\n`, so git was handed `"\n"` as a
path. Loud, obvious, and honest about being broken.

The manifest guard was the real problem. An earlier edit meant to extend it to
`data/fixtures/MANIFEST.json` had **silently failed to apply**, so for two runs
it passed while checking less than its own name claimed. A red check tells you
something is wrong. A green check that verifies less than you believe tells you
nothing while feeling like assurance - the same failure this project's own
metrics work keeps arguing against.

Both are now explicit `if` blocks. The backslash continuation bought nothing and
cost a working guard.

### Run 3 - the artefact was not canonical

With the guard finally running, it caught a real difference:

```
- "b": 0.059706702901627724     (Windows)
+ "b": 0.05970670290162777      (Linux)
```

5e-17. Last-bit float64 noise from a different libm implementation of `exp` and
`log`. Every one of the fifteen coefficients matched exactly, because those were
rounded to six places - the Platt calibration parameters and the intercept never
were.

The fix is not more tolerance in the guard, it is a canonical artefact. Every
persisted float is now rounded to `ARTIFACT_PRECISION = 10`, which is orders of
magnitude finer than any decision needs and orders of magnitude coarser than
platform noise. A test asserts it, so the invariant cannot quietly regress.

**The lesson is about the claim, not the bug.** "Deterministic" was written in
the `fit.py` docstring on day 6 and it was true in the sense meant - same seed,
same iteration count, no randomness. It was not true in the sense CI tests:
byte-identical output on any machine. Those are different claims and only one of
them supports "CI refits the model and asserts the artefact is unchanged".
Nothing but a second machine was ever going to reveal which one had been built.

### What the three runs proved

| | |
|---|---|
| ruff, mypy strict, 143 tests | pass on Linux |
| Corpus regeneration and hash | identical across platforms |
| Fixture rendering | identical - the Arial/DejaVu font fallback works |
| Model refit | identical **after** canonicalising the artefact |

---

## D13 · The gate verifies citations, not prose

**25 Aug 2026 · day 7**

The provenance gate is the mechanism the whole "model never decides" claim
rests on. Building it forced a choice about *what* gets verified.

The obvious design is post-hoc: let the model write a letter, then check whether
each sentence is supported by the evidence. That check has to read free prose
and judge whether it "seems supported" - which means a second model marking the
first model's homework. Two models agreeing is not verification, and neither one
can be read by a reviewer.

**Chosen instead: the drafter emits structured claims that name their sources.**
Each sentence carries `cites` (fact ids) and `asserts` (the values the sentence
states). The gate resolves each citation against a closed fact index built
deterministically from the harvest, and checks the asserted value matches. It
either resolves or it does not, the same way every time, in about forty lines a
reviewer can read.

Everything else follows from that:

- **A fact not in the index cannot be asserted.** Withdraw a courier document
  and its facts leave the index; every claim citing them stops resolving and
  leaves the letter. `make draft` against `make draft-without-proof` on the same
  case shows three sentences disappear - delivery date, signature, tracking -
  with nothing invented to replace them.
- **The dangerous failure is a real citation with a wrong value.** It looks
  sourced and is false. `asserts` is what catches it: cite
  `delivery.delivered_at` while claiming a date the record does not hold and the
  sentence is removed with the mismatch named.
- **A factual claim can hide behind a non-factual label.** Sentences declared
  "argument" carry no citations, so they are checked differently: they must
  contain no dates, amounts, tracking numbers or identifiers. A courtesy
  sentence carrying a date is a factual claim in disguise and is stripped too.
- **Stripping to nothing is a valid outcome.** If every sentence fails, the
  letter is empty. That is correct - better than a letter of apologies with the
  facts quietly removed.

### The template drafter is not a placeholder

`TemplateDrafter` builds the letter from the fact index directly, with no model.
It exists for three reasons and only the first is convenience:

1. It runs with no API key, so the path is exercised in CI.
2. It is the **baseline**. If a generated letter does not beat a mechanical one
   on claims verified, facts used and strip rate, then the model is decoration
   and the honest thing is to say so. That comparison is day 9's job.
3. It is the **deadline fallback**. Four hours before a response window closes,
   a plain letter that files beats an elegant one still waiting on a rate limit.

It cannot hallucinate by construction - each sentence is generated *from* a
fact, so an absent fact produces no sentence. That is a different guarantee from
the gate's, and worth keeping distinct: the template is safe structurally, the
model drafters are safe only because they are checked.

Both model drafters are written and tested against the schema; neither has run
against a live API yet, because the Anthropic account has no credit and the
Gemini free tier was exhausted by extraction. The template path is what produces
the letters above.

---

## D14 · The quota classifier was wrong in the expensive direction

**25 Aug 2026 · day 7**

D11 added a classifier so an exhausted daily allowance would abort a run rather
than be retried 175 times. It matched on `"exceeded your current quota"`.

That wording is what Google returns for **both** kinds of 429. The resumed
extraction run aborted after six documents reporting "free-tier quota
exhausted", and the error carried the detail that settled it:

```
Quota exceeded for metric:
  generativelanguage.googleapis.com/generate_content_free_tier_requests,
  limit: 5
```

**Limit 5 - and 23 requests had already succeeded that day.** So 5 was never a
daily allowance. It is the free tier's requests-per-minute ceiling, and the
throttle was set to 10: double the real limit, manufacturing the very 429s the
classifier then read as terminal.

Two fixes, and the second matters more than the first.

**The throttle is now 4 RPM**, measured rather than guessed. Throttling above a
provider's real limit does not go faster; it converts successes into 429s.

**The classifier now requires a per-day metric** - `per_day`, `daily limit`,
`requests per day`. Anything else that returns 429 is a rate limit and gets the
retry ladder.

### The asymmetry is the point

D11 framed this as "retrying a wall wastes hours", which was true, and built a
detector biased toward calling things terminal. That bias has its own cost, and
it is the one that bit: **a false positive stops a run that would have
succeeded; a false negative costs one retry ladder.** Those are not equal, and
the detector should lean toward retrying. It now does.

Worth noting the shape of the mistake rather than the mistake itself. D11 fixed
a real problem and introduced a smaller one pointing the other way, because the
fix was written while the pain of the four-hour run was fresh. Over-correcting
after a bad failure is ordinary, and the guard against it is asking which
direction of error is cheaper - not which failure is most recent.

### What is now measured

Extraction has scored **13 documents, 65 fields, 100% correct, zero
fabrication**, across all three quality tiers. The checkpoint from D11 earned
its keep on its first real use: a killed run kept all 7 documents it had
finished, and the resume skipped them.

The 100% still reads as a statement about the fixtures rather than about
extraction. Synthetic pages rendered from clean vector text and then degraded
test whether a 2026 vision model can read a blurry document - it can. They do
not test handwriting across a whole form, folds, thermal fade, occlusion, or
regional-language fields, which is what real courier documents bring. If the
full run holds at 100%, the honest write-up is that this benchmark is saturated
and no longer discriminating.
