"""Deterministic Phase 11 mission-control service.

The service owns continuity and decision records.  It never calls a live
phone, voice, calendar, notification, deployment, or Codex adapter.  A caller
may provide the existing Guardian control boundary for emergency-stop and
revocation propagation; those calls are explicit and still do not grant
remote authority.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Protocol

from .contracts import (
    AmbientPresence,
    AuthorityGrantScope,
    ChannelConsent,
    ContinuityAcknowledgment,
    Interruption,
    InterruptionKind,
    InterruptionStatus,
    MissionPortfolioItem,
    MissionSummary,
    MissionUpdate,
    NotificationPolicy,
    PresenceState,
    RedactionEvidence,
    RemoteQuery,
    RemoteSteering,
    SteeringStatus,
    UpdateDelivery,
    UpdateDeliveryStatus,
    UpdateKind,
    utc_now,
)
from .store import AmbientStore


class GuardianControl(Protocol):
    """Minimal existing Guardian boundary used by ambient safety controls."""

    def emergency_stop(self, *, reason: str = "emergency stop") -> None: ...

    def revoke(self, session_id: str, *, reason: str = "authority revoked") -> None: ...


class LocalChannelAdapter:
    """A local test sink; it has no network or external side effect."""

    def __init__(self, *, failed_channels: set[str] | None = None) -> None:
        self.failed_channels = set(failed_channels or set())
        self.local_deliveries: list[dict[str, Any]] = []
        self.external_effects: list[dict[str, Any]] = []

    def send(self, channel: str, payload: str) -> bool:
        if channel in self.failed_channels:
            return False
        self.local_deliveries.append({"channel": channel, "payload": payload})
        return True


_PRIORITY = {
    InterruptionKind.EMERGENCY_STOP: 100,
    InterruptionKind.REVOCATION: 95,
    InterruptionKind.CANCEL: 90,
    InterruptionKind.BARGE_IN: 80,
    InterruptionKind.BUDGET_EXCEEDED: 75,
    InterruptionKind.REMOTE_CHANNEL_FAILURE: 70,
}
_PATH_PATTERN = re.compile(
    r"(?:[A-Za-z]:\\[^\s,;]+|/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+)"
)
_SECRET_PATTERN = re.compile(
    r"(?i)\b(?:bearer\s+\S+|(?:api[_-]?key|token|password|secret|credential)\s*[:=]\s*\S+)"
)
_DIFF_PATTERN = re.compile(r"(?s)(?:diff --git|@@ -\d+).*?(?=\n\S|$)")
_COMMAND_PATTERN = re.compile(
    r"(?im)(?:^|(?<=\s))(?:\$\s+|command\s*:\s*|powershell\s*:\s*|"
    r"cmd\s*:\s*|(?:powershell|pwsh|cmd(?:\.exe)?|bash|sh)\s+)[^\r\n]*"
)


def _safe_text(text: str) -> tuple[str, list[str], list[str]]:
    """Return safe text plus redaction categories and field labels."""

    categories: list[str] = []
    fields: list[str] = []
    result = text
    if _SECRET_PATTERN.search(result):
        result = _SECRET_PATTERN.sub("[REDACTED_SECRET]", result)
        categories.append("credential")
        fields.append("secret-like content")
    if _DIFF_PATTERN.search(result):
        result = _DIFF_PATTERN.sub("[REDACTED_DIFF]", result)
        categories.append("diff")
        fields.append("diff content")
    if _COMMAND_PATTERN.search(result):
        result = _COMMAND_PATTERN.sub("\n[REDACTED_COMMAND]", result)
        categories.append("command")
        fields.append("command content")
    if _PATH_PATTERN.search(result):
        result = _PATH_PATTERN.sub("[REDACTED_PATH]", result)
        categories.append("path")
        fields.append("workspace path")
    return result, sorted(set(categories)), sorted(set(fields))


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _redact_value(value: Any) -> Any:
    """Redact string leaves before arbitrary contract data is persisted."""

    if isinstance(value, str):
        return _safe_text(value)[0]
    if isinstance(value, dict):
        return {
            _safe_text(str(key))[0]: _redact_value(item) for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_value(item) for item in value)
    return value


def _expired(value: str | None) -> bool:
    if not value:
        return False
    return datetime.fromisoformat(value) <= datetime.now(timezone.utc)


class AmbientMissionControl:
    """One coherent, interruptible local mission-control identity."""

    def __init__(
        self,
        store: AmbientStore,
        *,
        guardian: GuardianControl | None = None,
        channel_adapter: LocalChannelAdapter | None = None,
    ) -> None:
        self.store = store
        self.guardian = guardian
        self.channel_adapter = channel_adapter or LocalChannelAdapter()
        self._portfolio: dict[str, MissionPortfolioItem] = {}
        self._emergency_stop = any(
            isinstance(record, Interruption)
            and record.kind is InterruptionKind.EMERGENCY_STOP
            and record.status is InterruptionStatus.APPLIED
            for record in self.store.list_records(kind=Interruption.contract_type)
        )
        self._load_portfolio()

    def _load_portfolio(self) -> None:
        for record in self.store.list_records(kind=MissionPortfolioItem.contract_type):
            if isinstance(record, MissionPortfolioItem):
                self._portfolio[record.item_id] = record

    def register_mission(
        self,
        *,
        mission_id: str,
        thread_id: str,
        turn_id: str,
        workspace: str,
        goal: str,
        budget: dict[str, int | float] | None = None,
        authority_scope: dict[str, Any] | None = None,
    ) -> MissionSummary:
        summary = MissionSummary(
            id=f"mission-summary:{mission_id}",
            mission_id=mission_id,
            thread_id=thread_id,
            turn_id=turn_id,
            workspace=workspace,
            goal=goal,
            budget=dict(budget or {}),
            authority_scope=dict(authority_scope or {}),
            provenance={"component": "phase11-ambient"},
            sensitivity_labels=["workspace"],
        )
        self.store.put(summary)
        self._save_continuity(summary, channel="local-desktop")
        return summary

    def grant_channel_consent(self, consent: ChannelConsent) -> ChannelConsent:
        self.store.put(consent)
        self.store.audit(consent.id, "consent:granted", {"channel": consent.channel})
        return consent

    def revoke_channel_consent(self, consent_id: str, *, reason: str) -> ChannelConsent:
        record = self.store.get(consent_id)
        if not isinstance(record, ChannelConsent):
            raise KeyError(consent_id)
        record.active = False
        record.revoked_at = utc_now()
        self.store.put(record)
        self.store.audit(record.id, "consent:revoked", {"reason": reason})
        return record

    def register_existing_authority_scope(
        self, scope: AuthorityGrantScope
    ) -> AuthorityGrantScope:
        """Register a pre-existing scope; never create Guardian authority."""

        if scope.provenance.get("created_by") == "remote-channel":
            raise PermissionError("remote channel cannot create Guardian authority")
        try:
            existing = self.store.get(scope.id)
        except KeyError:
            existing = None
        if (
            isinstance(existing, AuthorityGrantScope)
            and existing.to_json() != scope.to_json()
        ):
            raise ValueError("authority scope cannot be replaced after registration")
        self.store.put(scope)
        self.store.audit(scope.id, "authority:referenced", {"created": False})
        return scope

    def record_presence(
        self,
        mission_id: str,
        state: PresenceState,
        *,
        source: str = "deterministic-fixture",
        consent_id: str | None = None,
        uncertainty: list[str] | None = None,
    ) -> AmbientPresence:
        summary = self.store.get_mission(mission_id)
        presence_id = (
            f"presence:{mission_id}:{state.value}:{summary.last_update_seq + 1}"
        )
        presence = AmbientPresence(
            id=presence_id,
            presence_id=presence_id,
            mission_id=mission_id,
            state=state,
            source=source,
            uncertainty=list(uncertainty or []),
            provenance={"component": "phase11-ambient", "consent_id": consent_id},
            sensitivity_labels=["presence"],
        )
        self.store.put(presence)
        summary.stale = False
        summary.uncertainty = [
            item for item in summary.uncertainty if "requires refresh" not in item
        ]
        self.store.put(summary)
        self._save_continuity(summary, channel="local-desktop", presence_state=state)
        self.store.audit(presence.id, "presence:recorded", {"state": state.value})
        return presence

    def start_speech(
        self, mission_id: str, *, channel: str = "voice"
    ) -> ContinuityAcknowledgment:
        summary = self.store.get_mission(mission_id)
        ack = self._save_continuity(summary, channel=channel, speech_state="speaking")
        self.store.audit(ack.id, "speech:started", {"channel": channel})
        return ack

    def interrupt_speech(
        self, mission_id: str, *, reason: str = "Marc started speaking"
    ) -> Interruption:
        return self.interrupt(
            mission_id,
            InterruptionKind.BARGE_IN,
            reason=reason,
            applies_to=["speech", "lower-priority-work"],
        )

    def interrupt(
        self,
        mission_id: str,
        kind: InterruptionKind,
        *,
        reason: str,
        applies_to: list[str] | None = None,
    ) -> Interruption:
        summary = self.store.get_mission(mission_id)
        current = self._highest_interruption(mission_id)
        interruption_number = (
            len(self.store.list_records(kind=Interruption.contract_type)) + 1
        )
        interruption_id = (
            f"interruption:{mission_id}:{kind.value}:{interruption_number}"
        )
        interruption = Interruption(
            id=interruption_id,
            interruption_id=interruption_id,
            mission_id=mission_id,
            turn_id=summary.turn_id,
            kind=kind,
            priority=_PRIORITY[kind],
            status=InterruptionStatus.REQUESTED,
            reason=reason,
            applies_to=list(applies_to or ["active-work"]),
            provenance={"component": "phase11-ambient"},
            sensitivity_labels=["mission-control"],
        )
        if current is not None and current.priority > interruption.priority:
            interruption.status = InterruptionStatus.DECLINED
            interruption.reason = (
                f"higher-priority interruption {current.kind.value} already applies"
            )
        else:
            interruption.status = InterruptionStatus.APPLIED
            if kind in {
                InterruptionKind.CANCEL,
                InterruptionKind.EMERGENCY_STOP,
                InterruptionKind.REVOCATION,
            }:
                summary.canceled = kind is not InterruptionKind.EMERGENCY_STOP
                if kind is not InterruptionKind.EMERGENCY_STOP:
                    summary.state = "canceled"
                self.store.put(summary)
            if kind in {
                InterruptionKind.BARGE_IN,
                InterruptionKind.CANCEL,
                InterruptionKind.EMERGENCY_STOP,
            }:
                self._save_continuity(summary, channel="voice", speech_state="canceled")
            if kind is InterruptionKind.EMERGENCY_STOP:
                self._emergency_stop = True
                if self.guardian is not None:
                    self.guardian.emergency_stop(reason=reason)
        self.store.put(interruption)
        self.store.audit(
            interruption.id,
            "interruption:applied"
            if interruption.status is InterruptionStatus.APPLIED
            else "interruption:declined",
            {"status": interruption.status.value},
        )
        return interruption

    def cancel_mission(
        self, mission_id: str, *, reason: str = "Marc canceled mission"
    ) -> Interruption:
        return self.interrupt(mission_id, InterruptionKind.CANCEL, reason=reason)

    def emergency_stop(
        self, *, reason: str = "Marc requested emergency stop"
    ) -> list[Interruption]:
        self._emergency_stop = True
        interruptions: list[Interruption] = []
        for summary in self.store.list_records(kind=MissionSummary.contract_type):
            if isinstance(summary, MissionSummary):
                interruptions.append(
                    self.interrupt(
                        summary.mission_id,
                        InterruptionKind.EMERGENCY_STOP,
                        reason=reason,
                    )
                )
        if not interruptions and self.guardian is not None:
            self.guardian.emergency_stop(reason=reason)
        return interruptions

    def revoke_authority(
        self, grant_id: str, *, reason: str = "Marc revoked authority"
    ) -> AuthorityGrantScope:
        scope = self.store.get(grant_id)
        if not isinstance(scope, AuthorityGrantScope):
            raise KeyError(grant_id)
        scope.revoked = True
        self.store.put(scope)
        self.store.audit(scope.id, "authority:revoked", {"reason": reason})
        if self.guardian is not None:
            self.guardian.revoke(scope.mission_id, reason=reason)
        return scope

    def add_portfolio_item(self, item: MissionPortfolioItem) -> MissionPortfolioItem:
        self._portfolio[item.item_id] = item
        self.store.put(item)
        return item

    def rank_portfolio(self) -> list[MissionPortfolioItem]:
        ranked: list[MissionPortfolioItem] = []
        for item in self._portfolio.values():
            if item.cancellation_state in {"canceled", "completed"}:
                continue
            budget_fit = 1.0 if item.available_time_minutes > 0 else 0.0
            item.ranked_score = round(
                (item.urgency * 0.30)
                + (item.value * 0.30)
                + (item.confidence * 0.20)
                + (budget_fit * 0.10)
                + ((1.0 - item.attention_cost) * 0.10),
                6,
            )
            self.store.put(item)
            ranked.append(item)
        return sorted(ranked, key=lambda value: (-value.ranked_score, value.item_id))

    def record_update(self, update: MissionUpdate) -> MissionUpdate:
        summary = self.store.get_mission(update.mission_id)
        redaction_categories: set[str] = set()
        redaction_fields: set[str] = set()
        for value in (
            update.state,
            update.summary,
            *update.changed_fields,
            *update.blockers,
            *update.uncertainty,
            *update.evidence_ids,
            *update.artifact_ids,
            *update.sensitivity_labels,
        ):
            _, categories, fields = _safe_text(value)
            redaction_categories.update(categories)
            redaction_fields.update(fields)
        safe_provenance = _redact_value(update.provenance)
        if redaction_categories:
            safe_provenance["redaction_categories"] = sorted(redaction_categories)
            safe_provenance["redaction_fields"] = sorted(redaction_fields)
        safe_update = replace(
            update,
            id=update.update_id,
            state=_safe_text(update.state)[0],
            summary=_safe_text(update.summary)[0],
            changed_fields=[_safe_text(item)[0] for item in update.changed_fields],
            blockers=[_safe_text(item)[0] for item in update.blockers],
            uncertainty=[_safe_text(item)[0] for item in update.uncertainty],
            evidence_ids=[_safe_text(item)[0] for item in update.evidence_ids],
            artifact_ids=[_safe_text(item)[0] for item in update.artifact_ids],
            sensitivity_labels=[
                _safe_text(item)[0] for item in update.sensitivity_labels
            ],
            provenance=safe_provenance,
        )
        existing = next(
            (
                record
                for record in self.store.list_records(kind=MissionUpdate.contract_type)
                if isinstance(record, MissionUpdate)
                and record.update_id == safe_update.update_id
            ),
            None,
        )
        if existing is not None:
            if existing.to_json() != safe_update.to_json():
                raise ValueError("conflicting duplicate mission update")
            return existing
        if safe_update.sequence < summary.last_update_seq:
            raise ValueError("stale mission update sequence")
        if safe_update.sequence == summary.last_update_seq and summary.last_update_seq:
            raise ValueError("conflicting mission update sequence")
        self.store.put(safe_update)
        summary.turn_id = safe_update.turn_id
        summary.last_update_seq = max(summary.last_update_seq, safe_update.sequence)
        summary.latest_summary = safe_update.summary
        summary.state = safe_update.state
        summary.blockers = list(safe_update.blockers)
        summary.changed = list(safe_update.changed_fields)
        summary.source_evidence = list(
            dict.fromkeys([*summary.source_evidence, *safe_update.evidence_ids])
        )
        summary.artifacts = list(
            dict.fromkeys([*summary.artifacts, *safe_update.artifact_ids])
        )
        summary.uncertainty = list(safe_update.uncertainty)
        summary.stale = False
        self.store.put(summary)
        self.store.audit(
            safe_update.id,
            "mission:update_recorded",
            {"sequence": safe_update.sequence},
        )
        return safe_update

    def deliver_update(
        self,
        update_id: str,
        policy: NotificationPolicy,
        *,
        consent_id: str | None = None,
        channel_available: bool = True,
    ) -> UpdateDelivery:
        update = self.store.get(update_id)
        if not isinstance(update, MissionUpdate):
            raise KeyError(update_id)
        delivery_id = f"delivery:{update.update_id}:{policy.channel}"
        try:
            existing = self.store.get(delivery_id)
        except KeyError:
            existing = None
        if isinstance(existing, UpdateDelivery):
            return existing
        try:
            consent = self._require_consent(
                update.mission_id,
                policy.channel,
                consent_id or policy.require_consent_id,
                operation="notify",
            )
        except (KeyError, PermissionError, ValueError):
            if (
                policy.channel == "local-desktop"
                and not policy.require_consent_id
                and not consent_id
            ):
                # The active local desktop surface is the explicit local
                # indicator; phone/voice/remote channels always require a
                # persisted consent record.
                consent = None
            else:
                consent = None
        local_consent_exempt = (
            policy.channel == "local-desktop"
            and not policy.require_consent_id
            and not consent_id
        )
        safe_summary, categories, fields = _safe_text(update.summary)
        categories = sorted(
            set(categories) | set(update.provenance.get("redaction_categories", []))
        )
        fields = sorted(
            set(fields) | set(update.provenance.get("redaction_fields", []))
        )
        redaction_id = f"redaction:{update.update_id}:{policy.channel}"
        evidence = RedactionEvidence(
            id=redaction_id,
            redaction_id=redaction_id,
            source_id=update.update_id,
            channel=policy.channel,
            fields_redacted=fields,
            categories=categories,
            safe_digest=_digest(safe_summary),
            provenance={"component": "phase11-redactor"},
            sensitivity_labels=["redaction-evidence"],
        )
        self.store.put(evidence)
        status = UpdateDeliveryStatus.READY
        reason = ""
        previous = self._last_delivery(update.mission_id, policy.channel)
        fingerprint = _digest(
            safe_summary + "|" + update.state + "|" + ",".join(update.blockers)
        )
        if consent is None and not local_consent_exempt:
            status = UpdateDeliveryStatus.BLOCKED_CONSENT
            reason = "explicit channel consent is required"
        elif any(
            label not in policy.allowed_sensitivity
            for label in update.sensitivity_labels
        ):
            status = UpdateDeliveryStatus.BLOCKED_SENSITIVITY
            reason = "update sensitivity exceeds channel policy"
        elif consent is not None and any(
            label not in consent.allowed_sensitivity
            for label in update.sensitivity_labels
        ):
            status = UpdateDeliveryStatus.BLOCKED_SENSITIVITY
            reason = "update sensitivity exceeds channel consent"
        elif (
            policy.suppress_unchanged
            and previous is not None
            and previous.provenance.get("fingerprint") == fingerprint
            and not update.force_delivery
        ):
            status = UpdateDeliveryStatus.SUPPRESSED_UNCHANGED
            reason = "same material state already delivered"
        elif (
            not update.force_delivery
            and update.kind is UpdateKind.HEARTBEAT
            and update.materiality < policy.minimum_materiality
        ):
            status = UpdateDeliveryStatus.SUPPRESSED_UNCHANGED
            reason = "heartbeat below materiality threshold"
        elif (
            update.materiality < policy.minimum_materiality
            and not update.changed_fields
            and not update.blockers
        ):
            status = UpdateDeliveryStatus.SUPPRESSED_UNCHANGED
            reason = "update below materiality threshold"
        elif not channel_available or not self.channel_adapter.send(
            policy.channel, safe_summary
        ):
            status = UpdateDeliveryStatus.FAILED_CHANNEL
            reason = "channel unavailable; no retry side effect was attempted"
            self.interrupt(
                update.mission_id,
                InterruptionKind.REMOTE_CHANNEL_FAILURE,
                reason=reason,
                applies_to=[policy.channel],
            )
        delivery = UpdateDelivery(
            id=delivery_id,
            delivery_id=delivery_id,
            update_id=update.update_id,
            mission_id=update.mission_id,
            channel=policy.channel,
            status=status,
            safe_summary=safe_summary,
            suppressed_reason=reason,
            delivered_at=utc_now()
            if status is UpdateDeliveryStatus.DELIVERED_LOCAL
            else None,
            consent_id=consent.consent_id if consent else None,
            redaction_id=evidence.redaction_id,
            provenance={
                "component": "phase11-local-channel",
                "fingerprint": fingerprint,
                "live_effect": False,
            },
            sensitivity_labels=["remote-summary"],
        )
        if status is UpdateDeliveryStatus.READY:
            delivery.status = UpdateDeliveryStatus.DELIVERED_LOCAL
            delivery.delivered_at = utc_now()
        self.store.put(delivery)
        self.store.audit(
            delivery.id,
            "delivery:recorded",
            {"status": delivery.status.value, "live_effect": False},
        )
        return delivery

    def answer_remote_query(
        self,
        *,
        query_id: str,
        mission_id: str,
        channel: str,
        question: str,
        consent_id: str,
        channel_available: bool = True,
    ) -> RemoteQuery:
        summary = self.store.get_mission(mission_id)
        consent = self._require_consent(
            mission_id, channel, consent_id, operation="query"
        )
        if any(
            label not in consent.allowed_sensitivity
            for label in summary.sensitivity_labels
        ):
            raise PermissionError("query sensitivity exceeds channel consent")
        safe_source = (
            f"Last known mission state at update {summary.last_update_seq}: "
            f"{summary.state}. "
            f"Changed: {', '.join(summary.changed) or 'nothing recorded'}. "
            f"Blocked: {', '.join(summary.blockers) or 'nothing recorded'}."
        )
        answer, categories, fields = _safe_text(safe_source)
        redaction_id = f"redaction:{query_id}:{channel}"
        evidence = RedactionEvidence(
            id=redaction_id,
            redaction_id=redaction_id,
            source_id=query_id,
            channel=channel,
            fields_redacted=fields,
            categories=categories,
            safe_digest=_digest(answer),
            provenance={"component": "phase11-redactor"},
            sensitivity_labels=["redaction-evidence"],
        )
        self.store.put(evidence)
        if not channel_available:
            query = RemoteQuery(
                id=query_id,
                query_id=query_id,
                mission_id=mission_id,
                channel=channel,
                question=_safe_text(question)[0],
                consent_id=consent.consent_id,
                status="failed_channel",
                failure_reason="remote channel unavailable; no steering was attempted",
                redaction_id=redaction_id,
                provenance={"component": "phase11-remote-query", "live_effect": False},
            )
            self.store.put(query)
            self.interrupt(
                mission_id,
                InterruptionKind.REMOTE_CHANNEL_FAILURE,
                reason=query.failure_reason,
            )
            return query
        query = RemoteQuery(
            id=query_id,
            query_id=query_id,
            mission_id=mission_id,
            channel=channel,
            question=_safe_text(question)[0],
            consent_id=consent.consent_id,
            status="answered_stale" if summary.stale else "answered",
            answer=answer
            + (" Currentness: stale; refresh required." if summary.stale else ""),
            answer_as_of_update_seq=summary.last_update_seq,
            stale=summary.stale,
            redaction_id=redaction_id,
            provenance={"component": "phase11-remote-query", "live_effect": False},
            sensitivity_labels=["remote-summary"],
        )
        self.store.put(query)
        self.store.audit(
            query.id,
            "remote-query:answered",
            {"stale": query.stale, "live_effect": False},
        )
        return query

    def request_steering(
        self,
        *,
        steering_id: str,
        mission_id: str,
        turn_id: str,
        workspace: str,
        channel: str,
        consent_id: str,
        authority_grant_id: str,
    ) -> RemoteSteering:
        summary = self.store.get_mission(mission_id)
        status = SteeringStatus.REQUESTED
        reason = ""
        try:
            self._require_consent(mission_id, channel, consent_id, operation="steer")
        except (PermissionError, KeyError, ValueError) as exc:
            status = SteeringStatus.DECLINED
            reason = str(exc)
        scope = None
        if status is SteeringStatus.REQUESTED:
            try:
                record = self.store.get(authority_grant_id)
            except KeyError:
                record = None
            if not isinstance(record, AuthorityGrantScope):
                status = SteeringStatus.DECLINED
                reason = "no pre-existing exact authority scope"
            else:
                scope = record
                if record.revoked:
                    status = SteeringStatus.REVOKED
                    reason = "authority scope was revoked"
                elif _expired(record.expires_at):
                    status = SteeringStatus.STALE
                    reason = "authority scope expired"
                elif (record.mission_id, record.turn_id, record.workspace) != (
                    mission_id,
                    turn_id,
                    workspace,
                ):
                    status = SteeringStatus.STALE
                    reason = "mission, turn, or workspace scope mismatch"
                elif summary.canceled:
                    status = SteeringStatus.CANCELED
                    reason = "mission is canceled"
                elif self._emergency_stop:
                    status = SteeringStatus.EMERGENCY_STOPPED
                    reason = "Guardian emergency stop is active"
                elif (
                    summary.stale
                    or summary.turn_id != turn_id
                    or summary.workspace != workspace
                ):
                    status = SteeringStatus.STALE
                    reason = "mission ledger is stale or turn/workspace changed"
                elif self._budget_exceeded(summary):
                    status = SteeringStatus.BUDGET_EXCEEDED
                    reason = "mission steering budget is exhausted"
                elif record.capability != "codex:fork-stop":
                    status = SteeringStatus.DECLINED
                    reason = "authority capability does not cover fork/stop"
                else:
                    status = SteeringStatus.APPROVED
                    reason = "exact pre-existing scope matched"
        steering = RemoteSteering(
            id=steering_id,
            steering_id=steering_id,
            mission_id=mission_id,
            turn_id=turn_id,
            workspace=workspace,
            channel=channel,
            consent_id=consent_id,
            authority_grant_id=authority_grant_id,
            status=status,
            reason=reason,
            provenance={
                "component": "phase11-remote-steering",
                "guardian_authority_created": False,
            },
            sensitivity_labels=["mission-control"],
        )
        self.store.put(steering)
        self.store.audit(
            steering.id,
            "steering:decision",
            {
                "status": status.value,
                "scope": bool(scope),
                "guardian_authority_created": False,
            },
        )
        return steering

    def execute_steering(
        self, steering_id: str, *, tests_failed: bool
    ) -> RemoteSteering:
        steering = self.store.get_steering(steering_id)
        if steering.status is not SteeringStatus.APPROVED:
            return steering
        summary = self.store.get_mission(steering.mission_id)
        try:
            scope = self.store.get(steering.authority_grant_id)
        except KeyError:
            scope = None
        if (
            self._emergency_stop
            or summary.canceled
            or summary.stale
            or summary.turn_id != steering.turn_id
            or summary.workspace != steering.workspace
        ):
            steering.status = (
                SteeringStatus.EMERGENCY_STOPPED
                if self._emergency_stop
                else SteeringStatus.STALE
            )
            steering.reason = "execution boundary failed closed on current scope"
        elif (
            not isinstance(scope, AuthorityGrantScope)
            or scope.revoked
            or _expired(scope.expires_at)
            or (scope.mission_id, scope.turn_id, scope.workspace)
            != (steering.mission_id, steering.turn_id, steering.workspace)
        ):
            steering.status = SteeringStatus.REVOKED
            steering.reason = "authority scope is no longer live"
        elif scope.capability != "codex:fork-stop":
            steering.status = SteeringStatus.DECLINED
            steering.reason = "authority capability does not cover fork/stop"
        else:
            try:
                self._require_consent(
                    steering.mission_id,
                    steering.channel,
                    steering.consent_id,
                    operation="steer",
                )
            except (KeyError, PermissionError, ValueError) as exc:
                steering.status = SteeringStatus.DECLINED
                steering.reason = str(exc)
            if steering.status is not SteeringStatus.APPROVED:
                self.store.put(steering)
                return steering
        if steering.status is not SteeringStatus.APPROVED:
            self.store.put(steering)
            return steering
        if steering.operation != "fork_safer_stop_original_if_tests_fail" or (
            steering.requested_condition != "tests_fail"
        ):
            steering.status = SteeringStatus.DECLINED
            steering.reason = "steering condition is not the bounded Phase 11 operation"
        elif self._budget_exceeded(summary):
            steering.status = SteeringStatus.BUDGET_EXCEEDED
            steering.reason = "mission steering budget is exhausted"
        elif not tests_failed:
            steering.status = SteeringStatus.DECLINED
            steering.reason = "requested condition tests_fail was not met"
        else:
            # This is a deterministic local ledger action, not a Codex call.
            steering.status = SteeringStatus.EXECUTED_LOCAL
            steering.fork_id = f"local-fork:{steering.steering_id}"
            steering.original_stop_requested = True
            steering.live_effect = False
            steering.reason = "local fixture fork/stop recorded; no external effect"
            summary.used_budget["steering_count"] = (
                summary.used_budget.get("steering_count", 0) + 1
            )
            summary.state = "forked_local_original_stopped"
            summary.changed = ["safer fork selected", "original mission stopped"]
            summary.stale = False
            self.store.put(summary)
            self.store.audit(
                steering.id,
                "steering:executed_local",
                {"live_effect": False, "fork_id": steering.fork_id},
            )
        self.store.put(steering)
        return steering

    def timeline(self, mission_id: str) -> dict[str, Any]:
        records: list[dict[str, Any]] = []
        for record in self.store.list_records():
            if getattr(record, "mission_id", None) == mission_id:
                records.append(record.to_dict())
        record_ids = {record["id"] for record in records}
        audit = [
            event
            for event in self.store.audit_events()
            if event["object_id"] in record_ids
        ]
        return {
            "mission_id": mission_id,
            "records": records,
            "audit": audit,
            "live_external_effects": list(self.channel_adapter.external_effects),
        }

    def _require_consent(
        self, mission_id: str, channel: str, consent_id: str | None, *, operation: str
    ) -> ChannelConsent:
        if not consent_id:
            raise PermissionError("explicit channel consent is required")
        record = self.store.get(consent_id)
        if not isinstance(record, ChannelConsent):
            raise PermissionError("consent record is invalid")
        if not record.active or record.revoked_at or _expired(record.expires_at):
            raise PermissionError("channel consent is inactive or expired")
        if record.mission_id != mission_id or record.channel != channel:
            raise PermissionError("channel consent is outside the exact mission scope")
        if operation not in record.operations and "all" not in record.operations:
            raise PermissionError(f"channel consent does not cover {operation}")
        return record

    def _last_delivery(self, mission_id: str, channel: str) -> UpdateDelivery | None:
        deliveries = [
            record
            for record in self.store.list_records(kind=UpdateDelivery.contract_type)
            if isinstance(record, UpdateDelivery)
            and record.mission_id == mission_id
            and record.channel == channel
            and record.status is UpdateDeliveryStatus.DELIVERED_LOCAL
        ]
        return deliveries[-1] if deliveries else None

    def _highest_interruption(self, mission_id: str) -> Interruption | None:
        interruptions = [
            record
            for record in self.store.list_records(kind=Interruption.contract_type)
            if isinstance(record, Interruption)
            and record.mission_id == mission_id
            and record.status is InterruptionStatus.APPLIED
        ]
        return max(interruptions, key=lambda value: value.priority, default=None)

    def _budget_exceeded(self, summary: MissionSummary) -> bool:
        limit = summary.budget.get("steering_count")
        used = summary.used_budget.get("steering_count", 0)
        return limit is not None and used >= limit

    def _save_continuity(
        self,
        summary: MissionSummary,
        *,
        channel: str,
        presence_state: PresenceState | None = None,
        speech_state: str = "idle",
    ) -> ContinuityAcknowledgment:
        record_id = f"continuity:{summary.mission_id}:{channel}"
        try:
            current = self.store.get(record_id)
        except KeyError:
            current = None
        if isinstance(current, ContinuityAcknowledgment):
            current.last_update_seq = summary.last_update_seq
            current.presence_state = presence_state or current.presence_state
            current.speech_state = speech_state
            current.continuity_state = "continuous"
            current.last_update_id = (
                f"mission-update:{summary.last_update_seq}"
                if summary.last_update_seq
                else None
            )
            current.acknowledged_at = utc_now()
            self.store.put(current)
            return current
        record = ContinuityAcknowledgment(
            id=record_id,
            acknowledgment_id=record_id,
            mission_id=summary.mission_id,
            channel=channel,
            last_update_seq=summary.last_update_seq,
            last_update_id=f"mission-update:{summary.last_update_seq}"
            if summary.last_update_seq
            else None,
            presence_state=presence_state or PresenceState.UNKNOWN,
            speech_state=speech_state,
            provenance={"component": "phase11-continuity"},
            sensitivity_labels=["mission-control"],
        )
        self.store.put(record)
        return record


__all__ = ["AmbientMissionControl", "GuardianControl", "LocalChannelAdapter"]
