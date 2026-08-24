# Vakil - held-out evaluation

Split `D:\Razorpay\vakil\data\test` | n=100 | dataset sha256 `a67456b827d7fe3d`

## Fight-or-Fold decision

| metric | value |
|---|---:|
| precision | 0.492 |
| recall | 1.000 |
| F1 | 0.660 |
| accuracy | 0.492 |
| decided / escalated | 67 / 33 |
| TP / FP / FN / TN | 33 / 34 / 0 / 0 |

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
| Vakil | 67 | Rs 222,858 | Rs 16,750 | **Rs 206,108** |
| always fight | 100 | Rs 285,946 | Rs 25,000 | **Rs 260,946** |
| always fold | 0 | Rs 0 | Rs 0 | **Rs 0** |

False-positive cost (money burned fighting losers): **Rs 8,500**

Uplift vs always-fight Rs -54,838 | vs always-fold Rs 206,108

## Per reason code

| code | cases | fought | of those, won |
|---|---:|---:|---:|
| 10.4 | 28 | 19 | 9 |
| 12.6 | 5 | 3 | 2 |
| 13.1 | 25 | 19 | 9 |
| 13.2 | 13 | 13 | 8 |
| 13.3 | 16 | 5 | 2 |
| 13.6 | 13 | 8 | 3 |

## Exceptions (refused to decide)

33 of 100 cases were handed to a human.

- `case_0003` (13.3): low decision confidence
- `case_0010` (13.3): low decision confidence
- `case_0036` (10.4): response deadline passed 26.0h ago
- `case_0049` (13.1): response deadline passed 76.0h ago
- `case_0054` (10.4): low decision confidence
- `case_0063` (13.1): response deadline passed 21.0h ago
- `case_0066` (10.4): response deadline passed 55.0h ago
- `case_0068` (13.1): response deadline passed 22.0h ago
- `case_0075` (13.6): low decision confidence
- `case_0077` (12.6): response deadline passed 38.0h ago

Throughput: 47118.7 cases/sec (decision path only, no model calls).

> Synthetic corpus. See docs/DATA-CARD.md for how it was built and what it cannot tell you.