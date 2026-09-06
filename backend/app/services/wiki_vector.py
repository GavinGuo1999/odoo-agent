from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.request import Request, urlopen
from uuid import uuid4

import faiss
from llama_index.core.schema import TextNode
from llama_index.core.vector_stores.types import VectorStoreQuery
from llama_index.vector_stores.faiss import FaissVectorStore
from openai import OpenAI


class EmbeddingClient(Protocol):
    model: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class RerankerClient(Protocol):
    model: str

    def rerank(
        self,
        query: str,
        documents: list[str],
        *,
        top_n: int,
    ) -> list[tuple[int, float]]: ...


@dataclass(frozen=True, slots=True)
class VectorRecord:
    chunk_id: str
    text: str


def _normalized(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(float(value) ** 2 for value in vector))
    if not vector or not math.isfinite(norm) or norm <= 0:
        raise ValueError("InvalidEmbeddingVector")
    normalized = [float(value) / norm for value in vector]
    if not all(math.isfinite(value) for value in normalized):
        raise ValueError("InvalidEmbeddingVector")
    return normalized


class SiliconFlowEmbeddingClient:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: float,
        batch_size: int = 32,
    ) -> None:
        self.model = model
        self._batch_size = batch_size
        self._client = OpenAI(
            api_key=api_key,
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds,
            max_retries=1,
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        embeddings: list[list[float]] = []
        for offset in range(0, len(texts), self._batch_size):
            batch = texts[offset : offset + self._batch_size]
            response = self._client.embeddings.create(model=self.model, input=batch)
            ordered = sorted(response.data, key=lambda item: item.index)
            embeddings.extend([list(item.embedding) for item in ordered])
        if len(embeddings) != len(texts):
            raise RuntimeError("EmbeddingCountMismatch")
        return embeddings


class SiliconFlowRerankerClient:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: float,
    ) -> None:
        self.model = model
        self._api_key = api_key
        self._url = f"{base_url.rstrip('/')}/rerank"
        self._timeout_seconds = timeout_seconds

    def rerank(
        self,
        query: str,
        documents: list[str],
        *,
        top_n: int,
    ) -> list[tuple[int, float]]:
        if not documents:
            return []
        payload = json.dumps(
            {
                "model": self.model,
                "query": query,
                "documents": documents,
                "top_n": min(max(top_n, 1), len(documents)),
                "return_documents": False,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        request = Request(
            self._url,
            data=payload,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urlopen(request, timeout=self._timeout_seconds) as response:
            body = json.loads(response.read().decode("utf-8"))
        ranked: list[tuple[int, float]] = []
        seen: set[int] = set()
        for item in body.get("results", []):
            index = int(item["index"])
            if index in seen or index < 0 or index >= len(documents):
                continue
            score = float(item["relevance_score"])
            if not math.isfinite(score):
                continue
            ranked.append((index, score))
            seen.add(index)
        return ranked


class LlamaIndexFaissStore:
    """Persist normalized Wiki embeddings through LlamaIndex's FAISS adapter."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.meta_path = path.with_suffix(path.suffix + ".meta.json")

    def is_current(self, *, source_fingerprint: str, model: str) -> bool:
        if not self.path.is_file() or not self.meta_path.is_file():
            return False
        try:
            metadata = json.loads(self.meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        return bool(
            metadata.get("source_fingerprint") == source_fingerprint
            and metadata.get("embedding_model") == model
            and metadata.get("chunk_ids")
        )

    def build(
        self,
        *,
        records: list[VectorRecord],
        embeddings: list[list[float]],
        source_fingerprint: str,
        model: str,
    ) -> None:
        if not records or len(records) != len(embeddings):
            raise ValueError("EmbeddingCountMismatch")
        normalized = [_normalized(vector) for vector in embeddings]
        dimensions = {len(vector) for vector in normalized}
        if len(dimensions) != 1:
            raise ValueError("EmbeddingDimensionMismatch")
        dimension = dimensions.pop()
        index = faiss.IndexFlatIP(dimension)
        store = FaissVectorStore(faiss_index=index)
        nodes = [
            TextNode(id_=record.chunk_id, text=record.text, embedding=embedding)
            for record, embedding in zip(records, normalized, strict=True)
        ]
        store.add(nodes)

        self.path.parent.mkdir(parents=True, exist_ok=True)
        suffix = f".{uuid4().hex}.tmp"
        temp_index = self.path.with_name(self.path.name + suffix)
        temp_meta = self.meta_path.with_name(self.meta_path.name + suffix)
        metadata = {
            "source_fingerprint": source_fingerprint,
            "embedding_model": model,
            "dimension": dimension,
            "chunk_ids": [record.chunk_id for record in records],
        }
        try:
            store.persist(str(temp_index))
            temp_meta.write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            temp_index.replace(self.path)
            temp_meta.replace(self.meta_path)
        finally:
            temp_index.unlink(missing_ok=True)
            temp_meta.unlink(missing_ok=True)

    def search(self, embedding: list[float], *, limit: int) -> list[tuple[str, float]]:
        metadata = json.loads(self.meta_path.read_text(encoding="utf-8"))
        chunk_ids = [str(item) for item in metadata["chunk_ids"]]
        store = FaissVectorStore.from_persist_path(str(self.path))
        result = store.query(
            VectorStoreQuery(
                query_embedding=_normalized(embedding),
                similarity_top_k=min(max(limit, 1), len(chunk_ids)),
            )
        )
        ranked: list[tuple[str, float]] = []
        for raw_index, raw_score in zip(result.ids or [], result.similarities or []):
            index = int(raw_index)
            if 0 <= index < len(chunk_ids):
                ranked.append((chunk_ids[index], float(raw_score)))
        return ranked
