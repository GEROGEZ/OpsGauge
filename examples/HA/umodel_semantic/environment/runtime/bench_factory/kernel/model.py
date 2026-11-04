from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class EvidenceNode:
    node_id: str
    node_type: str
    epistemic_status: str
    provenance_ref: str
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EvidenceEdge:
    edge_id: str
    source: str
    relation: str
    target: str
    epistemic_status: str
    provenance_ref: str


@dataclass(frozen=True)
class Lineage:
    lineage_id: str
    parent_case_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class EpisodeIR:
    episode_id: str
    domain: str
    episode_type: str
    ontology_version: str
    lineage: Lineage
    nodes: tuple[EvidenceNode, ...]
    edges: tuple[EvidenceEdge, ...]
    environment_ref: str | None = None
