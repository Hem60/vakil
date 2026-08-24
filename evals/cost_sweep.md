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
| Rs 250 | 40 | 15 | 45 | Rs 187,963 | Rs 249,299 | Rs 260,946 | Rs -72,983 | Rs -11,647 |
| Rs 500 | 39 | 21 | 40 | Rs 172,665 | Rs 220,051 | Rs 235,946 | Rs -63,281 | Rs -15,895 |
| Rs 800 | 36 | 28 | 36 | Rs 155,467 | Rs 195,952 | Rs 205,946 | Rs -50,479 | Rs -9,994 |
| Rs 1,200 | 34 | 37 | 29 | Rs 130,968 | Rs 171,953 | Rs 165,946 | Rs -34,978 | Rs 6,007 |
| Rs 1,600 | 22 | 42 | 36 | Rs 77,580 | Rs 146,056 | Rs 125,946 | Rs -48,366 | Rs 20,110 |
| Rs 2,000 | 18 | 49 | 33 | Rs 61,185 | Rs 123,261 | Rs 85,946 | Rs -24,761 | Rs 37,315 |
| Rs 2,500 | 13 | 53 | 34 | Rs 57,988 | Rs 101,064 | Rs 35,946 | **Rs 22,042** | **Rs 65,118** |

**Crossover: Rs 2,500** at the optimistic bound.
 **Robust crossover: Rs 2,500** - holds at both bounds, so it is not an abstention artefact.

### Charging arbitration exposure

The EV engine prices arbitration exposure into every decision, so a
scoreboard that omits it scores Vakil against a cost it was told to
avoid - and that omission penalises folding specifically. Same sweep,
with a lost representment also paying the exposure:

| filing cost | Vakil (opt) | always-fight | uplift opt | uplift pess |
|---:|---:|---:|---:|---:|
| Rs 250 | Rs 178,363 | Rs 215,346 | Rs -36,983 | Rs -2,047 |
| Rs 500 | Rs 163,065 | Rs 190,346 | Rs -27,281 | Rs -3,095 |
| Rs 800 | Rs 146,667 | Rs 160,346 | Rs -13,679 | Rs 7,606 |
| Rs 1,200 | Rs 122,968 | Rs 120,346 | **Rs 2,622** | **Rs 30,007** |
| Rs 1,600 | Rs 71,980 | Rs 80,346 | Rs -8,366 | Rs 46,510 |
| Rs 2,000 | Rs 55,585 | Rs 40,346 | **Rs 15,239** | **Rs 65,315** |
| Rs 2,500 | Rs 54,788 | Rs -9,654 | **Rs 64,442** | **Rs 93,918** |

**Robust crossover with arbitration charged: Rs 1,200**

## By dispute size

The hypothesis worth testing: value concentrates where the filing cost is a
large fraction of what is recoverable. Bands are terciles of this corpus.

### At Rs 250 (automated marginal cost)

| band | n | fought | folded | esc | Vakil (opt) | always-fight | uplift opt | uplift pess |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| small | 26 | 13 | 7 | 6 | Rs 21,639 | Rs 28,884 | Rs -7,245 | Rs -149 |
| mid | 36 | 10 | 5 | 21 | Rs 25,093 | Rs 58,586 | Rs -33,493 | Rs -4,749 |
| large | 38 | 17 | 3 | 18 | Rs 141,231 | Rs 173,476 | Rs -32,245 | Rs -6,749 |

### At Rs 2,000 (fully loaded manual cost)

| band | n | fought | folded | esc | Vakil (opt) | always-fight | uplift opt | uplift pess |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| small | 26 | 3 | 16 | 7 | Rs 697 | Rs -16,616 | **Rs 17,313** | **Rs 18,606** |
| mid | 36 | 5 | 21 | 10 | Rs -4,001 | Rs -4,414 | **Rs 413** | **Rs 2,208** |
| large | 38 | 10 | 12 | 16 | Rs 64,489 | Rs 106,976 | Rs -42,487 | Rs 16,501 |

> Synthetic corpus, and the corpus has no long tail of very small or very
> large disputes - see docs/DATA-CARD.md. These figures describe the regime
> this corpus covers, not the whole problem.