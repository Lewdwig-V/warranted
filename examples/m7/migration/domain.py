"""Prototype migration domain: the M5 configuration checker behind the M7 checker shape.

Each reference case runs in its own contained job, so cases share no process or
filesystem. The private `contract.json` names the obligations that gate
acceptance; a revision replaces it. Feedback shows only those obligations, so a
result the contract does not require stays host-only.
"""

import json
import runpy
from pathlib import Path

from warranted import (
    DEFAULT_WORKER_IMAGE,
    CheckContext,
    LeanProof,
    ProofTarget,
    Verdict,
    VerdictStatus,
)

HERE = Path(__file__).resolve().parent
M5_SOURCE = HERE.parent.parent / "m5/demo.py"
M5 = runpy.run_path(str(M5_SOURCE))
OBLIGATIONS = (
    "repository_integrity",
    "output_schema",
    "renaming",
    "legacy_label",
    "input_rejection",
    "repetition",
)


def run_cases(ctx: CheckContext, source: bytes, cases: dict) -> dict:
    """Run each case in its own contained job; return {case name: judge result}."""
    detail = {}
    for name, case in cases.items():
        job = ctx.run_job(
            DEFAULT_WORKER_IMAGE,
            ["python", "-I", "migrate.py"],
            {"migrate.py": source},
            stdin=case["input"].encode(),
            timeout_seconds=M5["CASE_SECONDS"],
        )
        detail[name] = M5["judge"](
            case, job.stdout, job.returncode, job.timed_out or job.truncated
        )
    return detail


MIGRATION = ProofTarget(
    HERE / "MigrationChallenge.lean", "Warranted.migration_renaming"
)


def renaming_correspondence(ctx: CheckContext) -> dict[str, bool]:
    """M5's migration application: the candidate renames host and timeout exactly.

    The theorem covers both label variants, so the label is not a premise; the
    legacy_label obligation keeps that gap closed.
    """
    try:
        source = M5["candidate_source"](ctx.candidate["result.json"])
    except (KeyError, ValueError, RecursionError):
        return {"renaming_correspondence": False}
    cases = {
        n: c
        for n, c in json.loads(ctx.private["references.json"]).items()
        if c["kind"] == "migrate"
    }
    if not cases:
        raise ValueError("no migrate case assesses renaming")
    detail = run_cases(ctx, source, cases)
    return {"renaming_correspondence": all(d["renaming"] for d in detail.values())}


class Migration:
    version = "1"
    isolated = True  # contained jobs over the candidate and task bytes only

    def check(self, ctx: CheckContext) -> Verdict:
        required = json.loads(ctx.private["contract.json"])["obligations"]
        if not required or not set(required) <= set(OBLIGATIONS):
            raise ValueError(f"contract names an unknown obligation: {required}")
        cases = json.loads(ctx.private["references.json"])
        try:
            source = M5["candidate_source"](ctx.candidate["result.json"])
        except KeyError:
            source, error = None, "result.json is missing"
        except (ValueError, RecursionError) as invalid:
            source, error = None, str(invalid)
        # An obligation is established only by the cases that assess it, and an
        # invalid patch is never executed, so it establishes nothing.
        results = {"repository_integrity": source is not None}
        detail = run_cases(ctx, source, cases) if source is not None else {}
        for values in detail.values():
            for obligation, passed in values.items():
                results[obligation] = results.get(obligation, True) and passed
        missing = set(required) - results.keys()
        if source is not None and missing:
            raise ValueError(f"no reference case assesses: {sorted(missing)}")
        shown = {name: results.get(name, False) for name in required}
        status = VerdictStatus.PASSED if all(shown.values()) else VerdictStatus.REJECTED
        feedback = {"obligations": shown}
        if source is None:
            feedback["error"] = error
        return Verdict(status, feedback, {"obligations": results, "cases": detail})


class MigrationDomain:
    name = "config-migration"
    version = "0.1"
    worker_image = DEFAULT_WORKER_IMAGE
    checkers = {
        "migration": Migration(),
        "renaming": LeanProof(MIGRATION, correspondence=renaming_correspondence),
    }
    sources = (M5_SOURCE,)  # loaded with runpy, so not found by following imports
