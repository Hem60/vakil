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
| Rs 250 | 78 | 1 | 21 | Rs 210,857 | Rs 261,196 | Rs 260,946 | Rs -50,089 | Rs 250 |
| Rs 500 | 75 | 1 | 24 | Rs 192,857 | Rs 236,446 | Rs 235,946 | Rs -43,089 | Rs 500 |
| Rs 800 | 72 | 1 | 27 | Rs 165,258 | Rs 206,746 | Rs 205,946 | Rs -40,688 | Rs 800 |
| Rs 1,200 | 68 | 3 | 29 | Rs 141,258 | Rs 169,546 | Rs 165,946 | Rs -24,688 | Rs 3,600 |
| Rs 1,600 | 63 | 10 | 27 | Rs 116,361 | Rs 135,947 | Rs 125,946 | Rs -9,585 | Rs 10,001 |
| Rs 2,000 | 52 | 14 | 34 | Rs 89,668 | Rs 102,250 | Rs 85,946 | **Rs 3,722** | **Rs 16,304** |
| Rs 2,500 | 46 | 21 | 33 | Rs 72,870 | Rs 63,554 | Rs 35,946 | **Rs 36,924** | **Rs 27,608** |

**Crossover: Rs 2,000** at the optimistic bound.
 **Robust crossover: Rs 2,000** - holds at both bounds, so it is not an abstention artefact.

## By dispute size

The hypothesis worth testing: value concentrates where the filing cost is a
large fraction of what is recoverable. Bands are terciles of this corpus.

### At Rs 250 (automated marginal cost)

| band | n | fought | folded | esc | Vakil (opt) | always-fight | uplift opt | uplift pess |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| small | 26 | 21 | 1 | 4 | Rs 21,538 | Rs 28,884 | Rs -7,346 | Rs 250 |
| mid | 36 | 27 | 0 | 9 | Rs 43,839 | Rs 58,586 | Rs -14,747 | Rs 0 |
| large | 38 | 30 | 0 | 8 | Rs 145,480 | Rs 173,476 | Rs -27,996 | Rs 0 |

### At Rs 2,000 (fully loaded manual cost)

| band | n | fought | folded | esc | Vakil (opt) | always-fight | uplift opt | uplift pess |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| small | 26 | 5 | 10 | 11 | Rs -404 | Rs -16,616 | **Rs 16,212** | **Rs 14,303** |
| mid | 36 | 20 | 3 | 13 | Rs -1,409 | Rs -4,414 | **Rs 3,005** | **Rs 1** |
| large | 38 | 27 | 1 | 10 | Rs 91,481 | Rs 106,976 | Rs -15,495 | Rs 2,000 |

> Synthetic corpus, and the corpus has no long tail of very small or very
> large disputes - see docs/DATA-CARD.md. These figures describe the regime
> this corpus covers, not the whole problem.