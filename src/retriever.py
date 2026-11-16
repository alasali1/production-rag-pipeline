"""Stage 1: dense retrieval over markdown docs stored in ChromaDB.

Documents are split on markdown headings (ignoring '#' lines inside code
fences), oversized sections are packed into ~max_chars chunks, and each chunk
is prefixed with its heading path so both the embedder and the cross-encoder
see where it came from.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator, List, Optional

from src.schemas import RetrievedChunk

if TYPE_CHECKING:
    from src.config import Settings

logger = logging.getLogger(__name__)

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")


@dataclass(frozen=True)
class DocChunk:
    chunk_id: str
    source: str
    heading: str
    text: str


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "section"


def _iter_sections(markdown: str) -> Iterator[tuple[list[str], str]]:
    """Yield (heading_path, body) for each heading-delimited section."""
    path: list[str] = []
    body: list[str] = []
    in_fence = False

    for line in markdown.splitlines():
        if _FENCE_RE.match(line):
            in_fence = not in_fence
        match = None if in_fence else _HEADING_RE.match(line)
        if match:
            yield list(path), "\n".join(body).strip()
            level, title = len(match.group(1)), match.group(2).strip()
            path = path[: level - 1] + [title]
            body = []
        else:
            body.append(line)
    yield list(path), "\n".join(body).strip()


def _pack(text: str, max_chars: int, overlap: int) -> List[str]:
    """Pack paragraphs into chunks <= max_chars; hard-split oversize paragraphs."""
    if len(text) <= max_chars:
        return [text]

    pieces: list[str] = []
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        if len(para) <= max_chars:
            pieces.append(para)
            continue
        step = max_chars - overlap
        pieces.extend(para[i : i + max_chars] for i in range(0, len(para), step))

    chunks: list[str] = []
    current = ""
    for piece in pieces:
        candidate = f"{current}\n\n{piece}" if current else piece
        if len(candidate) <= max_chars:
            current = candidate
        else:
            if current:
                chunks.append(current)
            current = piece
    if current:
        chunks.append(current)
    return chunks


def chunk_markdown(markdown: str, source: str, max_chars: int = 1500, overlap: int = 200) -> List[DocChunk]:
    """Split one markdown document into retrievable chunks with stable IDs."""
    chunks: list[DocChunk] = []
    seen_ids: dict[str, int] = {}
    doc_title: Optional[str] = None

    for path, body in _iter_sections(markdown):
        if path and doc_title is None:
            doc_title = path[0]
        if not body:
            continue
        heading = " > ".join(path) if path else (doc_title or source)
        anchor = _slugify(path[-1]) if path else "intro"

        for part in _pack(body, max_chars, overlap):
            base_id = f"{source}#{anchor}"
            n = seen_ids.get(base_id, 0)
            seen_ids[base_id] = n + 1
            chunk_id = base_id if n == 0 else f"{base_id}-{n + 1}"
            chunks.append(DocChunk(chunk_id=chunk_id, source=source, heading=heading, text=f"{heading}\n\n{part}"))
    return chunks


class DocumentRetriever:
    """ChromaDB-backed vector retriever (stage 1, high recall)."""

    def __init__(self, collection: Any) -> None:
        self._collection = collection

    @classmethod
    def from_settings(cls, settings: "Settings") -> "DocumentRetriever":
        import chromadb
        from chromadb.utils import embedding_functions as ef

        if settings.embedding_backend == "openai":
            embed_fn: Any = ef.OpenAIEmbeddingFunction(
                api_key=settings.require_openai_key(),
                model_name=settings.openai_embedding_model,
            )
            model_name = settings.openai_embedding_model
        else:
            embed_fn = ef.SentenceTransformerEmbeddingFunction(model_name=settings.local_embedding_model)
            model_name = settings.local_embedding_model

        client = chromadb.PersistentClient(path=str(settings.chroma_dir))
        # One collection per embedding model: vectors from different models are not comparable.
        collection = client.get_or_create_collection(
            name=f"docs_{_slugify(model_name)}"[:63],
            embedding_function=embed_fn,
            metadata={"hnsw:space": "cosine"},
        )
        return cls(collection)

    def count(self) -> int:
        return int(self._collection.count())

    def index_directory(self, docs_dir: Path, pattern: str = "*.md") -> int:
        """(Re)index every markdown file under docs_dir. Returns the number of chunks written.

        Each file's previous chunks are deleted first, so re-running after an
        edit never leaves stale sections behind.
        """
        files = sorted(docs_dir.rglob(pattern))
        if not files:
            raise FileNotFoundError(f"No files matching {pattern!r} under {docs_dir}")

        total = 0
        for path in files:
            source = path.relative_to(docs_dir).as_posix()
            chunks = chunk_markdown(path.read_text(encoding="utf-8"), source=source)
            self._collection.delete(where={"source": source})
            if not chunks:
                continue
            self._collection.add(
                ids=[c.chunk_id for c in chunks],
                documents=[c.text for c in chunks],
                metadatas=[{"source": c.source, "heading": c.heading} for c in chunks],
            )
            total += len(chunks)
            logger.info("Indexed %s (%d chunks)", source, len(chunks))
        return total

    def retrieve(self, query: str, k: int = 20) -> List[RetrievedChunk]:
        """Return up to k candidates, best first. `score` is cosine similarity (1 - distance)."""
        available = self.count()
        if available == 0:
            logger.warning("Vector store is empty; run indexing first")
            return []

        result = self._collection.query(
            query_texts=[query],
            n_results=min(k, available),
            include=["documents", "distances"],
        )
        ids = result["ids"][0]
        docs = result["documents"][0]
        distances = result["distances"][0]
        return [
            RetrievedChunk(chunk_id=cid, content=doc, score=1.0 - float(dist), source="internal_doc")
            for cid, doc, dist in zip(ids, docs, distances)
        ]
