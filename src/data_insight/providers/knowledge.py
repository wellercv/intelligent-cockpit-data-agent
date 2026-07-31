"""Hybrid RAG provider over business knowledge documents."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List

import yaml

from data_insight.providers.base import DataProvider
from data_insight.retrieval import EmbeddingProvider, HybridKnowledgeIndex, KnowledgeChunk
from data_insight.schemas import SourceRef, ToolCall, ToolObservation


class KnowledgeProvider(DataProvider):
    name = "business_knowledge"

    def __init__(
        self,
        knowledge_dir: Path,
        index_path: Path,
        vector_path: Path | None = None,
        embedding: EmbeddingProvider | None = None,
    ) -> None:
        self.knowledge_dir = knowledge_dir
        self.index = HybridKnowledgeIndex(
            knowledge_dir,
            index_path,
            vector_path or index_path.parent / "knowledge_chroma",
            embedding,
        )
        self._build_index()

    def close(self) -> None:
        self.index.close()

    def _build_index(self) -> None:
        chunks: List[KnowledgeChunk] = []
        for path in sorted(self.knowledge_dir.rglob("*.md")):
            metadata, text = self._read_document(path)
            if metadata.get("index") is False:
                continue
            relative = path.relative_to(self.knowledge_dir).as_posix()
            title = str(metadata.get("title") or next(
                (
                    line.lstrip("# ").strip()
                    for line in text.splitlines()
                    if line.startswith("#")
                ),
                path.stem,
            ))
            for index, content in enumerate(self._chunks(text), start=1):
                digest = hashlib.sha256(
                    f"{relative}\x1f{index}\x1f{content}".encode("utf-8")
                ).hexdigest()[:20]
                chunks.append(
                    KnowledgeChunk(
                        chunk_id=f"kb-{digest}",
                        path=relative,
                        title=title,
                        content=content,
                        metadata={
                            "document_type": path.parent.name
                            if path.parent != self.knowledge_dir
                            else "general",
                            "chunk_index": index,
                            **{
                                key: value
                                for key, value in metadata.items()
                                if key not in {"index", "title"}
                            },
                        },
                    )
                )
        self.index.rebuild(chunks)

    @staticmethod
    def _read_document(path: Path) -> tuple[Dict[str, Any], str]:
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        if not lines or lines[0].strip() != "---":
            return {}, text
        closing = next(
            (index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---"),
            None,
        )
        if closing is None:
            return {}, text
        payload = yaml.safe_load("\n".join(lines[1:closing])) or {}
        if not isinstance(payload, dict):
            raise ValueError(f"Knowledge front matter must be an object: {path}")
        normalized = json.loads(
            json.dumps(payload, ensure_ascii=False, default=str)
        )
        return normalized, "\n".join(lines[closing + 1 :]).strip()

    def tool_catalog(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "search_knowledge",
                "description": (
                    "Hybrid retrieval over metric definitions, source-scope policy, "
                    "test SOPs, and business documentation"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 10},
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
            }
        ]

    def execute(self, call: ToolCall) -> ToolObservation:
        if call.name != "search_knowledge":
            return ToolObservation(
                call_id=call.call_id,
                tool_name=call.name,
                success=False,
                error=f"Unknown knowledge tool: {call.name}",
            )
        query = str(call.arguments.get("query", "")).strip()
        if not query:
            return ToolObservation(
                call_id=call.call_id,
                tool_name=call.name,
                success=False,
                error="query is required",
            )
        limit = max(1, min(int(call.arguments.get("limit", 5)), 10))
        rewritten = self.index.rewrite_query(query)
        rows = self.index.search(query, limit)
        sources: List[SourceRef] = []
        for path in dict.fromkeys(row["path"] for row in rows):
            first = next(row for row in rows if row["path"] == path)
            sources.append(
                SourceRef(
                    source_id=f"kb-{len(sources) + 1}",
                    label=first["title"],
                    path=f"knowledge/{path}",
                    scope="business_knowledge",
                )
            )
        return ToolObservation(
            call_id=call.call_id,
            tool_name=call.name,
            rows=rows,
            data={
                "query": query,
                "rewritten_query": rewritten,
                "returned": len(rows),
                "retrieval": self.index.health(),
            },
            sources=sources,
            warnings=[] if rows else ["No matching business document was found."],
        )

    def health(self) -> Dict[str, Any]:
        return {"provider": self.name, "ready": True, **self.index.health()}

    @staticmethod
    def _chunks(text: str, max_chars: int = 1200) -> List[str]:
        paragraphs = [item.strip() for item in text.split("\n\n") if item.strip()]
        chunks: List[str] = []
        current = ""
        for paragraph in paragraphs:
            if current and len(current) + len(paragraph) + 2 > max_chars:
                chunks.append(current)
                current = paragraph
            else:
                current = f"{current}\n\n{paragraph}".strip()
        if current:
            chunks.append(current)
        return chunks
