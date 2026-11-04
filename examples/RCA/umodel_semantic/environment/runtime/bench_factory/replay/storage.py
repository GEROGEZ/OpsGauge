"""Compile public Evidence Bundles into a portable DuckDB/Parquet store."""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import tempfile
from threading import RLock
from typing import Any

import duckdb


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        handle.write(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _sql_path(path: Path) -> str:
    return str(path).replace("'", "''")


def _create_schema(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute("""
        CREATE TABLE bundles (
            bundle_id VARCHAR PRIMARY KEY,
            workspace VARCHAR NOT NULL,
            region_id VARCHAR NOT NULL,
            time_start TIMESTAMPTZ NOT NULL,
            time_end TIMESTAMPTZ NOT NULL,
            fidelity VARCHAR NOT NULL,
            modalities JSON NOT NULL,
            entity_count INTEGER NOT NULL,
            record_count INTEGER NOT NULL
        )
    """)
    connection.execute("""
        CREATE TABLE entities (
            bundle_id VARCHAR NOT NULL,
            entity_id VARCHAR NOT NULL,
            entity_name VARCHAR NOT NULL,
            entity_type VARCHAR NOT NULL,
            entity_domain VARCHAR NOT NULL,
            attributes JSON NOT NULL,
            PRIMARY KEY (bundle_id, entity_id)
        )
    """)
    connection.execute("""
        CREATE TABLE records (
            bundle_id VARCHAR NOT NULL,
            record_ref VARCHAR NOT NULL,
            provenance_ref VARCHAR NOT NULL,
            modality VARCHAR NOT NULL,
            entity_ids JSON NOT NULL,
            signal VARCHAR NOT NULL,
            time_start TIMESTAMPTZ NOT NULL,
            time_end TIMESTAMPTZ NOT NULL,
            observed_at TIMESTAMPTZ NOT NULL,
            value_json JSON NOT NULL,
            unit VARCHAR NOT NULL,
            stat VARCHAR NOT NULL,
            dimensions JSON NOT NULL,
            attributes JSON NOT NULL,
            source_kind VARCHAR NOT NULL,
            synthetic BOOLEAN NOT NULL,
            PRIMARY KEY (bundle_id, record_ref)
        )
    """)
    connection.execute("CREATE INDEX entity_bundle_idx ON entities(bundle_id)")
    connection.execute(
        "CREATE INDEX record_lookup_idx ON records(bundle_id, modality, signal)"
    )


def compile_replay_store(bundles_dir: Path, output_dir: Path) -> dict[str, Any]:
    """Compile all public bundle halves into one immutable replay snapshot."""

    source = bundles_dir.expanduser().resolve()
    destination = output_dir.expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    parquet_dir = destination / "parquet"
    parquet_dir.mkdir(parents=True, exist_ok=True)
    database_path = destination / "replay.duckdb"
    temporary_database = destination / ".replay.duckdb.tmp"
    temporary_database.unlink(missing_ok=True)

    bundle_index = _read_jsonl(source / "bundle-index.jsonl")
    bundle_rows: list[tuple[Any, ...]] = []
    entity_rows: list[tuple[Any, ...]] = []
    record_rows: list[tuple[Any, ...]] = []
    modality_counts: Counter[str] = Counter()
    for index_row in sorted(bundle_index, key=lambda item: item["bundle_id"]):
        public_root = source / index_row["public_ref"]
        manifest = _read_json(public_root / "manifest.json")
        entities = _read_jsonl(public_root / manifest["files"]["entities"])
        records = _read_jsonl(public_root / manifest["files"]["records"])
        if manifest["bundle_id"] != index_row["bundle_id"]:
            raise ValueError(f"bundle index mismatch: {index_row['bundle_id']}")
        if manifest["entity_count"] != len(entities):
            raise ValueError(f"entity count mismatch: {manifest['bundle_id']}")
        if manifest["record_count"] != len(records):
            raise ValueError(f"record count mismatch: {manifest['bundle_id']}")
        entity_ids = {item["entity_id"] for item in entities}
        for record in records:
            missing = set(record["entity_ids"]) - entity_ids
            if missing:
                raise ValueError(
                    f"record {record['record_ref']} references unknown entities: "
                    f"{sorted(missing)}"
                )
        bundle_rows.append((
            manifest["bundle_id"],
            manifest["workspace"],
            manifest["region_id"],
            manifest["time_range"]["start"],
            manifest["time_range"]["end"],
            manifest["fidelity"],
            _json(manifest["modalities"]),
            len(entities),
            len(records),
        ))
        entity_rows.extend((
            manifest["bundle_id"],
            item["entity_id"],
            item["entity_name"],
            item["entity_type"],
            item["entity_domain"],
            _json(item.get("attributes") or {}),
        ) for item in entities)
        for item in records:
            modality_counts[item["modality"]] += 1
            record_rows.append((
                manifest["bundle_id"],
                item["record_ref"],
                item["provenance_ref"],
                item["modality"],
                _json(item["entity_ids"]),
                item["signal"],
                item["time_range"]["start"],
                item["time_range"]["end"],
                item["observed_at"],
                _json(item["value"]),
                item.get("unit") or "",
                item.get("stat") or "identity",
                _json(item.get("dimensions") or {}),
                _json(item.get("attributes") or {}),
                item["source_kind"],
                bool(item["synthetic"]),
            ))

    connection = duckdb.connect(str(temporary_database))
    try:
        _create_schema(connection)
        connection.executemany(
            "INSERT INTO bundles VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", bundle_rows
        )
        connection.executemany(
            "INSERT INTO entities VALUES (?, ?, ?, ?, ?, ?)", entity_rows
        )
        connection.executemany(
            "INSERT INTO records VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            record_rows,
        )
        for table in ("bundles", "entities", "records"):
            temporary_parquet = parquet_dir / f".{table}.parquet.tmp"
            final_parquet = parquet_dir / f"{table}.parquet"
            temporary_parquet.unlink(missing_ok=True)
            connection.execute(
                f"COPY {table} TO '{_sql_path(temporary_parquet)}' "
                "(FORMAT PARQUET, COMPRESSION ZSTD)"
            )
            temporary_parquet.replace(final_parquet)
        connection.execute("CHECKPOINT")
    finally:
        connection.close()
    temporary_database.replace(database_path)

    manifest = {
        "schema_version": "replay_store_manifest_v1",
        "source_bundles": str(source),
        "bundle_count": len(bundle_rows),
        "entity_count": len(entity_rows),
        "record_count": len(record_rows),
        "modality_counts": dict(sorted(modality_counts.items())),
        "database": "replay.duckdb",
        "parquet": {
            "bundles": "parquet/bundles.parquet",
            "entities": "parquet/entities.parquet",
            "records": "parquet/records.parquet",
        },
    }
    _write_json(destination / "manifest.json", manifest)
    return manifest


class ReplayStore:
    """Small read-only access layer used by the query engine and tests."""

    def __init__(self, database_path: Path):
        self.database_path = database_path.expanduser().resolve()
        self.connection = duckdb.connect(str(self.database_path), read_only=True)
        self._lock = RLock()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "ReplayStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def has_bundle(self, bundle_id: str) -> bool:
        rows = self._rows(
            "SELECT count(*) AS count FROM bundles WHERE bundle_id = ?", [bundle_id]
        )
        row = rows[0] if rows else None
        return bool(row and row["count"] == 1)

    def counts_for_bundle(self, bundle_id: str) -> dict[str, int]:
        rows = self._rows(
            "SELECT entity_count, record_count FROM bundles WHERE bundle_id = ?",
            [bundle_id],
        )
        bundle = rows[0] if rows else None
        if bundle is None:
            raise KeyError(bundle_id)
        return {
            "entity_count": int(bundle["entity_count"]),
            "record_count": int(bundle["record_count"]),
        }

    def _rows(self, query: str, parameters: list[Any]) -> list[dict[str, Any]]:
        with self._lock:
            cursor = self.connection.execute(query, parameters)
            columns = [item[0] for item in cursor.description]
            return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]

    def bundle_metadata(self, bundle_id: str) -> dict[str, Any]:
        rows = self._rows(
            "SELECT * FROM bundles WHERE bundle_id = ?", [bundle_id]
        )
        if not rows:
            raise KeyError(bundle_id)
        return rows[0]

    def entity_rows(self, bundle_id: str) -> list[dict[str, Any]]:
        return self._rows(
            "SELECT * FROM entities WHERE bundle_id = ? "
            "ORDER BY entity_type, entity_name, entity_id",
            [bundle_id],
        )

    def record_rows(
        self,
        bundle_id: str,
        modality: str,
        *,
        time_start: Any | None = None,
        time_end: Any | None = None,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM records WHERE bundle_id = ? AND modality = ?"
        parameters: list[Any] = [bundle_id, modality]
        if time_start is not None and time_end is not None:
            if modality == "topology":
                query += " AND time_start <= ? AND time_end >= ?"
                parameters.extend([time_end, time_start])
            elif modality == "trace":
                query += " AND time_start >= ? AND time_start <= ?"
                parameters.extend([time_start, time_end])
            else:
                query += " AND observed_at >= ? AND observed_at <= ?"
                parameters.extend([time_start, time_end])
        query += " ORDER BY observed_at, record_ref"
        return self._rows(query, parameters)

    def signal_rows(
        self, bundle_id: str, modalities: list[str]
    ) -> list[dict[str, Any]]:
        if not modalities:
            return []
        placeholders = ", ".join("?" for _ in modalities)
        return self._rows(
            "SELECT DISTINCT modality, signal, unit, entity_ids, stat "
            f"FROM records WHERE bundle_id = ? AND modality IN ({placeholders}) "
            "ORDER BY modality, signal, unit, entity_ids, stat",
            [bundle_id, *modalities],
        )


def export_replay_bundle_database(
    source_database_path: Path,
    bundle_id: str,
    output_database_path: Path,
) -> dict[str, Any]:
    """Copy exactly one bundle into a standalone immutable Replay database."""

    source = source_database_path.expanduser().resolve()
    destination = output_database_path.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise ValueError(f"output database already exists: {destination}")
    temporary = destination.with_name(f".{destination.name}.tmp")
    temporary.unlink(missing_ok=True)
    connection = duckdb.connect(str(temporary))
    try:
        _create_schema(connection)
        connection.execute(
            f"ATTACH '{_sql_path(source)}' AS source_store (READ_ONLY)"
        )
        count = connection.execute(
            "SELECT count(*) FROM source_store.bundles WHERE bundle_id = ?",
            [bundle_id],
        ).fetchone()[0]
        if count != 1:
            raise KeyError(bundle_id)
        for table in ("bundles", "entities", "records"):
            connection.execute(
                f"INSERT INTO {table} SELECT * FROM source_store.{table} "
                "WHERE bundle_id = ?",
                [bundle_id],
            )
        entity_count = connection.execute(
            "SELECT count(*) FROM entities WHERE bundle_id = ?", [bundle_id]
        ).fetchone()[0]
        record_count = connection.execute(
            "SELECT count(*) FROM records WHERE bundle_id = ?", [bundle_id]
        ).fetchone()[0]
        connection.execute("DETACH source_store")
        connection.execute("CHECKPOINT")
    finally:
        connection.close()
    temporary.replace(destination)
    return {
        "schema_version": "standalone_replay_bundle_v1",
        "bundle_id": bundle_id,
        "entity_count": int(entity_count),
        "record_count": int(record_count),
        "database": destination.name,
    }
