# Extraction evaluation

21 proof-of-delivery documents | model `gemini-3.6-flash`

Resumed from checkpoint: 21 documents were already scored by an earlier run and were not re-sent.

> **Run aborted after 21 documents** - GeminiUnavailable: daily quota exhausted (matched 'perday'): HTTP 429: {
  "error": {
    "code": 429,
    "message": "You exceeded your current quota, please check your plan and billing details. For more information on this error, head to: https://ai.google.dev/gemini-api/docs/rate-limits. To monitor your current usage, head to: https://ai.dev/rate-limit. \n* Quota exceeded for metric: generativelanguage.googleapis.com/generate_content_free_tier_requests, limit: 20,
>
> 153 documents were not attempted. These figures describe the documents that completed, not the full set. Re-run to continue from the checkpoint.

Three outcomes, deliberately kept apart. An **abstention** costs a missing
sentence in a rebuttal letter. A **wrong** value puts a fabricated fact in a
document filed with a bank. The headline number is the wrong rate.

| outcome | rate | n |
|---|---:|---:|
| correct | 99.1% | 104 |
| **wrong** | **0.9%** | 1 |
| abstained | 0.0% | 0 |

Usable delivery proof produced for 100% of documents.

## By document quality

| quality | correct | wrong | abstained |
|---|---:|---:|---:|
| clean | 100.0% | **0.0%** | 0.0% |
| photo | 96.7% | **3.3%** | 0.0% |
| scanned | 100.0% | **0.0%** | 0.0% |

## By field

| field | correct | wrong | abstained |
|---|---:|---:|---:|
| `tracking_id` | 100.0% | **0.0%** | 0.0% |
| `carrier` | 100.0% | **0.0%** | 0.0% |
| `delivered_at` | 100.0% | **0.0%** | 0.0% |
| `signed_by` | 100.0% | **0.0%** | 0.0% |
| `delivered_to_address` | 95.2% | **4.8%** | 0.0% |

## Cost

17,682 input + 4,076 output tokens | Rs 0.00 total | Rs 0.00 per document | 1.1s

## Failures

- `fixtures/pod/0100.pdf`: GeminiUnavailable: daily quota exhausted (matched 'perday'): HTTP 429: {
  "error": {
    "code": 429,
    "message": "You exceeded your current quota, please check your plan and billing details. For more information on this error, head to: https://ai.google.dev/gemini-api/docs/rate-limits. To monitor your current usage, head to: https://ai.dev/rate-limit. \n* Quota exceeded for metric: generativelanguage.googleapis.com/generate_content_free_tier_requests, limit: 20,

> Synthetic documents rendered by data/generator/fixtures.py. Ground truth is
> read from the case JSON, not re-authored, so extraction is scored against the
> same source the oracle used. See docs/DATA-CARD.md.