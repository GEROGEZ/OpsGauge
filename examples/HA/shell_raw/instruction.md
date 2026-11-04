# MISSION raw scene task

请对 pc-j6ca6v49n1t62ft27 执行一次PolarDB专项巡检。时间范围：2026-08-03T22:33:46+08:00 至 2026-08-03T22:38:30+08:00；workspace：rca-benchmark；region：cn-hongkong。请先界定PolarDB范围内需要检查的实体，再自主规划检查项，从可用观测中区分有效异常证据与无关信号，完成覆盖性检查后再汇总结论。识别范围内发生的全部异常，并给出异常实体、时间、判断和关键证据。不要求完成跨域根因分析。

The scene is mounted at `/scene`. Start with `/scene/manifest.json`, discover
entities in `/scene/entities.jsonl`, and inspect raw observations under
`/scene/telemetry/*.jsonl` using Bash, jq, or Python. Each observation exposes
its exact `record_ref`; cite those identifiers in evidence-bearing answers. The
files contain scene distractors, including observations outside the requested
query window, and do not identify which entities answer the question. Apply the
question's time window and perform all selection, alignment, judgement, and
causal assembly yourself. Do not read `/tests`; it is verifier-only. Write the
required JSON to `/logs/agent/answer.json`.
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
