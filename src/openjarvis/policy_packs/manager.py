# ruff: noqa: E501
"""Generic, installable policy-pack registry and lifecycle manager."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable, Mapping, Optional, Protocol

from .contracts import (
    ActionSpec,
    CausalTimelineEntry,
    InstallationGrant,
    PackInstallation,
    PackInstallState,
    PackOperation,
    PolicyActionProposal,
    PolicyEvaluation,
    PolicyEvent,
    PolicyOutcome,
    PolicyPackManifest,
    new_id,
    utc_now,
    version_satisfies,
)
from .store import Phase12Store


class PackInstallError(ValueError):
    """A pack could not be installed or changed without widening authority."""


class PolicyPack(Protocol):
    manifest: PolicyPackManifest

    def evaluate(
        self, event: PolicyEvent, installation: PackInstallation
    ) -> PolicyEvaluation: ...


class PolicyPackRegistry:
    """In-memory code registry; it never grants authority or activates a pack."""

    def __init__(self) -> None:
        self._packs: dict[tuple[str, str], PolicyPack] = {}

    def register(self, pack: PolicyPack) -> None:
        key = (pack.manifest.pack_id, pack.manifest.version)
        if key in self._packs:
            raise ValueError(f"pack is already registered: {key[0]}@{key[1]}")
        self._packs[key] = pack

    def get(self, pack_id: str, version: str) -> PolicyPack:
        try:
            return self._packs[(pack_id, version)]
        except KeyError as exc:
            raise PackInstallError(
                f"pack implementation is not registered: {pack_id}@{version}"
            ) from exc

    def versions(self, pack_id: str) -> tuple[str, ...]:
        return tuple(version for item, version in self._packs if item == pack_id)


MigrationRunner = Callable[[PolicyPackManifest, PolicyPackManifest], None]


class PackManager:
    """Validate and persist pack lifecycle without modifying Guardian internals."""

    def __init__(
        self,
        store: Phase12Store,
        registry: PolicyPackRegistry,
        *,
        trusted_publishers: Optional[Mapping[str, str]] = None,
        migration_runner: Optional[MigrationRunner] = None,
        ophanim_version: str = "12.0.0",
    ) -> None:
        self.store = store
        self.registry = registry
        self.trusted_publishers = dict(trusted_publishers or {})
        self.migration_runner = migration_runner
        self.ophanim_version = ophanim_version

    def install(self, pack: PolicyPack, grant: InstallationGrant) -> PackInstallation:
        self._validate_manifest(pack.manifest)
        self._ensure_registered(pack)
        self._validate_authority(pack.manifest, grant)
        current = self.store.get_installation(pack.manifest.pack_id)
        if current and current.state is PackInstallState.INSTALLED:
            if (
                current.manifest.version == pack.manifest.version
                and current.installation_id == grant.installation_id
            ):
                return current
            raise PackInstallError(
                "an installed version exists; use upgrade or downgrade"
            )
        now = utc_now()
        operation = PackOperation.REINSTALL if current else PackOperation.INSTALL
        installation = PackInstallation(
            installation_id=grant.installation_id,
            manifest=pack.manifest,
            state=PackInstallState.INSTALLED,
            authority_scope=grant,
            installed_at=now,
            updated_at=now,
            previous_version=current.manifest.version if current else None,
            migration_state="verified",
            rollback_state="available",
            production_active=False,
        )
        self.store.save_installation(installation)
        self.store.save_pack_version(installation)
        self.store.record_pack_event(
            installation.installation_id,
            operation.value,
            current.state.value if current else None,
            installation.state.value,
            {
                "pack_id": pack.manifest.pack_id,
                "version": pack.manifest.version,
                "signature_verified": True,
                "production_active": False,
                "authority_scope": grant.to_dict(),
            },
        )
        return installation

    def upgrade(self, pack: PolicyPack) -> PackInstallation:
        return self._change_version(pack, PackOperation.UPGRADE, expect_newer=True)

    def downgrade(self, pack: PolicyPack) -> PackInstallation:
        return self._change_version(pack, PackOperation.DOWNGRADE, expect_newer=False)

    def rollback(self, pack_id: str) -> PackInstallation:
        current = self._require_installed(pack_id)
        if not current.previous_version:
            raise PackInstallError("no previous version is available for rollback")
        previous = self.registry.get(pack_id, current.previous_version)
        return self._change_version(previous, PackOperation.ROLLBACK, expect_newer=None)

    def uninstall(
        self, pack_id: str, *, installation_id: Optional[str] = None
    ) -> PackInstallation:
        current = self._require_installation(pack_id)
        if installation_id and current.installation_id != installation_id:
            raise PackInstallError("installation identity does not match")
        if current.state is PackInstallState.UNINSTALLED:
            return current
        updated = replace(
            current,
            state=PackInstallState.UNINSTALLED,
            updated_at=utc_now(),
            production_active=False,
        )
        self.store.save_installation(updated)
        self.store.record_pack_event(
            current.installation_id,
            PackOperation.UNINSTALL.value,
            current.state.value,
            updated.state.value,
            {"pack_id": pack_id, "reversible": True, "live_effects": False},
        )
        return updated

    def revoke(
        self, pack_id: str, *, reason: str = "installation authority revoked"
    ) -> PackInstallation:
        current = self._require_installation(pack_id)
        grant = replace(current.authority_scope, revoked_at=utc_now())
        updated = replace(
            current,
            state=PackInstallState.REVOKED,
            authority_scope=grant,
            updated_at=utc_now(),
            production_active=False,
        )
        self.store.save_installation(updated)
        self.store.record_pack_event(
            current.installation_id,
            "revoke",
            current.state.value,
            updated.state.value,
            {"pack_id": pack_id, "reason": reason, "fail_closed": True},
        )
        return updated

    def replay(self, pack_id: str, event: PolicyEvent) -> PolicyEvaluation:
        installation = self._require_installed(pack_id)
        existing = self.store.get_replay(pack_id, event.dedupe_key)
        if existing:
            return replace(
                existing,
                outcome=PolicyOutcome.REPLAYED,
                explanation="Duplicate replay suppressed; the original causal evidence is retained.",
            )
        pack = self.registry.get(pack_id, installation.manifest.version)
        evaluation = pack.evaluate(event, installation)
        bounded = self._bound_evaluation(evaluation, installation, event.event_id)
        if not self.store.save_replay(pack_id, event, bounded):
            existing = self.store.get_replay(pack_id, event.dedupe_key)
            if existing is not None:
                return replace(
                    existing,
                    outcome=PolicyOutcome.REPLAYED,
                    explanation="Duplicate replay suppressed; the original causal evidence is retained.",
                )
        return bounded

    def build_proposal(
        self,
        pack_id: str,
        *,
        action_type: str,
        parameters: dict[str, Any],
        plan_id: str,
        idempotency_key: str,
        expected_effect: Optional[dict[str, Any]] = None,
    ) -> PolicyActionProposal:
        installation = self._require_installed(pack_id)
        spec = self._action_spec(installation.manifest, action_type)
        if not _parameters_match_schema(spec, parameters):
            raise PackInstallError(
                "proposal parameters do not match the declared action schema"
            )
        if not installation.authority_scope.allows(spec, parameters):
            raise PackInstallError(
                "installation grant does not cover this exact action"
            )
        return PolicyActionProposal(
            proposal_id=new_id("policy-proposal"),
            action_type=action_type,
            parameters=dict(parameters),
            risk_class=spec.risk_class,
            consequence_class=spec.consequence_class,
            plan_id=plan_id,
            installation_id=installation.installation_id,
            idempotency_key=idempotency_key,
            expected_effect=dict(expected_effect or {}),
        )

    def installation(self, pack_id: str) -> Optional[PackInstallation]:
        return self.store.get_installation(pack_id)

    def _change_version(
        self,
        pack: PolicyPack,
        operation: PackOperation,
        *,
        expect_newer: Optional[bool],
    ) -> PackInstallation:
        self._validate_manifest(pack.manifest)
        self._ensure_registered(pack)
        current = self._require_installed(pack.manifest.pack_id)
        if expect_newer is True and _version_tuple(
            pack.manifest.version
        ) <= _version_tuple(current.manifest.version):
            raise PackInstallError("upgrade requires a newer version")
        if expect_newer is False and _version_tuple(
            pack.manifest.version
        ) >= _version_tuple(current.manifest.version):
            raise PackInstallError("downgrade requires an older version")
        self._validate_authority(pack.manifest, current.authority_scope)
        try:
            if self.migration_runner:
                self.migration_runner(current.manifest, pack.manifest)
            elif not all(item.reversible for item in pack.manifest.migrations):
                raise PackInstallError("migration has no verified rollback")
        except Exception as exc:
            self.store.record_pack_event(
                current.installation_id,
                operation.value,
                current.state.value,
                PackInstallState.FAILED.value,
                {
                    "pack_id": pack.manifest.pack_id,
                    "version": pack.manifest.version,
                    "migration_error": str(exc),
                    "rolled_back": True,
                },
            )
            raise PackInstallError(
                f"migration failed; previous version remains installed: {exc}"
            ) from exc
        now = utc_now()
        updated = PackInstallation(
            installation_id=current.installation_id,
            manifest=pack.manifest,
            state=PackInstallState.INSTALLED,
            authority_scope=current.authority_scope,
            installed_at=current.installed_at,
            updated_at=now,
            previous_version=current.manifest.version,
            migration_state="applied-and-reversible",
            rollback_state="available",
            production_active=False,
        )
        self.store.save_pack_version(current)
        self.store.save_installation(updated)
        self.store.save_pack_version(updated)
        self.store.record_pack_event(
            current.installation_id,
            operation.value,
            current.state.value,
            updated.state.value,
            {
                "from_version": current.manifest.version,
                "to_version": pack.manifest.version,
                "migration_rollback": True,
            },
        )
        return updated

    def _validate_manifest(self, manifest: PolicyPackManifest) -> None:
        verified, reason = manifest.verify(self.trusted_publishers)
        if not verified:
            raise PackInstallError(reason)
        if not version_satisfies(
            self.ophanim_version, manifest.compatibility.get("ophanim", "*")
        ):
            raise PackInstallError("pack is incompatible with this Ophanim version")
        if any(gate.required and not gate.passed for gate in manifest.release_gates):
            raise PackInstallError("required release gate is not passed")
        for dependency in manifest.dependencies:
            dependency_installation = self.store.get_installation(dependency.pack_id)
            if (
                dependency_installation is None
                or dependency_installation.state is not PackInstallState.INSTALLED
            ):
                if dependency.optional:
                    continue
                raise PackInstallError(f"missing dependency: {dependency.pack_id}")
            if not version_satisfies(
                dependency_installation.manifest.version, dependency.version_range
            ):
                raise PackInstallError(
                    f"dependency version is incompatible: {dependency.pack_id}"
                )

    def _validate_authority(
        self, manifest: PolicyPackManifest, grant: InstallationGrant
    ) -> None:
        if grant.pack_id != manifest.pack_id:
            raise PackInstallError("installation grant is for a different pack")
        allowed = {item.action_type for item in manifest.supported_actions}
        if any(action not in allowed for action in grant.allowed_action_types):
            raise PackInstallError("installation grant widens the pack action set")
        capabilities = {item.capability for item in manifest.supported_actions}
        if any(
            capability not in capabilities for capability in grant.allowed_capabilities
        ):
            raise PackInstallError("installation grant widens the pack capability set")
        limits = manifest.authority_limits
        max_risk = limits.get("max_risk_class")
        max_consequence = limits.get("max_consequence_class")
        if max_risk and _risk_rank(grant.max_risk_class) > _risk_rank(max_risk):
            raise PackInstallError("installation grant exceeds pack risk limit")
        if max_consequence and _risk_rank(grant.max_consequence_class) > _risk_rank(
            max_consequence
        ):
            raise PackInstallError("installation grant exceeds pack consequence limit")

    def _bound_evaluation(
        self,
        evaluation: PolicyEvaluation,
        installation: PackInstallation,
        event_id: str,
    ) -> PolicyEvaluation:
        reasons: list[str] = []
        if evaluation.event_id != event_id:
            reasons.append("evaluation-event-mismatch")
        if evaluation.pack_id != installation.manifest.pack_id:
            reasons.append("evaluation-pack-mismatch")
        if evaluation.pack_version != installation.manifest.version:
            reasons.append("evaluation-version-mismatch")
        if not evaluation.simulation_only:
            reasons.append("replay-must-be-simulation-only")
        if evaluation.live_effects:
            reasons.append("replay-live-effects-are-forbidden")
        for proposal in evaluation.proposals:
            try:
                spec = self._action_spec(installation.manifest, proposal.action_type)
            except PackInstallError:
                reasons.append("undeclared-action")
                continue
            if proposal.installation_id != installation.installation_id:
                reasons.append("proposal-installation-mismatch")
            if proposal.risk_class != spec.risk_class:
                reasons.append("proposal-risk-mismatch")
            if proposal.consequence_class != spec.consequence_class:
                reasons.append("proposal-consequence-mismatch")
            if not _parameters_match_schema(spec, proposal.parameters):
                reasons.append("proposal-schema-mismatch")
            if not installation.authority_scope.allows(spec, proposal.parameters):
                reasons.append("installation-authority-bound")
        if reasons:
            return replace(
                evaluation,
                outcome=PolicyOutcome.BLOCKED,
                explanation="The policy evaluation failed the installation safety boundary; no action was attempted.",
                reasons=tuple(dict.fromkeys((*evaluation.reasons, *reasons))),
                proposals=(),
                simulation_only=True,
                live_effects=False,
            )
        return evaluation

    def _ensure_registered(self, pack: PolicyPack) -> None:
        registered = self.registry.get(pack.manifest.pack_id, pack.manifest.version)
        if registered is not pack:
            raise PackInstallError(
                "pack implementation does not match the registered verified implementation"
            )

    def _require_installation(self, pack_id: str) -> PackInstallation:
        installation = self.store.get_installation(pack_id)
        if installation is None:
            raise PackInstallError(f"pack is not installed: {pack_id}")
        return installation

    def _require_installed(self, pack_id: str) -> PackInstallation:
        installation = self._require_installation(pack_id)
        if (
            installation.state is not PackInstallState.INSTALLED
            or installation.authority_scope.revoked
        ):
            raise PackInstallError("pack installation is not live")
        return installation

    @staticmethod
    def _action_spec(manifest: PolicyPackManifest, action_type: str) -> ActionSpec:
        for item in manifest.supported_actions:
            if item.action_type == action_type:
                return item
        raise PackInstallError(f"action is not declared by the pack: {action_type}")


def evaluation_from_dict(raw: Mapping[str, Any]) -> PolicyEvaluation:
    from .contracts import PolicyActionProposal, PolicyOutcome

    data = dict(raw)
    data["outcome"] = PolicyOutcome(data["outcome"])
    data["reasons"] = tuple(data.get("reasons", ()))
    data["evidence_ids"] = tuple(data.get("evidence_ids", ()))
    data["proposals"] = tuple(
        PolicyActionProposal(**item) for item in data.get("proposals", ())
    )
    data["timeline"] = tuple(
        CausalTimelineEntry(**item) for item in data.get("timeline", ())
    )
    return PolicyEvaluation(**data)


def _version_tuple(version: str) -> tuple[int, int, int]:
    return tuple(int(item) for item in version.split(".")[:3])  # type: ignore[return-value]


def _risk_rank(value: str) -> int:
    return {"low": 0, "medium": 1, "high": 2, "critical": 3}.get(value.lower(), 99)


def _parameters_match_schema(spec: ActionSpec, parameters: Mapping[str, Any]) -> bool:
    if not isinstance(parameters, Mapping):
        return False
    schema = spec.input_schema
    required = set(schema.get("required", ()))
    if not required.issubset(parameters):
        return False
    properties = schema.get("properties", {})
    if schema.get("additionalProperties") is False and set(parameters) - set(
        properties
    ):
        return False
    for key, expected in properties.items():
        if key not in parameters:
            continue
        value = parameters[key]
        if expected.get("type") == "string" and not isinstance(value, str):
            return False
        if expected.get("type") == "number" and (
            isinstance(value, bool) or not isinstance(value, (int, float))
        ):
            return False
    return not spec.allowed_targets or parameters.get("target") in spec.allowed_targets


PolicyPackManager = PackManager
