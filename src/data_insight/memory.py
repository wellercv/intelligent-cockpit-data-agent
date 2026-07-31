"""SQLite conversation memory and durable agent traces."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

import chromadb

from data_insight.retrieval import (
    EmbeddingProvider,
    FeatureHashEmbedding,
    HybridKnowledgeIndex,
)
from data_insight.schemas import ConversationContext, TraceEvent


class AgentStateStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS conversations (
                    conversation_id TEXT PRIMARY KEY,
                    context_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS traces (
                    trace_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    event_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(trace_id, sequence)
                );
                CREATE TABLE IF NOT EXISTS evaluations (
                    run_id TEXT NOT NULL,
                    case_id TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(run_id, case_id)
                );
                CREATE TABLE IF NOT EXISTS evaluation_runs (
                    run_id TEXT PRIMARY KEY,
                    dataset TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    status TEXT NOT NULL,
                    summary_json TEXT NOT NULL DEFAULT '{}',
                    started_at TEXT NOT NULL,
                    finished_at TEXT
                );
                CREATE TABLE IF NOT EXISTS evaluation_judgments (
                    run_id TEXT NOT NULL,
                    case_id TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(run_id, case_id)
                );
                CREATE TABLE IF NOT EXISTS llm_calls (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    operation TEXT NOT NULL,
                    deployment TEXT NOT NULL,
                    success INTEGER NOT NULL,
                    latency_ms REAL NOT NULL,
                    prompt_tokens INTEGER NOT NULL DEFAULT 0,
                    completion_tokens INTEGER NOT NULL DEFAULT 0,
                    total_tokens INTEGER NOT NULL DEFAULT 0,
                    error_type TEXT,
                    created_at TEXT NOT NULL
                );
                """
            )

    def load_context(self, conversation_id: str | None) -> ConversationContext:
        if not conversation_id:
            return ConversationContext()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT context_json FROM conversations WHERE conversation_id = ?", [conversation_id]
            ).fetchone()
        return ConversationContext.model_validate_json(row[0]) if row else ConversationContext(conversation_id=conversation_id)

    def save_context(self, context: ConversationContext) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO conversations(conversation_id, context_json) VALUES (?, ?)
                ON CONFLICT(conversation_id) DO UPDATE SET context_json=excluded.context_json, updated_at=CURRENT_TIMESTAMP
                """, [context.conversation_id, context.model_dump_json()],
            )

    def add_message(self, conversation_id: str, role: str, content: str, metadata: Dict[str, Any] | None = None) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO messages(conversation_id, role, content, metadata_json) VALUES (?, ?, ?, ?)",
                [conversation_id, role, content, json.dumps(metadata or {}, ensure_ascii=False)],
            )

    def recent_messages(self, conversation_id: str, limit: int = 12) -> List[Dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT role, content, metadata_json, created_at FROM messages
                WHERE conversation_id = ? ORDER BY id DESC LIMIT ?
                """, [conversation_id, limit],
            ).fetchall()
        return [
            {"role": row["role"], "content": row["content"], "metadata": json.loads(row["metadata_json"]), "created_at": row["created_at"]}
            for row in reversed(rows)
        ]

    def save_trace(self, trace_id: str, events: List[TraceEvent]) -> None:
        with self._connect() as connection:
            connection.executemany(
                "INSERT OR REPLACE INTO traces(trace_id, sequence, event_json) VALUES (?, ?, ?)",
                [(trace_id, index, event.model_dump_json()) for index, event in enumerate(events, 1)],
            )

    def load_trace(self, trace_id: str) -> List[TraceEvent]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT event_json FROM traces WHERE trace_id = ? ORDER BY sequence", [trace_id]
            ).fetchall()
        return [TraceEvent.model_validate_json(row[0]) for row in rows]

    def save_evaluation(self, run_id: str, case_id: str, payload: Dict[str, Any]) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO evaluations(run_id, case_id, result_json) VALUES (?, ?, ?)",
                [run_id, case_id, json.dumps(payload, ensure_ascii=False)],
            )

    def start_evaluation_run(self, run_id: str, dataset: str, mode: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        stale_before = (
            datetime.now(timezone.utc) - timedelta(hours=24)
        ).isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE evaluation_runs
                SET status='ABORTED', finished_at=?
                WHERE status='RUNNING' AND run_id<>? AND started_at<?
                """,
                [now, run_id, stale_before],
            )
            connection.execute(
                """
                INSERT OR REPLACE INTO evaluation_runs(
                    run_id, dataset, mode, status, summary_json,
                    started_at, finished_at
                ) VALUES (?, ?, ?, 'RUNNING', '{}', ?, NULL)
                """,
                [run_id, dataset, mode, now],
            )

    def finish_evaluation_run(
        self,
        run_id: str,
        summary: Dict[str, Any],
        status: str = "COMPLETED",
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE evaluation_runs
                SET status=?, summary_json=?, finished_at=?
                WHERE run_id=?
                """,
                [status, json.dumps(summary, ensure_ascii=False), now, run_id],
            )

    def save_evaluation_judgment(
        self, run_id: str, case_id: str, payload: Dict[str, Any]
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO evaluation_judgments(
                    run_id, case_id, result_json, created_at
                ) VALUES (?, ?, ?, ?)
                """,
                [run_id, case_id, json.dumps(payload, ensure_ascii=False), now],
            )

    def get_evaluation_run(self, run_id: str) -> Dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM evaluation_runs WHERE run_id=?", [run_id]
            ).fetchone()
            judgments = connection.execute(
                """
                SELECT case_id, result_json, created_at
                FROM evaluation_judgments WHERE run_id=? ORDER BY case_id
                """,
                [run_id],
            ).fetchall()
        if row is None:
            return None
        return {
            **dict(row),
            "summary": json.loads(row["summary_json"]),
            "judgments": [
                {
                    "case_id": item["case_id"],
                    "result": json.loads(item["result_json"]),
                    "created_at": item["created_at"],
                }
                for item in judgments
            ],
        }

    def list_evaluation_runs(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM evaluation_runs
                ORDER BY started_at DESC LIMIT ?
                """,
                [max(1, min(limit, 200))],
            ).fetchall()
        return [
            {
                **dict(row),
                "summary": json.loads(row["summary_json"]),
            }
            for row in rows
        ]

    def save_llm_call(self, payload: Dict[str, Any]) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO llm_calls(
                    operation, deployment, success, latency_ms,
                    prompt_tokens, completion_tokens, total_tokens,
                    error_type, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    payload["operation"],
                    payload["deployment"],
                    int(bool(payload["success"])),
                    float(payload["latency_ms"]),
                    int(payload.get("prompt_tokens", 0)),
                    int(payload.get("completion_tokens", 0)),
                    int(payload.get("total_tokens", 0)),
                    payload.get("error_type"),
                    payload["created_at"],
                ],
            )

    def llm_usage_summary(
        self,
        input_price_per_million: float = 0.0,
        output_price_per_million: float = 0.0,
        after_id: int = 0,
    ) -> Dict[str, Any]:
        with self._connect() as connection:
            totals = connection.execute(
                """
                SELECT count(*) calls,
                       coalesce(avg(latency_ms), 0) average_latency_ms,
                       coalesce(sum(prompt_tokens), 0) prompt_tokens,
                       coalesce(sum(completion_tokens), 0) completion_tokens,
                       coalesce(sum(total_tokens), 0) total_tokens,
                       coalesce(avg(CAST(success AS REAL)), 1) success_rate
                FROM llm_calls WHERE id > ?
                """,
                [after_id],
            ).fetchone()
            operations = connection.execute(
                """
                SELECT operation, count(*) count, sum(success) successful
                FROM llm_calls WHERE id > ? GROUP BY operation
                """,
                [after_id],
            ).fetchall()
            errors = connection.execute(
                """
                SELECT error_type, count(*) count FROM llm_calls
                WHERE id > ? AND error_type IS NOT NULL GROUP BY error_type
                """,
                [after_id],
            ).fetchall()
            successful_latencies = [
                float(row["latency_ms"])
                for row in connection.execute(
                    """
                    SELECT latency_ms FROM llm_calls
                    WHERE id > ? AND success=1 ORDER BY latency_ms
                    """,
                    [after_id],
                ).fetchall()
            ]
        prompt_tokens = int(totals["prompt_tokens"])
        completion_tokens = int(totals["completion_tokens"])
        estimated_input_cost = prompt_tokens / 1_000_000 * input_price_per_million
        estimated_output_cost = (
            completion_tokens / 1_000_000 * output_price_per_million
        )
        return {
            "calls": int(totals["calls"]),
            "success_rate": round(float(totals["success_rate"]), 4),
            "average_latency_ms": round(float(totals["average_latency_ms"]), 2),
            "p50_latency_ms": self._percentile(successful_latencies, 0.50),
            "p95_latency_ms": self._percentile(successful_latencies, 0.95),
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": int(totals["total_tokens"]),
            "estimated_cost_usd": round(
                estimated_input_cost + estimated_output_cost, 6
            ),
            "estimated_input_cost_usd": round(estimated_input_cost, 6),
            "estimated_output_cost_usd": round(estimated_output_cost, 6),
            "pricing_note": "Estimate only; Azure invoice and contract pricing are authoritative.",
            "by_operation": {row["operation"]: row["count"] for row in operations},
            "successful_by_operation": {
                row["operation"]: int(row["successful"] or 0)
                for row in operations
            },
            "errors": {row["error_type"]: row["count"] for row in errors},
        }

    def latest_llm_call_id(self) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT coalesce(max(id), 0) latest_id FROM llm_calls"
            ).fetchone()
        return int(row["latest_id"])

    @staticmethod
    def _percentile(values: List[float], quantile: float) -> float:
        if not values:
            return 0.0
        index = max(0, min(len(values) - 1, int((len(values) - 1) * quantile + 0.5)))
        return round(values[index], 2)


