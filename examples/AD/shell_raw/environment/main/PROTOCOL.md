# Information-level evidence protocol v2

Read `/scene/evidence.jsonl.gz` and `/scene/claim-prompts.json`. Publish interim work to `/logs/agent/progress.json` with `state: active`, then the final answer to `/logs/agent/answer.json` with `state: final`.

For each public claim ID, return `status`, valid `evidence_refs`, and `value` when the prompt asks for a quantity. Do not turn request rows, retries, alerts or reports into unique business objects without an identity witness. Missing or unverified results must be `not_established`; do not guess. No scoring feedback is available during execution.
