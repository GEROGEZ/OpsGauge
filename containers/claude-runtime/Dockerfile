FROM debian:bookworm-slim
ARG NODE_VERSION=22.23.4
ARG CLAUDE_CODE_VERSION=2.1.309
ARG TARGETARCH
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl tar gzip libstdc++6 \
    && rm -rf /var/lib/apt/lists/*
ENV PATH="/installed-agent/claude-node/bin:${PATH}"
RUN set -eu; \
    case "${TARGETARCH:-$(dpkg --print-architecture)}" in \
      amd64) node_arch=x64 ;; arm64) node_arch=arm64 ;; \
      *) echo "Supported architectures: amd64, arm64" >&2; exit 1 ;; \
    esac; \
    archive="node-v${NODE_VERSION}-linux-${node_arch}.tar.gz"; \
    mkdir -p /installed-agent/claude-node /tmp/node-install; \
    cd /tmp/node-install; \
    curl -fsSLO "https://nodejs.org/dist/v${NODE_VERSION}/${archive}"; \
    curl -fsSLO "https://nodejs.org/dist/v${NODE_VERSION}/SHASUMS256.txt"; \
    grep " ${archive}$" SHASUMS256.txt | sha256sum -c -; \
    tar -xzf "${archive}" -C /installed-agent/claude-node --strip-components=1; \
    npm install --global --prefix /installed-agent/claude-node \
      "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}"; \
    test "$(node --version)" = "v${NODE_VERSION}"; \
    claude --version | grep -F "${CLAUDE_CODE_VERSION}"; \
    npm cache clean --force; \
    rm -rf /tmp/node-install /root/.npm
CMD ["claude", "--version"]
