"""Durable, checkable knowledge for long-horizon agent work.

This package is Warranted's task layer: the public API a domain project builds
on. It will follow semantic versioning from 1.0; until then, changes are listed
in the changelog. The low-level kit is `warranted.host`, which is public but not
versioned. Every module whose name starts with an underscore is private.
"""

from importlib.metadata import version as _version

from warranted._chat_completions import LocalChatCompletions
from warranted._claims import Applicability
from warranted._guard import DuplicateGuard, default_normalize
from warranted._jobs import JobContext, JobLimits, JobResult, JobRunner, PodmanJobs
from warranted._ledger import Balance, Outcome
from warranted._openrouter import OpenRouterChatCompletions
from warranted._operations import Operation, OperationContext, OperationResult
from warranted._sandbox import IMAGE as DEFAULT_WORKER_IMAGE
from warranted._tasks import (
    FACTS_COUNT,
    FACTS_LIMIT,
    FEEDBACK_LIMIT,
    MEMORY_LIMIT,
    NOTES_COUNT,
    NOTES_LIMIT,
    CheckContext,
    Checker,
    Domain,
    MemoryEntry,
    MemorySpec,
    Project,
    RunConfig,
    RunOutcome,
    RunResult,
    RunStatus,
    Submission,
    TaskSpec,
    Verdict,
    VerdictStatus,
    domain_identity,
)

__version__ = _version("warranted")

__all__ = [
    "DEFAULT_WORKER_IMAGE",
    "FACTS_COUNT",
    "FACTS_LIMIT",
    "FEEDBACK_LIMIT",
    "MEMORY_LIMIT",
    "NOTES_COUNT",
    "NOTES_LIMIT",
    "Applicability",
    "Balance",
    "CheckContext",
    "Checker",
    "Domain",
    "DuplicateGuard",
    "JobContext",
    "JobLimits",
    "JobResult",
    "JobRunner",
    "LocalChatCompletions",
    "MemoryEntry",
    "MemorySpec",
    "OpenRouterChatCompletions",
    "Operation",
    "OperationContext",
    "OperationResult",
    "Outcome",
    "PodmanJobs",
    "Project",
    "RunConfig",
    "RunOutcome",
    "RunResult",
    "RunStatus",
    "Submission",
    "TaskSpec",
    "Verdict",
    "VerdictStatus",
    "default_normalize",
    "domain_identity",
]
