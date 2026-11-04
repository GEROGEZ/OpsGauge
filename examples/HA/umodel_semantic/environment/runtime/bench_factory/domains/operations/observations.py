"""Task-neutral queryable facts materialized from checked episode evidence."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import re
import tempfile
from typing import Any, Literal

from pydantic import Field, model_validator

from ...construction.models import NonEmptyText, SourceReference, _ProtocolModel
from ...kernel.model import EpisodeIR
from .rca import RcaEntityRef, RcaProjectionInput


SupportedModality = Literal[
    "metric", "log", "trace", "event", "change", "topology"
]
MetricStatistic = Literal["avg", "max", "min", "sum", "count", "latest", "identity"]

_SCOPE_ALIASES = {
    "alikafka": "kafka",
    "kvstore": "redis",
}


def infer_metric_statistic(unit: str) -> MetricStatistic:
    """Recover the reducer of a checked scalar from legacy GT annotations."""

    text = str(unit or "").replace("**", "").strip().lower()
    if re.search(r"(?:exact\s+)?point(?:\s+at)?|sampled\s+at|首个非零采样点", text):
        return "latest"
    if re.search(r"\(\s*peak\b|in-window\s+instant\s+peak|窗口峰值", text):
        return "max"
    if re.search(r"\(\s*sum\b|current(?:-window|\s+window)?\s+sum\b", text):
        return "sum"
    if re.search(
        r"^mean\s+|\(\s*(?:mean|avg)\b|mean\s+(?:of\s+current_window|per\s+replica)",
        text,
    ):
        return "avg"
    return "latest"


def canonical_metric_unit(unit: str) -> str:
    """Return the physical unit without baseline/window explanation prose."""

    text = str(unit or "").replace("**", "").strip()
    if not text:
        return ""
    head = re.split(
        r"\s*[（(]|;|\s+(?=(?:exact\s+)?point\b|sampled\s+at\b|native\b|"
        r"baseline\b|at\s+\d{1,2}:\d{2})",
        text,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0].strip()
    aliases = {
        "percent": "%",
        "percentage": "%",
        "seconds": "s",
        "second": "s",
        "microsecond": "us",
        "microseconds": "us",
        "restarts": "count",
        "replica": "count",
        "replicas": "count",
    }
    mean_unit = re.fullmatch(r"mean\s+([A-Za-z%]+)(?:/window)?", head, re.I)
    if mean_unit:
        head = mean_unit.group(1)
    head = re.sub(
        r"/\d+\s*(?:ms|s|m|h|d)(?:[-_ ]?sliding)?$",
        "",
        head,
        flags=re.IGNORECASE,
    ).strip()
    return aliases.get(head.lower(), head)


def metric_aggregation_description(stat: str, unit: str) -> str:
    """Expose sampling granularity only when legacy evidence retained it."""

    match = re.search(
        r"/(\d+\s*(?:ms|s|m|h|d))(?P<sliding>[-_ ]?sliding)?",
        str(unit or ""),
        re.IGNORECASE,
    )
    if match is None:
        return ""
    period = re.sub(r"\s+", "", match.group(1)).lower()
    sampling = "sliding samples" if match.group("sliding") else "samples"
    return f"{period} {sampling}; {str(stat or 'identity').lower()}"


class ObservationEntity(_ProtocolModel):
    entity_id: NonEmptyText
    entity_name: NonEmptyText
    entity_domain: NonEmptyText
    entity_type: NonEmptyText


class ObservationQuery(_ProtocolModel):
    backend: Literal["o11y", "brise", "umodel", "unknown"]
    command: NonEmptyText
    signal: NonEmptyText


class ObservationAtom(_ProtocolModel):
    """One independently queryable fact before it becomes any task Case."""

    schema_version: Literal["observation_atom_v1"] = "observation_atom_v1"
    atom_id: NonEmptyText
    episode_id: NonEmptyText
    source_step: int = Field(ge=1)
    scope: NonEmptyText
    modality: SupportedModality
    distribution_label: NonEmptyText
    entities: tuple[ObservationEntity, ...]
    time_range: dict[str, NonEmptyText]
    query: ObservationQuery
    observed_at: NonEmptyText
    expected: dict[str, Any]
    measurements: dict[str, Any] = Field(default_factory=dict)
    evidence_status: Literal["checked_projection"] = "checked_projection"
    source_refs: tuple[SourceReference, ...]

    @model_validator(mode="after")
    def validate_identity(self) -> "ObservationAtom":
        if self.distribution_label != f"{self.scope}.{self.modality}":
            raise ValueError("distribution_label must equal scope.modality")
        if set(self.time_range) != {"start", "end"}:
            raise ValueError("time_range must contain exactly start and end")
        if not self.entities:
            raise ValueError("an observation atom requires a resolved entity")
        return self


class RejectedObservation(_ProtocolModel):
    source_step: int = Field(ge=1)
    signal: NonEmptyText
    reason: NonEmptyText


class ObservationInventory(_ProtocolModel):
    schema_version: Literal["observation_inventory_v1"] = "observation_inventory_v1"
    episode_id: NonEmptyText
    workspace: NonEmptyText
    region_id: NonEmptyText
    atoms: tuple[ObservationAtom, ...]
    context_atom_ids: tuple[NonEmptyText, ...] = ()
    rejected: tuple[RejectedObservation, ...] = ()
    source_refs: tuple[SourceReference, ...]

    @model_validator(mode="after")
    def validate_context_atoms(self) -> "ObservationInventory":
        known = {atom.atom_id for atom in self.atoms}
        if len(self.context_atom_ids) != len(set(self.context_atom_ids)):
            raise ValueError("context_atom_ids must be unique")
        if not set(self.context_atom_ids).issubset(known):
            raise ValueError("context_atom_ids must reference inventory atoms")
        return self


class RcaProjectionObservationAdapter:
    """Adapt checked projection facts without leaking RCA semantics downstream."""

    supported_modalities = {
        "metric", "log", "trace", "event", "change", "topology"
    }

    def compile(
        self, episode: EpisodeIR, projection: RcaProjectionInput
    ) -> ObservationInventory:
        entities = self._entity_index(episode, projection)
        atoms: list[ObservationAtom] = []
        context_atom_ids: list[str] = []
        rejected: list[RejectedObservation] = []

        steps = (
            *((step, False) for step in projection.reasoning_steps),
            *((step, True) for step in projection.context_steps),
        )
        for step, is_context in steps:
            if not step.queryable:
                continue
            source_step = step.source_step if is_context else step.step
            resolved = tuple(
                entities[entity_id]
                for entity_id in step.target_entity_ids
                if entity_id in entities
            )
            for position, observation in enumerate(step.observations, start=1):
                modality = observation.source_type.strip().lower()
                reason = self._rejection_reason(
                    modality, resolved, step.target_entity_ids
                )
                if reason:
                    rejected.append(RejectedObservation(
                        source_step=source_step,
                        signal=observation.signal,
                        reason=reason,
                    ))
                    continue

                entity = resolved[0]
                if modality == "topology":
                    source_entity_id = str(
                        observation.evidence.get("source_entity_id") or ""
                    )
                    entity = next(
                        (
                            item for item in resolved
                            if item.entity_id == source_entity_id
                        ),
                        entity,
                    )
                scope = self._scope(entity)
                measurements = {
                    **observation.evidence,
                    **(
                        {"baseline": observation.baseline}
                        if observation.baseline is not None else {}
                    ),
                    **(
                        {"current": observation.current}
                        if observation.current is not None else {}
                    ),
                    **(
                        {"ratio": observation.ratio}
                        if observation.ratio is not None else {}
                    ),
                }
                atom_id = self._atom_id(
                    episode.episode_id,
                    source_step,
                    position,
                    observation.signal,
                    context=is_context,
                )
                atoms.append(ObservationAtom(
                    atom_id=atom_id,
                    episode_id=episode.episode_id,
                    source_step=source_step,
                    scope=scope,
                    modality=modality,
                    distribution_label=f"{scope}.{modality}",
                    entities=resolved,
                    time_range=step.time_window,
                    query=ObservationQuery(
                        backend=self._backend(observation.source),
                        command=observation.source,
                        signal=observation.signal,
                    ),
                    observed_at=observation.observed_at,
                    expected=observation.expected.model_dump(mode="json"),
                    measurements=measurements,
                    source_refs=(
                        SourceReference(role="evidence", ref=observation.evidence_ref),
                        SourceReference(
                            role="projection",
                            ref=(
                                f"{projection.projection_id}#context-{step.context_id}"
                                if is_context else
                                f"{projection.projection_id}#reasoning-step-{step.step}"
                            ),
                        ),
                    ),
                ))
                if is_context:
                    context_atom_ids.append(atom_id)

        return ObservationInventory(
            episode_id=episode.episode_id,
            workspace=projection.workspace,
            region_id=projection.region_id,
            atoms=tuple(atoms),
            context_atom_ids=tuple(context_atom_ids),
            rejected=tuple(rejected),
            source_refs=(
                SourceReference(role="episode", ref=episode.lineage.lineage_id),
                *projection.source_refs,
            ),
        )

    def _rejection_reason(
        self,
        modality: str,
        resolved: tuple[ObservationEntity, ...],
        requested_entity_ids: tuple[str, ...],
    ) -> str | None:
        if modality not in self.supported_modalities:
            return f"unsupported L2 modality: {modality}"
        if modality == "topology" and len(requested_entity_ids) < 2:
            return "topology observations require at least two target entities"
        if modality != "topology" and len(requested_entity_ids) != 1:
            return "non-topology L2 observations require exactly one target entity"
        if len(resolved) != len(requested_entity_ids):
            return "target entity was not resolved from checked evidence"
        return None

    @staticmethod
    def _entity_index(
        episode: EpisodeIR, projection: RcaProjectionInput
    ) -> dict[str, ObservationEntity]:
        index: dict[str, ObservationEntity] = {}
        for node in episode.nodes:
            if node.node_type != "OperationalEntity":
                continue
            properties = node.properties
            required = ("entity_id", "entity_name", "entity_domain", "entity_type")
            if all(properties.get(key) for key in required):
                entity = ObservationEntity(
                    **{key: properties[key] for key in required}
                )
                index[entity.entity_id] = entity
        for root in projection.root_causes:
            for raw in root.entities:
                entity = RcaProjectionObservationAdapter._from_rca_entity(raw)
                index[entity.entity_id] = entity
        for raw in projection.resolved_entities:
            entity = RcaProjectionObservationAdapter._from_rca_entity(raw)
            index[entity.entity_id] = entity
        return index

    @staticmethod
    def _from_rca_entity(entity: RcaEntityRef) -> ObservationEntity:
        return ObservationEntity(
            entity_id=entity.entity_id,
            entity_name=entity.entity_name,
            entity_domain=entity.entity_domain,
            entity_type=entity.entity_type,
        )

    @staticmethod
    def _scope(entity: ObservationEntity) -> str:
        domain = entity.entity_domain.lower()
        if domain == "apm":
            return "application"
        if domain == "k8s":
            return "k8s"
        parts = entity.entity_type.lower().split(".")
        if domain == "acs" and len(parts) >= 2:
            return _SCOPE_ALIASES.get(parts[1], parts[1])
        return domain

    @staticmethod
    def _backend(source: str) -> str:
        lowered = source.lstrip().lower()
        if lowered.startswith("o11y"):
            return "o11y"
        if lowered.startswith("brise"):
            return "brise"
        if lowered.startswith("umodel"):
            return "umodel"
        return "unknown"

    @staticmethod
    def _atom_id(
        episode_id: str,
        step: int,
        position: int,
        signal: str,
        *,
        context: bool = False,
    ) -> str:
        prefix = "context:" if context else ""
        raw = f"{prefix}{episode_id}:{step}:{position}:{signal}"
        return f"obs-{sha256(raw.encode('utf-8')).hexdigest()[:16]}"


def write_observation_inventory(
    path: Path, inventory: ObservationInventory
) -> Path:
    destination = path.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=destination.parent, delete=False
    ) as handle:
        json.dump(
            inventory.model_dump(mode="json"), handle,
            ensure_ascii=False, indent=2, sort_keys=True,
        )
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(destination)
    return destination
