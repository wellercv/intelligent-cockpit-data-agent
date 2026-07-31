"""Deterministic hybrid retrieval over SQLite FTS5 and ChromaDB."""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Protocol, Sequence

import chromadb

from data_insight.llm import AzureLLMGateway

_TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9_.-]*|[\u3400-\u9fff]")


class EmbeddingProvider(Protocol):
    name: str
    dimensions: int

    def embed(self, texts: Sequence[str]) -> List[List[float]]: ...


class FeatureHashEmbedding:
    """Offline embedding fallback using stable word and CJK n-gram features."""

    name = "feature-hash-v1"

    def __init__(self, dimensions: int = 384) -> None:
        if dimensions < 64:
            raise ValueError("dimensions must be at least 64")
        self.dimensions = dimensions

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> List[float]:
        normalized = text.casefold()
        features = list(_TOKEN_RE.findall(normalized))
        cjk = "".join(char for char in normalized if "\u3400" <= char <= "\u9fff")
        features.extend(cjk[index : index + 2] for index in range(max(0, len(cjk) - 1)))
        features.extend(cjk[index : index + 3] for index in range(max(0, len(cjk) - 2)))
        vector = [0.0] * self.dimensions
        for feature in features or [normalized]:
            digest = hashlib.sha256(feature.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]


class AzureEmbedding:
    """Azure OpenAI embedding adapter used when a deployment is configured."""

    def __init__(
        self,
        gateway: AzureLLMGateway,
        deployment: str,
        dimensions: int = 1536,
    ) -> None:
        if not deployment.strip():
            raise ValueError("embedding deployment is required")
        self.gateway = gateway
        self.deployment = deployment.strip()
        self.dimensions = dimensions
        self.name = f"azure-openai:{self.deployment}"

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        if not texts:
            return []
        return self.gateway.embed(
            "knowledge_embedding",
            texts,
            self.deployment,
        )


@dataclass(frozen=True)
class KnowledgeChunk:
    chunk_id: str
    path: str
    title: str
    content: str
    metadata: Dict[str, Any]


