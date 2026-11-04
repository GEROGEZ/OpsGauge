# Scoring

Use `opsgauge score` to evaluate a submission. All tasks use deterministic rules, without LLM judging. The wrapper returns `reward` on 0–1 and `score` on 0–100, with task-specific components in `scorer_output`.

| Task | Inputs | Assessment |
|---|---|---|
| DR | `--trial` | Answer correctness (60%) and process evidence (40%). |
| AD | `--answer`, optionally `--progress`; or `--trial` | Claim correctness (60%) and process checks (40%). |
| HA | `--answer` with `--query-trace`, or explicit `--source-only` | Equal weights for finding recall, observation recall, scope compliance, and grounding. |
| RCA | `--answer` with `--query-trace`, or explicit `--source-only` | Equal weights for root-entity F1, entity-matched fault-type F1, ordered-step recall, evidence grounding, and claim coverage. |

An unstarted or unscorable AD execution has `scored: false` and null reward/score; it is not converted to zero.

DR requires saved execution artifacts for process credit. AD without progress receives only credit established by the supplied inputs. HA/RCA query traces must contain verifier-compatible JSONL events; an arbitrary agent transcript is not equivalent. `--source-only` omits query-derived evidence and can change the score.

Grounding checks record resolution and entity associations. Related operation and service entities are not interchangeable. Fault-type credit requires a matching root entity; RCA steps must also match type and order.

Implementations and dependencies are in `src/opsgauge/scorers/`; per-case AD scorer files are restored with each task. Source digests are in `data/provenance/frozen-code-bindings.json`. The wrapper normalizes output presentation without changing task reward rules. See [usage](USAGE.md) for commands.
