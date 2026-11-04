"""Scene-bound query engine for the provider-neutral Replay v1 protocol."""

from __future__ import annotations

from base64 import urlsafe_b64decode, urlsafe_b64encode
from binascii import Error as Base64Error
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from ..domains.operations.observations import (
    canonical_metric_unit,
    metric_aggregation_description,
)
from .query_contracts import (
    AggregateValue,
    EntityRecord,
    EntitySearchRequest,
    EntitySearchResponse,
    EventQueryResponse,
    EventResult,
    LogQueryResponse,
    LogResult,
    MetricPoint,
    MetricQueryResponse,
    MetricResult,
    PageInfo,
    RecordProvenance,
    ReplayQueryRequest,
    SignalCatalogRequest,
    SignalCatalogResponse,
    SignalDescriptor,
    TopologyQueryResponse,
    TopologyResult,
    TraceQueryResponse,
    TraceResult,
)
from .metric_identity import canonical_metric_name
from .record_identity import deduplicate_metric_records
from .storage import ReplayStore


def _public_metric_unit(stat: str, unit: str) -> str:
    """Expose the physical unit; report sampling granularity separately."""

    if metric_aggregation_description(stat, unit):
        return canonical_metric_unit(unit)
    return unit


def _percentile(values: list[float], quantile: float) -> float:
    """Return the deterministic linear-interpolated sample percentile."""

    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _derived_metric_aggregation(
    rows: list[dict[str, Any]], stat: str,
) -> str:
    """Expose a retained or deterministically inferred sampling interval."""

    retained = metric_aggregation_description(stat, rows[-1]["unit"])
    if retained:
        return retained
    timestamps = sorted({row["observed_at"] for row in rows})
    if len(timestamps) < 2:
        return ""
    deltas = [
        (current - previous).total_seconds()
        for previous, current in zip(timestamps, timestamps[1:])
        if current > previous
    ]
    if not deltas or any(delta != deltas[0] for delta in deltas[1:]):
        return ""
    seconds = deltas[0]
    period = str(int(seconds)) if seconds.is_integer() else f"{seconds:g}"
    return f"{period}s raw samples; {stat}"


ReplayToolProfile = Literal["semantic_aggregate", "raw_telemetry"]


def _public_metric_stats(
    modality: str,
    stored_stats: set[str],
    *,
    tool_profile: ReplayToolProfile = "semantic_aggregate",
) -> tuple[str, ...]:
    """Advertise every statistic the query engine can actually produce.

    Snapshot collectors preserve raw metric points as ``identity`` records.
    The query path can deterministically derive window statistics from those
    points, so exposing only ``identity`` makes an agent copy a storage detail
    into its answer instead of requesting the task-level operation (for
    example, ``latest``).
    """

    if modality == "metric" and tool_profile == "raw_telemetry":
        return ("identity", "series") if "identity" in stored_stats else ()
    if modality == "metric" and stored_stats == {"identity"}:
        return (
            "avg", "count", "identity", "latest", "max", "min",
            "p50", "p90", "p95", "p99", "series", "sum",
        )
    return tuple(sorted(stored_stats))


class ReplayQueryError(Exception):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


