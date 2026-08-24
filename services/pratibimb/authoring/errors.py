"""Authoring harness domain errors."""
from __future__ import annotations

from services.pratibimb.authoring.constants import PolicyRejectionCode, PublishPreconditionCode


class AuthoringError(Exception):
    """Base for authoring harness failures."""


class PolicyError:
    """Recoverable policy failure — data for the UI, not a stack trace."""

    def __init__(self, code: PolicyRejectionCode, path: str, message: str) -> None:
        self.code = code
        self.path = path
        self.message = message


class PolicyRejected(AuthoringError):
    """
    Recoverable authoring/approval failure.

    Distinct from InvalidTransitionError (genuine invariant: wrong state).
    HTTP maps these to structured error lists the way CompilerErrors work.
    """

    def __init__(self, errors: tuple[PolicyError, ...]) -> None:
        if not errors:
            raise ValueError("PolicyRejected requires at least one PolicyError")
        self.errors = errors
        super().__init__(errors[0].message)

    @property
    def code(self) -> PolicyRejectionCode:
        return self.errors[0].code


class InvalidTransitionError(AuthoringError):
    """Requested state transition is not allowed from the current state."""


class InvalidStateTransitionError(AuthoringError):
    """Publish/retire precondition rejected: source state cannot reach target."""

    def __init__(
        self,
        *,
        from_state: str,
        to_state: str,
        code: str = PublishPreconditionCode.WRONG_STATE.value,
    ) -> None:
        self.from_state = from_state
        self.to_state = to_state
        self.code = code
        super().__init__(f"invalid transition {from_state} -> {to_state} ({code})")


class ScopeDeniedError(PolicyRejected):
    """Actor lacks the consent scope required for the transition."""

    def __init__(self, message: str) -> None:
        super().__init__((
            PolicyError(code=PolicyRejectionCode.SCOPE_DENIED, path="actor.scopes", message=message),
        ))


class AuthorCannotApproveError(PolicyRejected):
    """Author and approver must be distinct principals."""

    def __init__(self, message: str = "author cannot act as approver; distinct principals required") -> None:
        super().__init__((
            PolicyError(
                code=PolicyRejectionCode.AUTHOR_CANNOT_APPROVE,
                path="actor_subject_id",
                message=message,
            ),
        ))


class MissingFixturesError(AuthoringError):
    """Golden fixture pair is incomplete for the draft."""


class DryRunFailedError(AuthoringError):
    """Dry-run against golden fixtures did not pass."""


class BlueprintValidationError(AuthoringError):
    """Matcher compile failed; submit is blocked until the rubric is valid."""

    def __init__(self, errors: tuple[str, ...]) -> None:
        self.errors = errors
        super().__init__("blueprint compile failed")


class DryRunHashMismatchError(PolicyRejected):
    """Fresh dry-run at approval does not match the submit-time hash."""

    def __init__(
        self,
        submit_hash: str,
        fresh_hash: str,
        *,
        phase: str = "approve",
    ) -> None:
        self.submit_hash = submit_hash
        self.fresh_hash = fresh_hash
        self.phase = phase
        super().__init__((
            PolicyError(
                code=PolicyRejectionCode.DRY_RUN_HASH_MISMATCH,
                path="dry_run_result_hash",
                message=(
                    f"dry-run hash at {phase} does not match submit-time hash; "
                    "rubric or fixtures drifted since IN_REVIEW"
                ),
            ),
        ))


class ContentHashDriftError(PolicyRejected):
    """Blueprint content_hash at approve does not match the submit freeze."""

    def __init__(
        self,
        submit_hash: str,
        current_hash: str,
        *,
        phase: str = "approve",
    ) -> None:
        self.submit_hash = submit_hash
        self.current_hash = current_hash
        self.phase = phase
        super().__init__((
            PolicyError(
                code=PolicyRejectionCode.CONTENT_HASH_DRIFT,
                path="content_hash_snapshot",
                message=(
                    f"content_hash at {phase} does not match the DRAFT→IN_REVIEW freeze; "
                    "IN_REVIEW blueprints are immutable"
                ),
            ),
        ))


class DuplicateApproverError(PolicyRejected):
    """Same principal cannot supply both summative approvals."""

    def __init__(self, message: str = "approver_1_subject_id must differ from approver_2_subject_id") -> None:
        super().__init__((
            PolicyError(
                code=PolicyRejectionCode.DUPLICATE_APPROVER,
                path="actor_subject_id",
                message=message,
            ),
        ))


