from __future__ import annotations

from data_insight.providers.knowledge import KnowledgeProvider
from data_insight.retrieval import HybridKnowledgeIndex, KnowledgeChunk


class CountingEmbedding:
    name = "counting"
    dimensions = 64

    def __init__(self):
        self.texts = 0

    def embed(self, texts):
        self.texts += len(texts)
        return [[1.0] + [0.0] * 63 for _ in texts]


def test_knowledge_provider_reads_front_matter_and_skips_excluded_docs(tmp_path):
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    (knowledge / "included.md").write_text(
        "---\n"
        "title: Curated ASR policy\n"
        "source_type: public_official\n"
        "verified_on: 2026-07-28\n"
        "confidence: high\n"
        "---\n"
        "ASR evaluation uses representative audio and reference transcripts.\n",
        encoding="utf-8",
    )
    (knowledge / "excluded.md").write_text(
        "---\nindex: false\n---\nInternal infrastructure secrets.\n",
        encoding="utf-8",
    )
    provider = KnowledgeProvider(
        knowledge,
        tmp_path / "knowledge.db",
        tmp_path / "vectors",
    )
    try:
        rows = provider.index.search("representative audio transcripts", limit=5)

        assert provider.health()["chunks"] == 1
        assert rows[0]["title"] == "Curated ASR policy"
        assert rows[0]["metadata"]["source_type"] == "public_official"
        assert rows[0]["metadata"]["confidence"] == "high"
    finally:
        provider.close()


def test_hybrid_retrieval_fuses_bm25_and_chromadb(tmp_path):
    index = HybridKnowledgeIndex(
        tmp_path,
        tmp_path / "knowledge.db",
        tmp_path / "vectors",
    )
    try:
        index.rebuild(
            [
                KnowledgeChunk(
                    "metric",
                    "metrics.md",
                    "ASR metric definitions",
                    "Accuracy is correct / total * 100 for CSV case rows.",
                    {"document_type": "metric"},
                ),
                KnowledgeChunk(
                    "governance",
                    "governance.md",
                    "Dataset governance",
                    "Approved changes create a dataset version and can be rolled back.",
                    {"document_type": "policy"},
                ),
            ]
        )

        rows = index.search("How is ASR accuracy calculated?", limit=2)

        assert rows[0]["chunk_id"] == "metric"
        assert rows[0]["retrieval_modes"] == ["bm25", "vector"]
        assert rows[0]["rrf_score"] > 0
        assert rows[0]["rerank_score"] > 0
        assert index.health()["vector_store"] == "ChromaDB PersistentClient"
    finally:
        index.close()


def test_hybrid_retrieval_rewrite_supports_scope_questions(tmp_path):
    index = HybridKnowledgeIndex(
        tmp_path,
        tmp_path / "knowledge.db",
        tmp_path / "vectors",
    )
    try:
        index.rebuild(
            [
                KnowledgeChunk(
                    "scope",
                    "scope.md",
                    "CSV JSON source scope",
                    "CSV case rows and JSON summaries can use different data scopes.",
                    {"document_type": "policy"},
                )
            ]
        )

        rows = index.search("两个结果文件为什么对不上", limit=1)

        assert rows[0]["chunk_id"] == "scope"
        assert "source priority" in index.rewrite_query("两个结果文件为什么对不上")
    finally:
        index.close()


def test_hybrid_rebuild_keeps_existing_client_valid(tmp_path):
    first = HybridKnowledgeIndex(
        tmp_path,
        tmp_path / "knowledge.db",
        tmp_path / "vectors",
    )
    second = HybridKnowledgeIndex(
        tmp_path,
        tmp_path / "knowledge.db",
        tmp_path / "vectors",
    )
    try:
        first.rebuild(
            [
                KnowledgeChunk(
                    "old",
                    "old.md",
                    "Old policy",
                    "A retired source policy.",
                    {},
                )
            ]
        )
        second.rebuild(
            [
                KnowledgeChunk(
                    "current",
                    "current.md",
                    "Current metric",
                    "ASR accuracy uses correct and total.",
                    {},
                )
            ]
        )

        rows = first.search("ASR accuracy", limit=5)

        assert [row["chunk_id"] for row in rows] == ["current"]
        assert first.health()["vector_chunks"] == 1
    finally:
        first.close()
        second.close()


def test_hybrid_rebuild_does_not_reembed_unchanged_chunks(tmp_path):
    embedding = CountingEmbedding()
    index = HybridKnowledgeIndex(
        tmp_path,
        tmp_path / "knowledge.db",
        tmp_path / "vectors",
        embedding,
    )
    chunks = [
        KnowledgeChunk(
            "metric",
            "metrics.md",
            "ASR metric",
            "Accuracy uses correct and total.",
            {},
        )
    ]
    try:
        index.rebuild(chunks)
        first_count = embedding.texts
        index.rebuild(chunks)

        assert first_count == 1
        assert embedding.texts == first_count
    finally:
        index.close()