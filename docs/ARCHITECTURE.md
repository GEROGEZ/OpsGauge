# Repository structure

The command-line package is in `src/opsgauge`. Benchmark artifacts are stored separately in `data`; they are not bundled into the Python wheel.

| Component | Responsibility |
|---|---|
| `cli.py`, `paths.py` | Command dispatch and explicit checkout selection. |
| `data/` | Restore evidence and materialize a selected Harbor task. |
| `evaluation/` | Collect supplied scoring inputs and normalize output scales. |
| `scorers/` | Deterministic implementations of the four task contracts. |
| `agents/` | Codex and Claude Code integrations for Harbor. |
| `verification/` | Index, environment and scoring regression checks. |

The public evaluation path is: restore evidence → select and materialize a task → execute the selected external agent → retain answer/process records → score the supplied outputs. Offline scoring does not contact a model provider or a production monitoring service.

Materialized replay environments contain only their offline dependency closure. Live fault injection, collection clients, and the general construction CLI are outside this release. Case requests, reference answers and measurement records are kept separate from packaging code.

Run `python -m unittest discover -s tests` for command and contract regressions. `opsgauge check-scoring` uses reduced saved fixtures, not complete agent-session archives. Container execution remains a separate integration check on a Linux Docker host.
