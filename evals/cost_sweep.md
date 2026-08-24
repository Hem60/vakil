# Cost sensitivity sweep

Split `D:\Razorpay\vakil\data\test` | n=100 | dispute amounts Rs 1,899-Rs 24,998, median Rs 5,999

Does deciding *whether* to fight beat fighting everything, and at what
filing cost? Net figures are realised rupees on the held-out split.

## Net recovery vs filing cost

Escalated cases go to a human, so their outcome is not Vakil's to claim.
Crediting them with zero would reward abstention - raise the filing cost,
escalate more, and the net improves because the hard cases leave the
accounting. So both bounds are reported: **opt** assumes the human folds
every escalated case, **pess** assumes the human fights them all. A win
that holds only at *opt* is a result about abstention, not about deciding.

| filing cost | fought | folded | esc | Vakil (opt) | Vakil (pess) | always-fight | uplift opt | uplift pess |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Rs 250 | 67 | 0 | 33 | Rs 206,108 | Rs 260,946 | Rs 260,946 | Rs -54,838 | Rs 0 |
| Rs 500 | 59 | 1 | 40 | Rs 181,360 | Rs 236,446 | Rs 235,946 | Rs -54,586 | Rs 500 |
| Rs 800 | 55 | 1 | 44 | Rs 166,860 | Rs 206,746 | Rs 205,946 | Rs -39,086 | Rs 800 |
| Rs 1,200 | 46 | 1 | 53 | Rs 146,165 | Rs 167,146 | Rs 165,946 | Rs -19,781 | Rs 1,200 |
| Rs 1,600 | 37 | 1 | 62 | Rs 123,671 | Rs 127,546 | Rs 125,946 | Rs -2,275 | Rs 1,600 |
| Rs 2,000 | 28 | 6 | 66 | Rs 112,276 | Rs 97,946 | Rs 85,946 | **Rs 26,330** | **Rs 12,000** |
| Rs 2,500 | 26 | 12 | 62 | Rs 98,277 | Rs 58,350 | Rs 35,946 | **Rs 62,331** | **Rs 22,404** |

**Crossover: Rs 2,000** at the optimistic bound.
 **Robust crossover: Rs 2,000** - holds at both bounds, so it is not an abstention artefact.

## By dispute size

The hypothesis worth testing: value concentrates where the filing cost is a
large fraction of what is recoverable. Bands are terciles of this corpus.

### At Rs 250 (automated marginal cost)

| band | n | fought | folded | esc | Vakil (opt) | always-fight | uplift opt | uplift pess |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| small | 26 | 20 | 0 | 6 | Rs 21,788 | Rs 28,884 | Rs -7,096 | Rs 0 |
| mid | 36 | 25 | 0 | 11 | Rs 44,339 | Rs 58,586 | Rs -14,247 | Rs 0 |
| large | 38 | 22 | 0 | 16 | Rs 139,981 | Rs 173,476 | Rs -33,495 | Rs 0 |

### At Rs 2,000 (fully loaded manual cost)

| band | n | fought | folded | esc | Vakil (opt) | always-fight | uplift opt | uplift pess |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| small | 26 | 0 | 6 | 20 | Rs 0 | Rs -16,616 | **Rs 16,616** | **Rs 12,000** |
| mid | 36 | 8 | 0 | 28 | Rs 6,795 | Rs -4,414 | Rs 11,209 | Rs 0 |
| large | 38 | 20 | 0 | 18 | Rs 105,481 | Rs 106,976 | Rs -1,495 | Rs 0 |

> Synthetic corpus, and the corpus has no long tail of very small or very
> large disputes - see docs/DATA-CARD.md. These figures describe the regime
> this corpus covers, not the whole problem.