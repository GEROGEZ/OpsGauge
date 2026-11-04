"""Stable, domain-neutral protocol for a benchmark construction run.

The protocol models one target task type per request. Task-specific catalogs
define which distribution labels are legal; this module only enforces their
structural shape and remains independent from product adapters.
"""

from __future__ import annotations

from collections import Counter
from enum import StrEnum
from typing import Annotated, Iterable, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator


Identifier = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    ),
]
NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
PositiveCount = Annotated[int, Field(strict=True, ge=1)]
NonNegativeCount = Annotated[int, Field(strict=True, ge=0)]


class _ProtocolModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TaskType(StrEnum):
    L2 = "l2"
    L3 = "l3"
    MISSION = "mission"
    RCA = "rca"


class ReusePolicy(StrEnum):
    REUSE_FIRST = "reuse_first"
    NEW_ONLY = "new_only"


class AssetKind(StrEnum):
    EPISODE = "episode"
    CASE = "case"


class AssetStatus(StrEnum):
    CANDIDATE = "candidate"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class AttemptStatus(StrEnum):
    PLANNED = "planned"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class BuildPhase(StrEnum):
    CREATED = "created"
    PLANNING = "planning"
    EXECUTING = "executing"
    COLLECTING = "collecting"
    COMPLETED = "completed"
    FAILED = "failed"


