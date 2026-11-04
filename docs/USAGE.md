# Running and scoring cases

## Select a case

Commands run from the repository root. Restore data, then list and materialize tasks:

```bash
opsgauge list --task AD --profile native_raw
opsgauge materialize \
  --task-id AD--F035--L3--native_raw --output materialized/ad-native
```

Access identifiers are `shell_raw` (File-based), `native_raw` (Native-API), and `umodel_semantic` (Semantic-query). Listed task IDs use paper information levels, with L1 supplying the most information. Source IDs inside task files are retained; `data/tasks.jsonl` records their mapping.

Materialized files are read-only and may share storage with the object store. Use `--writable` for editable copies. Four example cases, each under three profiles, are available in `examples/`.

## Execute an agent

Use Linux Docker and the Harbor integration in [runners](../docs/agents.md). Materialized tasks contain environment definitions and interface instructions. Configure model credentials locally; `.env.example` documents variable names.

For tasks using the supplied replay image, load it first:

```bash
docker load -i data/images/replay-base.docker.tar.gz
```

The DR example Dockerfiles also copy a preinstalled Claude runtime from the local image `benchmark-claude-runtime:2.1.309-v1`. Build it using `docker build --platform linux/amd64 -t benchmark-claude-runtime:2.1.309-v1 containers/claude-runtime` from the repository root. The supplied recipe obtains the fixed Node.js and Claude Code versions from public sources; see `containers/claude-runtime/README.md` and the full setup in the root README.

Keep evaluator references, contracts, and tests outside agent-visible images and mounts. Save trial results, submitted artifacts, and tool/query records for scoring.

Use `opsgauge score` as the release scoring entry point after execution, retaining the required process and query inputs. Do not substitute a task's raw runtime output for the release score without checking the scorer contract.

## Score outputs

Replace task and path placeholders with actual values:

```bash
# DR: saved Harbor trial, including result.json and process artifacts.
opsgauge score --task-id '<DR-task-id>' \
  --trial /path/to/trial --output /path/to/dr-score.json

# AD: answer and structured process output.
opsgauge score --task-id AD--F035--L3--native_raw \
  --answer /path/to/answer.json --progress /path/to/progress.json \
  --output /path/to/ad-score.json

# HA/RCA: answer and verifier-compatible query events.
opsgauge score --task-id '<HA-or-RCA-task-id>' \
  --answer /path/to/answer.json --query-trace /path/to/query-events.jsonl \
  --output /path/to/score.json
```

Use `--task-dir` for an already materialized task; otherwise it is restored temporarily. AD also accepts `--trial` for collecting saved execution artifacts. HA/RCA `--source-only` explicitly omits query-derived evidence; it does not reproduce scoring with a complete query trace.

Outputs must be new files. Top-level `reward` uses 0–1 and `score` uses 0–100; `scorer_output` retains component definitions and native scales.

## Offline checks

```bash
opsgauge verify --metadata-only --output tests/reports/index-check.json
opsgauge verify --full-hash --output tests/reports/local-check.json
opsgauge check-environments
opsgauge check-scoring --output tests/reports/local-scoring-check.json
```

Metadata checks need no restored evidence. Full data and environment checks do. Scoring checks use five bundled submissions, without model calls. These checks do not execute Docker or evaluate a new agent.
