# Extraction evaluation

175 proof-of-delivery documents | model `stub`

Three outcomes, deliberately kept apart. An **abstention** costs a missing
sentence in a rebuttal letter. A **wrong** value puts a fabricated fact in a
document filed with a bank. The headline number is the wrong rate.

| outcome | rate | n |
|---|---:|---:|
| correct | 9.7% | 85 |
| **wrong** | **59.8%** | 523 |
| abstained | 30.5% | 267 |

Usable delivery proof produced for 100% of documents.

## By document quality

| quality | correct | wrong | abstained |
|---|---:|---:|---:|
| clean | 10.9% | **59.6%** | 29.4% |
| photo | 8.9% | **59.6%** | 31.6% |
| scanned | 9.3% | **60.0%** | 30.7% |

## By field

| field | correct | wrong | abstained |
|---|---:|---:|---:|
| `tracking_id` | 0.0% | **100.0%** | 0.0% |
| `carrier` | 0.0% | **100.0%** | 0.0% |
| `delivered_at` | 1.1% | **98.9%** | 0.0% |
| `signed_by` | 47.4% | **0.0%** | 52.6% |
| `delivered_to_address` | 0.0% | **0.0%** | 100.0% |

## Cost

0 input + 0 output tokens | Rs 0.00 total | Rs 0.00 per document | 0.2s

> Synthetic documents rendered by data/generator/fixtures.py. Ground truth is
> read from the case JSON, not re-authored, so extraction is scored against the
> same source the oracle used. See docs/DATA-CARD.md.