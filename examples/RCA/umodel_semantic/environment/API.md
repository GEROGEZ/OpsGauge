# UModel semantic query API

Base URL: `$UMODEL_URL`

1. `GET /v1/capabilities`
2. `POST /v1/entities/search`
3. `POST /v1/signals`
4. `POST /v1/query`

Every POST is JSON. Copy the values of `UMODEL_ENVIRONMENT_ID`,
`UMODEL_WORKSPACE`, and `UMODEL_REGION_ID` into the corresponding request
fields. All timestamps must include a timezone offset, and query windows are
closed: `start <= event_time <= end`.

List or search entities by type without guessing names:
```json
{"environment_id":"...","workspace":"...","region_id":"...","entity_types":["k8s.pod"],"page_size":200}
```
Follow `page.next_cursor` only when it is non-null.

Discover which metric signals and statistics exist for a scope:
```json
{"environment_id":"...","workspace":"...","region_id":"...","entity_selector":{"entity_types":["k8s.pod"]},"modalities":["metric"]}
```

Query one statistic for every matching entity:
```json
{
  "request_id":"unique-request-id",
  "environment_id":"...",
  "workspace":"...",
  "region_id":"...",
  "time_range":{"start":"2026-01-01T00:00:00+08:00","end":"2026-01-01T00:05:00+08:00"},
  "entity_selector":{"entity_types":["k8s.pod"]},
  "query":{"kind":"metric","metric_name":"metric_name_from_signal_catalog","stat":"avg"},
  "page_size":1000
}
```
Run a separate query for each requested statistic. A response includes
`results`, `page.result_count`, `page.truncated`, and `page.next_cursor`.
Preserve each result's `entity_id`, `metric_name`, `stat`, `value`, `unit`,
`dimensions`, and `aggregation` in the answer.

Retrieve a complete raw series by using the same metric request with
`"stat":"series"`. Align multiple returned series by their point timestamps
before computing a derived value.

Traverse topology from a known entity in either direction:
```json
{
  "request_id":"unique-topology-request-id",
  "environment_id":"...",
  "workspace":"...",
  "region_id":"...",
  "time_range":{"start":"2026-01-01T00:00:00+08:00","end":"2026-01-01T00:05:00+08:00"},
  "entity_selector":{"entity_ids":["opaque-start-entity-id"]},
  "query":{"kind":"topology","direction":"both","max_depth":2},
  "page_size":1000
}
```
Resolve every returned opaque endpoint through entity search before choosing
an entity by type. Never replace an opaque `entity_id` with a display name.

Query logs, traces, or events by changing `query.kind` and using the fields in
the corresponding example below. Every returned observation includes a
`record_ref`; preserve it in answer evidence when the answer schema has evidence
fields.

Trace spans are attached to existing service entities. Discover or select
`apm.service` entities; do not invent entity types such as `trace` or
`trace.span`. When the task gives a trace ID, put it in `query.trace_id` (not in
`query.attributes`). To retrieve the complete trace, make the entity selector
cover all relevant service entities and follow every response cursor.
```json
{"query":{"kind":"log","text":"","filters":{},"order":"asc"}}
{"entity_selector":{"entity_types":["apm.service"]},"query":{"kind":"trace","trace_id":"trace-id-from-question","service_name":"","operation_name":"","status":"","attributes":{}}}
{"query":{"kind":"event","event_type":"","status":"","rule_id":"","attributes":{}}}
```
Each object above is the relevant part of the same `/v1/query` request envelope
shown in the metric example; merge the shown `entity_selector` and `query`
fields into that envelope.