class PublishPreconditionFailed(PolicyRejected):
    """Recoverable publish failure with a typed sub-reason."""

    def __init__(
        self,
        *,
        precondition_code: PublishPreconditionCode,
        path: str,
        message: str,
    ) -> None:
        self.precondition_code = precondition_code
        super().__init__((
            PolicyError(
                code=PolicyRejectionCode.PUBLISH_PRECONDITION_FAILED,
                path=path,
                message=message,
            ),
        ))


class ApproverCannotPublishError(PolicyRejected):
    """Summative publisher must be distinct from author and both approvers."""

    def __init__(
        self,
        message: str = "publisher must be distinct from author and summative approvers",
    ) -> None:
        super().__init__((
            PolicyError(
                code=PolicyRejectionCode.APPROVER_CANNOT_PUBLISH,
                path="actor_subject_id",
                message=message,
            ),
        ))


class VersionStringBurnedError(PolicyRejected):
    """Version string permanently burned — republish after retire forbidden."""

    def __init__(self, *, case_id: str, version: str) -> None:
        super().__init__((
            PolicyError(
                code=PolicyRejectionCode.VERSION_STRING_BURNED,
                path="identity.version",
                message=f"version {case_id}@{version} is permanently burned",
            ),
        ))


class AlreadyPublishedError(PolicyRejected):
    """Draft already published."""

    def __init__(self) -> None:
        super().__init__((
            PolicyError(
                code=PolicyRejectionCode.ALREADY_PUBLISHED,
                path="current_state",
                message="draft is already PUBLISHED",
            ),
        ))


class RetirePreconditionFailed(PolicyRejected):
    """Recoverable retire failure with a typed sub-reason."""

    def __init__(
        self,
        *,
        precondition_code: str,
        path: str,
        message: str,
    ) -> None:
        self.precondition_code = precondition_code
        super().__init__((
            PolicyError(
                code=PolicyRejectionCode.RETIRE_PRECONDITION_FAILED,
                path=path,
                message=message,
            ),
        ))


class PublisherCannotRetireError(PolicyRejected):
    """Publisher and retirer must be distinct principals."""

    def __init__(
        self,
        message: str = "retirer must be distinct from the publisher",
    ) -> None:
        super().__init__((
            PolicyError(
                code=PolicyRejectionCode.PUBLISHER_CANNOT_RETIRE,
                path="actor_subject_id",
                message=message,
            ),
        ))


class AlreadyRetiredError(PolicyRejected):
    """Published version already carries a retirement tombstone."""

    def __init__(self) -> None:
        super().__init__((
            PolicyError(
                code=PolicyRejectionCode.ALREADY_RETIRED,
                path="retired_at",
                message="published case version is already RETIRED",
            ),
        ))


class BlueprintHashDriftError(PolicyRejected):
    """
    Session create-time blueprint hash does not match published row at finalize.

    Full hashes are retained on the exception for structured logs; the
    PolicyError message truncates to 12 hex chars each.
    """

    def __init__(
        self,
        *,
        session_id: str,
        case_id: str,
        case_version: str,
        stamped_hash: str,
        published_hash: str,
    ) -> None:
        self.session_id = session_id
        self.case_id = case_id
        self.case_version = case_version
        self.stamped_hash = stamped_hash
        self.published_hash = published_hash
        super().__init__((
            PolicyError(
                code=PolicyRejectionCode.BLUEPRINT_HASH_DRIFT,
                path=f"session.{session_id}.blueprint_content_hash",
                message=(
                    f"blueprint hash drift for {case_id}@{case_version}: "
                    f"stamped={stamped_hash[:12]} published={published_hash[:12]} "
                    f"(vs published.{case_id}.{case_version}.content_hash)"
                ),
            ),
        ))


class PublishedCaseNotFoundError(PolicyRejected):
    """No published_case_versions row for the tenant/case/version triple."""

    def __init__(
        self,
        *,
        tenant_id: str,
        case_id: str,
        case_version: str,
    ) -> None:
        self.tenant_id = tenant_id
        self.case_id = case_id
        self.case_version = case_version
        super().__init__((
            PolicyError(
                code=PolicyRejectionCode.PUBLISHED_CASE_NOT_FOUND,
                path=f"published.{case_id}.{case_version}",
                message=(
                    f"no published case version for tenant={tenant_id} "
                    f"{case_id}@{case_version}"
                ),
            ),
        ))


class PhaseNotImplementedError(NotImplementedError):
    """Transition belongs to a later delivery phase."""

    def __init__(self, phase: str = "phase D") -> None:
        super().__init__(phase)
