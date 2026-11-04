import os
from pathlib import Path
import uvicorn
from bench_factory.replay.gateway import create_gateway_app

app = create_gateway_app(
    Path("/scene/replay.duckdb"),
    bundle_id=os.environ["BUNDLE_ID"],
    environment_id=os.environ["ENVIRONMENT_ID"],
    trace_path=Path("/shared/umodel-trace.jsonl"),
    tool_profile="semantic_aggregate",
    entity_page_size_cap=None,
    query_page_size_cap=None,
)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)
