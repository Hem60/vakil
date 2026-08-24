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
