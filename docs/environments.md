# Environments

All 3,861 task environments are indexed in `data/tasks.jsonl` and `data/file-index.jsonl.gz`. Restore a task with `opsgauge materialize`; its `environment/` directory contains the build context. `examples/` provides one case per task under each profile.

Use Linux Docker and the task's interface instructions. Evaluator references and tests must remain outside agent-visible images and mounts. See [usage](USAGE.md) for image loading and scoring.
