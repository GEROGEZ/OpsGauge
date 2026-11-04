帮我回看 rca-benchmark 工作空间，cms-demo，2026-06-02T18:54:16+08:00—2026-06-02T19:21:46+08:00 内 recommendation，在最后一段副本连续配齐期间，这个时间窗口内新拉起的实例中 CPU 使用率与 CPU 上限占比分别最高的实例是哪些？按各自峰值给出结果、峰值首次出现时间和依据，只算实例运行期间的采样。

## 已知条件

本题给定的比较阶段为 `2026-06-02T18:54:16+08:00`—`2026-06-02T19:21:46+08:00`（北京时间，包含两端采样）。

# 接口

# Native API access

`NATIVE_API_URL` is a read-only HTTP service. Start with `GET /v1/catalog`. Metric values use the proxied Prometheus endpoints `/api/v1/query`, `/api/v1/query_range`, `/api/v1/series`, and metadata endpoints. Entity identities are also in `/scene/entities.jsonl`; topology observations are in `/scene/topology.jsonl`. Event, Trace, and Log use `/v1/events`, `/v1/traces`, and `/v1/logs`; resources use `/v1/resources`. Follow `next_offset` until null. Preserve returned record references, timestamps, units, labels, and entity IDs. PromQL and these Native HTTP endpoints are one Native access method.

# 交付

把答案保存到`/app/answer.json`，使用UTF-8 JSON：
```json
{"selected_period":{"first_met_sample":"<带时区时间>","last_met_sample":"<带时区时间>"},"cpu_usage":{"instance":"","value":0.0,"time":"<带时区时间>"},"cpu_limit_pressure":{"instance":"","value":0.0,"time":"<带时区时间>"}}
```
可选的工作证据格式见`/scene/EVIDENCE_FORMAT.md`。
