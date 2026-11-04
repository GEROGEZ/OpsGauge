"""Provider-neutral query contracts for a scene-isolated replay runtime."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, Field, model_validator

from ..construction.models import NonEmptyText, _ProtocolModel


class ReplayModality(StrEnum):
    METRIC = "metric"
    LOG = "log"
    TRACE = "trace"
    EVENT = "event"
    TOPOLOGY = "topology"


class MetricStatistic(StrEnum):
    AVG = "avg"
    MAX = "max"
    MIN = "min"
    SUM = "sum"
    COUNT = "count"
    LATEST = "latest"
    P50 = "p50"
    P90 = "p90"
    P95 = "p95"
    P99 = "p99"
    TOPK = "topk"
    SERIES = "series"
    IDENTITY = "identity"
    NO_DATA = "no_data"


class ReplayTimeRange(_ProtocolModel):
    """A timezone-aware, closed event-time interval: start <= t <= end."""

    start: AwareDatetime
    end: AwareDatetime
    boundary: Literal["closed"] = "closed"

    @model_validator(mode="after")
    def validate_order(self) -> "ReplayTimeRange":
        if self.end < self.start:
            raise ValueError("time range end must not precede start")
        return self


class EntitySelector(_ProtocolModel):
    entity_ids: tuple[NonEmptyText, ...] = ()
    entity_types: tuple[NonEmptyText, ...] = ()
    entity_names: tuple[NonEmptyText, ...] = ()
    attributes: dict[NonEmptyText, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_selector(self) -> "EntitySelector":
        if not (self.entity_ids or self.entity_types or self.entity_names or self.attributes):
            raise ValueError("at least one entity selector field is required")
        return self


class EntitySearchRequest(_ProtocolModel):
    schema_version: Literal[
        "replay_entity_search_request_v1"
    ] = "replay_entity_search_request_v1"
    environment_id: NonEmptyText
    workspace: NonEmptyText
    region_id: NonEmptyText
    query: str = ""
    entity_types: tuple[NonEmptyText, ...] = ()
    attributes: dict[NonEmptyText, Any] = Field(default_factory=dict)
    page_size: int = Field(default=50, ge=1, le=200)
    cursor: str | None = None

    @model_validator(mode="after")
    def require_search_term(self) -> "EntitySearchRequest":
        if not (self.query.strip() or self.entity_types or self.attributes):
            raise ValueError("entity search requires query, entity_types, or attributes")
        return self


class EntityRecord(_ProtocolModel):
    entity_id: NonEmptyText
    entity_name: NonEmptyText
    entity_type: NonEmptyText
    entity_domain: NonEmptyText
    attributes: dict[str, Any] = Field(default_factory=dict)


class PageInfo(_ProtocolModel):
    result_count: int = Field(ge=0)
    next_cursor: str | None = None
    truncated: bool = False


class EntitySearchResponse(_ProtocolModel):
    schema_version: Literal[
        "replay_entity_search_response_v1"
    ] = "replay_entity_search_response_v1"
    environment_id: NonEmptyText
    results: tuple[EntityRecord, ...]
    page: PageInfo


class SignalCatalogRequest(_ProtocolModel):
    schema_version: Literal[
        "replay_signal_catalog_request_v1"
    ] = "replay_signal_catalog_request_v1"
    environment_id: NonEmptyText
    workspace: NonEmptyText
    region_id: NonEmptyText
    entity_selector: EntitySelector
    modalities: tuple[ReplayModality, ...] = ()


class SignalDescriptor(_ProtocolModel):
    modality: ReplayModality
    signal: NonEmptyText
    # Some access profiles expose only schema-level signal metadata.  An empty
    # tuple means "this signal exists in the selected scope" without leaking
    # which entities actually have samples.
    entity_ids: tuple[NonEmptyText, ...] = ()
    unit: str = ""
    available_stats: tuple[MetricStatistic, ...] = ()


class SignalCatalogResponse(_ProtocolModel):
    schema_version: Literal[
        "replay_signal_catalog_response_v1"
    ] = "replay_signal_catalog_response_v1"
    environment_id: NonEmptyText
    results: tuple[SignalDescriptor, ...]


class MetricQuery(_ProtocolModel):
    kind: Literal["metric"] = "metric"
    metric_name: NonEmptyText
    stat: MetricStatistic
    dimensions: dict[NonEmptyText, Any] = Field(default_factory=dict)
    group_by: tuple[NonEmptyText, ...] = ()
    top_k: int | None = Field(default=None, ge=1, le=100)

    @model_validator(mode="after")
    def validate_topk(self) -> "MetricQuery":
        if (self.stat is MetricStatistic.TOPK) != (self.top_k is not None):
            raise ValueError("top_k is required exactly when stat is topk")
        return self


class LogQuery(_ProtocolModel):
    kind: Literal["log"] = "log"
    text: str = ""
    filters: dict[NonEmptyText, Any] = Field(default_factory=dict)
    order: Literal["asc", "desc"] = "asc"


class TraceQuery(_ProtocolModel):
    kind: Literal["trace"] = "trace"
    trace_id: str = ""
    service_name: str = ""
    operation_name: str = ""
    status: str = ""
    attributes: dict[NonEmptyText, Any] = Field(default_factory=dict)


class EventQuery(_ProtocolModel):
    kind: Literal["event"] = "event"
    event_type: str = ""
    status: str = ""
    rule_id: str = ""
    attributes: dict[NonEmptyText, Any] = Field(default_factory=dict)


class TopologyQuery(_ProtocolModel):
    kind: Literal["topology"] = "topology"
    direction: Literal["outgoing", "incoming", "both"] = "both"
    relation_types: tuple[NonEmptyText, ...] = ()
    max_depth: int = Field(default=1, ge=1, le=3)


QuerySpec = Annotated[
    MetricQuery | LogQuery | TraceQuery | EventQuery | TopologyQuery,
    Field(discriminator="kind"),
]


class ReplayQueryRequest(_ProtocolModel):
    schema_version: Literal[
        "replay_query_request_v1"
    ] = "replay_query_request_v1"
    request_id: NonEmptyText
    environment_id: NonEmptyText
    workspace: NonEmptyText
    region_id: NonEmptyText
    time_range: ReplayTimeRange
    entity_selector: EntitySelector
    query: QuerySpec
    page_size: int = Field(default=100, ge=1, le=1000)
    cursor: str | None = None


class RecordProvenance(_ProtocolModel):
    source_kind: Literal[
        "raw_telemetry",
        "measured_evidence",
        "verified_projection",
        "approved_assertion",
    ]
    provenance_ref: NonEmptyText
    synthetic: bool


class MetricPoint(_ProtocolModel):
    timestamp: AwareDatetime
    value: int | float


class MetricResult(_ProtocolModel):
    record_ref: NonEmptyText
    entity_id: NonEmptyText
    metric_name: NonEmptyText
    stat: MetricStatistic
    value: int | float | str | None
    unit: str = ""
    aggregation: str = ""
    dimensions: dict[str, Any] = Field(default_factory=dict)
    points: tuple[MetricPoint, ...] = ()
    observed_at: AwareDatetime | None = None
    provenance: RecordProvenance


class AggregateValue(_ProtocolModel):
    """A measured summary when the historical source did not retain raw rows."""

    name: NonEmptyText
    stat: MetricStatistic
    value: int | float | str | None
    unit: str = ""
    dimensions: dict[str, Any] = Field(default_factory=dict)


class LogResult(_ProtocolModel):
    record_ref: NonEmptyText
    record_kind: Literal["log", "aggregate"] = "log"
    record_id: str = ""
    timestamp: AwareDatetime
    entity_id: NonEmptyText
    severity: str = ""
    body: str = ""
    aggregate: AggregateValue | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    provenance: RecordProvenance

    @model_validator(mode="after")
    def validate_record_shape(self) -> "LogResult":
        if self.record_kind == "log" and not (self.record_id and self.body):
            raise ValueError("raw log result requires record_id and body")
        if self.record_kind == "aggregate" and self.aggregate is None:
            raise ValueError("aggregate log result requires aggregate")
        if self.record_kind == "log" and self.aggregate is not None:
            raise ValueError("raw log result must not contain aggregate")
        return self


class TraceResult(_ProtocolModel):
    record_ref: NonEmptyText
    record_kind: Literal["span", "aggregate"] = "span"
    trace_id: str = ""
    span_id: str = ""
    parent_span_id: str = ""
    entity_id: NonEmptyText
    service_name: str = ""
    operation_name: str = ""
    start_time: AwareDatetime
    end_time: AwareDatetime
    status: str = ""
    aggregate: AggregateValue | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    provenance: RecordProvenance

    @model_validator(mode="after")
    def validate_span_time(self) -> "TraceResult":
        if self.end_time < self.start_time:
            raise ValueError("span end_time must not precede start_time")
        if self.record_kind == "span" and not (
            self.trace_id and self.span_id and self.service_name and self.operation_name
        ):
            raise ValueError(
                "raw span result requires trace_id, span_id, service_name, and operation_name"
            )
        if self.record_kind == "aggregate" and self.aggregate is None:
            raise ValueError("aggregate trace result requires aggregate")
        if self.record_kind == "span" and self.aggregate is not None:
            raise ValueError("raw span result must not contain aggregate")
        return self


class EventResult(_ProtocolModel):
    record_ref: NonEmptyText
    event_id: NonEmptyText
    event_type: NonEmptyText
    occurred_at: AwareDatetime
    status: str = ""
    entity_ids: tuple[NonEmptyText, ...] = Field(min_length=1)
    attributes: dict[str, Any] = Field(default_factory=dict)
    provenance: RecordProvenance


class TopologyResult(_ProtocolModel):
    record_ref: NonEmptyText
    source_entity_id: NonEmptyText
    target_entity_id: NonEmptyText
    relation_type: NonEmptyText
    observed_at: AwareDatetime | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    provenance: RecordProvenance


class _QueryResponseBase(_ProtocolModel):
    request_id: NonEmptyText
    environment_id: NonEmptyText
    time_range: ReplayTimeRange
    page: PageInfo


class MetricQueryResponse(_QueryResponseBase):
    schema_version: Literal[
        "replay_metric_query_response_v1"
    ] = "replay_metric_query_response_v1"
    modality: Literal["metric"] = "metric"
    results: tuple[MetricResult, ...]


class LogQueryResponse(_QueryResponseBase):
    schema_version: Literal[
        "replay_log_query_response_v1"
    ] = "replay_log_query_response_v1"
    modality: Literal["log"] = "log"
    results: tuple[LogResult, ...]


class TraceQueryResponse(_QueryResponseBase):
    schema_version: Literal[
        "replay_trace_query_response_v1"
    ] = "replay_trace_query_response_v1"
    modality: Literal["trace"] = "trace"
    results: tuple[TraceResult, ...]


class EventQueryResponse(_QueryResponseBase):
    schema_version: Literal[
        "replay_event_query_response_v1"
    ] = "replay_event_query_response_v1"
    modality: Literal["event"] = "event"
    results: tuple[EventResult, ...]


class TopologyQueryResponse(_QueryResponseBase):
    schema_version: Literal[
        "replay_topology_query_response_v1"
    ] = "replay_topology_query_response_v1"
    modality: Literal["topology"] = "topology"
    results: tuple[TopologyResult, ...]


ReplayQueryResponse = Annotated[
    MetricQueryResponse
    | LogQueryResponse
    | TraceQueryResponse
    | EventQueryResponse
    | TopologyQueryResponse,
    Field(discriminator="modality"),
]


class ReplayError(_ProtocolModel):
    code: Literal[
        "INVALID_REQUEST",
        "ENVIRONMENT_NOT_FOUND",
        "ENVIRONMENT_MISMATCH",
        "AMBIGUOUS_ENTITY",
        "QUERY_NOT_SUPPORTED",
        "STAT_NOT_AVAILABLE",
        "CURSOR_INVALID",
    ]
    message: NonEmptyText
    details: dict[str, Any] = Field(default_factory=dict)


class ReplayErrorResponse(_ProtocolModel):
    schema_version: Literal[
        "replay_error_response_v1"
    ] = "replay_error_response_v1"
    request_id: str = ""
    error: ReplayError


def event_in_closed_range(value: datetime, time_range: ReplayTimeRange) -> bool:
    """Reference predicate used by gateway implementations and conformance tests."""

    return time_range.start <= value <= time_range.end
