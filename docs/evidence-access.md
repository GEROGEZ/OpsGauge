# Evidence access

Each materialized task contains its complete evidence-access environment and interface instructions.

| Profile | Identifier | Agent operations |
|---|---|---|
| File-based | `shell_raw` | Inspect, filter, and combine evidence files with local tools. |
| Native-API | `native_raw` | Select evidence sources and request records with source-specific parameters. |
| Semantic-query | `umodel_semantic` | Discover entities, relations, and observations through a unified query interface. |

Restore environments with `opsgauge materialize`. Implementations can differ across tasks; use the runtime packaged with the selected case. Reading replay evidence requires no live internal observability endpoint. Container base images and model-provider access are separate dependencies.
