"""Turn a stored document into chunks + embeddings in its Chroma collection."""
import io

from ..models import KnowledgeDocument
from . import store
from .chunking import chunk_text
from .embeddings import get_embedder


def extract_text(filename: str, data: bytes) -> str:
    name = filename.lower()
    if name.endswith(".pdf"):
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        return "\n\n".join((p.extract_text() or "") for p in reader.pages)
    return data.decode("utf-8", errors="replace")


def ingest_document(db, doc: KnowledgeDocument) -> int:
    embedder = get_embedder(store.collection_embedder(doc.collection), db)
    chunks = chunk_text(doc.content, doc.chunk_strategy, doc.chunk_size, doc.chunk_overlap)
    try:
        store.delete_document_chunks(doc.collection, doc.id)
    except Exception:
        pass
    if not chunks:
        doc.chunk_count = 0
        db.commit()
        return 0
    texts = [c["text"] for c in chunks]
    vectors = embedder.embed(texts)
    ids = [f"doc{doc.id}-c{c['index']}" for c in chunks]
    metas = [{"doc_id": doc.id, "title": doc.title, "access": doc.access, "chunk_index": c["index"],
              "chars": c["chars"]} for c in chunks]
    store.add_chunks(doc.collection, ids, texts, vectors, metas)
    doc.chunk_count = len(chunks)
    db.commit()
    return len(chunks)
