# Extraction evaluation

5 proof-of-delivery documents | model `stub`

Three outcomes, deliberately kept apart. An **abstention** costs a missing
sentence in a rebuttal letter. A **wrong** value puts a fabricated fact in a
document filed with a bank. The headline number is the wrong rate.

| outcome | rate | n |
|---|---:|---:|
| correct | 12.0% | 3 |
| **wrong** | **56.0%** | 14 |
| abstained | 32.0% | 8 |

Usable delivery proof produced for 100% of documents.

## By document quality

| quality | correct | wrong | abstained |
|---|---:|---:|---:|
| clean | 20.0% | **60.0%** | 20.0% |
| photo | 10.0% | **50.0%** | 40.0% |
| scanned | 10.0% | **60.0%** | 30.0% |

## By field

| field | correct | wrong | abstained |
|---|---:|---:|---:|
| `tracking_id` | 0.0% | **100.0%** | 0.0% |
| `carrier` | 0.0% | **100.0%** | 0.0% |
| `delivered_at` | 20.0% | **80.0%** | 0.0% |
| `signed_by` | 40.0% | **0.0%** | 60.0% |
| `delivered_to_address` | 0.0% | **0.0%** | 100.0% |

## Cost

0 input + 0 output tokens | Rs 0.00 total | Rs 0.00 per document | 0.0s

> Synthetic documents rendered by data/generator/fixtures.py. Ground truth is
> read from the case JSON, not re-authored, so extraction is scored against the
> same source the oracle used. See docs/DATA-CARD.md.