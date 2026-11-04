# Frozen scene schema

`entities`: bundle_id, entity_id, entity_name, entity_type, entity_domain, attributes.
`records`: record_ref, provenance_ref, modality, entity_ids, signal, observed_at, value_json, unit, stat, dimensions, attributes, source_kind, synthetic. JSON columns may be strings when read through DuckDB; decode them before use. Times are timezone-aware and window boundaries are inclusive unless the question states otherwise.
