FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends jq gzip && rm -rf /var/lib/apt/lists/*
COPY instruction.md evidence.jsonl.gz claim-prompts.json PROTOCOL.md answer-schema.json DATA.md DISCLOSURE.json /scene/
WORKDIR /app/work
CMD ["sleep","infinity"]
