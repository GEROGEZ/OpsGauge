"""Deterministic compilation of an Operations scene into a Brise RCA Case draft."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta
from hashlib import md5
from html import escape
import json
from pathlib import Path
import tempfile
from typing import Any, Literal

from pydantic import Field, model_validator

from ...construction.models import NonEmptyText, SourceReference, _ProtocolModel
from ...kernel.model import EpisodeIR


class RcaEntityRef(_ProtocolModel):
    entity_id: NonEmptyText
    entity_name: NonEmptyText
    entity_domain: NonEmptyText
    entity_type: NonEmptyText
    evidence_ref: NonEmptyText


class RcaRootCauseSpec(_ProtocolModel):
    rc_id: NonEmptyText
    fault_id: NonEmptyText
    entities: tuple[RcaEntityRef, ...]
    conclusion: NonEmptyText

    @model_validator(mode="after")
    def require_entities(self) -> "RcaRootCauseSpec":
        if not self.entities:
            raise ValueError("a root cause requires at least one resolved entity")
        return self


class RcaExpectedValue(_ProtocolModel):
    comparator: NonEmptyText
    value: Any
    unit: str = ""
    status: str = ""


class RcaObservationSpec(_ProtocolModel):
    source_type: NonEmptyText
    source: NonEmptyText
    signal: NonEmptyText
    required: bool = True
    expected: RcaExpectedValue
    evidence_ref: NonEmptyText
    observed_at: NonEmptyText
    baseline_window: dict[str, Any] | None = None
    current_window: dict[str, Any] | None = None
    baseline: Any | None = None
    current: Any | None = None
    ratio: Any | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)
    measurement_caliber: NonEmptyText | None = None


class RcaReasoningStepSpec(_ProtocolModel):
    step: int = Field(ge=1)
    title: NonEmptyText
    step_type: Literal["cause", "propagation", "impact"]
    target: NonEmptyText
    target_entity_ids: tuple[NonEmptyText, ...]
    target_entity_name: NonEmptyText
    fault_id: NonEmptyText
    fault_ids: tuple[NonEmptyText, ...] = ()
    attribution_scope: Literal["root_specific", "shared_scene"] = "root_specific"
    attribution_basis: NonEmptyText = "explicit_root_reference"
    description: NonEmptyText
    required: bool = True
    queryable: bool = True
    rc_refs: tuple[NonEmptyText, ...]
    time_window: dict[str, NonEmptyText]
    observations: tuple[RcaObservationSpec, ...]

    @model_validator(mode="after")
    def require_evidence(self) -> "RcaReasoningStepSpec":
        if not self.target_entity_ids:
            raise ValueError("reasoning steps require target_entity_ids")
        if self.required and not self.observations:
            raise ValueError("required reasoning steps require observations")
        if set(self.time_window) != {"start", "end"}:
            raise ValueError("time_window must contain exactly start and end")
        if self.fault_ids and self.fault_id not in self.fault_ids:
            raise ValueError("fault_id must be included in fault_ids")
        if self.attribution_scope == "shared_scene" and len(self.fault_ids) < 2:
            raise ValueError("shared-scene reasoning requires multiple fault_ids")
        return self


class RcaContextStepSpec(_ProtocolModel):
    """Queryable scene context kept outside the causal explanation."""

    context_id: NonEmptyText
    source_step: int = Field(ge=1)
    target_entity_ids: tuple[NonEmptyText, ...]
    time_window: dict[str, NonEmptyText]
    observations: tuple[RcaObservationSpec, ...]
    queryable: bool = True

    @model_validator(mode="after")
    def require_context_evidence(self) -> "RcaContextStepSpec":
        if not self.target_entity_ids:
            raise ValueError("context steps require target_entity_ids")
        if not self.observations:
            raise ValueError("context steps require observations")
        if set(self.time_window) != {"start", "end"}:
            raise ValueError("time_window must contain exactly start and end")
        return self


class RcaProjectionInput(_ProtocolModel):
    """Case-specific facts selected from a scene; no Brise rendering concerns."""

    schema_version: Literal["rca_projection_input_v1"] = "rca_projection_input_v1"
    projection_id: NonEmptyText
    experiment_name: NonEmptyText
    workspace: NonEmptyText
    region_id: NonEmptyText
    time_range_start: NonEmptyText
    time_range_end: NonEmptyText
    primary_alert_event_id: str
    trigger_mode: Literal["alert", "observation"] = "alert"
    expected_conclusion: NonEmptyText
    root_causes: tuple[RcaRootCauseSpec, ...]
    resolved_entities: tuple[RcaEntityRef, ...] = ()
    reasoning_steps: tuple[RcaReasoningStepSpec, ...]
    context_steps: tuple[RcaContextStepSpec, ...] = ()
    tags: tuple[NonEmptyText, ...] = ()
    source_refs: tuple[SourceReference, ...]
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_chain(self) -> "RcaProjectionInput":
        if self.trigger_mode == "alert" and not self.primary_alert_event_id:
            raise ValueError("alert-triggered projection requires primary_alert_event_id")
        if self.trigger_mode == "observation" and self.primary_alert_event_id:
            raise ValueError("observation-triggered projection cannot declare an alert event")
        if not self.root_causes:
            raise ValueError("RCA projection requires at least one root cause")
        numbers = [item.step for item in self.reasoning_steps]
        if numbers != list(range(1, len(numbers) + 1)):
            raise ValueError("reasoning steps must be ordered and contiguous from 1")
        kinds = {item.step_type for item in self.reasoning_steps}
        if "cause" not in kinds or "impact" not in kinds:
            raise ValueError("RCA projection requires cause and impact steps")
        root_ids = {entity.entity_id for root in self.root_causes for entity in root.entities}
        resolved_ids = [entity.entity_id for entity in self.resolved_entities]
        if len(resolved_ids) != len(set(resolved_ids)):
            raise ValueError("resolved_entities must have unique entity_id values")
        cause_ids = {
            entity_id
            for step in self.reasoning_steps if step.step_type == "cause"
            for entity_id in step.target_entity_ids
        }
        if not root_ids.issubset(cause_ids):
            raise ValueError("every root entity must appear in a cause step")
        return self


class BriseRcaCaseCompiler:
    """Render a checked RCA projection without inventing identifiers or values."""

    def compile(self, episode: EpisodeIR, projection: RcaProjectionInput) -> dict[str, Any]:
        if projection.trigger_mode != "alert":
            raise ValueError("new Brise RCA Cases require an alert-triggered projection")
        alert_node = self._primary_alert(episode, projection.primary_alert_event_id)
        alert = alert_node.properties
        alert_entity = self._alert_entity(episode, alert_node.node_id)
        expected_end = (
            datetime.fromisoformat(str(alert["occurred_at"])) + timedelta(minutes=2)
        )
        projected_end = datetime.fromisoformat(projection.time_range_end)
        if abs((projected_end - expected_end).total_seconds()) > 1:
            raise ValueError(
                "time_range_end must equal primary alert occurred_at plus two minutes"
            )
        declared_faults = {
            str(node.properties.get("fault_id"))
            for node in episode.nodes if node.node_type == "Fault"
        }
        requested_faults = {root.fault_id for root in projection.root_causes}
        if not requested_faults.issubset(declared_faults):
            raise ValueError("projection fault_id is not declared by the source episode")
        root_refs = {root.rc_id for root in projection.root_causes}
        for step in projection.reasoning_steps:
            step_faults = set(step.fault_ids or (step.fault_id,))
            if not step_faults.issubset(requested_faults):
                raise ValueError("reasoning step fault_id is not declared by a root cause")
            if not set(step.rc_refs).issubset(root_refs):
                raise ValueError("reasoning step contains an unknown root-cause reference")
        impact_entity_ids = {
            entity_id
            for step in projection.reasoning_steps if step.step_type == "impact"
            for entity_id in step.target_entity_ids
        }
        if str(alert_entity["entity_id"]) not in impact_entity_ids:
            raise ValueError("primary alert entity must appear in an impact step")
        for root in projection.root_causes:
            for entity in root.entities:
                if not self._matches_selector(episode, entity):
                    raise ValueError(
                        f"root entity {entity.entity_id} does not match an episode target selector"
                    )

        transaction_id = str(alert["transaction_id"])
        primary_fault = projection.root_causes[0].fault_id
        incident_id = f"{primary_fault}.{transaction_id}"
        source_run_id = f"rca_run_{md5(incident_id.encode('utf-8')).hexdigest()}"
        entity_name = str(alert_entity.get("entity_name") or "")
        prompt = self._prompt(alert, alert_entity)

        raw_ground_truth = {
            "outcome": {
                "root_causes": [
                    {
                        "rc_id": root.rc_id,
                        "fault_id": root.fault_id,
                        "entity_group": {
                            "required_entity_ids": [entity.entity_id for entity in root.entities],
                            "alias_entity_ids": [],
                            "entity_refs": [
                                {
                                    "entity_id": entity.entity_id,
                                    "entity_name": entity.entity_name,
                                    "entity_domain": entity.entity_domain,
                                    "entity_type": entity.entity_type,
                                }
                                for entity in root.entities
                            ],
                        },
                        "conclusion": root.conclusion,
                    }
                    for root in projection.root_causes
                ],
                "expected_conclusion": projection.expected_conclusion,
            },
            "reasoning": {
                "steps": [self._reasoning_step(step) for step in projection.reasoning_steps]
            },
            "metadata": {
                "ground_truth_mode": "layered_reasoning",
                "source": "benchmark_factory",
                "source_run_id": source_run_id,
                "source_fault_run_id": self._run_id(episode),
                "source_episode_id": episode.episode_id,
                "source_ontology_version": episode.ontology_version,
                "projection_id": projection.projection_id,
                "source_refs": [item.model_dump(mode="json") for item in projection.source_refs],
                "root_entity_evidence": [
                    {
                        "entity_id": entity.entity_id,
                        "evidence_ref": entity.evidence_ref,
                    }
                    for root in projection.root_causes for entity in root.entities
                ],
                **projection.metadata,
            },
        }
        root_entities = [entity for root in projection.root_causes for entity in root.entities]
        fault_types = [fault.split("-", 1)[-1] for fault in sorted(requested_faults)]
        return {
            "log_type": "evaluation",
            "experiment_name": projection.experiment_name,
            "incident_id": incident_id,
            "prompt_text": prompt,
            "prompt_source": "alert_event_store",
            "prompt_format": "xml",
            "prompt_style": "anchor_xml",
            "prompt_level": "P0",
            "raw_ground_truth": raw_ground_truth,
            "workspace": projection.workspace,
            "environment": episode.environment_ref,
            "region_id": projection.region_id,
            "alert_entity_id": str(alert_entity["entity_id"]),
            "alert_entity_name": entity_name,
            "alert_entity_type": str(alert_entity.get("entity_type") or ""),
            "alert_entity_domain": str(alert_entity.get("entity_domain") or ""),
            "alert_event_id": projection.primary_alert_event_id,
            "alert_trigger_time": str(alert["occurred_at"]),
            "alert_title": str(alert.get("rule_name") or ""),
            "time_range_start": projection.time_range_start,
            "time_range_end": projection.time_range_end,
            "root_cause_entities": [entity.entity_name for entity in root_entities],
            "root_cause_entity_ids": [entity.entity_id for entity in root_entities],
            "root_cause_types": fault_types,
            "root_cause_count": len(projection.root_causes),
            "fault_ids": sorted(requested_faults),
            "alert_event_ids": [projection.primary_alert_event_id],
            "alert_anchors": [{
                "rc_refs": [root.rc_id for root in projection.root_causes],
                "rule_id": str(alert["rule_id"]),
                "rule_name": str(alert.get("rule_name") or ""),
                "alert_event_id": projection.primary_alert_event_id,
                "alert_event_trans_id": transaction_id,
                "alert_trigger_time": str(alert["occurred_at"]),
                "alert_entity_id": str(alert_entity["entity_id"]),
                "alert_entity_type": str(alert_entity.get("entity_type") or ""),
                "alert_entity_domain": str(alert_entity.get("entity_domain") or ""),
            }],
            "trigger_source": "benchmark_factory",
            "run_id": "",
            "brise_run_id": "",
            "batch_id": "",
            "brise_batch_id": "",
            "is_dataset": True,
            "timestamp": projection.time_range_start,
            "tags": sorted(set(projection.tags)),
        }

    @staticmethod
    def _primary_alert(episode: EpisodeIR, event_id: str):
        matches = [
            node for node in episode.nodes
            if node.node_type == "Alert"
            and node.properties.get("role") == "primary_impact"
            and node.properties.get("canonical_event_id") == event_id
            and node.properties.get("status") == "OCCURRED"
        ]
        if len(matches) != 1:
            raise ValueError("projection requires exactly one matching OCCURRED primary alert")
        return matches[0]

    @staticmethod
    def _alert_entity(episode: EpisodeIR, alert_node_id: str) -> dict[str, Any]:
        entity_ids = [
            edge.target for edge in episode.edges
            if edge.source == alert_node_id and edge.relation == "observedOn"
        ]
        matches = [
            node for node in episode.nodes
            if node.node_id in entity_ids and node.node_type == "OperationalEntity"
        ]
        if len(matches) != 1:
            raise ValueError("primary alert must resolve to exactly one observed entity")
        required = {"entity_id", "entity_domain", "entity_type"}
        if not required.issubset(matches[0].properties):
            raise ValueError("primary alert entity is missing required identity fields")
        return matches[0].properties

    @staticmethod
    def _matches_selector(episode: EpisodeIR, entity: RcaEntityRef) -> bool:
        values = {
            str(value)
            for node in episode.nodes if node.node_type == "EntitySelector"
            for value in (node.properties.get("selector") or {}).values()
        }
        return entity.entity_name in values

    @staticmethod
    def _run_id(episode: EpisodeIR) -> str:
        runs = [node for node in episode.nodes if node.node_type == "FaultExecution"]
        if len(runs) != 1 or not runs[0].properties.get("run_id"):
            raise ValueError("episode requires exactly one fault execution run")
        return str(runs[0].properties["run_id"])

    @staticmethod
    def _prompt(alert: dict[str, Any], entity: dict[str, Any]) -> str:
        attributes = {
            "event_id": alert["canonical_event_id"],
            "trans_id": alert["transaction_id"],
            "rule_id": alert["rule_id"],
            "rule_name": alert.get("rule_name") or "",
            "entity_id": entity["entity_id"],
            "entity_name": entity.get("entity_name") or "",
            "entity_type": entity.get("entity_type") or "",
            "entity_domain": entity.get("entity_domain") or "",
            "alert_time": alert["occurred_at"],
            "status": alert["status"],
        }
        rendered = " ".join(
            f'{key}="{escape(str(value), quote=True)}"' for key, value in attributes.items()
        )
        return f'<code vibeops_object type="alert_event"><alert_event {rendered} /></code> 帮我分析下根因。'

    @staticmethod
    def _reasoning_step(step: RcaReasoningStepSpec) -> dict[str, Any]:
        return {
            "step": step.step,
            "title": step.title,
            "step_type": step.step_type,
            "target": step.target,
            "target_entity_ids": list(step.target_entity_ids),
            "target_entity_name": step.target_entity_name,
            "fault_id": step.fault_id,
            **({"fault_ids": list(step.fault_ids)} if len(step.fault_ids) > 1 else {}),
            "attribution_scope": step.attribution_scope,
            "attribution_basis": step.attribution_basis,
            "description": step.description,
            "required": step.required,
            "queryable": step.queryable,
            "rc_refs": list(step.rc_refs),
            "time_range_hint": f'{step.time_window["start"]}/{step.time_window["end"]}',
            "time_window": step.time_window,
            "observability": [
                {
                    "source_type": observation.source_type,
                    "source": observation.source,
                    "signal": observation.signal,
                    "required": observation.required,
                    "expected": observation.expected.model_dump(mode="json"),
                    "observed_at": observation.observed_at,
                    **({"baseline_window": observation.baseline_window} if observation.baseline_window else {}),
                    **({"current_window": observation.current_window} if observation.current_window else {}),
                    **({"baseline": observation.baseline} if observation.baseline is not None else {}),
                    **({"current": observation.current} if observation.current is not None else {}),
                    **({"ratio": observation.ratio} if observation.ratio is not None else {}),
                    "evidence": {"source_ref": observation.evidence_ref, **observation.evidence},
                    **(
                        {"measurement_caliber": observation.measurement_caliber}
                        if observation.measurement_caliber else {}
                    ),
                }
                for observation in step.observations
            ],
        }


def write_brise_rca_case(path: Path, payload: dict[str, Any]) -> Path:
    destination = path.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=destination.parent, delete=False
    ) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(destination)
    return destination
