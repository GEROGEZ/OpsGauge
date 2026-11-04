FROM opsgauge-replay-base:2026.10.27
COPY replay.duckdb /scene/replay.duckdb
COPY native_replay_server.py /app/native_replay_server.py
CMD ["python","/app/native_replay_server.py"]
