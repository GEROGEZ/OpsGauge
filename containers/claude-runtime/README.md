# DR preinstalled Claude runtime

Build from the repository root:

```bash
docker build --platform linux/amd64 \
  -t benchmark-claude-runtime:2.1.309-v1 containers/claude-runtime
docker run --rm --network none benchmark-claude-runtime:2.1.309-v1
```

The existing DR task Dockerfiles copy `/installed-agent/claude-node` from this local tag. Build it before any DR environment, including runs that select Codex. No credentials are needed for this build. It downloads Node.js 22.23.4 with SHA256 verification and installs the public npm package `@anthropic-ai/claude-code@2.1.309` into that directory. Third-party software is obtained by the reader, not bundled in the release archive.

The recipe recreates the required software layout and pinned top-level versions; it is not a byte-identical reconstruction of the historical image. Published replay images target Linux amd64. ARM64 use additionally needs suitable amd64 emulation; it is not part of the tested setup.
