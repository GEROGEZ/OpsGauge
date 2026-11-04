"""Dependency-free Harbor verifier for all Benchmark Factory task types.

This file is copied verbatim into generated tasks. Keep imports in the Python
standard library so Harbor can execute it in its minimal verifier runtime.
"""

from __future__ import annotations

from datetime import datetime, timezone

import json
import math
from math import isclose
import os
from pathlib import Path
import re
from typing import Any, Iterator




_METRIC_ALIASES = {
    "kube_deployment_status_replicas_ready": "deployment_ready_replicas",
    "kube_deployment_status_replicas_available": "deployment_available_replicas",
    "kube_deployment_status_replicas_unavailable": "deployment_unavailable_replicas",
    "kube_deployment_spec_replicas": "deployment_desired_replicas",
}
_UNIT_ALIASES = {
    "percent": "%", "percentage": "%", "seconds": "s", "second": "s",
    "microsecond": "us", "microseconds": "us", "restarts": "count",
    "replica": "count", "replicas": "count",
}


def _canonical_metric_name(value: Any) -> str:
    text = str(value or "")
    return _METRIC_ALIASES.get(text, text)


def _canonical_unit(value: Any) -> str:
    text = str(value or "").strip()
    return _UNIT_ALIASES.get(text.lower(), text)


def _equal_value(left: Any, right: Any) -> bool:
    try:
        return isclose(float(left), float(right), rel_tol=1e-5, abs_tol=1e-6)
    except (TypeError, ValueError):
        return left == right


def _normalized_datetime(value: Any) -> str:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc)
        return parsed.isoformat()
    except (TypeError, ValueError):
        return str(value or "")


def _time_range_equal(expected: Any, actual: Any) -> bool:
    expected_range = expected if isinstance(expected, dict) else {}
    actual_range = actual if isinstance(actual, dict) else {}
    return all(
        _normalized_datetime(expected_range.get(key))
        == _normalized_datetime(actual_range.get(key))
        for key in ("start", "end")
    )


def _aggregation_base(value: Any) -> str:
    text = str(value or "")
    head, separator, tail = text.rpartition("; ")
    stats = {
        "avg", "count", "identity", "latest", "max", "min", "p50",
        "p90", "p95", "p99", "series", "sum",
    }
    return head if separator and tail in stats else text


def _set_f1(expected: list[Any], actual: list[Any], matcher) -> tuple[float, int]:
    unused = set(range(len(actual)))
    matched = 0
    for expected_item in expected:
        for index in sorted(unused):
            if matcher(expected_item, actual[index]):
                unused.remove(index)
                matched += 1
                break
    precision = matched / len(actual) if actual else 0.0
    recall = matched / len(expected) if expected else 1.0
    score = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return score, matched


def _dimension(score: float, numerator: float, denominator: int) -> dict[str, Any]:
    return {"score": score, "numerator": numerator, "denominator": denominator}


def _metric_components(expected: dict[str, Any], actual: dict[str, Any]) -> dict[str, bool]:
    identity = (
        str(expected.get("entity_id", "")) == str(actual.get("entity_id", ""))
        and _canonical_metric_name(expected.get("metric_name"))
        == _canonical_metric_name(actual.get("metric_name"))
        and str(expected.get("stat", "")) == str(actual.get("stat", ""))
    )
    expected_dimensions = expected.get("dimensions") or {}
    actual_dimensions = actual.get("dimensions") or {}
    expected_aggregation = str(expected.get("aggregation") or "")
    return {
        "identity": identity,
        "numeric": identity and _equal_value(expected.get("value"), actual.get("value")),
        "unit": identity and _canonical_unit(expected.get("unit"))
        == _canonical_unit(actual.get("unit")),
        "dimensions": identity and all(
            actual_dimensions.get(key) == value
            for key, value in expected_dimensions.items()
        ),
        "aggregation": identity and (
            not expected_aggregation
            or _aggregation_base(expected_aggregation)
            == _aggregation_base(actual.get("aggregation"))
        ),
    }


def _score_l2_metric(gt: dict[str, Any], answer: dict[str, Any]) -> dict[str, Any]:
    expected = list(gt.get("results") or [])
    actual = list(answer.get("results") or [])
    unused = set(range(len(actual)))
    counts = {key: 0 for key in ("identity", "numeric", "unit", "dimensions", "aggregation")}
    strict = 0
    for expected_row in expected:
        candidates = []
        for index in sorted(unused):
            components = _metric_components(expected_row, actual[index])
            if components["identity"]:
                candidates.append((sum(components.values()), -index, index, components))
        if not candidates:
            continue
        _, _, index, components = max(candidates)
        unused.remove(index)
        for name, correct in components.items():
            counts[name] += int(correct)
        strict += int(all(components.values()))
    precision = strict / len(actual) if actual else 0.0
    recall = strict / len(expected) if expected else 1.0
    result_score = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    denominator = len(expected)
    dimension_names = {
        "identity": "identity_correctness",
        "numeric": "numeric_correctness",
        "unit": "unit_correctness",
        "dimensions": "dimension_correctness",
        "aggregation": "aggregation_correctness",
    }
    dimensions = {
        dimension_names[name]: _dimension(
            count / denominator if denominator else 1.0, count, denominator
        )
        for name, count in counts.items()
    }
    dimensions["result_correctness"] = _dimension(result_score, strict, denominator)
    time_ok = float(_time_range_equal(gt.get("time_range"), answer.get("time_range")))
    dimensions["time_range_correctness"] = _dimension(time_ok, time_ok, 1)
    return dimensions


