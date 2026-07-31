"""Portable exports for grounded Agent answers."""

from __future__ import annotations

from typing import Any

import pandas as pd

from data_insight.schemas import AgentAnswer


def answer_csv_bytes(answer: AgentAnswer) -> bytes:
    rows: list[dict[str, Any]] = []
    for observation_index, observation in enumerate(answer.observations, 1):
        base = {
            "trace_id": answer.trace_id,
            "question": answer.question,
            "observation": observation_index,
            "tool_name": observation.tool_name,
            "success": observation.success,
            "elapsed_ms": observation.elapsed_ms,
        }
        if observation.rows:
            rows.extend(
                {**row, **base, "row": row_index}
                for row_index, row in enumerate(observation.rows, 1)
            )
        elif observation.data:
            normalized = pd.json_normalize(observation.data, sep=".").to_dict(
                orient="records"
            )
            rows.extend({**row, **base, "row": 1} for row in normalized)
        else:
            rows.append({**base, "row": 1, "error": observation.error or ""})
    return pd.DataFrame(rows).to_csv(index=False).encode("utf-8-sig")