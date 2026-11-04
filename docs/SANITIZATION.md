# Data handling

Distributed evidence replaces the identified ARMS LicenseKey value with the invalid placeholder `OPSGAUGE_REDACTED_INVALID` and normalizes personal workstation prefixes to `/workspace`. The latter is a provenance placeholder, not an installation requirement.

Recorded internal HTTP(S) URL hosts use 38 one-to-one, random opaque aliases under `example.invalid`. The same source host has the same alias across evidence files, task materials, references, and access profiles. Scheme, port, path, query, and fragment are preserved; local replay interface addresses are excluded. These aliases are evidence labels, not externally reachable services. The original host-to-alias mapping is not distributed. The alias list is in `data/provenance/url-host-aliases.json`.

Task requirements, timestamps, entity/record identifiers, topology, and score definitions are preserved. URL text in observations and accompanying materials changes consistently under the above policy. Other deployment identifiers, business names, and injection-tool references remain; this is not a fully anonymous dataset.

Modified SQLite and DuckDB files are rebuilt, with complete-row equivalence checked against the specified substitutions. Object hashes and file-index bindings identify the distributed bytes. Scoring fixtures and representative replay queries are checked separately; these checks do not constitute a fresh agent evaluation. Recorded numerical results are retained unchanged. See [reproducibility](REPRODUCIBILITY.md) for verification boundaries.
