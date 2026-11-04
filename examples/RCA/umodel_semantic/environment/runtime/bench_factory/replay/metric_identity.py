"""Canonical metric identity shared by capture and Replay queries.

The public benchmark contract uses provider-facing metric names, while the
exporter stores a compact provider-neutral vocabulary.  Keeping the mapping in
one place prevents capture and query from disagreeing about the same series.
"""

from __future__ import annotations


PUBLIC_TO_CANONICAL_METRIC = {
    "kube_deployment_status_replicas_ready": "deployment_ready_replicas",
    "kube_deployment_status_replicas_available": "deployment_available_replicas",
    "kube_deployment_status_replicas_unavailable": "deployment_unavailable_replicas",
    "kube_deployment_spec_replicas": "deployment_desired_replicas",
}


def canonical_metric_name(metric_name: str) -> str:
    """Return the storage/query identity for a public or canonical name."""

    return PUBLIC_TO_CANONICAL_METRIC.get(metric_name, metric_name)
