"""Retrieval over a product's verified knowledge base (BM25, no external services).

Only claims that passed verification are indexed, so anything retrieved here is already
backed by a verbatim quote from a saved source.
"""
import math
from collections import Counter

from .textnorm import tokenize

K1, B = 1.5, 0.75


def _stem(token: str) -> str:
    # Crude prefix stemming: Turkish is agglutinative ("şubelerimizde" -> "şubel").
    return token[:5] if len(token) > 5 else token


def _terms(text: str) -> list[str]:
    return [_stem(t) for t in tokenize(text)]


def search(claims: list[dict], query: str, k: int = 5) -> list[tuple[float, dict]]:
    docs = [_terms(" ".join([c.get("statement", ""), c.get("quote", ""), " ".join(c.get("tags", []))]))
            for c in claims]
    if not docs:
        return []
    avgdl = sum(map(len, docs)) / len(docs)
    df = Counter(t for d in docs for t in set(d))
    n = len(docs)
    q = _terms(query)
    scored = []
    for claim, doc in zip(claims, docs):
        tf = Counter(doc)
        score = 0.0
        for term in q:
            if term not in tf:
                continue
            idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
            score += idf * tf[term] * (K1 + 1) / (tf[term] + K1 * (1 - B + B * len(doc) / avgdl))
        if score > 0:
            scored.append((score, claim))
    return sorted(scored, key=lambda x: -x[0])[:k]
