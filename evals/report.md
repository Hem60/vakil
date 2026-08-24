# Vakil - held-out evaluation

Split `D:\Razorpay\vakil\data\test` | n=100 | dataset sha256 `a67456b827d7fe3d`

## Fight-or-Fold decision

| metric | value |
|---|---:|
| precision | 0.436 |
| recall | 1.000 |
| F1 | 0.607 |
| accuracy | 0.443 |
| decided / escalated | 79 / 21 |
| TP / FP / FN / TN | 34 / 44 / 0 / 1 |

## Calibration

Brier score **0.2593** | largest bin gap 0.527

| predicted | observed | n |
|---:|---:|---:|
| 0.235 | 0.222 | 9 |
| 0.357 | 0.000 | 7 |
| 0.471 | 0.200 | 10 |
| 0.527 | 0.000 | 4 |
| 0.655 | 0.389 | 18 |
| 0.748 | 0.400 | 10 |
| 0.845 | 0.524 | 21 |
| 0.961 | 0.809 | 21 |

## Money

| strategy | fought | recovered | filing spend | net |
|---|---:|---:|---:|---:|
| Vakil | 78 | Rs 230,357 | Rs 19,500 | **Rs 210,857** |
| always fight | 100 | Rs 285,946 | Rs 25,000 | **Rs 260,946** |
| always fold | 0 | Rs 0 | Rs 0 | **Rs 0** |

False-positive cost (money burned fighting losers): **Rs 11,000**

Uplift vs always-fight Rs -50,089 | vs always-fold Rs 210,857

## Per reason code

| code | cases | fought | of those, won |
|---|---:|---:|---:|
| 10.4 | 28 | 22 | 9 |
| 12.6 | 5 | 3 | 2 |
| 13.1 | 25 | 19 | 9 |
| 13.2 | 13 | 13 | 8 |
| 13.3 | 16 | 10 | 2 |
| 13.6 | 13 | 11 | 4 |

## Exceptions (refused to decide)

21 of 100 cases were handed to a human.

- `case_0010` (13.3): win estimate 0.23 within 8% of break-even 0.15
- `case_0036` (10.4): response deadline passed 26.0h ago
- `case_0049` (13.1): response deadline passed 76.0h ago
- `case_0063` (13.1): response deadline passed 21.0h ago
- `case_0066` (10.4): response deadline passed 55.0h ago
- `case_0068` (13.1): response deadline passed 22.0h ago
- `case_0077` (12.6): response deadline passed 38.0h ago
- `case_0088` (10.4): response deadline passed 51.0h ago
- `case_0104` (13.6): response deadline passed 94.0h ago
- `case_0107` (12.6): response deadline passed 11.0h ago

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

Throughput: 27846.6 cases/sec (decision path only, no model calls).

> Synthetic corpus. See docs/DATA-CARD.md for how it was built and what it cannot tell you.