class InvestigationMemory:
    """Long-term semantic memory for completed, evidence-backed investigations."""

    def __init__(
        self,
        path: Path,
        embedding: EmbeddingProvider | None = None,
    ) -> None:
        path.mkdir(parents=True, exist_ok=True)
        self.embedding = embedding or FeatureHashEmbedding()
        self.collection_name = HybridKnowledgeIndex._collection_name(
            "investigation_history", self.embedding
        )
        self.client = chromadb.PersistentClient(path=str(path))
        self.collection = self.client.get_or_create_collection(
            self.collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    def close(self) -> None:
        self.client.close()

    def remember(
        self,
        *,
        question: str,
        answer: str,
        conversation_id: str,
        trace_id: str,
        source_paths: List[str],
    ) -> str:
        summary = self._summary(answer)
        stable = "\x1f".join((question, summary, *sorted(source_paths)))
        memory_id = "investigation-" + hashlib.sha256(
            stable.encode("utf-8")
        ).hexdigest()[:24]
        document = f"问题：{question}\n结论：{summary}"
        self.collection.upsert(
            ids=[memory_id],
            documents=[document],
            embeddings=self.embedding.embed([document]),
            metadatas=[
                {
                    "question": question[:1000],
                    "conversation_id": conversation_id,
                    "trace_id": trace_id,
                    "source_paths_json": json.dumps(
                        source_paths, ensure_ascii=False
                    ),
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
            ],
        )
        return memory_id

    def recall(self, query: str, limit: int = 3) -> List[Dict[str, Any]]:
        count = self.collection.count()
        if count == 0:
            return []
        result = self.collection.query(
            query_embeddings=self.embedding.embed([query]),
            n_results=min(max(1, limit), count),
            include=["documents", "metadatas", "distances"],
        )
        return [
            {
                "memory_id": memory_id,
                "document": document,
                "question": metadata["question"],
                "trace_id": metadata["trace_id"],
                "source_paths": json.loads(
                    str(metadata.get("source_paths_json", "[]"))
                ),
                "created_at": metadata["created_at"],
                "similarity": round(1.0 - float(distance), 6),
            }
            for memory_id, document, metadata, distance in zip(
                result["ids"][0],
                result["documents"][0],
                result["metadatas"][0],
                result["distances"][0],
            )
        ]

    def health(self) -> Dict[str, Any]:
        return {
            "store": "ChromaDB PersistentClient",
            "collection": self.collection_name,
            "embedding": self.embedding.name,
            "records": self.collection.count(),
        }

    @staticmethod
    def _summary(answer: str, max_chars: int = 1800) -> str:
        compact = "\n".join(
            line.strip() for line in answer.splitlines() if line.strip()
        )
        return compact[:max_chars]
