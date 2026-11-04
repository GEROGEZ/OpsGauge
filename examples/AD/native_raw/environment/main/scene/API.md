# Native API access

`NATIVE_API_URL` is a read-only HTTP service. Start with `GET /v1/catalog`. Metric values use the proxied Prometheus endpoints `/api/v1/query`, `/api/v1/query_range`, `/api/v1/series`, and metadata endpoints. Resources use `/v1/resources`; Event, Trace, and Log use `/v1/events`, `/v1/traces`, and `/v1/logs`. Topology records use `/v1/topology` and `/scene/topology.jsonl`. Follow `next_offset` until null. Preserve returned `record_ref`, timestamps, units, labels, and entity IDs. PromQL and all non-metric Native HTTP endpoints are one paper access method.
