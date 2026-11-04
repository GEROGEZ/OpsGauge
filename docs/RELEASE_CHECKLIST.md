# Release verification

A candidate is ready for publication only after both functionality and disclosure checks have been reviewed.

- Install the package and test its public commands from a fresh checkout.
- Verify case/profile coverage, scorer source digests, and all archive/object hashes.
- Re-run the bundled scoring fixtures and compare replay queries after runtime packaging changes.
- Scan source, examples, JSON/JSONL, gzip content, logical database tables, and every distributed image layer; record coverage failures separately from findings.
- Review each credential or internal-endpoint finding. Pattern hits in library documentation and parser strings are not by themselves proof of a usable credential.
- Keep production endpoints, setup logs, model-provider configuration, and unnecessary session records out of examples and fixtures.
- Record approved publication identities and the applicable data/code licenses.
- Publish from a clean reviewed snapshot. Removing a file from the latest tree does not remove it from Git history or earlier LFS objects.

Machine checks establish their stated scope only. A clean pattern scan does not prove the absence of arbitrary encoded secrets or grant permission to publish third-party data. Fresh container loading and execution must be checked independently of image descriptor validation.
