"""Observable record identity shared by Replay and portable exporters."""

from __future__ import annotations

import json
from typing import Any


def _decode(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def logical_metric_record_key(row: dict[str, Any]) -> str:
    """Exclude collection provenance while retaining observable identity."""

    logical = {
        "entity_ids": sorted(str(item) for item in (_decode(row["entity_ids"]) or [])),
        "signal": row["signal"],
        "stat": row["stat"],
        "value": _decode(row["value_json"]),
        "unit": row.get("unit", ""),
        "dimensions": _decode(row.get("dimensions", {})),
        "observed_at": row["observed_at"],
    }
    return json.dumps(logical, sort_keys=True, separators=(",", ":"), default=str)


def deduplicate_metric_records(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Keep one deterministic representative of each observable sample."""

    unique: dict[str, dict[str, Any]] = {}
    for row in sorted(rows, key=lambda item: str(item["record_ref"])):
        unique.setdefault(logical_metric_record_key(row), row)
    return list(unique.values())
