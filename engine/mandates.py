"""Mandate matching (spec 3.2 mandate features, M9): how well a purchase matches what the human
actually asked for, with no LLM call per payment.

The spec's choice is a small sentence-transformer running locally. That means downloading model
weights, so this is a stand-in with the same shape: a local embedding (hashed word and character
trigram features, L2-normalized), the request embedded once per mandate and each product once, and a
cosine similarity per payment from cached vectors, well under a millisecond. Swap `embed` for a real
sentence-transformer when one is installed; nothing else changes.
"""

import hashlib
import math
import re
from functools import lru_cache

DIMS = 512
STOPWORDS = frozenset("a an and as buy for i in it me my need needed of on or per please some the to under up with".split())


def _tokens(text: str) -> list[str]:
    words = [w for w in re.findall(r"[a-z]+", (text or "").lower().replace("_", " ")) if w not in STOPWORDS]
    grams = [f"#{w[i:i + 3]}" for w in words for i in range(max(1, len(w) - 2))]
    return words + grams


@lru_cache(maxsize=20_000)
def embed(text: str) -> tuple[float, ...]:
    vec = [0.0] * DIMS
    for token in _tokens(text):
        h = int.from_bytes(hashlib.blake2b(token.encode(), digest_size=8).digest(), "big")
        vec[h % DIMS] += 1.0 if (h >> 32) & 1 else -1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return tuple(v / norm for v in vec)


def similarity(a: str, b: str) -> float:
    """Cosine similarity of two texts in [0, 1] (negative similarities count as no match)."""
    if not a or not b:
        return 0.0
    va, vb = embed(a), embed(b)
    return max(0.0, sum(x * y for x, y in zip(va, vb)))


@lru_cache(maxsize=200_000)
def request_match(request_text: str, product_text: str) -> float:
    """How well a product matches the mandate's original request, in [0, 1]. No request means no
    constraint, so it matches fully."""
    if not request_text:
        return 1.0
    return similarity(request_text, product_text)
