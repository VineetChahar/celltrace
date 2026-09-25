"""Brute-force cosine-similarity retriever over the hand-authored failure-pattern
corpus, embedded locally with sentence-transformers (no API calls). At 25
entries a FAISS index would be pure overhead -- a numpy matmul against a
25x384 matrix is microseconds, so we do exactly that and no more.
"""
import json
from pathlib import Path

import numpy as np
from sentence_transformers import SentenceTransformer

CORPUS_PATH = Path(__file__).parent / "corpus.json"
_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"


class Retriever:
    def __init__(self):
        self.entries = json.loads(CORPUS_PATH.read_text())
        self.model = SentenceTransformer(_MODEL_NAME)
        texts = [f"{e['title']}. {e['text']}" for e in self.entries]
        embs = self.model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        self.embeddings = np.asarray(embs, dtype=np.float32)

    def search(self, query: str, k: int = 3) -> list[dict]:
        q = self.model.encode([query], normalize_embeddings=True, show_progress_bar=False)[0]
        sims = self.embeddings @ q  # cosine similarity, both sides already normalized
        top_idx = np.argsort(-sims)[:k]
        return [
            {**self.entries[i], "score": float(sims[i])}
            for i in top_idx
        ]


_singleton: Retriever | None = None


def get_retriever() -> Retriever:
    global _singleton
    if _singleton is None:
        _singleton = Retriever()
    return _singleton