class BuildTerminalStatus(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"


class SourceReference(_ProtocolModel):
    """Addressable provenance for an asset, not an embedded source payload."""

    role: NonEmptyText
    ref: NonEmptyText


class BuildRequest(_ProtocolModel):
    """One requested benchmark distribution for exactly one task type."""

    schema_version: Literal["build_request_v1"] = "build_request_v1"
    build_id: Identifier
    task_type: TaskType
    environment_ref: NonEmptyText
    target_distribution: dict[NonEmptyText, PositiveCount]
    reuse_policy: ReusePolicy = ReusePolicy.REUSE_FIRST

    @model_validator(mode="after")
    def validate_distribution(self) -> "BuildRequest":
        if not self.target_distribution:
            raise ValueError("target_distribution must contain at least one label")
        normalized = [label.strip() for label in self.target_distribution]
        if len(normalized) != len(set(normalized)):
            raise ValueError("target_distribution labels must be unique after normalization")
        return self


class AssetRecord(_ProtocolModel):
    """An episode or generated Case retained by the construction asset pool.

    Case assets point back to their source episode. Side-product Cases from
    other task types may coexist in a BuildState without satisfying its target.
    """

    schema_version: Literal["asset_record_v1"] = "asset_record_v1"
    asset_id: Identifier
    asset_kind: AssetKind
    status: AssetStatus
    environment_ref: NonEmptyText
    episode_id: Identifier
    case_id: Identifier | None = None
    task_type: TaskType | None = None
    distribution_label: NonEmptyText | None = None
    parent_asset_ids: tuple[Identifier, ...] = ()
    source_refs: tuple[SourceReference, ...] = ()

    @model_validator(mode="after")
    def validate_kind_specific_fields(self) -> "AssetRecord":
        case_fields = (self.case_id, self.task_type, self.distribution_label)
        if self.asset_kind is AssetKind.CASE and any(value is None for value in case_fields):
            raise ValueError("case assets require case_id, task_type, and distribution_label")
        if self.asset_kind is AssetKind.EPISODE and any(value is not None for value in case_fields):
            raise ValueError("episode assets cannot declare case-only fields")
        if len(self.parent_asset_ids) != len(set(self.parent_asset_ids)):
            raise ValueError("parent_asset_ids must be unique")
        return self


class AttemptRecord(_ProtocolModel):
    """Small resumable record of one future planning/execution attempt."""

    schema_version: Literal["attempt_record_v1"] = "attempt_record_v1"
    attempt_id: Identifier
    requested_label: NonEmptyText
    capability_id: Identifier | None = None
    status: AttemptStatus
    execution_ref: NonEmptyText | None = None
    produced_asset_ids: tuple[Identifier, ...] = ()
    failure_code: NonEmptyText | None = None

    @model_validator(mode="after")
    def validate_outcome(self) -> "AttemptRecord":
        if len(self.produced_asset_ids) != len(set(self.produced_asset_ids)):
            raise ValueError("produced_asset_ids must be unique")
        if self.status is AttemptStatus.FAILED and self.failure_code is None:
            raise ValueError("failed attempts require failure_code")
        if self.status is not AttemptStatus.FAILED and self.failure_code is not None:
            raise ValueError("failure_code is only valid for failed attempts")
        return self


def allocated_case_assets(
    request: BuildRequest,
    assets: Iterable[AssetRecord],
    attempts: Iterable[AttemptRecord] = (),
) -> tuple[AssetRecord, ...]:
    """Select an exact, deterministic set of Cases for the target distribution."""

    produced_ids = {
        asset_id
        for attempt in attempts
        for asset_id in attempt.produced_asset_ids
    }
    candidates = []
    for asset in assets:
        if (
            asset.asset_kind is not AssetKind.CASE
            or asset.status is not AssetStatus.ACCEPTED
            or asset.task_type is not request.task_type
            or asset.environment_ref != request.environment_ref
            or asset.case_id is None
            or asset.distribution_label not in request.target_distribution
        ):
            continue
        if request.reuse_policy is ReusePolicy.NEW_ONLY and asset.asset_id not in produced_ids:
            continue
        candidates.append(asset)

    candidates.sort(
        key=lambda asset: (
            asset.distribution_label or "",
            0 if asset.asset_id not in produced_ids else 1,
            asset.case_id or "",
            asset.asset_id,
        )
    )
    selected = []
    selected_counts: Counter[str] = Counter()
    for asset in candidates:
        label = asset.distribution_label
        if label is None or selected_counts[label] >= request.target_distribution[label]:
            continue
        selected.append(asset)
        selected_counts[label] += 1
    return tuple(selected)


def achieved_distribution(
    request: BuildRequest,
    assets: Iterable[AssetRecord],
    attempts: Iterable[AttemptRecord] = (),
) -> dict[str, int]:
    """Count the exact Case allocation that satisfies this request."""

    labels = Counter()
    for asset in allocated_case_assets(request, assets, attempts):
        if asset.distribution_label is None:
            raise AssertionError("allocated Case is missing its distribution label")
        labels[asset.distribution_label] += 1
    return {label: labels.get(label, 0) for label in request.target_distribution}


def distribution_gap(
    target: dict[str, int],
    achieved: dict[str, int],
) -> dict[str, int]:
    """Return remaining counts while retaining zeroes for a stable report shape."""

    return {
        label: max(0, required - achieved.get(label, 0))
        for label, required in target.items()
    }


class BuildState(_ProtocolModel):
    """Durable checkpoint for a construction run."""

    schema_version: Literal["build_state_v1"] = "build_state_v1"
    request: BuildRequest
    phase: BuildPhase = BuildPhase.CREATED
    revision: NonNegativeCount = 0
    iteration: NonNegativeCount = 0
    assets: tuple[AssetRecord, ...] = ()
    attempts: tuple[AttemptRecord, ...] = ()
    latest_plan_ref: NonEmptyText | None = None

    @model_validator(mode="after")
    def validate_identity(self) -> "BuildState":
        asset_ids = [asset.asset_id for asset in self.assets]
        if len(asset_ids) != len(set(asset_ids)):
            raise ValueError("asset_id values must be unique within a BuildState")
        case_ids = [asset.case_id for asset in self.assets if asset.case_id is not None]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("case_id values must be unique within a BuildState")
        attempt_ids = [attempt.attempt_id for attempt in self.attempts]
        if len(attempt_ids) != len(set(attempt_ids)):
            raise ValueError("attempt_id values must be unique within a BuildState")
        known_asset_ids = set(asset_ids)
        missing = {
            produced
            for attempt in self.attempts
            for produced in attempt.produced_asset_ids
            if produced not in known_asset_ids
        }
        if missing:
            raise ValueError(f"attempts refer to unknown produced assets: {sorted(missing)}")
        produced = [
            asset_id
            for attempt in self.attempts
            for asset_id in attempt.produced_asset_ids
        ]
        if len(produced) != len(set(produced)):
            raise ValueError("an asset may be produced by only one attempt")
        if self.phase is BuildPhase.COMPLETED and not self.target_satisfied():
            raise ValueError("completed BuildState must satisfy every target label")
        return self

    def allocated_case_assets(self) -> tuple[AssetRecord, ...]:
        return allocated_case_assets(self.request, self.assets, self.attempts)

    def achieved_distribution(self) -> dict[str, int]:
        return achieved_distribution(self.request, self.assets, self.attempts)

    def remaining_distribution(self) -> dict[str, int]:
        return distribution_gap(
            self.request.target_distribution,
            self.achieved_distribution(),
        )

    def target_satisfied(self) -> bool:
        return all(count == 0 for count in self.remaining_distribution().values())


class BuildResult(_ProtocolModel):
    """Frozen terminal summary produced from a durable BuildState."""

    schema_version: Literal["build_result_v1"] = "build_result_v1"
    build_id: Identifier
    task_type: TaskType
    environment_ref: NonEmptyText
    status: BuildTerminalStatus
    target_distribution: dict[NonEmptyText, PositiveCount]
    achieved_distribution: dict[NonEmptyText, NonNegativeCount]
    remaining_distribution: dict[NonEmptyText, NonNegativeCount]
    accepted_case_asset_ids: tuple[Identifier, ...]
    attempt_count: NonNegativeCount
    termination_reason: NonEmptyText

    @model_validator(mode="after")
    def validate_summary(self) -> "BuildResult":
        target_labels = set(self.target_distribution)
        if set(self.achieved_distribution) != target_labels:
            raise ValueError("achieved_distribution labels must match target_distribution")
        if set(self.remaining_distribution) != target_labels:
            raise ValueError("remaining_distribution labels must match target_distribution")
        expected_remaining = distribution_gap(
            self.target_distribution,
            self.achieved_distribution,
        )
        if self.remaining_distribution != expected_remaining:
            raise ValueError("remaining_distribution is inconsistent with target and achieved counts")
        satisfied = not any(expected_remaining.values())
        if (self.status is BuildTerminalStatus.COMPLETE) != satisfied:
            raise ValueError("complete status must exactly match target satisfaction")
        if len(self.accepted_case_asset_ids) != len(set(self.accepted_case_asset_ids)):
            raise ValueError("accepted_case_asset_ids must be unique")
        if len(self.accepted_case_asset_ids) != sum(self.achieved_distribution.values()):
            raise ValueError("accepted Case assets must account for every achieved count")
        return self

    @classmethod
    def from_state(
        cls,
        state: BuildState,
        *,
        termination_reason: str,
    ) -> "BuildResult":
        achieved = state.achieved_distribution()
        remaining = state.remaining_distribution()
        if not any(remaining.values()):
            status = BuildTerminalStatus.COMPLETE
        elif state.phase is BuildPhase.FAILED:
            status = BuildTerminalStatus.FAILED
        else:
            status = BuildTerminalStatus.PARTIAL
        accepted = tuple(asset.asset_id for asset in state.allocated_case_assets())
        return cls(
            build_id=state.request.build_id,
            task_type=state.request.task_type,
            environment_ref=state.request.environment_ref,
            status=status,
            target_distribution=state.request.target_distribution,
            achieved_distribution=achieved,
            remaining_distribution=remaining,
            accepted_case_asset_ids=accepted,
            attempt_count=len(state.attempts),
            termination_reason=termination_reason,
        )