class HybridKnowledgeIndex:
    """BM25 + vector retrieval with RRF fusion and deterministic reranking."""

    def __init__(
        self,
        knowledge_dir: Path,
        fts_path: Path,
        vector_path: Path,
        embedding: EmbeddingProvider | None = None,
    ) -> None:
        self.knowledge_dir = knowledge_dir
        self.fts_path = fts_path
        self.vector_path = vector_path
        self.embedding = embedding or FeatureHashEmbedding()
        self.collection_name = self._collection_name(
            "business_knowledge", self.embedding
        )
        self.fts_path.parent.mkdir(parents=True, exist_ok=True)
        self.vector_path.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(self.vector_path))
        self._collection = self._client.get_or_create_collection(
            self.collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    def close(self) -> None:
        self._client.close()

    def rebuild(self, chunks: Sequence[KnowledgeChunk]) -> None:
        with self._connect() as connection:
            connection.execute("DROP TABLE IF EXISTS knowledge_fts")
            connection.execute(
                """
                CREATE VIRTUAL TABLE knowledge_fts USING fts5(
                    chunk_id UNINDEXED,
                    path UNINDEXED,
                    title,
                    content,
                    metadata_json UNINDEXED
                )
                """
            )
            connection.executemany(
                "INSERT INTO knowledge_fts VALUES (?, ?, ?, ?, ?)",
                [
                    (
                        chunk.chunk_id,
                        chunk.path,
                        chunk.title,
                        chunk.content,
                        json.dumps(chunk.metadata, ensure_ascii=False, sort_keys=True),
                    )
                    for chunk in chunks
                ],
            )

        current_ids = {chunk.chunk_id for chunk in chunks}
        existing = self._collection.get(include=["metadatas"])
        existing_ids = set(existing["ids"])
        existing_hashes = {
            chunk_id: str(metadata.get("content_hash", ""))
            for chunk_id, metadata in zip(
                existing["ids"], existing["metadatas"]
            )
        }
        stale_ids = sorted(existing_ids - current_ids)
        if stale_ids:
            self._collection.delete(ids=stale_ids)
        changed = [
            chunk
            for chunk in chunks
            if existing_hashes.get(chunk.chunk_id) != self._content_hash(chunk)
        ]
        if not changed:
            return
        documents = [f"{chunk.title}\n{chunk.content}" for chunk in changed]
        self._collection.upsert(
            ids=[chunk.chunk_id for chunk in changed],
            documents=documents,
            metadatas=[self._chroma_metadata(chunk) for chunk in changed],
            embeddings=self.embedding.embed(documents),
        )

    def search(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        limit = max(1, min(limit, 20))
        candidate_limit = max(20, limit * 4)
        rewritten = self.rewrite_query(query)
        lexical = self._search_fts(rewritten, candidate_limit)
        vector = self._search_vector(rewritten, candidate_limit)
        candidates: Dict[str, Dict[str, Any]] = {}
        self._merge_ranked(candidates, lexical, "bm25_rank")
        self._merge_ranked(candidates, vector, "vector_rank")
        query_terms = self._terms(rewritten)
        for candidate in candidates.values():
            ranks = [
                candidate[name]
                for name in ("bm25_rank", "vector_rank")
                if candidate.get(name) is not None
            ]
            candidate["rrf_score"] = round(
                sum(1.0 / (60 + rank) for rank in ranks), 8
            )
            searchable = f"{candidate['title']} {candidate['content']}".casefold()
            matched = sum(term in searchable for term in query_terms)
            coverage = matched / len(query_terms) if query_terms else 0.0
            candidate["term_coverage"] = round(coverage, 4)
            vector_similarity = float(candidate.get("vector_similarity", 0.0))
            candidate["rerank_score"] = round(
                candidate["rrf_score"] * 100
                + coverage * 0.25
                + max(0.0, vector_similarity) * 0.05,
                6,
            )
            candidate["retrieval_modes"] = [
                mode
                for mode, rank_name in (
                    ("bm25", "bm25_rank"),
                    ("vector", "vector_rank"),
                )
                if candidate.get(rank_name) is not None
            ]
        return sorted(
            candidates.values(),
            key=lambda item: (-item["rerank_score"], item["chunk_id"]),
        )[:limit]

    def health(self) -> Dict[str, Any]:
        with self._connect() as connection:
            lexical_count = connection.execute(
                "SELECT count(*) FROM knowledge_fts"
            ).fetchone()[0]
        return {
            "retrieval": "hybrid",
            "lexical_engine": "SQLite FTS5/BM25",
            "vector_store": "ChromaDB PersistentClient",
            "embedding": self.embedding.name,
            "chunks": int(lexical_count),
            "vector_chunks": int(self._collection.count()),
            "fusion": "RRF",
            "reranker": "term-coverage + vector-similarity",
        }

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.fts_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _search_fts(self, query: str, limit: int) -> List[Dict[str, Any]]:
        terms = self._terms(query)
        expression = " OR ".join(f'"{term}"' for term in terms) or '"data"'
        with self._connect() as connection:
            try:
                rows = connection.execute(
                    """
                    SELECT chunk_id, path, title, content, metadata_json,
                           bm25(knowledge_fts) score
                    FROM knowledge_fts
                    WHERE knowledge_fts MATCH ?
                    ORDER BY score LIMIT ?
                    """,
                    [expression, limit],
                ).fetchall()
            except sqlite3.OperationalError:
                return []
        return [
            {
                **dict(row),
                "metadata": json.loads(row["metadata_json"]),
            }
            for row in rows
        ]

    def _search_vector(self, query: str, limit: int) -> List[Dict[str, Any]]:
        count = self._collection.count()
        if count == 0:
            return []
        result = self._collection.query(
            query_embeddings=self.embedding.embed([query]),
            n_results=min(limit, count),
            include=["documents", "metadatas", "distances"],
        )
        rows: List[Dict[str, Any]] = []
        for chunk_id, document, metadata, distance in zip(
            result["ids"][0],
            result["documents"][0],
            result["metadatas"][0],
            result["distances"][0],
        ):
            rows.append(
                {
                    "chunk_id": chunk_id,
                    "path": metadata["path"],
                    "title": metadata["title"],
                    "content": document.split("\n", 1)[-1],
                    "metadata": json.loads(str(metadata["metadata_json"])),
                    "vector_distance": round(float(distance), 6),
                    "vector_similarity": round(1.0 - float(distance), 6),
                }
            )
        return rows

    @staticmethod
    def _merge_ranked(
        candidates: Dict[str, Dict[str, Any]],
        rows: Iterable[Dict[str, Any]],
        rank_name: str,
    ) -> None:
        for rank, row in enumerate(rows, start=1):
            chunk_id = str(row["chunk_id"])
            candidate = candidates.setdefault(
                chunk_id,
                {
                    "chunk_id": chunk_id,
                    "path": row["path"],
                    "title": row["title"],
                    "content": row["content"],
                    "metadata": row.get("metadata", {}),
                },
            )
            candidate[rank_name] = rank
            for name in ("score", "vector_distance", "vector_similarity"):
                if name in row:
                    candidate[name] = row[name]

    @staticmethod
    def rewrite_query(query: str) -> str:
        lower = query.casefold()
        additions: List[str] = []
        if any(token in lower for token in ("wer", "ter", "词错误率", "token error")):
            additions.extend(
                ("word error rate", "insertion deletion substitution", "reference transcript")
            )
        elif any(token in lower for token in ("准确率", "错误率", "指标", "正确数")):
            additions.extend(
                ("ASR metric definitions", "CSV case-row analysis", "correct total formula")
            )
        if any(token in lower for token in ("最好", "最差", "best", "worst")):
            additions.extend(
                ("best worst language", "explicit measure", "accuracy error rate error count")
            )
        if any(token in lower for token in ("口径", "范围", "来源", "对不上", "csv", "json")):
            additions.extend(
                ("Multilingual ASR data scope", "CSV case rows", "JSON summaries", "source priority")
            )
        if any(token in lower for token in ("训练数据", "生成流程", "运行标签", "成功标记", "进程")):
            additions.extend(
                ("training data workflow", "run tag configuration", "output validation", "completion status")
            )
        if any(token in lower for token in ("音乐榜单", "批量爬取", "再分发", "mena", "沙特")):
            additions.extend(
                ("music chart source", "collection permission", "Official MENA Chart", "license status")
            )
        if any(token in lower for token in ("项目各方", "如何分工", "角色分工", "sdk", "应用接口")):
            additions.extend(
                ("project delivery roles", "SDK integration", "responsibility boundary", "root cause attribution")
            )
        if any(token in lower for token in ("合成银标", "生产准确率", "模板银标")):
            additions.extend(
                ("synthetic silver label", "template evaluation", "production accuracy boundary")
            )
        if any(token in lower for token in ("治理", "确认", "审批", "回滚", "修改")):
            additions.extend(("data governance", "confirmation", "dataset version", "rollback"))
        return " ".join([query, *additions])

    @staticmethod
    def _terms(text: str) -> List[str]:
        terms = [term.casefold() for term in _TOKEN_RE.findall(text)]
        return list(dict.fromkeys(term for term in terms if len(term) > 1 or ord(term) > 127))

    @staticmethod
    def _chroma_metadata(chunk: KnowledgeChunk) -> Dict[str, Any]:
        return {
            "path": chunk.path,
            "title": chunk.title,
            "content_hash": HybridKnowledgeIndex._content_hash(chunk),
            "metadata_json": json.dumps(
                chunk.metadata, ensure_ascii=False, sort_keys=True
            ),
        }

    @staticmethod
    def _content_hash(chunk: KnowledgeChunk) -> str:
        return hashlib.sha256(
            f"{chunk.title}\x1f{chunk.content}\x1f{json.dumps(chunk.metadata, sort_keys=True, ensure_ascii=False)}".encode(
                "utf-8"
            )
        ).hexdigest()

    @staticmethod
    def _collection_name(prefix: str, embedding: EmbeddingProvider) -> str:
        suffix = re.sub(r"[^a-zA-Z0-9_-]+", "_", embedding.name).strip("_")
        return f"{prefix}_{suffix}_{embedding.dimensions}"[:63]