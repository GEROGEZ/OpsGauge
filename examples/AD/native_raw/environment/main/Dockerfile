FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends curl jq ca-certificates && rm -rf /var/lib/apt/lists/*
COPY scene /scene
WORKDIR /app
CMD ["sleep","infinity"]
