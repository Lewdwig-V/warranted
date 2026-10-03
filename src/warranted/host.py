"""Warranted's low-level host kit: public, but outside semantic versioning.

The task layer in `warranted` is built from these parts. Use them to build a
host that the task layer does not cover yet. Names here may change between
minor releases; such changes are listed in the changelog.
"""

from warranted._acceptance import (
    Acceptance,
    AcceptanceContext,
    Decision,
    Evidence,
    Status,
)
from warranted._acceptance import _digest as digest
from warranted._acceptance import _encode as encode_json
from warranted._attempts import ReceiptService
from warranted._chat_completions import (
    MAX_BYTES,
    MESSAGE_MARGIN,
    LocalChatCompletions,
    http_response,
)
from warranted._claims import Applicability, Assessment, Claims
from warranted._containers import PODMAN_COMMAND_TIMEOUT_SECONDS, SandboxFailure
from warranted._contexts import Condition, capture_context, capture_history
from warranted._exports import export_evidence
from warranted._jobs import (
    JobContext,
    JobLimits,
    JobResult,
    JobRunner,
    PodmanJobs,
    pinned_image,
)
from warranted._ledger import (
    ROOT_SCOPE,
    ArtifactRef,
    Balance,
    BudgetExceeded,
    Completion,
    CorruptArtifact,
    InvalidProject,
    Ledger,
    Manifest,
    Observation,
    OperationConflict,
    Origin,
    Outcome,
    Request,
    Result,
    Snapshot,
    SnapshotRef,
    UnknownOutcome,
    UnsupportedVersion,
)
from warranted._ledger import Operation as LedgerOperation
from warranted._ledger import _json_object as strict_json_object
from warranted._openrouter import OpenRouterChatCompletions, command_format
from warranted._operations import Operation, OperationContext, OperationResult
from warranted._proof_receipts import Proofs, proof_status
from warranted._proofs import ProofStatus, Verification, policy_digest
from warranted._proofs import verify as verify_proof
from warranted._sandbox import (
    CANDIDATE_LIMIT,
    IMAGE,
    SANDBOX_ID,
    Sandbox,
    decode_workspace,
    sandbox_id,
)
from warranted._worker import (
    REQUEST_MARKER,
    SUBMIT_MARKER,
    TOKEN_UNITS,
    AttemptResult,
    Episode,
    Journal,
    WorkerModel,
    model_reservation,
    record_once,
    run_workflow,
    submitted_candidate,
    submitted_files,
    unresolved,
)

__all__ = [
    "CANDIDATE_LIMIT",
    "IMAGE",
    "MAX_BYTES",
    "MESSAGE_MARGIN",
    "PODMAN_COMMAND_TIMEOUT_SECONDS",
    "REQUEST_MARKER",
    "ROOT_SCOPE",
    "SANDBOX_ID",
    "SUBMIT_MARKER",
    "TOKEN_UNITS",
    "Acceptance",
    "AcceptanceContext",
    "Applicability",
    "ArtifactRef",
    "Assessment",
    "AttemptResult",
    "Balance",
    "BudgetExceeded",
    "Claims",
    "Completion",
    "CorruptArtifact",
    "Condition",
    "Decision",
    "Episode",
    "Evidence",
    "InvalidProject",
    "JobContext",
    "JobLimits",
    "JobResult",
    "JobRunner",
    "Journal",
    "Ledger",
    "LedgerOperation",
    "LocalChatCompletions",
    "Manifest",
    "Observation",
    "OpenRouterChatCompletions",
    "Operation",
    "OperationConflict",
    "OperationContext",
    "OperationResult",
    "Origin",
    "Outcome",
    "PodmanJobs",
    "ProofStatus",
    "Proofs",
    "ReceiptService",
    "Request",
    "Result",
    "Sandbox",
    "SandboxFailure",
    "Snapshot",
    "SnapshotRef",
    "Status",
    "UnknownOutcome",
    "UnsupportedVersion",
    "Verification",
    "WorkerModel",
    "capture_context",
    "capture_history",
    "command_format",
    "decode_workspace",
    "digest",
    "encode_json",
    "export_evidence",
    "http_response",
    "model_reservation",
    "pinned_image",
    "policy_digest",
    "proof_status",
    "record_once",
    "run_workflow",
    "sandbox_id",
    "strict_json_object",
    "submitted_candidate",
    "submitted_files",
    "unresolved",
    "verify_proof",
]
