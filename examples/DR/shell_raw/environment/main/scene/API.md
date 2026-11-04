# Shell raw access

The frozen scene is `/scene/replay.duckdb`. Use shell commands and Python with `duckdb` to query the read-only `entities` and `records` tables. `records.modality` distinguishes metric, event, trace, log, and topology. Preserve `record_ref`, timestamps, units, dimensions, and entity IDs. `/scene/SCHEMA.md` documents columns. Do not modify the database.
