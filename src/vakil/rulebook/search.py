"""Semantic-ish search over the rulebook, for the drafting stage.

Scope: this is **not** how requirements are found. Reason code to requirements
is a deterministic lookup in `store.py`, because putting a retriever between a
dispute and the evidence the network demands would introduce approximation into
a place where a table is exact. Search exists so the drafting stage can ask
open-ended questions - "what bears on a delivery to a different address" - and
get cited passages back.

Backend, stated plainly: this is BM25 over a corpus of roughly twenty short
entries. It runs with no API key, no database and no embedding provider, which
means the retrieval path is exercised from day one instead of being blocked on
credentials. Dense retrieval is a drop-in behind `Retriever` when it earns its
place - though note Anthropic ships no embeddings API, so that means adding a
separate provider (Voyage or similar) and a real dependency. On a corpus this
size, lexical scoring is not obviously worse, and pretending otherwise would be
resume-driven architecture.

The pgvector table in `scripts/init.sql` is provisioned for that future backend
and is deliberately unused today.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Protocol

from vakil.models import ReasonCode
from vakil.rulebook.store import Rule, Rulebook

K1 = 1.5
B = 0.75

_TOKEN = re.compile(r"[a-z0-9]+")

#: Words that carry no signal in a corpus where every entry is about disputes.
STOPWORDS = frozenset(
    """
    a an and are as at be by for from has have in is it its of on or that the
    to was were will with this these those which what when where must should
    evidence dispute disputed merchant cardholder transaction
    """.split()
)


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in STOPWORDS and len(t) > 1]


@dataclass(frozen=True)
class Hit:
    rule: Rule
    score: float

    def render(self) -> str:
        return f"[{self.score:.2f}] {self.rule.render()}"


class Retriever(Protocol):
    """Swap in a dense backend by satisfying this."""

    def search(
        self, query: str, *, k: int = 5, reason_code: ReasonCode | None = None
    ) -> list[Hit]: ...


class BM25Retriever:
    def __init__(self, rulebook: Rulebook) -> None:
        self._rules = list(rulebook)
        self._docs = [tokenize(self._text(r)) for r in self._rules]
        self._lengths = [len(d) for d in self._docs]
        self._avg_length = (sum(self._lengths) / len(self._docs)) if self._docs else 0.0
        self._term_frequencies = [Counter(d) for d in self._docs]

        document_frequency: Counter[str] = Counter()
        for doc in self._docs:
            document_frequency.update(set(doc))
        n = len(self._docs)
        self._idf = {
            term: math.log(1 + (n - df + 0.5) / (df + 0.5))
            for term, df in document_frequency.items()
        }

    @staticmethod
    def _text(rule: Rule) -> str:
        return " ".join([rule.title, rule.requirement, rule.note, rule.citation.section])

    def search(
        self, query: str, *, k: int = 5, reason_code: ReasonCode | None = None
    ) -> list[Hit]:
        """Score every rule against the query.

        `reason_code` filters rather than boosts: asking about 13.1 should never
        surface a 10.4-only rule, however similar the wording. Universal rules
        always stay in scope.
        """
        terms = tokenize(query)
        if not terms:
            return []

        hits: list[Hit] = []
        for index, rule in enumerate(self._rules):
            if reason_code is not None and rule.reason_code not in (None, reason_code):
                continue
            score = self._score(terms, index)
            if score > 0:
                hits.append(Hit(rule=rule, score=round(score, 4)))

        hits.sort(key=lambda h: (-h.score, h.rule.id))
        return hits[:k]

    def _score(self, terms: list[str], index: int) -> float:
        frequencies = self._term_frequencies[index]
        length = self._lengths[index]
        if not length:
            return 0.0

        total = 0.0
        for term in terms:
            frequency = frequencies.get(term, 0)
            if not frequency:
                continue
            idf = self._idf.get(term, 0.0)
            denominator = frequency + K1 * (1 - B + B * length / self._avg_length)
            total += idf * (frequency * (K1 + 1)) / denominator
        return total
