# Reproducibility

## Data and scores

`data/tasks.jsonl` maps 3,861 task/profile packages to 1,287 cases. The file index and object store bind every task file to its content digest. `results/selected-results.json` and CSV contain 15,444 final case scores; aggregation and plotting use the included analysis inputs.

Paper L1 contains the most investigative context. AD source levels run in the reverse direction (IL3 → L1, IL2 → L2, IL1 → L3); indexes contain the explicit mapping.

## Scoring inputs

Deterministic scorers assess submitted answers and task-specific process evidence. Native metric citations resolve through series/timestamp bindings; query-derived references require actual query records. Operation/service identities are not automatically equated. Grounding measures explicit record/entity consistency, not unrestricted semantic entailment.

Recorded URL hosts are consistently aliased in distributed evidence and task materials. Historical score files are unchanged; agents were not rerun after this substitution. Database row comparisons, bundled scoring fixtures, and representative query-response comparisons check the packaging transformation.

Recomputing aggregate results uses supplied scores. Scoring a new submission requires its own answer and process/query records; reproducing an individual reported execution also requires the exact artifacts selected for that execution. Complete agent trial archives are not included.

## Execution settings

Use `configs/dr-ad-runtime-settings.json`, `configs/ha-rca-audited-runtime-settings.json`, and the materialized task definitions. Harness versions and budgets vary across the recorded task/profile groups; do not infer one global setting from the most recent installed executable. Runner-host dependencies and replay-image dependencies are separate.

## Verification scope

Archive/object hashes, case/profile coverage, environment build contexts, source-code bindings, and five supplied scoring fixtures have been checked. The packaging workstation has no Docker. Separate Linux startup checks are reported in `tests/reports/container-startup-check.json` when available; they do not constitute a new agent evaluation. Container execution requires a compatible Linux host, base images, harnesses, and model-provider access.
