# Replay base image

`replay-base.docker.tar.gz` contains the Linux/amd64 replay dependency image used by task Dockerfiles, tagged `opsgauge-replay-base:2026.10.27`. Its image identity is recorded in `configs/custom-base-image-inspection.json` and archive digest in `checksums.sha256`.

```bash
docker load -i data/images/replay-base.docker.tar.gz
```

The image includes DuckDB 1.1.3 for evidence access, separately from offline scoring dependencies. It contains no Codex or Claude Code executable. The image contains only the offline replay dependency closure. Descriptor and layer digests, actual Linux amd64 image loading, runtime imports and the 12 bundled example environment startups have been checked. See `tests/reports/container-startup-check.json`. DR also needs a separately built Claude runtime; the recipe is in `containers/claude-runtime/`.
