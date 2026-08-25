# Vakil - held-out evaluation

Split `D:\Razorpay\vakil\data\test` | n=100 | dataset sha256 `a67456b827d7fe3d`

## Fight-or-Fold decision

| metric | value |
|---|---:|
| precision | 0.700 |
| recall | 0.903 |
| F1 | 0.789 |
| accuracy | 0.727 |
| decided / escalated | 55 / 45 |
| TP / FP / FN / TN | 28 / 12 / 3 / 12 |

## Calibration

Brier score **0.1746** | largest bin gap 0.637

| predicted | observed | n |
|---:|---:|---:|
| 0.044 | 0.125 | 32 |
| 0.129 | 0.250 | 8 |
| 0.240 | 0.421 | 19 |
| 0.363 | 1.000 | 2 |
| 0.453 | 0.143 | 7 |
| 0.506 | 0.500 | 4 |
| 0.665 | 1.000 | 2 |
| 0.771 | 1.000 | 4 |
| 0.864 | 0.667 | 9 |
| 0.968 | 0.923 | 13 |

## Money

Two accountings, because they disagree. The left excludes arbitration
exposure on a lost representment; the right charges it. The EV engine
prices that exposure into every decision, so excluding it scores a
strategy against a cost it was told to avoid - which penalises folding
specifically. See D9 in docs/DECISIONS.md.

| strategy | fought | recovered | net (no arb.) | net (arb. charged) |
|---|---:|---:|---:|---:|
| Vakil | 40 | Rs 197,963 | **Rs 187,963** | **Rs 178,363** |
| always fight | 100 | Rs 285,946 | **Rs 260,946** | **Rs 215,346** |
| always fold | 0 | Rs 0 | **Rs 0** | **Rs 0** |

False-positive cost (money burned fighting losers): **Rs 3,000**

Uplift vs always-fight: Rs -72,983 without arbitration, Rs -36,983 with it.

## Per reason code

| code | cases | fought | of those, won |
|---|---:|---:|---:|
| 10.4 | 28 | 13 | 9 |
| 12.6 | 5 | 2 | 1 |
| 13.1 | 25 | 12 | 9 |
| 13.2 | 13 | 8 | 6 |
| 13.3 | 16 | 2 | 2 |
| 13.6 | 13 | 3 | 1 |

## Exceptions (refused to decide)

45 of 100 cases were handed to a human.

- `case_0006` (10.4): win estimate 0.23 within 9% of break-even 0.23
- `case_0017` (13.1): win estimate 0.22 within 9% of break-even 0.18
- `case_0036` (10.4): response deadline passed 26.0h ago
- `case_0043` (13.1): win estimate 0.14 within 9% of break-even 0.07
- `case_0044` (13.3): win estimate 0.12 within 9% of break-even 0.13
- `case_0049` (13.1): response deadline passed 76.0h ago
- `case_0063` (13.1): response deadline passed 21.0h ago
- `case_0065` (13.2): win estimate 0.16 within 9% of break-even 0.15
- `case_0066` (10.4): response deadline passed 55.0h ago
- `case_0068` (13.1): response deadline passed 22.0h ago

## Rulebook coverage

20 cited requirements across 6 dispute conditions. **17 of 20 are authored summaries not yet checked against a licensed rulebook** - Visa and Mastercard rulebooks are proprietary and are not reproduced in this repository.

31 of 100 cases are missing evidence the network requires. Most commonly:

| evidence field | cases missing it |
|---|---:|
| `shipping_proof` | 19 |
| `customer_communication` | 6 |
| `refund_confirmation` | 5 |
| `term_and_conditions` | 1 |
| `refund_cancellation_policy` | 1 |

Gaps inform but do not gate: a missing document lowers the win probability and the EV engine folds on its own. Escalating every case with a gap would flood the human queue with cases a human cannot fix either.

Throughput: 14591.3 cases/sec (decision path only, no model calls).

> Synthetic corpus. See docs/DATA-CARD.md for how it was built and what it > cannot tell you.