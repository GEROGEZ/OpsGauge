FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends curl jq ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN apt-get update && apt-get install -y --no-install-recommends curl ripgrep ca-certificates coreutils bash procps tar gzip && rm -rf /var/lib/apt/lists/*
COPY --from=benchmark-claude-runtime:2.1.309-v1 /installed-agent/claude-node /installed-agent/claude-node
RUN ln -sf /installed-agent/claude-node/bin/node /usr/local/bin/node && ln -sf /installed-agent/claude-node/bin/claude /usr/local/bin/claude
COPY scene /scene
WORKDIR /app
CMD ["sleep", "infinity"]
