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

## D5 · Open: the filing cost may be too small for Fight-or-Fold to matter

**24 Aug 2026 · unresolved, decision needed before day 6**

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
