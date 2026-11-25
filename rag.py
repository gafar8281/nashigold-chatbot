"""
Shared RAG utilities: embeddings, vector store, retrieval.

Embeddings come from Gemini's multilingual gemini-embedding-001 API, so
Arabic and English questions match the same products and no ML model runs
in this process. Vectors are 768-d and L2-normalized; the Chroma collection
uses cosine distance and never gets an embedding function of its own.
"""
import logging
import math
import os
from functools import lru_cache

import chromadb
from google import genai
from google.genai import errors, types

log = logging.getLogger("nashigold.rag")

DB_DIR = os.getenv("CHROMA_DIR", "./chroma_db")
COLLECTION = "nashigold"
EMBED_MODEL = "gemini-embedding-001"
EMBED_DIM = 768


@lru_cache(maxsize=1)
def get_client() -> genai.Client:
    """One shared Gemini client (reads GEMINI_API_KEY or GOOGLE_API_KEY)."""
    return genai.Client()


def _normalize(v: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / norm for x in v]


def embed(texts: list[str], task_type: str) -> list[list[float]]:
    """Embed texts with Gemini. task_type: RETRIEVAL_DOCUMENT or RETRIEVAL_QUERY.

    Raises google.genai.errors.APIError on API failures; callers decide how to handle it.
    """
    resp = get_client().models.embed_content(
        model=EMBED_MODEL,
        contents=texts,
        config=types.EmbedContentConfig(task_type=task_type, output_dimensionality=EMBED_DIM),
    )
    return [_normalize(e.values) for e in resp.embeddings]


@lru_cache(maxsize=1)
def _db() -> chromadb.ClientAPI:
    return chromadb.PersistentClient(path=DB_DIR)


@lru_cache(maxsize=1)
def _collection():
    # embedding_function=None: we always pass vectors, so Chroma must never load its default model.
    return _db().get_or_create_collection(
        COLLECTION, metadata={"hnsw:space": "cosine"}, embedding_function=None
    )


def get_collection(reset: bool = False):
    if reset:
        try:
            _db().delete_collection(COLLECTION)
        except Exception:
            pass
        _collection.cache_clear()
    return _collection()


def retrieve(query: str, k: int = 6, max_distance: float = 0.6) -> list[dict]:
    """Return the top-k chunks for a query, dropping weak matches.

    Returns [] if the embedding API fails, so the bot falls back to offering human support.
    """
    col = get_collection()
    count = col.count()
    if count == 0:
        return []
    try:
        qvec = embed([query], "RETRIEVAL_QUERY")[0]
    except errors.APIError as e:
        log.warning("Embedding API error (%s): %s", e.code, e.message)
        return []
    except Exception as e:  # network errors, timeouts
        log.warning("Embedding request failed: %r", e)
        return []
    res = col.query(query_embeddings=[qvec], n_results=min(k, count))
    hits = []
    for doc, meta, dist in zip(res["documents"][0], res["metadatas"][0], res["distances"][0]):
        if dist <= max_distance:
            hits.append({"text": doc, "meta": meta, "score": round(1 - dist, 3)})
    return hits
