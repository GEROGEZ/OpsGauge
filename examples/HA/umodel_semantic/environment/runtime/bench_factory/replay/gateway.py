"""FastAPI gateway exposing one scene-bound ReplayQueryEngine."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
from threading import Lock
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .query_contracts import (
    EntitySearchRequest,
    EntitySearchResponse,
    ReplayError,
    ReplayErrorResponse,
    ReplayQueryRequest,
    SignalCatalogRequest,
    SignalCatalogResponse,
)
from .query_engine import ReplayQueryEngine, ReplayQueryError, ReplayToolProfile


class ReplayToolBudget:
    """Thread-safe hard cap over valid scene-data POST requests."""

    def __init__(self, maximum: int | None):
        if maximum is not None and maximum < 1:
            raise ValueError("max_tool_calls must be positive")
        self.maximum = maximum
        self._used = 0
        self._lock = Lock()

    def consume(self) -> bool:
        with self._lock:
            if self.maximum is not None and self._used >= self.maximum:
                return False
            self._used += 1
            return True

    def snapshot(self) -> dict[str, int | None]:
        with self._lock:
            remaining = (
                None if self.maximum is None
                else max(0, self.maximum - self._used)
            )
            return {
                "maximum": self.maximum,
                "used": self._used,
                "remaining": remaining,
            }


class ReplayTraceWriter:
    """Append-only evaluator trace with an in-process monotonic sequence."""

    def __init__(self, path: Path):
        self.path = path.expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._sequence = 0

    def write(
        self,
        *,
        operation: str,
        status: str,
        request_payload: dict[str, Any],
        response_payload: dict[str, Any],
    ) -> None:
        with self._lock:
            self._sequence += 1
            event = {
                "schema_version": "replay_query_trace_event_v1",
                "sequence": self._sequence,
                "emitted_at": datetime.now(timezone.utc).isoformat(),
                "operation": operation,
                "status": status,
                "environment_id": request_payload.get("environment_id", ""),
                "request_id": request_payload.get("request_id", ""),
                "request": request_payload,
                "response": response_payload,
            }
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True))
                handle.write("\n")


def _error_payload(
    code: str, message: str, *, request_id: str = "", details: dict[str, Any] | None = None
) -> dict[str, Any]:
    return ReplayErrorResponse(
        request_id=request_id,
        error=ReplayError(code=code, message=message, details=details or {}),
    ).model_dump(mode="json")


def _query_error_status(error: ReplayQueryError) -> int:
    if error.code in {"ENVIRONMENT_NOT_FOUND", "ENVIRONMENT_MISMATCH"}:
        return 403
    return 400


def create_gateway_app(
    database_path: Path,
    *,
    bundle_id: str,
    environment_id: str,
    trace_path: Path,
    tool_profile: ReplayToolProfile = "semantic_aggregate",
    entity_page_size_cap: int | None = None,
    query_page_size_cap: int | None = None,
    metric_raw_precedence: bool = False,
    max_tool_calls: int | None = None,
) -> FastAPI:
    """Create one gateway instance permanently bound to exactly one bundle."""

    engine = ReplayQueryEngine(
        database_path,
        bundle_id=bundle_id,
        environment_id=environment_id,
        tool_profile=tool_profile,
        entity_page_size_cap=entity_page_size_cap,
        query_page_size_cap=query_page_size_cap,
        metric_raw_precedence=metric_raw_precedence,
    )
    trace_writer = ReplayTraceWriter(trace_path)
    tool_budget = ReplayToolBudget(max_tool_calls)

    def budget_exhausted(
        operation: str, request_payload: dict[str, Any], *, request_id: str = ""
    ) -> JSONResponse | None:
        if tool_budget.consume():
            return None
        snapshot = tool_budget.snapshot()
        payload = _error_payload(
            "QUERY_BUDGET_EXHAUSTED",
            "the task-local scene query budget is exhausted",
            request_id=request_id,
            details=snapshot,
        )
        trace_writer.write(
            operation=operation,
            status="error",
            request_payload=request_payload,
            response_payload=payload,
        )
        return JSONResponse(status_code=429, content=payload)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        engine.close()

    app = FastAPI(
        title="Benchmark Factory Replay Gateway",
        version="1",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    @app.exception_handler(RequestValidationError)
    async def invalid_request_handler(
        request: Request, error: RequestValidationError
    ) -> JSONResponse:
        payload = _error_payload(
            "INVALID_REQUEST",
            "request does not conform to Replay v1",
            details={"errors": jsonable_encoder(error.errors())},
        )
        trace_writer.write(
            operation=request.url.path,
            status="error",
            request_payload={},
            response_payload=payload,
        )
        return JSONResponse(status_code=400, content=payload)

    @app.get("/v1/health")
    def health() -> dict[str, Any]:
        return {
            "schema_version": "replay_health_v1",
            "status": "ready",
            "api_version": "v1",
            "environment_id": environment_id,
        }

    @app.get("/v1/capabilities")
    def capabilities() -> dict[str, Any]:
        payload = engine.capabilities()
        payload["tool_call_budget"] = tool_budget.snapshot()
        return payload

    @app.post("/v1/entities/search", response_model=EntitySearchResponse)
    def entity_search(request: EntitySearchRequest):
        request_payload = request.model_dump(mode="json")
        exhausted = budget_exhausted("entity_search", request_payload)
        if exhausted is not None:
            return exhausted
        try:
            response = engine.search_entities(request)
        except ReplayQueryError as error:
            payload = _error_payload(
                error.code,
                error.message,
                details=error.details,
            )
            trace_writer.write(
                operation="entity_search",
                status="error",
                request_payload=request_payload,
                response_payload=payload,
            )
            return JSONResponse(status_code=_query_error_status(error), content=payload)
        response_payload = response.model_dump(mode="json")
        trace_writer.write(
            operation="entity_search",
            status="success",
            request_payload=request_payload,
            response_payload=response_payload,
        )
        return response

    @app.post("/v1/signals", response_model=SignalCatalogResponse)
    def signal_catalog(request: SignalCatalogRequest):
        request_payload = request.model_dump(mode="json")
        exhausted = budget_exhausted("signal_catalog", request_payload)
        if exhausted is not None:
            return exhausted
        try:
            response = engine.list_signals(request)
        except ReplayQueryError as error:
            payload = _error_payload(
                error.code,
                error.message,
                details=error.details,
            )
            trace_writer.write(
                operation="signal_catalog",
                status="error",
                request_payload=request_payload,
                response_payload=payload,
            )
            return JSONResponse(status_code=_query_error_status(error), content=payload)
        response_payload = response.model_dump(mode="json")
        trace_writer.write(
            operation="signal_catalog",
            status="success",
            request_payload=request_payload,
            response_payload=response_payload,
        )
        return response

    @app.post("/v1/query")
    def query(request: ReplayQueryRequest):
        request_payload = request.model_dump(mode="json")
        exhausted = budget_exhausted(
            "query", request_payload, request_id=request.request_id
        )
        if exhausted is not None:
            return exhausted
        try:
            response = engine.query(request)
        except ReplayQueryError as error:
            payload = _error_payload(
                error.code,
                error.message,
                request_id=request.request_id,
                details=error.details,
            )
            trace_writer.write(
                operation="query",
                status="error",
                request_payload=request_payload,
                response_payload=payload,
            )
            return JSONResponse(status_code=_query_error_status(error), content=payload)
        response_payload = response.model_dump(mode="json")
        trace_writer.write(
            operation="query",
            status="success",
            request_payload=request_payload,
            response_payload=response_payload,
        )
        return response_payload

    return app
