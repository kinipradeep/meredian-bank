"""Thin wrapper around a Chroma server (the vector database)."""
from functools import lru_cache

import chromadb

from ..config import get_settings
from .embeddings import get_embedder


@lru_cache
def client():
    s = get_settings()
    return chromadb.HttpClient(host=s.chroma_host, port=s.chroma_port)


def list_collections() -> list[dict]:
    out = []
    for c in client().list_collections():
        col = client().get_collection(c.name if hasattr(c, "name") else c)
        out.append({"name": col.name, "count": col.count(), "metadata": col.metadata or {}})
    return out


def ensure_collection(name: str, embedder: str = "hash", description: str = ""):
    return client().get_or_create_collection(
        name=name, metadata={"embedder": embedder, "description": description, "hnsw:space": "cosine"})


def get_collection(name: str):
    return client().get_collection(name)


def delete_collection(name: str):
    client().delete_collection(name)


def collection_embedder(name: str) -> str:
    return (get_collection(name).metadata or {}).get("embedder", "hash")


def add_chunks(name: str, ids, documents, embeddings, metadatas):
    get_collection(name).upsert(ids=ids, documents=documents, embeddings=embeddings, metadatas=metadatas)


def delete_document_chunks(name: str, doc_id: int):
    get_collection(name).delete(where={"doc_id": doc_id})


def browse(name: str, limit: int = 25, offset: int = 0, with_embeddings: bool = True) -> dict:
    include = ["documents", "metadatas"] + (["embeddings"] if with_embeddings else [])
    return get_collection(name).get(limit=limit, offset=offset, include=include)


def search(name: str, query: str, k: int = 4, allowed_access: list[str] | None = None, db=None) -> list[dict]:
    col = get_collection(name)
    emb = get_embedder(collection_embedder(name), db).embed([query])[0]
    where = {"access": {"$in": allowed_access}} if allowed_access else None
    res = col.query(query_embeddings=[emb], n_results=k, where=where,
                    include=["documents", "metadatas", "distances"])
    hits = []
    for i, cid in enumerate(res["ids"][0]):
        md = res["metadatas"][0][i] or {}
        dist = float(res["distances"][0][i])
        hits.append({"id": cid, "text": res["documents"][0][i], "title": md.get("title", ""),
                     "access": md.get("access", "public"), "doc_id": md.get("doc_id"),
                     "chunk_index": md.get("chunk_index"), "distance": round(dist, 4),
                     "similarity": round(1 - dist, 4)})
    return hits