def _metric_full_match(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
    return all(_metric_components(expected, actual).values())


def _entity_match(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
    return all(
        str(expected.get(key) or "") == str(actual.get(key) or "")
        for key in ("entity_id", "entity_name", "entity_type")
    )


def _score_l2_metric_ranking(
    gt: dict[str, Any], answer: dict[str, Any]
) -> dict[str, Any]:
    expected = list(gt.get("ranked_results") or [])
    actual = list(answer.get("ranked_results") or [])
    result_score, matched = _set_f1(expected, actual, _metric_full_match)
    entity_score, entity_matched = _set_f1(expected, actual, _entity_match)
    expected_order = [str(item.get("entity_id") or "") for item in expected]
    actual_order = [str(item.get("entity_id") or "") for item in actual]
    expected_ranks = [item.get("rank") for item in expected]
    actual_ranks = [item.get("rank") for item in actual]
    order_ok = expected_order == actual_order and expected_ranks == actual_ranks
    top_ok = set(gt.get("top_entity_ids") or []) == set(
        answer.get("top_entity_ids") or []
    )
    gap_ok = _equal_value(
        gt.get("top_minus_second"), answer.get("top_minus_second")
    )
    unit_ok = str(gt.get("unit") or "") == str(answer.get("unit") or "")
    time_ok = float(_time_range_equal(gt.get("time_range"), answer.get("time_range")))
    return {
        "source_result_correctness": _dimension(
            result_score, matched, len(expected)
        ),
        "entity_resolution": _dimension(
            entity_score, entity_matched, len(expected)
        ),
        "ranking_correctness": _dimension(
            (float(order_ok) + float(top_ok)) / 2,
            float(order_ok) + float(top_ok),
            2,
        ),
        "derived_computation": _dimension(
            (float(gap_ok) + float(unit_ok)) / 2,
            float(gap_ok) + float(unit_ok),
            2,
        ),
        "time_range_correctness": _dimension(time_ok, time_ok, 1),
    }


def _score_l2_derived_metric(
    gt: dict[str, Any], answer: dict[str, Any]
) -> dict[str, Any]:
    expected = list(gt.get("source_results") or [])
    actual = list(answer.get("source_results") or [])
    result_score, matched = _set_f1(expected, actual, _metric_full_match)
    expected_entities = list(gt.get("entity_expectations") or [])
    actual_entities = list(answer.get("entity_expectations") or [])
    entity_score, entity_matched = _set_f1(
        expected_entities, actual_entities, _entity_match
    )
    expected_computation = gt.get("computation") or {}
    actual_computation = answer.get("computation") or {}
    fields = [
        field
        for field in ("operator", "left_metric", "right_metric", "dominant_metric")
        if field in expected_computation
    ]
    field_matches = sum(
        str(expected_computation.get(field) or "")
        == str(actual_computation.get(field) or "")
        for field in fields
    )
    value_ok = _equal_value(
        expected_computation.get("value"), actual_computation.get("value")
    )
    unit_ok = str(expected_computation.get("unit") or "") == str(
        actual_computation.get("unit") or ""
    )
    computation_denominator = len(fields) + 2
    computation_numerator = field_matches + int(value_ok) + int(unit_ok)
    time_ok = float(_time_range_equal(gt.get("time_range"), answer.get("time_range")))
    return {
        "source_result_correctness": _dimension(
            result_score, matched, len(expected)
        ),
        "entity_resolution": _dimension(
            entity_score, entity_matched, len(expected_entities)
        ),
        "derived_computation": _dimension(
            computation_numerator / computation_denominator,
            computation_numerator,
            computation_denominator,
        ),
        "time_range_correctness": _dimension(time_ok, time_ok, 1),
    }


def _set_score(expected: list[str], actual: list[str]) -> float:
    expected_set = set(expected)
    actual_set = set(actual)
    if not expected_set and not actual_set:
        return 1.0
    matched = len(expected_set & actual_set)
    precision = matched / len(actual_set) if actual_set else 0.0
    recall = matched / len(expected_set) if expected_set else float(not actual_set)
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


def _judgement_name(value: Any) -> str:
    return " ".join(str(value).split()).casefold()


def _judgement_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _judgement_item_score(expected: dict[str, Any], actual: dict[str, Any] | None) -> float:
    if not isinstance(actual, dict):
        return 0.0
    kind = str(expected.get("kind") or "")
    expected_status = str(expected.get("status") or "")
    status = str(actual.get("status") or "")
    if kind == "claim":
        return float(status == expected_status)
    if expected_status == "insufficient":
        return float(status == "insufficient")
    if status != "answered":
        return 0.0
    want = expected.get("value")
    got = actual.get("value")
    tolerance = expected.get("tolerance") or {}
    if kind == "count":
        number = _judgement_number(got)
        return float(number is not None and number == float(int(want)))
    if kind == "number":
        number = _judgement_number(got)
        if number is None:
            return 0.0
        allowed = max(
            float(tolerance.get("abs") or 0.0),
            abs(float(want)) * float(tolerance.get("rel") or 0.0),
        )
        return float(abs(number - float(want)) <= allowed + 1e-12)
    if kind == "name":
        return float(isinstance(got, str) and _judgement_name(got) == _judgement_name(want))
    if kind == "name_set":
        if not isinstance(got, list) or not all(isinstance(item, str) for item in got):
            return 0.0
        return _set_score(
            [_judgement_name(item) for item in want],
            [_judgement_name(item) for item in got],
        )
    if kind == "timestamp":
        try:
            delta = abs((
                datetime.fromisoformat(str(got).replace("Z", "+00:00"))
                - datetime.fromisoformat(str(want).replace("Z", "+00:00"))
            ).total_seconds())
        except (TypeError, ValueError):
            return 0.0
        return float(delta <= float(tolerance.get("seconds") or 0.0))
    raise ValueError(f"unsupported judgement item kind: {kind}")


def _score_l2_observation_judgement(
    gt: dict[str, Any], answer: dict[str, Any]
) -> dict[str, Any]:
    submitted: dict[str, dict[str, Any]] = {}
    for item in answer.get("items") or []:
        if isinstance(item, dict):
            submitted.setdefault(str(item.get("item_id") or ""), item)
    dimensions: dict[str, Any] = {}
    families: dict[str, list[float]] = {}
    for expected in gt.get("items") or []:
        item_id = str(expected["item_id"])
        value = _judgement_item_score(expected, submitted.get(item_id))
        dimensions[f"item_{item_id}"] = _dimension(value, value, 1)
        families.setdefault(str(expected.get("family") or "other"), []).append(value)
    for family, values in sorted(families.items()):
        dimensions[f"family_{family}"] = _dimension(
            sum(values) / len(values), sum(values), len(values)
        )
    return dimensions


def _graph_measurement_match(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
    return (
        all(str(expected.get(key) or "") == str(actual.get(key) or "")
            for key in ("entity_id", "metric_name", "window", "stat", "unit"))
        and int(expected.get("sample_count") or 0) == int(actual.get("sample_count") or 0)
        and _equal_value(expected.get("value"), actual.get("value"))
        and (expected.get("dimensions") or {}) == (actual.get("dimensions") or {})
    )


def _graph_derived_match(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
    if not all(str(expected.get(key) or "") == str(actual.get(key) or "")
               for key in ("entity_id", "entity_name")):
        return False
    return all(_equal_value(expected.get(key), actual.get(key)) for key in (
        "baseline_cpu_avg", "current_cpu_avg",
        "baseline_memory_avg", "current_memory_avg", "pressure_delta_points",
    ))


def _score_l2_graph_metric_join(
    gt: dict[str, Any], answer: dict[str, Any]
) -> dict[str, Any]:
    root_ok = float(_entity_match(gt.get("root_entity") or {}, answer.get("root_entity") or {}))
    supporting_score, _ = _set_f1(
        list(gt.get("supporting_entities") or []),
        list(answer.get("supporting_entities") or []), _entity_match,
    )
    candidate_score, _ = _set_f1(
        list(gt.get("candidate_entities") or []),
        list(answer.get("candidate_entities") or []), _entity_match,
    )
    eligible_score, _ = _set_f1(
        list(gt.get("eligible_entities") or []),
        list(answer.get("eligible_entities") or []), _entity_match,
    )
    excluded_score = _set_score(
        [str(item) for item in gt.get("excluded_entity_ids") or []],
        [str(item) for item in answer.get("excluded_entity_ids") or []],
    )
    relation_score, _ = _set_f1(
        list(gt.get("supporting_relations") or []),
        list(answer.get("supporting_relations") or []), _relation_match,
    )
    scope_parts = (
        root_ok, supporting_score, candidate_score, eligible_score,
        excluded_score, relation_score,
    )
    scope_score = sum(scope_parts) / len(scope_parts)

    expected_measurements = list(gt.get("measurements") or [])
    measurement_score, measurement_matched = _set_f1(
        expected_measurements, list(answer.get("measurements") or []),
        _graph_measurement_match,
    )
    expected_derived = list(gt.get("ranked_results") or [])
    actual_derived = list(answer.get("ranked_results") or [])
    derived_score, derived_matched = _set_f1(
        expected_derived, actual_derived, _graph_derived_match,
    )
    expected_order = [str(item.get("entity_id") or "") for item in expected_derived]
    actual_order = [str(item.get("entity_id") or "") for item in actual_derived]
    expected_ranks = [item.get("rank") for item in expected_derived]
    actual_ranks = [item.get("rank") for item in actual_derived]
    ranking_score = float(
        expected_order == actual_order and expected_ranks == actual_ranks
    )
    midpoint_ok = float(
        _normalized_datetime(gt.get("midpoint"))
        == _normalized_datetime(answer.get("midpoint"))
    )
    time_ok = float(_time_range_equal(gt.get("time_range"), answer.get("time_range")))
    ranking_completeness = (ranking_score + midpoint_ok + time_ok) / 3
    return {
        "scope_graph_correctness": _dimension(scope_score, sum(scope_parts), len(scope_parts)),
        "source_measurement_correctness": _dimension(
            measurement_score, measurement_matched, len(expected_measurements)
        ),
        "derived_computation": _dimension(
            derived_score, derived_matched, len(expected_derived)
        ),
        "ranking_completeness": _dimension(
            ranking_completeness, ranking_score + midpoint_ok + time_ok, 3
        ),
    }


def _event_match(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
    expected_attributes = expected.get("attributes") or {}
    actual_attributes = actual.get("attributes") or {}
    return (
        str(expected.get("event_id") or "") == str(actual.get("event_id") or "")
        and str(expected.get("event_type") or "") == str(actual.get("event_type") or "")
        and _normalized_datetime(expected.get("occurred_at"))
        == _normalized_datetime(actual.get("occurred_at"))
        and all(actual_attributes.get(key) == value for key, value in expected_attributes.items())
    )


def _trace_equal_value(expected: dict[str, Any], actual_value: Any) -> bool:
    if str(expected.get("result_name") or "") not in {
        "span_duration", "span_exclusive_duration",
    }:
        return _equal_value(expected.get("value"), actual_value)
    try:
        return isclose(
            float(expected.get("value")), float(actual_value),
            rel_tol=0.0, abs_tol=0.002,
        )
    except (TypeError, ValueError):
        return expected.get("value") == actual_value


def _trace_match(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
    return (
        str(expected.get("trace_id") or "") == str(actual.get("trace_id") or "")
        and str(expected.get("span_id") or "") == str(actual.get("span_id") or "")
        and str(expected.get("result_name") or "") == str(actual.get("result_name") or "")
        and str(expected.get("stat") or "") == str(actual.get("stat") or "")
        and _trace_equal_value(expected, actual.get("value"))
        and _canonical_unit(expected.get("unit")) == _canonical_unit(actual.get("unit"))
        and all(
            (actual.get("attributes") or {}).get(key) == value
            for key, value in (expected.get("attributes") or {}).items()
        )
    )


def _exclusive_duration_ms(
    span: dict[str, Any], trace_rows: list[dict[str, Any]],
) -> float | None:
    try:
        start = datetime.fromisoformat(str(span.get("start_time")).replace("Z", "+00:00"))
        end = datetime.fromisoformat(str(span.get("end_time")).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    intervals = []
    trace_id = str(span.get("trace_id") or "")
    span_id = str(span.get("span_id") or "")
    for child in trace_rows:
        if (
            str(child.get("trace_id") or "") != trace_id
            or str(child.get("parent_span_id") or "") != span_id
        ):
            continue
        try:
            child_start = datetime.fromisoformat(
                str(child.get("start_time")).replace("Z", "+00:00")
            )
            child_end = datetime.fromisoformat(
                str(child.get("end_time")).replace("Z", "+00:00")
            )
        except (TypeError, ValueError):
            continue
        clipped_start = max(start, child_start)
        clipped_end = min(end, child_end)
        if clipped_end > clipped_start:
            intervals.append((clipped_start, clipped_end))
    intervals.sort()
    merged = []
    for interval_start, interval_end in intervals:
        if not merged or interval_start > merged[-1][1]:
            merged.append([interval_start, interval_end])
        elif interval_end > merged[-1][1]:
            merged[-1][1] = interval_end
    child_ms = sum((right-left).total_seconds()*1000 for left,right in merged)
    return (end-start).total_seconds()*1000-child_ms


def _trace_duration_ms(row: dict[str, Any]) -> float | None:
    try:
        start = datetime.fromisoformat(str(row.get("start_time")).replace("Z", "+00:00"))
        end = datetime.fromisoformat(str(row.get("end_time")).replace("Z", "+00:00"))
        return (end - start).total_seconds() * 1000
    except (TypeError, ValueError):
        pass
    attributes = row.get("attributes") or {}
    duration_ns = attributes.get("duration_ns")
    if duration_ns is None and str(row.get("unit") or "") == "ns":
        duration_ns = row.get("value")
    try:
        return float(duration_ns) / 1_000_000
    except (TypeError, ValueError):
        return None


def _trace_answer_grounded(answer: dict[str, Any], rows: list[dict[str, Any]]) -> bool:
    trace_id = str(answer.get("trace_id") or "")
    trace_rows = [
        row for row in rows
        if row.get("_response_modality") == "trace"
        and str(row.get("trace_id") or "") == trace_id
        and str(row.get("span_id") or "")
    ]
    if (
        str(answer.get("result_name") or "") == "span_count"
        and str(answer.get("stat") or "") == "count"
    ):
        return _equal_value(
            answer.get("value"), len({str(row.get("span_id")) for row in trace_rows})
        )
    span_id = str(answer.get("span_id") or "")
    for row in trace_rows:
        if str(row.get("span_id") or "") != span_id:
            continue
        result_name = str(answer.get("result_name") or "")
        if result_name == "span_exclusive_duration":
            duration_ms = _exclusive_duration_ms(row, trace_rows)
        elif result_name == "span_duration":
            duration_ms = _trace_duration_ms(row)
        else:
            continue
        if duration_ms is None or not _trace_equal_value(answer, duration_ms):
            continue
        if _canonical_unit(answer.get("unit")) != "ms":
            continue
        observable = {
            **(row.get("attributes") or {}),
            "parent_span_id": row.get("parent_span_id") or "",
            "service": row.get("service_name") or "",
            "span_name": row.get("operation_name") or "",
        }
        if all(
            observable.get(key) == value
            for key, value in (answer.get("attributes") or {}).items()
        ):
            return True
    return False


def _score_l2_trace(gt: dict[str, Any], answer: dict[str, Any], trace_rows: list[dict[str, Any]]) -> dict[str, Any]:
    expected = list(gt.get("results") or [])
    actual = list(answer.get("results") or [])
    score, matched = _set_f1(expected, actual, _trace_match)
    grounded = sum(_trace_answer_grounded(item, trace_rows) for item in actual)
    time_ok = float(_time_range_equal(gt.get("time_range"), answer.get("time_range")))
    return {
        "result_correctness": _dimension(score, matched, len(expected)),
        "time_range_correctness": _dimension(time_ok, time_ok, 1),
        "execution_grounding": _dimension(
            grounded / len(actual) if actual else 0.0, grounded, len(actual)
        ),
    }


def _score_l2_event(gt: dict[str, Any], answer: dict[str, Any]) -> dict[str, Any]:
    expected = list(gt.get("results") or [])
    actual = list(answer.get("results") or [])
    score, matched = _set_f1(expected, actual, _event_match)
    count_ok = float((answer.get("summary") or {}).get("event_count") == len(actual))
    time_ok = float(_time_range_equal(gt.get("time_range"), answer.get("time_range")))
    return {
        "result_correctness": _dimension(score, matched, len(expected)),
        "count_consistency": _dimension(count_ok, count_ok, 1),
        "time_range_correctness": _dimension(time_ok, time_ok, 1),
    }


def _relation_match(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
    return (
        str(expected.get("source") or "") == str(actual.get("source") or "")
        and str(expected.get("target") or "") == str(actual.get("target") or "")
        and str(expected.get("relation_type") or "") == str(actual.get("relation_type") or "")
        and all(
            (actual.get("attributes") or {}).get(key) == value
            for key, value in (expected.get("attributes") or {}).items()
        )
    )


def _relation_entity_match(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
    expected_attributes = expected.get("attributes") or {}
    actual_attributes = actual.get("attributes") or {}
    expected_identity = expected_attributes.get("entity_id") or expected.get("entity_id") or ""
    actual_identity = actual.get("entity_id") or actual_attributes.get("entity_id") or ""
    return (
        str(expected_identity) == str(actual_identity)
        and str(expected.get("entity_type") or "") == str(actual.get("entity_type") or "")
        and all(
            actual_attributes.get(key) == value
            for key, value in expected_attributes.items()
        )
    )


def _score_l2_relation(gt: dict[str, Any], answer: dict[str, Any]) -> dict[str, Any]:
    expected_relations = list(gt.get("relations") or [])
    actual_relations = list(answer.get("relations") or [])
    relation_score, relation_matched = _set_f1(
        expected_relations, actual_relations, _relation_match
    )
    expected_entities = list(gt.get("entities") or [])
    actual_entities = list(answer.get("entities") or [])
    entity_score, entity_matched = _set_f1(
        expected_entities, actual_entities, _relation_entity_match
    )
    time_ok = float(_time_range_equal(gt.get("time_range"), answer.get("time_range")))
    return {
        "entity_correctness": _dimension(entity_score, entity_matched, len(expected_entities)),
        "relation_correctness": _dimension(relation_score, relation_matched, len(expected_relations)),
        "time_range_correctness": _dimension(time_ok, time_ok, 1),
    }


def _record_key(record: dict[str, Any]) -> str:
    return str(record.get("record_ref") or "")


def _record_map(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {_record_key(item): item for item in records}


def _merge_trace_records(
    records: dict[str, dict[str, Any]], trace_events: list[dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    merged = dict(records)
    for event in trace_events or []:
        if str(event.get("operation") or "") != "query":
            continue
        if str(event.get("status") or "") != "success":
            continue
        response = event.get("response") or {}
        modality = str(response.get("modality") or "")
        for row in response.get("results") or []:
            ref = str(row.get("record_ref") or "")
            if not ref or ref in merged:
                continue
            entity_ids = [str(item) for item in row.get("entity_ids") or []]
            for key in ("entity_id", "source_entity_id", "target_entity_id"):
                if row.get(key):
                    entity_ids.append(str(row[key]))
            merged[ref] = {
                "record_ref": ref,
                "modality": modality,
                "signal": str(
                    row.get("signal")
                    or row.get("metric_name")
                    or row.get("relation_type")
                    or row.get("event_type")
                    or ""
                ),
                "entity_ids": entity_ids,
                "event_type": str(row.get("event_type") or ""),
                "attributes": row.get("attributes") or {},
                "observed_at": row.get("observed_at"),
                "value": row.get("value"),
                "unit": row.get("unit"),
                "stat": row.get("stat"),
            }
    return merged


def _evidence_grounded(evidence: dict[str, Any], records: dict[str, dict[str, Any]]) -> bool:
    entity_ids = {str(item) for item in evidence.get("entity_ids") or []}
    for reference in evidence.get("record_refs") or []:
        record = records.get(str(reference))
        if not record:
            continue
        record_entities = {str(item) for item in record.get("entity_ids") or []}
        if evidence.get("modality") and evidence.get("modality") != record.get("modality"):
            continue
        if evidence.get("signal") and evidence.get("signal") != record.get("signal"):
            record_event_type = str(
                record.get("event_type")
                or (record.get("attributes") or {}).get("event_type")
                or ""
            )
            if not (evidence.get("modality") == "event" and record_event_type == "ALERT"):
                continue
        if entity_ids and not entity_ids.intersection(record_entities):
            continue
        return True
    return False


_METRIC_ALIAS = {
    "kube_deployment_status_replicas_ready": "deployment_ready_replicas",
    "kube_deployment_status_replicas_available": "deployment_available_replicas",
    "kube_deployment_status_replicas_unavailable": "deployment_unavailable_replicas",
    "kube_deployment_spec_replicas": "deployment_desired_replicas",
}


def _canonical_signal(name: str) -> str:
    return _METRIC_ALIAS.get(name, name)


def _requirement_atom(requirement: dict[str, Any]) -> dict[str, Any]:
    return requirement.get("atom") or requirement


def _requirement_event_binding(atom: dict[str, Any]) -> tuple[str, str]:
    measurements = atom.get("measurements") or {}
    return (
        str(measurements.get("event_id") or measurements.get("anchor_event_id") or ""),
        str(measurements.get("rule_id") or ""),
    )


def _requirement_event_matches(
    requirement: dict[str, Any], evidence: dict[str, Any], records: dict[str, dict[str, Any]]
) -> bool:
    atom = _requirement_atom(requirement)
    expected_entities = {
        str(item.get("entity_id") if isinstance(item, dict) else item)
        for item in atom.get("entities") or atom.get("entity_ids") or []
    }
    actual_entities = {str(item) for item in evidence.get("entity_ids") or []}
    expected_event_id, expected_rule_id = _requirement_event_binding(atom)
    if not expected_event_id and not expected_rule_id:
        return False
    for reference in evidence.get("record_refs") or []:
        record = records.get(str(reference))
        if record is None:
            continue
        attributes = record.get("attributes") or {}
        actual_event_id = str(
            attributes.get("event_id") or record.get("event_id") or ""
        )
        actual_rule_id = str(
            attributes.get("rule_id") or record.get("rule_id") or ""
        )
        if expected_event_id and expected_event_id != actual_event_id:
            continue
        if expected_rule_id and expected_rule_id != actual_rule_id:
            continue
        if (
            expected_entities
            and not expected_entities.intersection(
                str(item) for item in record.get("entity_ids") or []
            )
        ):
            continue
        return True
    return False


def _evidence_matches_requirement(
    requirement: dict[str, Any], evidence: dict[str, Any], records: dict[str, dict[str, Any]]
) -> bool:
    atom = _requirement_atom(requirement)
    if "resource_event_binding" in atom:
        return _resource_event_observation(requirement, evidence, records) is not None
    modality = str(atom.get("modality") or "")
    if modality == "event" and _requirement_event_matches(
        requirement, evidence, records
    ):
        return True
    expected_entities = {
        str(item.get("entity_id") if isinstance(item, dict) else item)
        for item in atom.get("entities") or atom.get("entity_ids") or []
    }
    actual_entities = {str(item) for item in evidence.get("entity_ids") or []}
    signal = str(atom.get("signal") or (atom.get("query") or {}).get("signal") or "")
    actual_signal = str(evidence.get("signal") or "")
    signal_ok = not signal or signal == actual_signal
    if not signal_ok and modality.lower() == "metric":
        signal_ok = _canonical_signal(signal) == _canonical_signal(actual_signal)
    if not signal_ok:
        for pattern in requirement.get("acceptable_signal_patterns") or []:
            try:
                signal_ok = bool(re.search(str(pattern), actual_signal))
            except re.error:
                pass
            if signal_ok:
                break
    return (
        signal_ok
        and (not modality or modality == str(evidence.get("modality") or ""))
        and (not expected_entities or bool(expected_entities.intersection(actual_entities)))
        and _evidence_grounded(evidence, records)
    )


def _grounding_dimension(evidence: list[dict[str, Any]], records: dict[str, dict[str, Any]]) -> dict[str, Any]:
    grounded = sum(_evidence_grounded(item, records) for item in evidence)
    score = grounded / len(evidence) if evidence else 0.0
    return _dimension(score, grounded, len(evidence))


def _evidence_unit(value):
    """Remove explanatory annotations, retaining physical and rate units."""
    text = str(value or "").replace("**", "").strip()
    match = re.fullmatch(r"([^()（）]+?)\s*[（(](.*?)[）)]?(?:[;；].*)?", text)
    if (match and re.search(r"\b(mean|avg|sum|rate|p\d+|percentile)\b|平均|聚合|分位", match.group(2), re.I)
            and not re.search(r"baseline|actual|threshold|ratio|n\s*=|基线|实际|阈值|说明", match.group(2), re.I)):
        return _canonical_unit(text)
    text = re.split(r"\s*[（(]|[;；]", text, maxsplit=1)[0].strip()
    window = re.fullmatch(r"(?:sum|mean|avg|max|min)\s+(.+?)/window", text, re.I)
    if window:
        text = window.group(1).strip()
    return _canonical_unit(text)

def _structured_observation_value(record):
    """Use public numeric measurements, not counts inferred from arbitrary rows."""
    attributes = record.get("attributes") or {}
    modality = record.get("modality")
    value = record.get("value")
    aggregate = record.get("record_kind") == "aggregate" or bool(record.get("aggregate")) or attributes.get("record_kind") == "aggregate"
    if modality != "metric" and not aggregate:
        if modality != "event" or attributes.get("current_value") is None:
            return None
        measured = attributes["current_value"]
        try:
            numeric_value = float(value)
        except (TypeError, ValueError):
            numeric_value = None
        if numeric_value is not None and not _equal_value(numeric_value, measured):
            return None
        value = measured
    if isinstance(value, bool):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


def _aggregate_observation(requirement, evidence, records):
    """Evaluate an explicitly declared, complete, single-series sample window.

    No contracts are inferred from unit suffixes or observed timestamp spacing.
    Window semantics are [start, end); mean is an unweighted sample mean.
    Public data, not the GT's expected value, supplies every input value.
    """
    atom = _requirement_atom(requirement)
    contract = atom.get("aggregation") or {}
    if (contract.get("schema_version") != "sample_aggregation_v1"
            or contract.get("operator") not in {"mean", "sum", "max", "min"}
            or contract.get("window_closed") != "left"
            or not isinstance(contract.get("dimensions"), dict)
            or not contract.get("input_unit") or atom.get("modality") != "metric"):
        return None
    expected_unit = (atom.get("expected") or {}).get("unit")
    if expected_unit:
        suffix = re.fullmatch(r"(.+?)\s*\((mean|avg|sum|peak|max|min)\)", str(expected_unit), re.I)
        base_unit, expected_operator = suffix.groups() if suffix else (expected_unit, None)
        aliases = {"avg": "mean", "peak": "max"}
        if (_evidence_unit(base_unit.strip()) != _evidence_unit(contract["input_unit"])
                or (expected_operator and aliases.get(expected_operator.lower(), expected_operator.lower()) != contract["operator"])):
            return None
    entities = _declared_entities(atom)
    signal = (atom.get("query") or {}).get("signal") or atom.get("signal")
    step = contract.get("sample_period_seconds")
    if len(entities) != 1 or not signal or isinstance(step, bool) or not isinstance(step, int) or step <= 0:
        return None
    try:
        window = atom["time_range"]
        start, end = [datetime.fromisoformat(_normalized_datetime(window[k])) for k in ("start", "end")]
        seconds = (end - start).total_seconds()
        anchor = contract.get("grid_anchor", "window_start")
        if (start.tzinfo is None or end.tzinfo is None or seconds <= 0
                or anchor not in {"window_start", "unix_epoch"}):
            return None
        if anchor == "window_start":
            if seconds % step:
                return None
            first_offset = 0
        else:
            # Explicit query grid, not an anchor inferred from available points.
            first_offset = (-start.timestamp()) % step
        expected_count = max(0, math.ceil((seconds - first_offset) / step))
        if not expected_count:
            return None
    except (KeyError, TypeError, ValueError):
        return None
    selected = {}
    for ref, record in records.items():
        if (record.get("modality") != "metric" or set(record.get("entity_ids") or []) != entities
                or record.get("signal") != signal or record.get("dimensions", {}) != contract["dimensions"]):
            continue
        try:
            timestamp = datetime.fromisoformat(_normalized_datetime(record.get("observed_at")))
            if not start <= timestamp < end:
                continue
        except (TypeError, ValueError):
            return None
        offset = (timestamp - start).total_seconds()
        # Reject preaggregated rows, duplicate points, and off-grid samples.
        if ((offset - first_offset) % step or offset in selected
                or record.get("stat") not in {None, "identity", "series"}
                or (record.get("attributes") or {}).get("record_kind") not in {None, "point"}
                or _evidence_unit(record.get("unit")) != _evidence_unit(contract["input_unit"])):
            return None
        value = _structured_observation_value(record)
        if value is None:
            return None
        selected[offset] = (str(ref), value)
    if len(selected) != expected_count:
        return None
    refs = list(evidence.get("record_refs") or [])
    if len(refs) != len(set(refs)) or set(refs) != {ref for ref, _ in selected.values()}:
        return None
    if not _evidence_matches_requirement(requirement, evidence, records):
        return None
    values = [value for _, value in selected.values()]
    operator = contract["operator"]
    try:
        value = (math.fsum(values) / len(values) if operator == "mean" else
                 math.fsum(values) if operator == "sum" else
                 max(values) if operator == "max" else min(values))
    except (OverflowError, ValueError):
        return None
    if not math.isfinite(value):
        return None
    expected = (atom.get("measurements") or {}).get("current")
    if expected is not None and not _equal_value(expected, value):
        return None
    return {"value": value, "unit": contract["input_unit"], "record_refs": refs}


def _resource_event_observation(requirement, evidence, records):
    """Opt-in resource evidence: exact collection and independently bound topology.

    Contracts are evaluator-side artifacts compiled from captured data and census,
    never accepted from an agent answer. Unknown or incomplete contracts fail closed.
    """
    atom = _requirement_atom(requirement)
    c = atom.get("resource_event_binding") or {}
    if c.get("schema_version") != "resource_event_binding_v1" or c.get("coverage") != "complete":
        return None
    modality = c.get("modality")
    if (atom.get("modality"), modality) not in {("log", "log"), ("change", "event")}:
        return None
    if not c.get("capture_ref") or not c.get("census_ref"):
        return None
    subjects = _declared_entities(requirement)
    resource = c.get("resource_entity_id")
    refs = list(evidence.get("record_refs") or [])
    expected_refs = c.get("complete_record_refs") or []
    if (not refs or len(set(refs)) != len(refs) or set(refs) != set(expected_refs)
            or set(evidence.get("entity_ids") or []) != {resource}
            or evidence.get("modality") != modality or evidence.get("signal") != c.get("signal")):
        return None
    # Explicit directed path; reverse traversal is declared, never inferred by names.
    current = c.get("subject_entity_id")
    if subjects != {current}:
        return None
    for step in c.get("relation_path") or []:
        edge = records.get(step.get("record_ref"), {})
        attrs = edge.get("attributes") or {}
        if (edge.get("modality") != "topology" or edge.get("source_kind") != "raw_telemetry"
                or edge.get("synthetic") is not False or attrs.get("relation_type") != step.get("relation_type")):
            return None
        source, target = attrs.get("source_entity_id"), attrs.get("target_entity_id")
        if step.get("direction") == "reverse": source, target = target, source
        elif step.get("direction") != "forward": return None
        if source != current or not target: return None
        current = target
    if current != resource:
        return None
    try:
        start, end = [datetime.fromisoformat(_normalized_datetime(c["time_range"][k])) for k in ("start", "end")]
        if start >= end: return None
        keys = set()
        for ref in refs:
            row = records[ref]
            attrs = row.get("attributes") or {}
            timestamp = datetime.fromisoformat(_normalized_datetime(row.get("observed_at")))
            if (row.get("source_kind") != "raw_telemetry" or row.get("synthetic") is not False
                    or row.get("modality") != modality or row.get("signal") != c.get("signal")
                    or set(row.get("entity_ids") or []) != {resource} or not start <= timestamp < end):
                return None
            if any(attrs.get(k) != v for k, v in c.get("filters", {}).items()): return None
            key = attrs.get(c.get("unique_by"))
            if key is None: return None
            keys.add(str(key))
        if len(keys) != c.get("source_count"): return None
        expected = atom.get("expected") or {}
        count = len(keys)
        if expected.get("value") is not None:
            decision = _criterion_verdict({"observed_value":count,"threshold_value":expected["value"],"operator":expected.get("comparator")})
            if decision != "occurred": return None
        measured = atom.get("measurements", {}).get("current")
        if measured is not None and not _equal_value(measured,count): return None
        return {"value":count,"unit":"count","record_refs":refs}
    except (KeyError, TypeError, ValueError):
        return None


def _bound_requirement_matches(requirement, evidence, records):
    """Match the cited record itself, including its temporal/evidence role.

    evidence_id is a GT observation identifier, not necessarily a public record_ref.
    Do not compare these two namespaces or trust answer-supplied timestamps.
    """
    atom = _requirement_atom(requirement)
    if "resource_event_binding" in atom:
        return _resource_event_observation(requirement, evidence, records) is not None
    if "aggregation" in atom:
        return _aggregate_observation(requirement, evidence, records) is not None
    for ref in evidence.get("record_refs") or []:
        record = records.get(str(ref))
        if record is None:
            continue
        single = {**evidence, "record_refs": [ref]}
        if not _evidence_matches_requirement(requirement, single, {str(ref): record}):
            continue
        expected_at = atom.get("observed_at")
        window = atom.get("time_range") or {}
        if window.get("start") and window.get("end"):
            try:
                start, end, observed = [datetime.fromisoformat(_normalized_datetime(value)) for value in
                                        [window["start"], window["end"], record.get("observed_at")]]
                if not start <= observed <= end:
                    continue
            except (TypeError, ValueError):
                continue
        elif expected_at and _normalized_datetime(expected_at) != _normalized_datetime(record.get("observed_at")):
            continue
        allowed_refs = atom.get("acceptable_record_refs") or atom.get("record_refs")
        if allowed_refs and ref not in allowed_refs:
            continue
        measurement = atom.get("measurements") or {}
        value = measurement.get("baseline") if measurement.get("window_role") == "baseline" else measurement.get("current")
        if value is not None and not _equal_value(value, record.get("value")):
            continue
        unit = (atom.get("expected") or {}).get("unit")
        if unit and _evidence_unit(unit) != _evidence_unit(record.get("unit")):
            continue
        return True
    return False


def _criterion_verdict(criterion):
    try:
        value, threshold = float(criterion["observed_value"]), float(criterion["threshold_value"])
    except (KeyError, TypeError, ValueError):
        return None
    if not math.isfinite(value) or not math.isfinite(threshold):
        return None
    operators = {">": value > threshold, ">=": value >= threshold,
                 "<": value < threshold, "<=": value <= threshold,
                 "==": value == threshold, "!=": value != threshold}
    result = operators.get(criterion.get("operator"))
    return None if result is None else ("occurred" if result else "not_occurred")


def _score_l3(gt: dict[str, Any], answer: dict[str, Any], records: dict[str, dict[str, Any]]) -> dict[str, Any]:
    expected_subject = gt.get("subject") or {}
    actual_subject = answer.get("subject") or {}
    checks = [
        expected_subject.get("entity_id") == actual_subject.get("entity_id"),
        expected_subject.get("entity_type") == actual_subject.get("entity_type"),
        gt.get("phenomenon") == answer.get("phenomenon"),
        gt.get("verdict") == answer.get("verdict"),
    ]
    expected_criterion = gt.get("criterion") or {}
    actual_criterion = answer.get("criterion") or {}
    criterion_checks = [
        expected_criterion.get("operator") == actual_criterion.get("operator"),
        _equal_value(expected_criterion.get("observed_value"), actual_criterion.get("observed_value")),
        _equal_value(expected_criterion.get("threshold_value"), actual_criterion.get("threshold_value")),
        str(expected_criterion.get("unit") or "") == str(actual_criterion.get("unit") or ""),
    ]
    requirements = [
        item for item in gt.get("evidence_requirements") or [] if item.get("required", True)
    ]
    evidence = list(answer.get("evidence") or [])
    matched = sum(
        any(_evidence_matches_requirement(req, item, records) for item in evidence)
        for req in requirements
    )
    time_ok = float(_time_range_equal(gt.get("time_range"), answer.get("time_range")))
    return {
        "decision_correctness": _dimension(sum(checks) / len(checks), sum(checks), len(checks)),
        "criterion_correctness": _dimension(sum(criterion_checks) / len(criterion_checks), sum(criterion_checks), len(criterion_checks)),
        "evidence_coverage": _dimension(matched / len(requirements) if requirements else 1.0, matched, len(requirements)),
        "time_range_correctness": _dimension(time_ok, time_ok, 1),
        "evidence_grounding": _grounding_dimension(evidence, records),
    }


def _score_l3_v2(gt: dict[str, Any], answer: dict[str, Any], records: dict[str, dict[str, Any]]) -> dict[str, Any]:
    expected_subject = gt.get("subject") or {}
    actual_subject = answer.get("subject") or {}
    checks = [
        expected_subject.get("entity_id") == actual_subject.get("entity_id"),
        expected_subject.get("entity_type") == actual_subject.get("entity_type"),
        gt.get("phenomenon") == answer.get("phenomenon"),
        gt.get("verdict") == answer.get("verdict"),
    ]
    expected_criterion = gt.get("criterion") or {}
    actual_criterion = answer.get("criterion") or {}
    gt_baseline = expected_criterion.get("baseline")
    gt_threshold = expected_criterion.get("threshold_value")
    gt_observed = expected_criterion.get("observed_value")
    gt_k = expected_criterion.get("k")

    def _rel_close(actual: Any, expected: Any) -> bool:
        try:
            expected_f = float(expected)
            actual_f = float(actual)
        except (TypeError, ValueError):
            return False
        if expected_f == 0.0:
            return actual_f == 0.0
        return abs(actual_f - expected_f) / abs(expected_f) <= 0.05

    baseline_checks = [
        _rel_close(actual_criterion.get("baseline_value"), gt_baseline),
        _time_range_equal(expected_criterion.get("baseline_window"), actual_criterion.get("baseline_window")),
    ]
    threshold_checks = [
        expected_criterion.get("operator") == actual_criterion.get("operator"),
        _rel_close(actual_criterion.get("threshold_value"), gt_threshold),
        _equal_value(gt_k, actual_criterion.get("k")),
    ]
    observed_checks = [
        _rel_close(actual_criterion.get("observed_value"), gt_observed),
        str(expected_criterion.get("unit") or "") == str(actual_criterion.get("unit") or ""),
    ]
    requirements = [
        item for item in gt.get("evidence_requirements") or [] if item.get("required", True)
    ]
    evidence = list(answer.get("evidence") or [])
    matched = sum(
        any(_bound_requirement_matches(req, item, records) for item in evidence)
        for req in requirements
    )
    time_ok = float(_time_range_equal(gt.get("time_range"), answer.get("time_range")))
    verdict_ok = float(gt.get("verdict") == answer.get("verdict"))
    consistency = float(_criterion_verdict(actual_criterion) == answer.get("verdict"))
    return {
        "decision_correctness": _dimension(sum(checks) / len(checks), sum(checks), len(checks)),
        "baseline_correctness": _dimension(sum(baseline_checks) / len(baseline_checks), sum(baseline_checks), len(baseline_checks)),
        "threshold_correctness": _dimension(sum(threshold_checks) / len(threshold_checks), sum(threshold_checks), len(threshold_checks)),
        "observed_value_correctness": _dimension(sum(observed_checks) / len(observed_checks), sum(observed_checks), len(observed_checks)),
        "evidence_coverage": _dimension(matched / len(requirements) if requirements else 1.0, matched, len(requirements)),
        "evidence_grounding": _grounding_dimension(evidence, records),
        "time_range_correctness": _dimension(time_ok, time_ok, 1),
        "verdict_accuracy": _dimension(verdict_ok, verdict_ok, 1),
        "decision_consistency": _dimension(consistency, consistency, 1),
    }


def _declared_entities(item: dict[str, Any]) -> set[str]:
    atom = item.get("atom") or item.get("claim") or item
    return {
        str(value.get("entity_id") if isinstance(value, dict) else value)
        for value in atom.get("entities") or atom.get("subject_entities") or atom.get("entity_ids") or []
    }


def _finding_match(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
    claim = expected.get("claim") or expected
    expected_entities = _declared_entities(expected)
    actual_entities = {str(item) for item in actual.get("entity_ids") or []}
    expected_phenomenon = str(claim.get("phenomenon") or "")
    actual_phenomenon = str(actual.get("phenomenon") or "")
    phenomenon_ok = expected_phenomenon == actual_phenomenon
    if not phenomenon_ok:
        for pattern in claim.get("acceptable_signal_patterns") or []:
            try:
                phenomenon_ok = bool(re.search(str(pattern), actual_phenomenon))
            except re.error:
                pass
            if phenomenon_ok:
                break
    return (
        phenomenon_ok
        and str(claim.get("verdict", "occurred")) == str(actual.get("verdict", "occurred"))
        and (not expected_entities or bool(expected_entities.intersection(actual_entities)))
    )


def _mission_plain(value):
    """Accept protocol objects without adding dependencies to the copied verifier."""
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if isinstance(value, dict):
        return {key: _mission_plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_mission_plain(item) for item in value]
    return value


def _mission_records(records, trace_events=()):
    """Normalize canonical records and successful public responses identically."""
    result = {}

    def add(row, modality=""):
        row = _mission_plain(row)
        ref = str(row.get("record_ref") or "")
        if not ref:
            return
        aggregate = row.get("aggregate") or {}
        entities = set(str(item) for item in row.get("entity_ids") or [])
        entities.update(str(row[key]) for key in
                        ("entity_id", "source_entity_id", "target_entity_id") if row.get(key))
        attributes = dict(row.get("attributes") or {})
        for key in ("event_id", "rule_id", "relation_type", "source_entity_id", "target_entity_id"):
            if key in row:
                attributes.setdefault(key, row[key])
        kind = row.get("modality") or row.get("_response_modality") or modality
        signal = str(row.get("signal") or row.get("metric_name") or aggregate.get("name")
                     or row.get("relation_type") or row.get("event_type") or "")
        instant = (row.get("observed_at") or aggregate.get("observed_at")
                   or row.get("occurred_at") or row.get("timestamp") or row.get("start_time"))
        statistic = row.get("stat") or aggregate.get("stat") or ("identity" if kind in {"event", "log", "trace"} else None)
        result[ref] = {
            **row, "modality": kind, "entity_ids": sorted(entities), "attributes": attributes,
            "signal": _canonical_metric_name(signal) if kind == "metric" else signal,
            "value": row.get("value") if row.get("value") is not None else aggregate.get("value"),
            "unit": row.get("unit") if row.get("unit") is not None else aggregate.get("unit", ""),
            "observed_at": instant, "stat": statistic,
        }

    for row in (records.values() if isinstance(records, dict) else records):
        add(row)
    for event in _mission_plain(trace_events):
        if event.get("operation") != "query" or event.get("status") != "success":
            continue
        response = event.get("response") or {}
        for row in response.get("results") or []:
            add(row, response.get("modality") or "")
    return result


def _mission_number(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _mission_quantity(value, unit, target_unit):
    source, target = _evidence_unit(unit), _evidence_unit(target_unit)
    number = _mission_number(value)
    if number is None:
        return None
    scales = {"s": 1.0, "ms": 0.001, "us": 0.000001, "ns": 0.000000001}
    aliases = {"millisecond": "ms", "milliseconds": "ms", "nanosecond": "ns", "nanoseconds": "ns"}
    source, target = aliases.get(source, source), aliases.get(target, target)
    if source == target:
        return number
    if source in scales and target in scales:
        return number * scales[source] / scales[target]
    return None


def _mission_instant(left, right):
    try:
        a, b = [datetime.fromisoformat(_normalized_datetime(item)) for item in (left, right)]
        return a.tzinfo is not None and b.tzinfo is not None and a == b
    except (TypeError, ValueError):
        return False


def _mission_stat(value):
    return {"avg": "mean", "peak": "max"}.get(str(value or ""), str(value or ""))


def _mission_signals(record):
    attributes = record.get("attributes") or {}
    names = {str(record.get("signal") or ""), str(record.get("metric_name") or ""),
             str((record.get("aggregate") or {}).get("name") or ""),
             str((record.get("dimensions") or {}).get("__name__") or ""),
             str(attributes.get("__name__") or ""), str(attributes.get("signal") or "")}
    return {_canonical_metric_name(name) for name in names if name}


def _mission_identity(evidence, record):
    modality = evidence.get("modality")
    entities = set(str(item) for item in evidence.get("entity_ids") or [])
    signal = str(evidence.get("signal") or "")
    if (not modality or modality != record.get("modality") or not entities
            or not entities.issubset(set(record.get("entity_ids") or [])) or not signal):
        return False
    return (_canonical_metric_name(signal) in _mission_signals(record)
            or (modality == "event" and record.get("event_type") == "ALERT"))


def _mission_citation_matches(evidence, observation, *, scalar=False, time_required=False):
    """Missing required fields never inherit the returned row's value or instant."""
    value, unit, instant = [observation.get(key) for key in ("value", "unit", "observed_at")]
    if scalar:
        if "value" not in evidence or "unit" not in evidence:
            return False
        measured = _mission_quantity(evidence.get("value"), evidence.get("unit"), unit)
        if measured is None or _mission_number(value) is None or not _equal_value(measured, value):
            return False
    else:
        if evidence.get("value") is not None and not _equal_value(evidence["value"], value):
            return False
        if evidence.get("unit") and _evidence_unit(evidence["unit"]) != _evidence_unit(unit):
            return False
    if time_required or evidence.get("observed_at") is not None:
        if not _mission_instant(evidence.get("observed_at"), instant):
            return False
    stat = evidence.get("stat") or (evidence.get("attributes") or {}).get("stat")
    return not stat or _mission_stat(stat) == _mission_stat(observation.get("stat"))


def _mission_aggregate_observation(requirement, evidence, records):
    """Convert compatible input quantities without changing the aggregation contract."""
    atom = _requirement_atom(requirement)
    target = (atom.get("aggregation") or {}).get("input_unit")
    converted_records = {}
    for ref, record in records.items():
        value = _mission_quantity(_structured_observation_value(record), record.get("unit"), target)
        converted_records[ref] = ({**record, "value": value, "unit": target}
                                  if record.get("modality") == "metric" and value is not None else record)
    expected = dict(atom.get("expected") or {})
    measurements = dict(atom.get("measurements") or {})
    unit = str(expected.get("unit") or "")
    suffix = re.fullmatch(r"(.+?)\s*\((mean|avg|sum|peak|max|min)\)", unit, re.I)
    base, operator = suffix.groups() if suffix else (unit, None)
    if _mission_quantity(1, base, target) is not None:
        expected["unit"] = str(target) + ("(" + operator + ")" if operator else "")
        if measurements.get("current") is not None:
            measurements["current"] = _mission_quantity(measurements["current"], base, target)
            if measurements["current"] is None:
                return None
    atom = {**atom, "expected": expected, "measurements": measurements}
    return _aggregate_observation({**requirement, "atom": atom}, evidence, converted_records)


def _mission_requirement_observation(requirement, evidence, records):
    """Ground snapshot, complete aggregation, or resource-event evidence contracts."""
    atom = _requirement_atom(requirement)
    if atom.get("modality") == "metric":
        atom = {**atom, "signal": _canonical_metric_name(atom.get("signal")),
                "query": {**(atom.get("query") or {}), "signal": _canonical_metric_name(
                    (atom.get("query") or {}).get("signal") or atom.get("signal"))}}
        requirement = {**requirement, "atom": atom}
        evidence = {**evidence, "signal": _canonical_metric_name(evidence.get("signal"))}
    binding = atom.get("observation_binding")
    if binding is not None and (not isinstance(binding, dict) or binding.get("mode") != "snapshot"):
        return None
    if "resource_event_binding" in atom or "aggregation" in atom:
        aggregate = (_resource_event_observation(requirement, evidence, records)
                     if "resource_event_binding" in atom else _mission_aggregate_observation(requirement, evidence, records))
        if aggregate is None:
            return None
        scalar = "aggregation" in atom or (binding is not None and binding.get("value") is not None)
        observation = {**aggregate, "observed_at": atom.get("observed_at") or (atom.get("time_range") or {}).get("end"),
                       "stat": (atom.get("aggregation") or {}).get("operator"), "scalar": True}
        if binding is not None:
            if not _mission_snapshot_matches(binding, observation, scalar=scalar):
                return None
        # Legacy resource collections are non-scalar citations; their count is computed.
        if not scalar and evidence.get("value") is None and not evidence.get("unit"):
            if evidence.get("observed_at") is not None and not any(
                    _mission_instant(evidence["observed_at"], records.get(str(ref), {}).get("observed_at"))
                    for ref in evidence.get("record_refs") or []):
                return None
            return observation
        if not _mission_citation_matches(evidence, observation, scalar=scalar,
                                         time_required=binding is not None):
            return None
        return observation

    expected_entities = _declared_entities(requirement)
    expected_signal = str((atom.get("query") or {}).get("signal") or atom.get("signal") or "")
    if binding is not None and (not expected_entities or (
            not expected_signal and not (atom.get("modality") == "event" and any(_requirement_event_binding(atom))))):
        return None
    for ref in evidence.get("record_refs") or []:
        record = records.get(str(ref))
        if record is None or not _mission_identity(evidence, record):
            continue
        if (atom.get("modality") != record.get("modality")
                or not expected_entities.issubset(set(evidence.get("entity_ids") or []))):
            continue
        event_binding = atom.get("modality") == "event" and any(_requirement_event_binding(atom))
        if event_binding:
            if not _requirement_event_matches(requirement, evidence, {str(ref): record}):
                continue
        elif expected_signal and _canonical_metric_name(expected_signal) not in _mission_signals(record):
            matched = False
            for pattern in requirement.get("acceptable_signal_patterns") or []:
                try:
                    matched |= any(bool(re.search(str(pattern), name)) for name in _mission_signals(record))
                except re.error:
                    pass
            if not matched:
                continue
        value = _structured_observation_value(record)
        scalar = _mission_record_scalar(record)
        observation = {**record, "value": value if scalar else record.get("value"), "scalar": scalar}
        if binding is not None:
            if not _mission_snapshot_matches(binding, observation, scalar=scalar):
                continue
            expected_aggregation = binding.get("aggregation")
            submitted_aggregation = (evidence.get("attributes") or {}).get("aggregation")
            if expected_aggregation and submitted_aggregation != expected_aggregation:
                continue
        else:
            window = atom.get("time_range") or {}
            if window.get("start") or window.get("end"):
                try:
                    start, end, instant = [datetime.fromisoformat(_normalized_datetime(item)) for item in
                                           (window.get("start"), window.get("end"), record.get("observed_at"))]
                    if any(item.tzinfo is None for item in (start, end, instant)) or not start <= instant <= end:
                        continue
                except (TypeError, ValueError):
                    continue
            elif atom.get("observed_at") and not _mission_instant(atom["observed_at"], record.get("observed_at")):
                continue
            allowed_refs = atom.get("acceptable_record_refs") or atom.get("record_refs")
            if allowed_refs and ref not in allowed_refs:
                continue
            measurement = atom.get("measurements") or {}
            expected_value = measurement.get("baseline") if measurement.get("window_role") == "baseline" else measurement.get("current")
            expected_unit = (atom.get("expected") or {}).get("unit")
            converted = _mission_quantity(value, record.get("unit"), expected_unit or record.get("unit"))
            if scalar and (converted is None or (expected_value is not None and not _equal_value(expected_value, converted))):
                continue
        if _mission_citation_matches(evidence, observation, scalar=scalar,
                                     time_required=binding is not None or record.get("observed_at") is not None):
            return observation
    return None


def _mission_snapshot_matches(binding, observation, *, scalar):
    # record_refs are canonical examples, not an allowlist: equivalent measurements
    # must have the same entity/signal (checked by the caller), stat and instant.
    if not _mission_instant(binding.get("observed_at"), observation.get("observed_at")):
        return False
    if binding.get("stat") and _mission_stat(binding["stat"]) != _mission_stat(observation.get("stat")):
        return False
    if binding.get("aggregation") and binding["aggregation"] != observation.get("aggregation"):
        return False
    if scalar:
        value = _mission_quantity(observation.get("value"), observation.get("unit"), binding.get("unit"))
        return value is not None and _mission_number(binding.get("value")) is not None and _equal_value(value, binding["value"])
    return ((binding.get("value") is None or (
        _mission_number(binding["value"]) is None and _equal_value(binding["value"], observation.get("value"))))
        and (not binding.get("unit") or _evidence_unit(binding["unit"]) == _evidence_unit(observation.get("unit"))))


def _mission_record_scalar(record):
    attributes = record.get("attributes") or {}
    event_value = attributes.get("current_value")
    return (record.get("modality") == "metric" or record.get("record_kind") == "aggregate"
            or bool(record.get("aggregate")) or attributes.get("record_kind") == "aggregate"
            or (record.get("modality") == "event" and _mission_number(event_value) is not None))


def _mission_evidence_grounded(evidence, records, requirements=()):
    for requirement in requirements:
        atom = _requirement_atom(requirement)
        if ("aggregation" in atom or "resource_event_binding" in atom) and _mission_requirement_observation(requirement, evidence, records) is not None:
            return True
    for ref in evidence.get("record_refs") or []:
        record = records.get(str(ref))
        if record is None or not _mission_identity(evidence, record):
            continue
        value = _structured_observation_value(record)
        scalar = _mission_record_scalar(record)
        observation = {**record, "value": value if scalar else record.get("value"), "scalar": scalar}
        if _mission_citation_matches(evidence, observation, scalar=scalar,
                                     time_required=record.get("observed_at") is not None):
            return True
    return False


def _mission_finding_supported(expected, actual, observations, evidence_by_id, records):
    claim = expected.get("claim") or expected
    if not _finding_match(expected, actual):
        return False
    bindings = list(claim.get("evidence") or [])
    primary = [item for item in bindings if item.get("role") == "primary"]
    bindings = primary or bindings
    atom_ids = {str(item.get("atom_id") or "") for item in bindings}
    bound = [req for req in observations if str(_requirement_atom(req).get("atom_id") or "") in atom_ids]
    if (not atom_ids or "" in atom_ids
            or atom_ids - {str(_requirement_atom(req).get("atom_id") or "") for req in bound}):
        return False
    cited = [evidence_by_id[str(item)] for item in actual.get("evidence_ids") or [] if str(item) in evidence_by_id]
    observed = []
    for req in bound:
        supported = [_mission_requirement_observation(req, ev, records) for ev in cited]
        supported = [item for item in supported if item is not None]
        if not supported:
            return False
        observed.extend(supported)
    criterion = claim.get("criterion") or {}
    if criterion.get("threshold_value") is not None:
        # The GT threshold is never substituted for the public measurement.
        for observation in observed:
            if not observation.get("scalar"):
                continue
            criterion_unit = criterion.get("unit")
            bucket = re.match(r"^(.+?)/(\d+\s*(?:ms|s|m|h|d))(?:\b|$)", str(criterion_unit or ""), re.I)
            if bucket and str(observation.get("aggregation") or "").lower().startswith(
                    re.sub(r"\s+", "", bucket.group(2)).lower() + " "):
                criterion_unit = bucket.group(1).strip()
            value = _mission_quantity(observation.get("value"), observation.get("unit"), criterion_unit)
            measured = {**criterion, "observed_value": value}
            if value is not None and _criterion_verdict(measured) == claim.get("verdict", "occurred"):
                return True
        return False
    return True


def _score_mission(gt: dict[str, Any], answer: dict[str, Any], records: dict[str, dict[str, Any]]) -> dict[str, Any]:
    gt, answer = _mission_plain(gt), _mission_plain(answer)
    records = _mission_records(records)
    required_findings = [item for item in gt.get("required_findings") or [] if item.get("required", True)]
    findings = list(answer.get("findings") or [])
    evidence = list(answer.get("evidence") or [])
    evidence_by_id = {str(item.get("evidence_id") or ""): item for item in evidence}
    observations = [item for item in gt.get("required_observations") or [] if item.get("required", True)]
    all_observations = [*(gt.get("required_observations") or []), *(gt.get("context_observations") or [])]
    known = [*required_findings, *(gt.get("optional_findings") or [])]
    canonical = gt.get("schema_version") == "mission_inspection_gt_v2"

    def labels_match(expected, actual):
        claim = expected.get("claim") or expected
        return (_finding_match(expected, actual)
                and (not canonical or claim.get("phenomenon") == actual.get("phenomenon")))

    finding_matches = sum(any(
        labels_match(req, finding)
        and _mission_finding_supported(req, finding, all_observations, evidence_by_id, records)
        for finding in findings) for req in required_findings)
    health = gt.get("health_verdict")
    negative_verdict = health == "no_actionable_finding" and not required_findings
    finding_score = finding_matches / len(required_findings) if required_findings else 1.0
    if ((health and answer.get("health_verdict") != health)
            or (health == "no_actionable_finding" and findings)
            or (health == "actionable_findings_present" and not findings)
            or (findings and answer.get("health_verdict") == "no_actionable_finding")):
        finding_score, finding_matches = 0.0, 0

    def observation_supported(requirement):
        atom = _requirement_atom(requirement)
        atom_id = str(atom.get("atom_id") or "")
        owners = [req for req in known if any(
            str(ev.get("atom_id") or "") == atom_id
            for ev in (req.get("claim") or req).get("evidence") or []
        )] if atom_id else []
        independent = negative_verdict or requirement.get("scoring_role", atom.get("scoring_role")) == "context_evidence"
        for evidence_id, item in evidence_by_id.items():
            if _mission_requirement_observation(requirement, item, records) is None:
                continue
            if independent or any(
                evidence_id in finding.get("evidence_ids", [])
                and (not owners or any(labels_match(owner, finding) for owner in owners))
                for finding in findings):
                return True
        return False

    observation_matches = sum(observation_supported(req) for req in observations)
    # Preserve the original entity-based scope contract, including declared resources.
    allowed_entities = set().union(*(_declared_entities(item) for item in [
        *(gt.get("required_observations") or []), *(gt.get("context_observations") or []), *required_findings,
    ]))
    allowed_entities.update(
        _requirement_atom(req)["resource_event_binding"]["resource_entity_id"] for req in observations
        if _requirement_atom(req).get("resource_event_binding", {}).get("resource_entity_id"))
    submitted_entities = {str(entity_id) for item in [*evidence, *findings] for entity_id in item.get("entity_ids") or []}
    out_of_scope = sorted(submitted_entities - allowed_entities) if allowed_entities else []
    scope_ok = float(not out_of_scope)
    unsupported = [str(item.get("evidence_id") or "") for item in evidence
                   if not _mission_evidence_grounded(item, records, all_observations)]
    grounded = len(evidence) - len(unsupported)
    return {
        "required_finding_recall": _dimension(finding_score, finding_matches, len(required_findings)),
        "required_observation_recall": _dimension(
            observation_matches / len(observations) if observations else 1.0, observation_matches, len(observations)),
        "scope_compliance": {**_dimension(scope_ok, scope_ok, 1), "out_of_scope_entity_ids": out_of_scope},
        "evidence_grounding": {
            **_dimension(grounded / len(evidence) if evidence else 0.0, grounded, len(evidence)),
            "unsupported_evidence_ids": unsupported,
        },
    }


def _expected_roots(gt: dict[str, Any]) -> list[dict[str, Any]]:
    outcome = gt.get("outcome") or {}
    roots = []
    for root in outcome.get("root_causes") or []:
        group = root.get("entity_group") or {}
        entity_ids = {str(item) for item in group.get("required_entity_ids") or []}
        entity_ids |= {str(item) for item in group.get("alias_entity_ids") or []}
        entity_ids |= {
            str(item.get("entity_id"))
            for item in group.get("entity_refs") or [] if item.get("entity_id")
        }
        roots.append({"fault_id": str(root.get("fault_id") or ""), "entity_ids": entity_ids})
    if not roots and outcome.get("expected_fault_id"):
        roots.append({
            "fault_id": str(outcome.get("expected_fault_id")),
            "entity_ids": {str(item) for item in outcome.get("target_entity_ids") or []},
        })
    return roots


def _root_match(expected: dict[str, Any], actual: dict[str, Any], *, require_fault: bool) -> bool:
    actual_entities = {str(item) for item in actual.get("entity_ids") or []}
    return (
        (not require_fault or expected["fault_id"] == str(actual.get("fault_id") or ""))
        and (not expected["entity_ids"] or bool(expected["entity_ids"].intersection(actual_entities)))
    )


def _step_match(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
    expected_entities = {str(item) for item in expected.get("target_entity_ids") or []}
    actual_entities = {str(item) for item in actual.get("entity_ids") or []}
    return (
        expected.get("step_type") == actual.get("step_type")
        and (not expected_entities or bool(expected_entities.intersection(actual_entities)))
    )


def _ordered_step_matches(expected: list[dict[str, Any]], actual: list[dict[str, Any]]) -> int:
    previous = [0] * (len(actual) + 1)
    for expected_step in expected:
        current = [0]
        for index, actual_step in enumerate(actual, start=1):
            current.append(
                previous[index - 1] + 1
                if _step_match(expected_step, actual_step)
                else max(previous[index], current[-1])
            )
        previous = current
    return previous[-1]


def _record_entities(record: dict[str, Any]) -> set[str]:
    entities = {str(item) for item in record.get("entity_ids") or []}
    if record.get("entity_id"):
        entities.add(str(record["entity_id"]))
    return entities


def _rca_step_match(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
    expected_entities = {str(item) for item in expected.get("target_entity_ids") or []}
    actual_entities = {str(item) for item in actual.get("entity_ids") or []}
    return (
        expected.get("step_type") == actual.get("step_type")
        and bool(expected_entities.intersection(actual_entities))
    )


def _rca_evidence_connected(
    evidence: dict[str, Any], structure: dict[str, Any], records: dict[str, dict[str, Any]]
) -> bool:
    resolved: set[str] = set()
    any_resolved = False
    for reference in evidence.get("record_refs") or []:
        record = records.get(str(reference))
        if record is None:
            continue
        any_resolved = True
        resolved |= _record_entities(record)
    structure_entities = {str(item) for item in structure.get("entity_ids") or []}
    if any_resolved:
        return bool(resolved.intersection(structure_entities))
    return False


def _score_rca(gt: dict[str, Any], answer: dict[str, Any], records: dict[str, dict[str, Any]]) -> dict[str, Any]:
    expected_roots = _expected_roots(gt)
    actual_roots = list(answer.get("root_causes") or [])
    entity_score, entity_matches = _set_f1(
        expected_roots, actual_roots, lambda left, right: _root_match(left, right, require_fault=False)
    )
    fault_score, fault_matches = _set_f1(
        expected_roots, actual_roots, lambda left, right: _root_match(left, right, require_fault=True)
    )
    expected_steps = [
        item for item in (gt.get("reasoning") or {}).get("steps") or []
        if item.get("required", True) and item.get("queryable", True)
    ]
    judgeable_steps = [item for item in expected_steps if item.get("target_entity_ids")]
    actual_steps = list(answer.get("causal_chain") or [])

    def ordered_hard_matches(expected, actual):
        previous = [0] * (len(actual) + 1)
        for expected_step in expected:
            current = [0]
            for index, actual_step in enumerate(actual, start=1):
                current.append(
                    previous[index - 1] + 1
                    if _rca_step_match(expected_step, actual_step)
                    else max(previous[index], current[-1])
                )
            previous = current
        return previous[-1]

    if judgeable_steps:
        step_matches = ordered_hard_matches(judgeable_steps, actual_steps)
        step_score = step_matches / len(judgeable_steps)
        step_total = len(judgeable_steps)
    else:
        step_matches = _ordered_step_matches(expected_steps, actual_steps)
        step_score = step_matches / len(expected_steps) if expected_steps else float(not actual_steps)
        step_total = len(expected_steps)
    completion = float(answer.get("status") == "completed")
    evidence = list(answer.get("evidence") or [])
    structures = [*actual_roots, *actual_steps]

    def cited_ids_of(structure: dict[str, Any]) -> set[str]:
        return {str(value) for value in structure.get("evidence_ids") or []}

    cited_evidence = [
        item for item in evidence
        if any(str(item.get("evidence_id") or "") in cited_ids_of(structure) for structure in structures)
    ]
    grounded_evidence = sum(
        any(
            str(item.get("evidence_id") or "") in cited_ids_of(structure)
            and _rca_evidence_connected(item, structure, records)
            for structure in structures
        )
        for item in cited_evidence
    )
    evidence_score = grounded_evidence / len(cited_evidence) if cited_evidence else 0.0
    connected_structures = sum(
        any(
            str(item.get("evidence_id") or "") in cited_ids_of(structure)
            and _rca_evidence_connected(item, structure, records)
            for item in evidence
        )
        for structure in structures
    )
    structure_score = connected_structures / len(structures) if structures else 0.0
    return {
        "root_entity_f1": _dimension(entity_score, entity_matches, len(expected_roots)),
        "fault_type_f1": _dimension(fault_score, fault_matches, len(expected_roots)),
        "causal_chain_recall": _dimension(step_score, step_matches, step_total),
        "completion": _dimension(completion, completion, 1),
        "evidence_grounding": _dimension(evidence_score, grounded_evidence, len(cited_evidence)),
        "claim_evidence_grounding": _dimension(structure_score, connected_structures, len(structures)),
    }


def _validate_answer_shape(task_type: str, answer_schema: str, answer: dict[str, Any]) -> None:
    expected_schema = {
        "metric_query": "metric_query",
        "event_query": "event_query",
        "anomaly_judgement": "anomaly_judgement_answer_v1",
        "anomaly_judgement_answer_v1": "anomaly_judgement_answer_v1",
        "mission_inspection_answer_v1": "mission_inspection_answer_v1",
        "rca_answer_v1": "rca_answer_v1",
    }.get(answer_schema, answer_schema)
    if answer.get("schema_version") != expected_schema:
        raise ValueError(
            f"schema_version must be {expected_schema!r}, got {answer.get('schema_version')!r}"
        )
    if task_type in {"l2", "l3"} and not isinstance(answer.get("time_range"), dict):
        raise ValueError("time_range object is required")
    if task_type == "l2":
        if answer_schema == "metric_ranking_answer_v1":
            if not isinstance(answer.get("ranked_results"), list):
                raise ValueError("metric ranking requires ranked_results")
            if not isinstance(answer.get("top_entity_ids"), list):
                raise ValueError("metric ranking requires top_entity_ids")
            if "top_minus_second" not in answer:
                raise ValueError("metric ranking requires top_minus_second")
        elif answer_schema == "graph_metric_join_answer_v1":
            if not isinstance(answer.get("root_entity"), dict):
                raise ValueError("graph metric join requires root_entity")
            for field in (
                "supporting_entities", "candidate_entities", "eligible_entities",
                "excluded_entity_ids", "supporting_relations", "measurements",
                "ranked_results",
            ):
                if not isinstance(answer.get(field), list):
                    raise ValueError(f"graph metric join requires {field}")
            if not answer.get("midpoint"):
                raise ValueError("graph metric join requires midpoint")
        elif answer_schema == "l2_observation_judgement_answer_v2":
            if not isinstance(answer.get("items"), list):
                raise ValueError("observation judgement requires items")
        elif answer_schema == "derived_metric_query_answer_v1":
            if not isinstance(answer.get("source_results"), list):
                raise ValueError("derived metric query requires source_results")
            if not isinstance(answer.get("entity_expectations"), list):
                raise ValueError("derived metric query requires entity_expectations")
            if not isinstance(answer.get("computation"), dict):
                raise ValueError("derived metric query requires computation")
        elif answer_schema == "relation_query":
            if not isinstance(answer.get("entities"), list):
                raise ValueError("relation_query requires entities")
            if not isinstance(answer.get("relations"), list):
                raise ValueError("relation_query requires relations")
        elif not isinstance(answer.get("results"), list):
            raise ValueError("results array is required")
        if answer_schema == "event_query" and not isinstance(answer.get("summary"), dict):
            raise ValueError("event_query requires summary")
        return
    if task_type == "l3":
        if not isinstance(answer.get("subject"), dict):
            raise ValueError("L3 subject object is required")
        if not str(answer.get("phenomenon") or ""):
            raise ValueError("L3 phenomenon is required")
        verdict = answer.get("verdict")
        if verdict not in {"occurred", "not_occurred", "insufficient_evidence"}:
            raise ValueError("invalid L3 verdict")
        if verdict != "insufficient_evidence" and (
            not isinstance(answer.get("criterion"), dict)
            or not isinstance(answer.get("evidence"), list)
            or not answer.get("evidence")
        ):
            raise ValueError("decided L3 answer requires criterion and evidence")
        if not str(answer.get("conclusion") or ""):
            raise ValueError("L3 conclusion is required")
        return
    if task_type == "mission":
        findings = answer.get("findings")
        evidence = answer.get("evidence")
        if not isinstance(findings, list) or not isinstance(evidence, list):
            raise ValueError("Mission findings and evidence arrays are required")
        known = {str(item.get("evidence_id") or "") for item in evidence}
        cited = {
            str(value)
            for finding in findings
            for value in finding.get("evidence_ids") or []
        }
        if cited - known:
            raise ValueError("Mission findings cite unknown evidence")
        if not str(answer.get("conclusion") or ""):
            raise ValueError("Mission conclusion is required")
        return
    if task_type == "rca":
        status = answer.get("status")
        roots = answer.get("root_causes")
        steps = answer.get("causal_chain")
        evidence = answer.get("evidence")
        if status not in {"completed", "insufficient_evidence"}:
            raise ValueError("invalid RCA status")
        if not all(isinstance(value, list) for value in (roots, steps, evidence)):
            raise ValueError("RCA root_causes, causal_chain, and evidence arrays are required")
        if status == "completed" and not roots:
            raise ValueError("completed RCA answer requires a root cause")
        known = {str(item.get("evidence_id") or "") for item in evidence}
        cited = {
            str(value)
            for item in [*roots, *steps]
            for value in item.get("evidence_ids") or []
        }
        if cited - known:
            raise ValueError("RCA claims cite unknown evidence")
        if not str(answer.get("conclusion") or ""):
            raise ValueError("RCA conclusion is required")


def _trace_rows(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for event in events:
        if event.get("operation") != "query" or event.get("status") != "success":
            continue
        response = event.get("response") or {}
        modality = response.get("modality", "")
        rows.extend(
            {**result, "_response_modality": modality}
            for result in response.get("results") or []
        )
    return rows


def _is_trace_record(record: dict[str, Any]) -> bool:
    return str(record.get("modality") or "") == "trace"


def _uses_scene_trace_rows(task_type: str, answer_schema: str) -> bool:
    return task_type == "l2" and answer_schema == "trace_query"


def _trace_rows_from_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for record in records:
        if not _is_trace_record(record):
            continue
        attributes = record.get("attributes") or {}
        rows.append({
            **attributes,
            "trace_id": str(attributes.get("trace_id") or ""),
            "span_id": str(attributes.get("span_id") or ""),
            "parent_span_id": str(attributes.get("parent_span_id") or ""),
            "service_name": str(attributes.get("service_name") or ""),
            "operation_name": str(attributes.get("operation_name") or ""),
            "start_time": record.get("time_start") or attributes.get("start_time"),
            "end_time": record.get("time_end") or attributes.get("end_time"),
            "value": record.get("value"),
            "unit": record.get("unit"),
            "attributes": attributes,
            "_response_modality": "trace",
        })
    return rows


def _score_l2_trace_policy(
    gt: dict[str, Any], answer: dict[str, Any], policy: dict[str, Any],
    trace_events: list[dict[str, Any]],
) -> dict[str, Any]:
    if not policy:
        return {}
    evaluation = gt.get("candidate_evaluation") or {}
    expected_ids = {
        str(item)
        for item in (
            evaluation.get("candidate_entity_ids")
            or evaluation.get("eligible_entity_ids")
            or []
        )
    }
    rows = _trace_rows(trace_events)
    dimensions = {}
    if policy.get("require_query_budget"):
        maximum = int(policy.get("max_successful_query_count") or 0)
        query_kinds = {
            str(item) for item in policy.get("query_kinds") or [] if str(item)
        }
        actual_queries = sum(
            event.get("operation") == "query"
            and event.get("status") == "success"
            and (
                not query_kinds
                or str(((event.get("request") or {}).get("query") or {}).get("kind") or "")
                in query_kinds
            )
            for event in trace_events
        )
        budget_ok = maximum > 0 and actual_queries <= maximum
        budget_score = (
            1.0 if budget_ok else maximum / actual_queries
            if maximum > 0 and actual_queries else 0.0
        )
        dimensions["query_budget_compliance"] = _dimension(
            budget_score, min(actual_queries, maximum) if maximum > 0 else 0,
            actual_queries if actual_queries else 1,
        )
    if policy.get("require_trace_result_order"):
        expected_order = [
            (str(item.get("result_name") or ""), str(item.get("span_id") or ""))
            for item in gt.get("results") or []
        ]
        actual_order = [
            (str(item.get("result_name") or ""), str(item.get("span_id") or ""))
            for item in answer.get("results") or []
        ]
        ok = float(expected_order == actual_order)
        dimensions["trace_result_ordering"] = _dimension(ok, ok, 1)
    if policy.get("require_complete_candidate_evaluation"):
        expected_rows = gt.get("results") or gt.get("ranked_results") or [{}]
        expected_signal = _canonical_metric_name(
            expected_rows[0].get("metric_name")
        )
        returned_ids = {
            str(row.get("entity_id") or "") for row in rows
            if _canonical_metric_name(
                row.get("metric_name") or row.get("signal")
            ) == expected_signal
        }
        matched = len(expected_ids.intersection(returned_ids))
        dimensions["candidate_scope_completeness"] = _dimension(
            matched / len(expected_ids) if expected_ids else 0.0,
            matched, len(expected_ids),
        )
        if policy.get("require_coverage_report"):
            actual = (answer.get("candidate_evaluation") or {}).get(
                "evaluated_entity_count"
            )
            ok = float(actual == len(expected_ids))
            dimensions["candidate_coverage_reporting"] = _dimension(ok, ok, 1)
    if policy.get("require_complete_candidate_signal_matrix"):
        signals = {
            _canonical_metric_name(item)
            for item in evaluation.get("required_signal_names") or []
            if str(item)
        }
        declared_pairs = evaluation.get("required_entity_signal_pairs") or []
        expected_pairs = (
            {
                (str(item.get("entity_id") or ""), _canonical_metric_name(item.get("metric_name")))
                for item in declared_pairs
                if isinstance(item, dict) and item.get("entity_id") and item.get("metric_name")
            }
            if declared_pairs
            else {
                (entity_id, signal)
                for entity_id in {str(item) for item in evaluation.get("eligible_entity_ids") or expected_ids}
                for signal in signals
            }
        )
        returned_pairs = {
            (str(row.get("entity_id") or ""),
             _canonical_metric_name(row.get("metric_name") or row.get("signal")))
            for row in rows
        }
        matched_pairs = expected_pairs.intersection(returned_pairs)
        dimensions["candidate_signal_matrix_completeness"] = _dimension(
            len(matched_pairs) / len(expected_pairs) if expected_pairs else 0.0,
            len(matched_pairs), len(expected_pairs),
        )
        if policy.get("require_candidate_timestamp_alignment"):
            minimum = int(evaluation.get("minimum_aligned_timestamp_count") or 1)
            timestamps = {}
            for row in rows:
                key = (str(row.get("entity_id") or ""),
                       _canonical_metric_name(row.get("metric_name") or row.get("signal")))
                if key not in expected_pairs:
                    continue
                values = timestamps.setdefault(key, set())
                values.update(
                    str(point.get("timestamp")) for point in row.get("points") or []
                    if point.get("timestamp")
                )
                if row.get("observed_at"):
                    values.add(str(row["observed_at"]))
            aligned = 0
            alignment_ids = {
                str(item)
                for item in evaluation.get("eligible_entity_ids") or expected_ids
            }
            for entity_id in alignment_ids:
                groups = [timestamps.get((entity_id, signal), set()) for signal in signals]
                shared = len(set.intersection(*groups)) if groups and all(groups) else 0
                aligned += int(shared >= minimum)
            dimensions["candidate_timestamp_alignment"] = _dimension(
                aligned / len(alignment_ids) if alignment_ids else 0.0,
                aligned, len(alignment_ids),
            )
    if policy.get("require_topology_inventory_grounding"):
        expected_edges = {
            (
                str(item.get("source_entity_id") or ""),
                str(item.get("target_entity_id") or ""),
                str(item.get("relation_type") or ""),
            )
            for item in evaluation.get("required_topology_edges") or []
            if isinstance(item, dict)
        }
        returned_edges = {
            (
                str(row.get("source_entity_id") or ""),
                str(row.get("target_entity_id") or ""),
                str(row.get("relation_type") or ""),
            )
            for row in rows
            if row.get("_response_modality") == "topology"
            and row.get("source_entity_id") and row.get("target_entity_id")
        }
        matched_edges = expected_edges.intersection(returned_edges)
        dimensions["topology_inventory_grounding"] = _dimension(
            len(matched_edges) / len(expected_edges) if expected_edges else 0.0,
            len(matched_edges), len(expected_edges),
        )
    if policy.get("require_cross_modal_topology_evidence"):
        metric_requirements = [
            item for item in evaluation.get("required_metric_evidence") or []
            if isinstance(item, dict)
        ]
        matched_metric = 0
        for requirement in metric_requirements:
            entity_id = str(requirement.get("entity_id") or "")
            metric_name = _canonical_metric_name(requirement.get("metric_name"))
            minimum = int(requirement.get("minimum_point_count") or 1)
            required_stat = str(requirement.get("stat") or "")
            supported = False
            for row in rows:
                if row.get("_response_modality") != "metric":
                    continue
                if str(row.get("entity_id") or "") != entity_id:
                    continue
                if _canonical_metric_name(row.get("metric_name") or row.get("signal")) != metric_name:
                    continue
                if required_stat and str(row.get("stat") or "") != required_stat:
                    continue
                timestamps = {
                    str(point.get("timestamp")) for point in row.get("points") or []
                    if point.get("timestamp")
                }
                if row.get("observed_at"):
                    timestamps.add(str(row["observed_at"]))
                if len(timestamps) >= minimum:
                    supported = True
                    break
            matched_metric += int(supported)
        dimensions["topology_metric_evidence_completeness"] = _dimension(
            matched_metric / len(metric_requirements) if metric_requirements else 0.0,
            matched_metric, len(metric_requirements),
        )

        event_requirements = [
            item for item in evaluation.get("required_event_evidence") or []
            if isinstance(item, dict)
        ]
        matched_event = 0
        for requirement in event_requirements:
            entity_id = str(requirement.get("entity_id") or "")
            event_type = str(requirement.get("event_type") or "")
            status = str(requirement.get("status") or "")
            attributes = requirement.get("attributes") or {}
            supported = any(
                row.get("_response_modality") == "event"
                and entity_id in {str(item) for item in row.get("entity_ids") or []}
                and str(row.get("event_type") or "") == event_type
                and (not status or str(row.get("status") or "") == status)
                and all((row.get("attributes") or {}).get(key) == value for key, value in attributes.items())
                for row in rows
            )
            matched_event += int(supported)
        dimensions["topology_event_evidence_completeness"] = _dimension(
            matched_event / len(event_requirements) if event_requirements else 0.0,
            matched_event, len(event_requirements),
        )
    if policy.get("require_complete_candidate_trace_evaluation"):
        expected_trace_ids = {
            str(item) for item in evaluation.get("candidate_trace_ids") or []
            if str(item)
        }
        returned_trace_ids = {
            str(row.get("trace_id") or "") for row in rows
            if row.get("_response_modality") == "trace" and row.get("trace_id")
        }
        matched_trace_ids = expected_trace_ids.intersection(returned_trace_ids)
        dimensions["candidate_trace_completeness"] = _dimension(
            len(matched_trace_ids) / len(expected_trace_ids)
            if expected_trace_ids else 0.0,
            len(matched_trace_ids), len(expected_trace_ids),
        )
    if policy.get("require_complete_target_trace"):
        target_trace_id = str(evaluation.get("target_trace_id") or "")
        expected_span_ids = {
            str(item) for item in evaluation.get("target_trace_span_ids") or []
            if str(item)
        }
        returned_span_ids = {
            str(row.get("span_id") or "") for row in rows
            if row.get("_response_modality") == "trace"
            and str(row.get("trace_id") or "") == target_trace_id
            and row.get("span_id")
        }
        matched_span_ids = expected_span_ids.intersection(returned_span_ids)
        dimensions["target_trace_completeness"] = _dimension(
            len(matched_span_ids) / len(expected_span_ids)
            if expected_span_ids else 0.0,
            len(matched_span_ids), len(expected_span_ids),
        )
    return dimensions


def score(
    envelope: dict[str, Any], answer: dict[str, Any], records: list[dict[str, Any]],
    trace_events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    task_type = str(envelope.get("task_type") or "")
    answer_schema = str(envelope.get("answer_schema") or "")
    gt = envelope.get("ground_truth") or {}
    _validate_answer_shape(task_type, answer_schema, answer)
    record_index = _record_map(records)
    record_index = _merge_trace_records(record_index, trace_events or [])
    if task_type == "l2" and answer_schema == "event_query":
        dimensions = _score_l2_event(gt, answer)
    elif _uses_scene_trace_rows(task_type, answer_schema):
        trace_rows = [
            *_trace_rows(trace_events or []),
            *_trace_rows_from_records(records),
        ]
        dimensions = _score_l2_trace(gt, answer, trace_rows)
    elif task_type == "l2" and answer_schema == "relation_query":
        dimensions = _score_l2_relation(gt, answer)
    elif task_type == "l2" and answer_schema == "metric_ranking_answer_v1":
        dimensions = _score_l2_metric_ranking(gt, answer)
    elif task_type == "l2" and answer_schema == "graph_metric_join_answer_v1":
        dimensions = _score_l2_graph_metric_join(gt, answer)
    elif task_type == "l2" and answer_schema == "l2_observation_judgement_answer_v2":
        dimensions = _score_l2_observation_judgement(gt, answer)
    elif task_type == "l2" and answer_schema == "derived_metric_query_answer_v1":
        dimensions = _score_l2_derived_metric(gt, answer)
    elif task_type == "l2":
        dimensions = _score_l2_metric(gt, answer)
    elif task_type == "l3":
        dimensions = (
            _score_l3_v2(gt, answer, record_index)
            if str(gt.get("schema_version") or "") == "anomaly_judgement_v2"
            else _score_l3(gt, answer, record_index)
        )
    elif task_type == "mission":
        dimensions = _score_mission(gt, answer, _mission_records(records, trace_events or []))
    elif task_type == "rca":
        dimensions = _score_rca(gt, answer, record_index)
    else:
        raise ValueError(f"unsupported task type: {task_type}")
    if task_type == "l2":
        dimensions.update(_score_l2_trace_policy(
            gt, answer, envelope.get("scoring_policy") or {}, trace_events or []
        ))
    reward_dimensions = [
        item for name, item in dimensions.items()
        if task_type != "rca" or name != "completion"
    ]
    scores = [float(item["score"]) for item in reward_dimensions]
    if task_type == "l2" and answer_schema == "graph_metric_join_answer_v1":
        weights = {
            "scope_graph_correctness": 0.25,
            "source_measurement_correctness": 0.25,
            "derived_computation": 0.30,
            "ranking_completeness": 0.20,
        }
        reward = sum(
            float(dimensions[name]["score"]) * weight
            for name, weight in weights.items()
        )
    elif task_type == "l2" and answer_schema == "l2_observation_judgement_answer_v2":
        item_scores = [
            float(item["score"]) for name, item in dimensions.items()
            if name.startswith("item_")
        ]
        reward = sum(item_scores) / len(item_scores) if item_scores else 0.0
    else:
        reward = sum(scores) / len(scores) if scores else 0.0
    strict_pass = float(bool(scores) and all(value == 1.0 for value in scores))
    return {
        "schema_version": "harbor_multitask_score_v1",
        "task_type": task_type,
        "answer_schema": answer_schema,
        "dimensions": dimensions,
        "pass": strict_pass,
        "reward": reward,
    }


def _iter_jsonl(path: Path) -> Iterator[Any]:
    with path.open(encoding="utf-8") as handle:
        for chunk in handle:
            for line in chunk.splitlines():
                if line.strip():
                    yield json.loads(line)


def _reference_keys(*values: Any) -> set[str]:
    keys: set[str] = set()
    stack = list(values)
    while stack:
        value = stack.pop()
        if isinstance(value, dict):
            keys.update(str(key) for key in value)
            stack.extend(value.values())
        elif isinstance(value, list):
            keys.update(str(item) for item in value)
            stack.extend(value)
        elif isinstance(value, str):
            keys.add(value)
            keys.update(value)
        else:
            keys.add(str(value))
    return keys


def _trace_record_refs(trace_events: list[Any]) -> set[str]:
    refs: set[str] = set()
    stack = list(trace_events)
    while stack:
        value = stack.pop()
        if isinstance(value, dict):
            if "record_ref" in value:
                refs.add(str(value.get("record_ref") or ""))
            stack.extend(value.values())
        elif isinstance(value, list):
            stack.extend(value)
    return refs


def _load_scene_records(
    path: Path, envelope: dict[str, Any], answer: Any, trace_events: list[Any]
) -> list[Any]:
    if not path.exists():
        return []
    # Scoring reads records only via evidence/trace ref lookups and L2 trace scans.
    needed = _reference_keys(answer, envelope) | _trace_record_refs(trace_events)
    keep_trace = _uses_scene_trace_rows(
        str(envelope.get("task_type") or ""), str(envelope.get("answer_schema") or "")
    )
    return [
        record for record in _iter_jsonl(path)
        if not isinstance(record, dict)
        or _record_key(record) in needed
        or (keep_trace and _is_trace_record(record))
    ]


def main() -> int:
    gt_path = Path(os.environ.get("GT_PATH", "/tests/ground_truth.json"))
    answer_path = Path(os.environ.get("ANSWER_PATH", "/logs/agent/answer.json"))
    records_path = Path(os.environ.get("SCENE_RECORDS_PATH", "/tests/scene-records.jsonl"))
    trace_path = Path(
        os.environ.get("QUERY_TRACE_PATH")
        or os.environ.get("UMODEL_TRACE_PATH")
        or "/shared/umodel-trace.jsonl"
    )
    verifier_dir = Path(os.environ.get("VERIFIER_DIR", "/logs/verifier"))
    envelope = json.loads(gt_path.read_text(encoding="utf-8"))
    answer = json.loads(answer_path.read_text(encoding="utf-8")) if answer_path.exists() else {}
    trace_events = list(_iter_jsonl(trace_path)) if trace_path.exists() else []
    records = _load_scene_records(records_path, envelope, answer, trace_events)
    try:
        payload = score(envelope, answer, records, trace_events)
        rewards = {
            name: float(value["score"])
            for name, value in payload["dimensions"].items()
        }
        rewards.update({
            "protocol_valid": 1.0,
            "pass": float(payload["pass"]),
            "reward": float(payload["reward"]),
        })
    except (KeyError, TypeError, ValueError) as error:
        payload = {
            "schema_version": "harbor_multitask_score_v1",
            "task_type": envelope.get("task_type", ""),
            "answer_schema": envelope.get("answer_schema", ""),
            "dimensions": {},
            "pass": 0.0,
            "reward": 0.0,
            "error": str(error),
        }
        rewards = {"protocol_valid": 0.0, "pass": 0.0, "reward": 0.0}
    verifier_dir.mkdir(parents=True, exist_ok=True)
    (verifier_dir / "reward.json").write_text(
        json.dumps(rewards, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )
    (verifier_dir / "details.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(json.dumps(rewards, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
