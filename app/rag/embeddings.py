"""Embedding back ends.

- fastembed: small local ONNX model (default BAAI/bge-small-en-v1.5, 384 dimensions). No API cost.
- openai:    text-embedding-3-small through the OpenAI provider's key (1536 dimensions).
- hash:      a deterministic word-hashing embedder (256 dimensions). Works fully offline;
             useful for tests and for teaching how embeddings behave without a model.
"""
import hashlib
import math
import re
from functools import lru_cache

import numpy as np

from ..config import get_settings

EMBEDDERS = {
    "fastembed": "Local model (FastEmbed, BAAI/bge-small-en-v1.5, 384 dims)",
    "openai": "OpenAI text-embedding-3-small (1536 dims, uses the OpenAI key)",
    "hash": "Offline hashing embedder (256 dims, no model)",
}


class HashEmbedder:
    name = "hash"
    dim = 256

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for t in texts:
            v = np.zeros(self.dim, dtype=np.float32)
            words = re.findall(r"[a-z0-9₹]+", t.lower())
            feats = words + [a + "_" + b for a, b in zip(words, words[1:])]
            for f in feats:
                h = int(hashlib.md5(f.encode()).hexdigest(), 16)
                v[h % self.dim] += 1.0 if (h >> 8) & 1 else -1.0
            n = float(np.linalg.norm(v)) or 1.0
            out.append((v / n).tolist())
        return out


class FastEmbedder:
    name = "fastembed"

    def __init__(self):
        from fastembed import TextEmbedding
        s = get_settings()
        self.model = TextEmbedding(s.fastembed_model, cache_dir=s.fastembed_cache)
        self.dim = len(next(iter(self.model.embed(["probe"]))))

    def embed(self, texts):
        return [list(map(float, v)) for v in self.model.embed(texts)]


class OpenAIEmbedder:
    name = "openai"
    dim = 1536

    def __init__(self, api_key: str, base_url: str = "https://api.openai.com/v1"):
        import openai
        self.client = openai.OpenAI(api_key=api_key, base_url=base_url)

    def embed(self, texts):
        resp = self.client.embeddings.create(model="text-embedding-3-small", input=texts)
        return [d.embedding for d in resp.data]


@lru_cache
def _fastembed() -> FastEmbedder:
    return FastEmbedder()


def get_embedder(name: str, db=None):
    if name == "hash":
        return HashEmbedder()
    if name == "fastembed":
        return _fastembed()
    if name == "openai":
        from ..models import Provider
        from ..security import decrypt
        p = db.query(Provider).filter_by(slug="openai").first() if db else None
        key = decrypt(p.api_key_enc) if p else ""
        if not key:
            raise RuntimeError("The OpenAI embedder needs an OpenAI API key (Admin > Providers).")
        return OpenAIEmbedder(key, p.base_url or "https://api.openai.com/v1")
    raise ValueError(f"Unknown embedder {name}")


def cosine(a, b) -> float:
    a, b = np.asarray(a), np.asarray(b)
    d = float(np.linalg.norm(a) * np.linalg.norm(b)) or 1.0
    return float(a @ b) / d


def describe_vector(v: list[float], n: int = 24) -> dict:
    arr = np.asarray(v, dtype=float)
    return {"dims": len(v), "norm": round(float(np.linalg.norm(arr)), 4),
            "head": [round(float(x), 4) for x in arr[:n]],
            "min": round(float(arr.min()), 4), "max": round(float(arr.max()), 4),
            "mean": round(float(arr.mean()), 4)}


def pca_2d(vectors: list[list[float]]) -> list[tuple[float, float]]:
    """Project vectors to 2D for the vector map (simple SVD-based PCA)."""
    if len(vectors) < 2:
        return [(0.0, 0.0) for _ in vectors]
    X = np.asarray(vectors, dtype=float)
    X = X - X.mean(axis=0)
    _, _, vt = np.linalg.svd(X, full_matrices=False)
    P = X @ vt[:2].T
    return [(float(x), float(y)) for x, y in P]


def project_point(vectors, point) -> tuple[float, float]:
    X = np.asarray(vectors, dtype=float)
    mu = X.mean(axis=0)
    _, _, vt = np.linalg.svd(X - mu, full_matrices=False)
    p = (np.asarray(point) - mu) @ vt[:2].T
    return float(p[0]), float(p[1])


def safe_round(x):
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else round(x, 4)