def _decode_json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def _fingerprint(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(encoded.encode("utf-8")).hexdigest()[:20]


def _encode_cursor(fingerprint: str, offset: int) -> str:
    raw = json.dumps({"fingerprint": fingerprint, "offset": offset}, sort_keys=True)
    return urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str, fingerprint: str) -> int:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(urlsafe_b64decode(padded).decode("utf-8"))
        if payload["fingerprint"] != fingerprint:
            raise ValueError("fingerprint mismatch")
        offset = int(payload["offset"])
        if offset < 0:
            raise ValueError("negative offset")
        return offset
    except (
        Base64Error,
        KeyError,
        TypeError,
        UnicodeDecodeError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        raise ReplayQueryError("CURSOR_INVALID", "cursor does not match this query") from exc


def _page(
    values: list[Any], page_size: int, cursor: str | None, fingerprint: str
) -> tuple[list[Any], PageInfo]:
    offset = _decode_cursor(cursor, fingerprint) if cursor else 0
    page_values = values[offset : offset + page_size]
    next_offset = offset + len(page_values)
    truncated = next_offset < len(values)
    return page_values, PageInfo(
        result_count=len(page_values),
        next_cursor=_encode_cursor(fingerprint, next_offset) if truncated else None,
        truncated=truncated,
    )


def _mapping_contains(actual: dict[str, Any], expected: dict[str, Any]) -> bool:
    return all(actual.get(key) == value for key, value in expected.items())


class ReplayQueryEngine:
    """A query engine permanently bound to one opaque environment and bundle."""

    def __init__(
        self,
        database_path: Path,
        *,
        bundle_id: str,
        environment_id: str,
        tool_profile: ReplayToolProfile = "semantic_aggregate",
        entity_page_size_cap: int | None = None,
        query_page_size_cap: int | None = None,
    ):
        if tool_profile not in {"semantic_aggregate", "raw_telemetry"}:
            raise ValueError(f"unknown replay tool profile: {tool_profile}")
        for name, value in (
            ("entity_page_size_cap", entity_page_size_cap),
            ("query_page_size_cap", query_page_size_cap),
        ):
            if value is not None and value < 1:
                raise ValueError(f"{name} must be positive when configured")
        self.store = ReplayStore(database_path)
        if not self.store.has_bundle(bundle_id):
            self.store.close()
            raise ValueError(f"unknown bundle_id: {bundle_id}")
        self.bundle_id = bundle_id
        self.environment_id = environment_id
        self.tool_profile = tool_profile
        self.entity_page_size_cap = entity_page_size_cap
        self.query_page_size_cap = query_page_size_cap
        self.bundle = self.store.bundle_metadata(bundle_id)

    def close(self) -> None:
        self.store.close()

    def __enter__(self) -> "ReplayQueryEngine":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def capabilities(self) -> dict[str, Any]:
        return {
            "schema_version": "replay_capabilities_v1",
            "api_version": "v1",
            "environment_id": self.environment_id,
            "modalities": _decode_json(self.bundle["modalities"]),
            "time_semantics": "closed_event_time",
            "scene_isolation": True,
            "tool_profile": self.tool_profile,
            "page_limits": {
                "entity_search": self.entity_page_size_cap or 200,
                "query": self.query_page_size_cap or 1000,
            },
        }

    def _validate_context(
        self, environment_id: str, workspace: str, region_id: str
    ) -> None:
        if environment_id != self.environment_id:
            raise ReplayQueryError(
                "ENVIRONMENT_MISMATCH",
                "request environment is not bound to this run",
            )
        if workspace != self.bundle["workspace"] or region_id != self.bundle["region_id"]:
            raise ReplayQueryError(
                "ENVIRONMENT_MISMATCH",
                "workspace or region does not match the bound environment",
            )

    def _entities(self) -> list[dict[str, Any]]:
        rows = self.store.entity_rows(self.bundle_id)
        for row in rows:
            row["attributes"] = _decode_json(row["attributes"])
        return rows

    def search_entities(self, request: EntitySearchRequest) -> EntitySearchResponse:
        self._validate_context(
            request.environment_id, request.workspace, request.region_id
        )
        query = request.query.casefold().strip()
        matches: list[EntityRecord] = []
        for row in self._entities():
            searchable = " ".join(
                str(row[key])
                for key in ("entity_id", "entity_name", "entity_type", "entity_domain")
            ).casefold()
            if query and query not in searchable:
                continue
            if request.entity_types and row["entity_type"] not in request.entity_types:
                continue
            if not _mapping_contains(row["attributes"], request.attributes):
                continue
            matches.append(EntityRecord(
                entity_id=row["entity_id"],
                entity_name=row["entity_name"],
                entity_type=row["entity_type"],
                entity_domain=row["entity_domain"],
                attributes=row["attributes"],
            ))
        payload = request.model_dump(mode="json", exclude={"cursor"})
        page_size = min(
            request.page_size, self.entity_page_size_cap or request.page_size
        )
        page_values, page = _page(
            matches, page_size, request.cursor, _fingerprint(payload)
        )
        return EntitySearchResponse(
            environment_id=self.environment_id,
            results=tuple(page_values),
            page=page,
        )

    def list_signals(self, request: SignalCatalogRequest) -> SignalCatalogResponse:
        """Return queryable signal metadata without exposing observations or GT."""

        self._validate_context(
            request.environment_id, request.workspace, request.region_id
        )
        selected = self._selected_entity_ids(request)
        allowed = {item.value for item in request.modalities}
        modalities = [
            str(item) for item in _decode_json(self.bundle["modalities"])
            if not allowed or str(item) in allowed
        ]
        grouped: dict[tuple[str, str, str, tuple[str, ...]], set[str]] = {}
        for row in self.store.signal_rows(self.bundle_id, modalities):
            row["entity_ids"] = _decode_json(row["entity_ids"])
            modality = str(row["modality"])
            if (
                self.tool_profile == "raw_telemetry"
                and modality == "metric"
                and row["stat"] != "identity"
            ):
                continue
            matched_entity_ids = tuple(
                sorted(selected.intersection(row["entity_ids"]))
            )
            if not matched_entity_ids:
                continue
            # The semantic profile is convenient for small offline replay, but
            # it reveals the exact eligible entities.  The raw profile mirrors
            # a real telemetry catalog: it exposes signal semantics, then makes
            # the agent discover which entities have data through queries.
            entity_ids = (
                () if self.tool_profile == "raw_telemetry"
                else matched_entity_ids
            )
            unit = _public_metric_unit(row["stat"], row["unit"])
            key = (modality, row["signal"], unit, entity_ids)
            grouped.setdefault(key, set()).add(row["stat"])
        results = tuple(
            SignalDescriptor(
                modality=modality,
                signal=signal,
                entity_ids=entity_ids,
                unit=unit,
                available_stats=_public_metric_stats(
                    modality, stats, tool_profile=self.tool_profile
                ),
            )
            for (modality, signal, unit, entity_ids), stats in sorted(grouped.items())
        )
        return SignalCatalogResponse(
            environment_id=self.environment_id,
            results=results,
        )

    def _selected_entity_ids(
        self, request: ReplayQueryRequest | SignalCatalogRequest
    ) -> set[str]:
        selector = request.entity_selector
        result: set[str] = set()
        for row in self._entities():
            if selector.entity_ids and row["entity_id"] not in selector.entity_ids:
                continue
            if selector.entity_types and row["entity_type"] not in selector.entity_types:
                continue
            if selector.entity_names and row["entity_name"] not in selector.entity_names:
                continue
            if not _mapping_contains(row["attributes"], selector.attributes):
                continue
            result.add(row["entity_id"])
        return result

    def _records(
        self, modality: str, request: ReplayQueryRequest | None = None
    ) -> list[dict[str, Any]]:
        rows = self.store.record_rows(
            self.bundle_id,
            modality,
            time_start=request.time_range.start if request else None,
            time_end=request.time_range.end if request else None,
        )
        for row in rows:
            for key in ("entity_ids", "value_json", "dimensions", "attributes"):
                row[key] = _decode_json(row[key])
        return rows

    @staticmethod
    def _in_time(row: dict[str, Any], request: ReplayQueryRequest) -> bool:
        start, end = request.time_range.start, request.time_range.end
        if row["modality"] == "topology":
            return row["time_start"] <= end and row["time_end"] >= start
        if row["modality"] == "trace" and row["attributes"].get("record_kind") != "aggregate":
            return start <= row["time_start"] <= end
        return start <= row["observed_at"] <= end

    @staticmethod
    def _provenance(row: dict[str, Any]) -> RecordProvenance:
        return RecordProvenance(
            source_kind=row["source_kind"],
            provenance_ref=row["provenance_ref"],
            synthetic=row["synthetic"],
        )

    def _base_records(
        self, request: ReplayQueryRequest, modality: str, selected: set[str]
    ) -> list[dict[str, Any]]:
        return [
            row
            for row in self._records(modality, request)
            if selected.intersection(row["entity_ids"]) and self._in_time(row, request)
        ]

    def query(self, request: ReplayQueryRequest) -> BaseModel:
        self._validate_context(
            request.environment_id, request.workspace, request.region_id
        )
        selected = self._selected_entity_ids(request)
        kind = request.query.kind
        if kind == "metric":
            values = self._query_metric(request, selected)
            response_type = MetricQueryResponse
        elif kind == "log":
            values = self._query_log(request, selected)
            response_type = LogQueryResponse
        elif kind == "trace":
            values = self._query_trace(request, selected)
            response_type = TraceQueryResponse
        elif kind == "event":
            values = self._query_event(request, selected)
            response_type = EventQueryResponse
        elif kind == "topology":
            values = self._query_topology(request, selected)
            response_type = TopologyQueryResponse
        else:  # pragma: no cover - protected by the discriminated request schema
            raise ReplayQueryError("QUERY_NOT_SUPPORTED", f"unsupported query: {kind}")
        payload = request.model_dump(mode="json", exclude={"cursor"})
        page_size = min(
            request.page_size, self.query_page_size_cap or request.page_size
        )
        page_values, page = _page(
            values, page_size, request.cursor, _fingerprint(payload)
        )
        return response_type(
            request_id=request.request_id,
            environment_id=self.environment_id,
            time_range=request.time_range,
            page=page,
            results=tuple(page_values),
        )

    def _query_metric(
        self, request: ReplayQueryRequest, selected: set[str]
    ) -> list[MetricResult]:
        query = request.query
        stored_name = canonical_metric_name(query.metric_name)
        candidates = deduplicate_metric_records([
            row for row in self._base_records(request, "metric", selected)
            if row["signal"] == stored_name
            and _mapping_contains(row["dimensions"], query.dimensions)
        ])
        requested_stat = str(query.stat)
        if (
            self.tool_profile == "raw_telemetry"
            and requested_stat not in {"identity", "series"}
        ):
            raise ReplayQueryError(
                "STAT_NOT_AVAILABLE",
                "raw telemetry profile exposes samples, not server-side aggregates",
                {"available_stats": ["identity", "series"]},
            )
        matching = [row for row in candidates if row["stat"] == str(query.stat)]
        if (
            not matching
            and candidates
            and {row["stat"] for row in candidates} == {"identity"}
        ):
            numeric_stats = {
                "avg", "max", "min", "sum", "count", "latest", "series",
                "p50", "p90", "p95", "p99",
            }
            if requested_stat not in numeric_stats:
                raise ReplayQueryError(
                    "STAT_NOT_AVAILABLE",
                    "the requested statistic cannot be derived from raw metric points",
                    {"available_stats": sorted(numeric_stats)},
                )
            grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
            for row in candidates:
                for entity_id in sorted(selected.intersection(row["entity_ids"])):
                    key = (entity_id, json.dumps(row["dimensions"], sort_keys=True))
                    grouped.setdefault(key, []).append(row)
            result: list[MetricResult] = []
            for (entity_id, _), rows in sorted(grouped.items()):
                rows.sort(key=lambda row: (row["observed_at"], row["record_ref"]))
                numeric = [float(row["value_json"]) for row in rows]
                if requested_stat == "avg":
                    value = sum(numeric) / len(numeric)
                elif requested_stat == "max":
                    value = max(numeric)
                elif requested_stat == "min":
                    value = min(numeric)
                elif requested_stat == "sum":
                    value = sum(numeric)
                elif requested_stat == "count":
                    value = len(numeric)
                elif requested_stat == "latest":
                    value = numeric[-1]
                elif requested_stat in {"p50", "p90", "p95", "p99"}:
                    value = _percentile(
                        numeric,
                        {
                            "p50": 0.50, "p90": 0.90,
                            "p95": 0.95, "p99": 0.99,
                        }[requested_stat],
                    )
                else:
                    value = None
                record_ref = rows[-1]["record_ref"]
                if requested_stat != "latest":
                    record_ref = "metric-derived-" + _fingerprint(
                        [
                            request.request_id,
                            entity_id,
                            requested_stat,
                            rows[0]["record_ref"],
                            rows[-1]["record_ref"],
                        ]
                    )
                result.append(
                    MetricResult(
                        record_ref=record_ref,
                        entity_id=entity_id,
                        metric_name=query.metric_name,
                        stat=requested_stat,
                        value=value,
                        unit=_public_metric_unit(requested_stat, rows[-1]["unit"]),
                        aggregation=_derived_metric_aggregation(
                            rows, requested_stat
                        ),
                        dimensions=rows[-1]["dimensions"],
                        points=tuple(
                            MetricPoint(
                                timestamp=row["observed_at"],
                                value=row["value_json"],
                            )
                            for row in rows
                        )
                        if requested_stat == "series"
                        else (),
                        observed_at=rows[-1]["observed_at"],
                        provenance=self._provenance(rows[-1]),
                    )
                )
            return result
        if candidates and not matching:
            raise ReplayQueryError(
                "STAT_NOT_AVAILABLE",
                "the requested statistic was not retained for this evidence",
                {"available_stats": sorted({row["stat"] for row in candidates})},
            )
        grouped_matching: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for row in matching:
            dimensions_key = json.dumps(row["dimensions"], sort_keys=True)
            for entity_id in sorted(selected.intersection(row["entity_ids"])):
                grouped_matching.setdefault((entity_id, dimensions_key), []).append(row)
        result: list[MetricResult] = []
        for (entity_id, _), rows in sorted(grouped_matching.items()):
            # A record may mention several entities when it represents a
            # relationship or causal context.  When the caller selects one
            # entity explicitly, prefer the observation with the narrowest
            # ownership before applying the latest-snapshot rule.  Otherwise
            # a newer cross-entity record can silently shadow the entity's
            # direct metric observation.
            smallest_scope = min(len(set(row["entity_ids"])) for row in rows)
            scoped_rows = [
                row for row in rows
                if len(set(row["entity_ids"])) == smallest_scope
            ]
            latest_observed_at = max(row["observed_at"] for row in scoped_rows)
            latest_rows = sorted(
                (
                    row for row in scoped_rows
                    if row["observed_at"] == latest_observed_at
                ),
                key=lambda item: str(item["record_ref"]),
            )
            for row in latest_rows:
                result.append(MetricResult(
                    record_ref=row["record_ref"],
                    entity_id=entity_id,
                    metric_name=query.metric_name,
                    stat=row["stat"],
                    value=row["value_json"],
                    unit=_public_metric_unit(row["stat"], row["unit"]),
                    aggregation=metric_aggregation_description(
                        row["stat"], row["unit"]
                    ),
                    dimensions=row["dimensions"],
                    observed_at=row["observed_at"],
                    provenance=self._provenance(row),
                ))
        return result

    def _query_log(
        self, request: ReplayQueryRequest, selected: set[str]
    ) -> list[LogResult]:
        query = request.query
        result: list[LogResult] = []
        for row in self._base_records(request, "log", selected):
            haystack = " ".join((
                row["signal"],
                json.dumps(row["value_json"], ensure_ascii=False),
                json.dumps(row["attributes"], ensure_ascii=False),
            )).casefold()
            if query.text and query.text.casefold() not in haystack:
                continue
            if not _mapping_contains(
                {**row["dimensions"], **row["attributes"]}, query.filters
            ):
                continue
            for entity_id in sorted(selected.intersection(row["entity_ids"])):
                if row["attributes"].get("record_kind") == "log":
                    result.append(
                        LogResult(
                            record_ref=row["record_ref"],
                            record_kind="log",
                            record_id=str(
                                row["attributes"].get("record_id")
                                or row["record_ref"]
                            ),
                            timestamp=row["observed_at"],
                            entity_id=entity_id,
                            severity=str(row["attributes"].get("severity") or ""),
                            body=str(
                                row["attributes"].get("body")
                                or row["value_json"]
                            ),
                            attributes={
                                key: value
                                for key, value in row["attributes"].items()
                                if key
                                not in {"record_kind", "record_id", "severity", "body"}
                            },
                            provenance=self._provenance(row),
                        )
                    )
                    continue
                result.append(LogResult(
                    record_ref=row["record_ref"],
                    record_kind="aggregate",
                    timestamp=row["observed_at"],
                    entity_id=entity_id,
                    aggregate=AggregateValue(
                        name=row["signal"], stat=row["stat"],
                        value=row["value_json"], unit=row["unit"],
                        dimensions=row["dimensions"],
                    ),
                    attributes={
                        key: value for key, value in row["attributes"].items()
                        if key != "record_kind"
                    },
                    provenance=self._provenance(row),
                ))
        if query.order == "desc":
            result.reverse()
        return result

    def _query_trace(
        self, request: ReplayQueryRequest, selected: set[str]
    ) -> list[TraceResult]:
        query = request.query
        entity_names = {
            row["entity_id"]: row["entity_name"] for row in self._entities()
        }
        result: list[TraceResult] = []
        for row in self._base_records(request, "trace", selected):
            attrs = row["attributes"]
            if query.trace_id and attrs.get("trace_id") != query.trace_id:
                continue
            if query.operation_name and query.operation_name not in {
                row["signal"], attrs.get("operation_name")
            }:
                continue
            if query.status and attrs.get("status") != query.status:
                continue
            if not _mapping_contains(attrs, query.attributes):
                continue
            for entity_id in sorted(selected.intersection(row["entity_ids"])):
                if query.service_name and query.service_name not in {
                    entity_names.get(entity_id), attrs.get("service_name")
                }:
                    continue
                if attrs.get("record_kind") == "span":
                    result.append(
                        TraceResult(
                            record_ref=row["record_ref"],
                            record_kind="span",
                            trace_id=str(attrs.get("trace_id") or ""),
                            span_id=str(attrs.get("span_id") or ""),
                            parent_span_id=str(attrs.get("parent_span_id") or ""),
                            entity_id=entity_id,
                            service_name=str(
                                attrs.get("service_name")
                                or entity_names.get(entity_id)
                                or ""
                            ),
                            operation_name=str(
                                attrs.get("operation_name") or row["signal"]
                            ),
                            start_time=row["time_start"],
                            end_time=row["time_end"],
                            status=str(attrs.get("status") or ""),
                            attributes={
                                key: value
                                for key, value in attrs.items()
                                if key
                                not in {
                                    "record_kind",
                                    "trace_id",
                                    "span_id",
                                    "parent_span_id",
                                    "service_name",
                                    "operation_name",
                                    "status",
                                }
                            },
                            provenance=self._provenance(row),
                        )
                    )
                    continue
                result.append(TraceResult(
                    record_ref=row["record_ref"],
                    record_kind="aggregate",
                    entity_id=entity_id,
                    start_time=row["time_start"],
                    end_time=row["time_end"],
                    aggregate=AggregateValue(
                        name=row["signal"], stat=row["stat"],
                        value=row["value_json"], unit=row["unit"],
                        dimensions=row["dimensions"],
                    ),
                    attributes={
                        key: value for key, value in attrs.items()
                        if key != "record_kind"
                    },
                    provenance=self._provenance(row),
                ))
        return result

    def _query_event(
        self, request: ReplayQueryRequest, selected: set[str]
    ) -> list[EventResult]:
        query = request.query
        result: list[EventResult] = []
        for row in self._base_records(request, "event", selected):
            attrs = row["attributes"]
            event_type = str(attrs.get("event_type") or row["signal"])
            status = str(attrs.get("status") or row["value_json"])
            if query.event_type and query.event_type != event_type:
                continue
            if query.status and query.status != status:
                continue
            if query.rule_id and query.rule_id != attrs.get("rule_id"):
                continue
            if not _mapping_contains(attrs, query.attributes):
                continue
            result.append(EventResult(
                record_ref=row["record_ref"],
                event_id=str(attrs.get("event_id") or row["record_ref"]),
                event_type=event_type,
                occurred_at=row["observed_at"],
                status=status,
                entity_ids=tuple(sorted(selected.intersection(row["entity_ids"]))),
                attributes=attrs,
                provenance=self._provenance(row),
            ))
        return result

    def _query_topology(
        self, request: ReplayQueryRequest, selected: set[str]
    ) -> list[TopologyResult]:
        query = request.query
        rows = [
            row for row in self._records("topology", request)
            if self._in_time(row, request)
            and (
                not query.relation_types
                or row["attributes"].get("relation_type") in query.relation_types
            )
        ]
        frontier = set(selected)
        visited_nodes = set(selected)
        chosen: dict[str, dict[str, Any]] = {}
        for _ in range(query.max_depth):
            next_frontier: set[str] = set()
            for row in rows:
                source = row["attributes"].get("source_entity_id")
                target = row["attributes"].get("target_entity_id")
                follows = (
                    (query.direction in {"outgoing", "both"} and source in frontier)
                    or (query.direction in {"incoming", "both"} and target in frontier)
                )
                if not follows:
                    continue
                chosen[row["record_ref"]] = row
                if source not in visited_nodes:
                    next_frontier.add(source)
                if target not in visited_nodes:
                    next_frontier.add(target)
            visited_nodes.update(next_frontier)
            frontier = next_frontier
            if not frontier:
                break
        return [
            TopologyResult(
                record_ref=row["record_ref"],
                source_entity_id=row["attributes"]["source_entity_id"],
                target_entity_id=row["attributes"]["target_entity_id"],
                relation_type=row["attributes"]["relation_type"],
                observed_at=row["observed_at"],
                attributes={
                    key: value for key, value in row["attributes"].items()
                    if key not in {"source_entity_id", "target_entity_id", "relation_type"}
                },
                provenance=self._provenance(row),
            )
            for row in sorted(chosen.values(), key=lambda item: item["record_ref"])
        ]
