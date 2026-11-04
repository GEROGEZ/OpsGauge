# MISSION raw scene task

请对 pc-j6ca6v49n1t62ft27 执行一次PolarDB专项巡检。时间范围：2026-08-03T22:33:46+08:00 至 2026-08-03T22:38:30+08:00；workspace：rca-benchmark；region：cn-hongkong。请先界定PolarDB范围内需要检查的实体，再自主规划检查项，从可用观测中区分有效异常证据与无关信号，完成覆盖性检查后再汇总结论。识别范围内发生的全部异常，并给出异常实体、时间、判断和关键证据。不要求完成跨域根因分析。

The scene is accessible only through the read-only native replay API at
`$NATIVE_API_URL` (http://native-api:8080). This is a benchmark replay interface,
not a live cluster or a Kubernetes-compatible API. Start with GET /v1/catalog.

Use curl -sG "$NATIVE_API_URL/v1/resources" --data-urlencode 'q=checkout'.
Endpoints: /v1/resources, /v1/events, /v1/logs, /v1/traces, /v1/topology.
Filters: entity_id (exact), start/end (inclusive observed_at, RFC3339 with timezone),
signal (exact), trace_id (exact), record_ref (exact), q (case-insensitive text),
limit (1..200, default 50), offset (default 0). Responses have items, total and
next_offset. Follow next_offset until null; a first page is not all evidence.
The API returns source records and does not infer anomalies, causal relationships,
or entity aliases. Apply the task scope/window and assemble evidence yourself.

Metric values are available ONLY via real PromQL at $PROMETHEUS_URL/api/v1/query
and /api/v1/query_range. Discover series through GET /v1/metrics/index with the
same filters; this value-free index provides prometheus_metric_name,
prometheus_labels, record_ref, observed_at, original stat, unit and provenance.
Use curl -sG "$PROMETHEUS_URL/api/v1/query" --data-urlencode 'query=METRIC{entity_id="ID"}'
--data-urlencode 'time=ORIGINAL_OBSERVED_AT'. Source dimensions use collision-free dim_ labels; dimension_label_map in the index maps original dimension names to these Prometheus labels. Use the returned metric_unit and
source_stat labels. Metrics with __recorded_STAT suffix are retained recorded
aggregates, NOT reconstructed raw samples. Do not treat avg/latest as counters.
Prometheus uses evaluation timestamps and lookback; inspect timestamp(METRIC)
or query at the indexed original sample time to retain source timing.
Match metric name, labels and source time to the index when citing record_ref.
Never infer evidence IDs from their contents. Non-metric APIs return record_ref
directly. No raw scene files are mounted in the agent container.

Public answer-schema.json and task contracts remain under /scene. Do not read
/tests; those are verifier-only. Write your answer to /logs/agent/answer.json.

Non-finite source fields are returned as string tokens with source_value_encoding listing exact paths and original tokens. These are source anomalies, not valid span/trace identifiers. No identifier is reconstructed. For submillisecond samples use prometheus_sample_time from the index for PromQL; original observed_at is preserved in the index and source_observed_at label.

For Mission, read `/scene/mission-contract.json`, use only its canonical phenomena, and keep findings inside the target scope.

## Strict output contract

Read `/scene/answer-schema.json` before writing the answer. The evaluator reads
only `/logs/agent/answer.json`. Write one UTF-8 JSON object that conforms to
that schema. Do not wrap the object in Markdown and do not read `/tests`.

For every evidence item, copy `record_ref` values returned by successful scene
queries into `record_refs`. Findings and causal claims must reference those
evidence items through `evidence_ids`; unsupported narrative claims do not
receive evidence-grounding credit.

`record_ref` values are opaque server-assigned identifiers: copy them verbatim
and never attempt to decode, reverse-engineer, or validate their internal
structure. This task has a hard wall-clock limit: write a complete draft of
`/logs/agent/answer.json` as soon as you have defensible results — partial
results are better than none — and refine the file in place as evidence
accumulates. An answer file that is never written scores zero, so when time
runs short, stop exploring and finalize the file immediately.

For retained observations, keep the returned statistic, unit, observation time, and aggregation window. The public Mission contract defines the inspection checks and operational predicates; retrieve each observation before evaluating a predicate. Thresholds are not observed values. Grounded normal/context observations belong in evidence, not in anomaly findings.

For retained observations, keep the returned statistic, unit, observation time, and aggregation window. The public Mission contract defines the inspection checks and operational predicates; retrieve each observation before evaluating a predicate. Thresholds are not observed values. Grounded normal/context observations belong in evidence, not in anomaly findings.

For retained observations, keep the returned statistic, unit, observation time, and aggregation window. The public Mission contract defines the inspection checks and operational predicates; retrieve each observation before evaluating a predicate. Thresholds are not observed values. Grounded normal/context observations belong in evidence, not in anomaly findings.

For retained observations, keep the returned statistic, unit, observation time, and aggregation window. The public Mission contract defines the inspection checks and operational predicates; retrieve each observation before evaluating a predicate. Thresholds are not observed values. Grounded normal/context observations belong in evidence, not in anomaly findings.

For retained observations, keep the returned statistic, unit, observation time, and aggregation window. The public Mission contract defines the inspection checks and operational predicates; retrieve each observation before evaluating a predicate. Thresholds are not observed values. Grounded normal/context observations belong in evidence, not in anomaly findings.

For retained observations, keep the returned statistic, unit, observation time, and aggregation window. The public Mission contract defines the inspection checks and operational predicates; retrieve each observation before evaluating a predicate. Thresholds are not observed values. Grounded normal/context observations belong in evidence, not in anomaly findings.

For retained observations, keep the returned statistic, unit, observation time, and aggregation window. The public Mission contract defines the inspection checks and operational predicates; retrieve each observation before evaluating a predicate. Thresholds are not observed values. Grounded normal/context observations belong in evidence, not in anomaly findings.

For retained observations, keep the returned statistic, unit, observation time, and aggregation window. The public Mission contract defines the inspection checks and operational predicates; retrieve each observation before evaluating a predicate. Thresholds are not observed values. Grounded normal/context observations belong in evidence, not in anomaly findings.

For retained observations, keep the returned statistic, unit, observation time, and aggregation window. The public Mission contract defines the inspection checks and operational predicates; retrieve each observation before evaluating a predicate. Thresholds are not observed values. Grounded normal/context observations belong in evidence, not in anomaly findings.
